"""
KSE CalmTest B2 — local checking service.

  * Serves the test page (static/index.html) at  http://localhost:8000/
  * Keeps the Writing Task 5 answer key in an SQLite database (kse_tests.db),
    imported from writing_key.json on first start.
  * Checks Writing answers with typo tolerance:   POST /api/writing/check
  * Stores finished test results for the instructor: POST /api/results,
    GET /api/results (JSON), GET /api/results.csv (Excel-friendly CSV)

Standard library only (Python 3.8+). Run:

    python server.py                    # http://localhost:8000
    python server.py --host 0.0.0.0     # let students on the same Wi-Fi connect
    python server.py --reload-key       # re-import writing_key.json into the database
"""

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from writing_checker import Tolerance, check_answer  # noqa: E402

STATIC_DIR = os.path.join(HERE, "static")
DEFAULT_DB = os.path.join(HERE, "kse_tests.db")
DEFAULT_KEY = os.path.join(HERE, "writing_key.json")
MAX_BODY = 2 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS key_meta (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS writing_items (
    id           TEXT PRIMARY KEY,
    variant      INTEGER NOT NULL,
    question     INTEGER NOT NULL,
    cue          TEXT,
    source       TEXT,
    model_answer TEXT,
    UNIQUE (variant, question)
);
CREATE TABLE IF NOT EXISTS writing_answers (
    item_id TEXT NOT NULL REFERENCES writing_items(id) ON DELETE CASCADE,
    kind    TEXT NOT NULL CHECK (kind IN ('full', 'partial')),
    text    TEXT NOT NULL,
    note    TEXT
);
CREATE INDEX IF NOT EXISTS idx_answers_item ON writing_answers(item_id);
CREATE TABLE IF NOT EXISTS writing_common_errors (
    item_id  TEXT NOT NULL REFERENCES writing_items(id) ON DELETE CASCADE,
    fragment TEXT NOT NULL,
    feedback TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    student     TEXT,
    group_name  TEXT,
    test_type   TEXT,
    variant     TEXT,
    total       REAL,
    max_score   REAL,
    writing_json TEXT,
    report_json TEXT NOT NULL
);
"""


# ---------------------------------------------------------------- database

def connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def import_key(con, key_path):
    with open(key_path, encoding="utf-8") as f:
        key = json.load(f)
    with con:
        con.execute("DELETE FROM writing_answers")
        con.execute("DELETE FROM writing_common_errors")
        con.execute("DELETE FROM writing_items")
        con.execute("DELETE FROM key_meta")
        for name in ("schema", "test", "points_per_item", "scoring", "tolerance", "normalization", "matching_order"):
            con.execute("INSERT INTO key_meta(name, value) VALUES (?, ?)", (name, json.dumps(key.get(name), ensure_ascii=False)))
        con.execute("INSERT INTO key_meta(name, value) VALUES ('imported_at', ?)", (json.dumps(datetime.now().isoformat(timespec="seconds")),))
        for it in key["items"]:
            con.execute(
                "INSERT INTO writing_items(id, variant, question, cue, source, model_answer) VALUES (?,?,?,?,?,?)",
                (it["id"], int(it["variant"]), int(it["question"]), it["cue"], it["source"], it["model_answer"]),
            )
            for t in it.get("full_expanded", []):
                con.execute("INSERT INTO writing_answers(item_id, kind, text) VALUES (?, 'full', ?)", (it["id"], t))
            for p in it.get("partial_expanded", []):
                con.execute("INSERT INTO writing_answers(item_id, kind, text, note) VALUES (?, 'partial', ?, ?)",
                            (it["id"], p["text"], p.get("note")))
            for ce in it.get("common_errors", []):
                con.execute("INSERT INTO writing_common_errors(item_id, fragment, feedback) VALUES (?,?,?)",
                            (it["id"], ce["fragment"], ce["feedback"]))
    return len(key["items"])


def load_key(con):
    """Read the key from the database into memory for fast checking."""
    meta = {r["name"]: json.loads(r["value"]) for r in con.execute("SELECT name, value FROM key_meta")}
    items = {}
    for r in con.execute("SELECT * FROM writing_items ORDER BY variant, question"):
        items[(r["variant"], r["question"])] = {
            "id": r["id"], "variant": r["variant"], "question": r["question"], "cue": r["cue"],
            "source": r["source"], "model_answer": r["model_answer"],
            "full": [], "partial": [], "common_errors": [],
        }
    by_id = {it["id"]: it for it in items.values()}
    for r in con.execute("SELECT item_id, kind, text, note FROM writing_answers ORDER BY rowid"):
        it = by_id[r["item_id"]]
        if r["kind"] == "full":
            it["full"].append(r["text"])
        else:
            it["partial"].append({"text": r["text"], "note": r["note"]})
    for r in con.execute("SELECT item_id, fragment, feedback FROM writing_common_errors ORDER BY rowid"):
        by_id[r["item_id"]]["common_errors"].append({"fragment": r["fragment"], "feedback": r["feedback"]})
    return meta, items


class KeyStore:
    def __init__(self, db_path, key_path, reload_key=False):
        self.db_path = db_path
        self.lock = threading.Lock()
        con = connect(db_path)
        con.executescript(SCHEMA)
        count = con.execute("SELECT COUNT(*) FROM writing_items").fetchone()[0]
        if reload_key or count == 0:
            n = import_key(con, key_path)
            print(f"[key] imported {n} Writing items from {os.path.basename(key_path)} into {os.path.basename(db_path)}")
        self.meta, self.items = load_key(con)
        con.close()
        self.tol = Tolerance(self.meta["tolerance"])
        self.scoring = self.meta["scoring"]

    def check(self, variant, answers):
        results = []
        for i, ans in enumerate(answers, start=1):
            item = self.items.get((variant, i))
            if item is None:
                raise KeyError(f"no Writing item for variant {variant}, question {i}")
            results.append(check_answer(ans, item, self.tol, self.scoring))
        return results

    def save_submission(self, report):
        writing = report.get("writingCheck")
        with self.lock:
            con = connect(self.db_path)
            with con:
                cur = con.execute(
                    "INSERT INTO submissions(received_at, student, group_name, test_type, variant, total, max_score, writing_json, report_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), str(report.get("student", ""))[:200],
                     str(report.get("group", ""))[:200], str(report.get("type", ""))[:20], str(report.get("variant", ""))[:200],
                     _num(report.get("total")), _num(report.get("max")),
                     json.dumps(writing, ensure_ascii=False) if writing is not None else None,
                     json.dumps(report, ensure_ascii=False)),
                )
            new_id = cur.lastrowid
            con.close()
        return new_id

    def list_submissions(self):
        con = connect(self.db_path)
        rows = [dict(r) for r in con.execute("SELECT * FROM submissions ORDER BY id DESC")]
        con.close()
        return rows


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    store = None  # set in main()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        # The page may also be opened straight from disk (file://), so allow cross-origin API calls.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        if self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, obj, status=HTTPStatus.OK):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("empty or too large request body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({"ok": True, "schema": self.store.meta.get("schema"),
                               "items": len(self.store.items), "imported_at": self.store.meta.get("imported_at")})
        if path == "/api/results":
            rows = self.store.list_submissions()
            for r in rows:
                r.pop("report_json", None)
                r["writing"] = json.loads(r.pop("writing_json")) if r.get("writing_json") else None
            return self._json({"results": rows})
        if path == "/api/results.csv":
            return self._send_csv()
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        if path in ("/", ""):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._read_json()
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": f"bad request: {e}"}, HTTPStatus.BAD_REQUEST)

        if path == "/api/writing/check":
            try:
                variant = int(data["variant"])
                answers = data["answers"]
                if not isinstance(answers, list) or len(answers) != 6:
                    raise ValueError("answers must be a list of 6 strings")
                answers = [str(a or "")[:500] for a in answers]
                results = self.store.check(variant, answers)
            except (KeyError, ValueError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            total = sum(r["points"] for r in results)
            max_total = sum(r["max_points"] for r in results)
            return self._json({"variant": variant, "results": results, "points": total, "max_points": max_total,
                               "schema": self.store.meta.get("schema")})

        if path == "/api/results":
            if not isinstance(data, dict):
                return self._json({"error": "report must be an object"}, HTTPStatus.BAD_REQUEST)
            new_id = self.store.save_submission(data)
            return self._json({"ok": True, "id": new_id})

        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_csv(self):
        rows = self.store.list_submissions()
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        header = ["ID", "Отримано", "Студент", "Група", "Тест", "Варіант", "Разом", "Макс"]
        task_titles = []
        for r in rows:
            rep = json.loads(r["report_json"])
            for b in rep.get("blocks", []):
                if _col(b.get("title")) not in task_titles:
                    task_titles.append(_col(b.get("title")))
        header += task_titles + ["Speaking", "Writing: повні / часткові / неправильні"]
        w.writerow(header)
        for r in rows:
            rep = json.loads(r["report_json"])
            scores = {_col(b.get("title")): b.get("score") for b in rep.get("blocks", [])}
            wr = json.loads(r["writing_json"]) if r.get("writing_json") else None
            wsum = ""
            if wr:
                st = [x.get("status") for x in wr]
                wsum = f'{st.count("full")} / {st.count("partial")} / {st.count("wrong") + st.count("empty")}'
            sp = rep.get("speaking") or {}
            w.writerow([r["id"], r["received_at"], r["student"], r["group_name"], rep.get("testName", r["test_type"]),
                        r["variant"], _fmt(r["total"]), _fmt(r["max_score"])]
                       + [_fmt(scores.get(t)) for t in task_titles]
                       + [_fmt(sp.get("score")) if sp else "", wsum])
        body = ("﻿" + out.getvalue()).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="kse_results.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _col(title):
    """Column name per task, independent of the variant's text title: 'Reading 1 — GreenCup (True/False)' -> 'Reading 1'."""
    return str(title or "").split(" \u2014 ")[0].split(" (")[0].strip()


def _fmt(v):
    """Up to 3 decimals for 0.125-point items: 0.875 -> 0,875; 0.6 -> 0,60."""
    if v is None:
        return ""
    s3 = f"{round(float(v), 3):.3f}"
    return (s3[:-1] if s3.endswith("0") else s3).replace(".", ",")


def main():
    ap = argparse.ArgumentParser(description="KSE CalmTest B2 checking service")
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only; 0.0.0.0 = whole local network")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--reload-key", action="store_true", help="re-import writing_key.json into the database")
    args = ap.parse_args()

    if not os.path.exists(os.path.join(STATIC_DIR, "index.html")):
        sys.exit("static/index.html not found — put the test page there.")
    Handler.store = KeyStore(args.db, args.key, reload_key=args.reload_key)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"KSE CalmTest service: http://{shown}:{args.port}/   ({len(Handler.store.items)} Writing items, "
          f"schema {Handler.store.meta.get('schema')})")
    print(f"Instructor results: http://{shown}:{args.port}/api/results.csv")
    if args.host == "0.0.0.0":
        print("Students on the same network: open http://<this computer's IP>:%d/" % args.port)
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
