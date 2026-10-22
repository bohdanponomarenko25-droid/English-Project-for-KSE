"""
Typo-tolerant checker for Writing Task 5 (schema be-writing-key/1.0).

Implements the rules from Writing_Task5_Key_HTML.pdf exactly:
  * normalization steps 1-7 (applied to the student answer and to the key),
  * word-by-word comparison with Damerau-Levenshtein (OSA) distance,
  * per-word allowance by key-word length (<=3: 0, 4-6: 1, >=7: 2),
  * protected words must match exactly (auxiliaries, modals, pronouns, if/whether, time words...),
  * suffix class must match (-ing / -ed / -s / none),
  * one missing/extra space = 1 typo,
  * at most 3 typos per answer,
  * full match = 2 points, partial match = 1 point + note, else 0.

Pure Python, standard library only.
"""

import re
import unicodedata

# ---------------------------------------------------------------- normalization

_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
_RESTORE_NT = re.compile(r"\b(do|does|did|could|would|should|is|are|was|were|has|have|had|ca|wo)(nt|n't|'t)\b")
_RESTORE_D = re.compile(r"\b(you|i|we|they|he|she)d (better|ask|check|acknowledge|prepare|support)\b")


def normalize(text, spelling_variants=None):
    """Steps 1-7 of the key's normalization block."""
    s = unicodedata.normalize("NFKC", str(text or "")).lower()           # 1
    s = s.translate(_APOS)                                                  # 2
    s = s.replace("“", "").replace("”", "").replace('"', "")
    s = re.sub(r"[-‐-―−]", " ", s)                           # 3
    s = s.replace(".", "")
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _RESTORE_NT.sub(lambda m: m.group(1) + "n't", s)                  # 4
    s = _RESTORE_D.sub(lambda m: m.group(1) + "'d " + m.group(2), s)
    s = re.sub(r"\bcan't\b", "cannot", s)                                  # 5
    s = re.sub(r"\bwon't\b", "will not", s)
    s = re.sub(r"\bshan't\b", "shall not", s)
    s = s.replace("n't", " not")
    s = s.replace("'re", " are").replace("'ve", " have")
    s = re.sub(r"\bi'm\b", "i am", s)
    if spelling_variants:                                                   # 6
        s = " ".join(spelling_variants.get(w, w) for w in s.split())
    return re.sub(r"\s+", " ", s).strip()                                   # 7


# ---------------------------------------------------------------- token matching

def osa_distance(a, b):
    """Optimal string alignment (restricted Damerau-Levenshtein) distance."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def suffix_class(word):
    if word.endswith("ing"):
        return "ing"
    if word.endswith("ed"):
        return "ed"
    if word.endswith("s") and len(word) > 3:
        return "s"
    return "none"


def allowed_edits(key_word, edit_cfg):
    n = len(key_word)
    if n <= 3:
        return edit_cfg.get("len<=3", 0)
    if n <= 6:
        return edit_cfg.get("len4-6", 1)
    return edit_cfg.get("len>=7", 2)


class Tolerance:
    def __init__(self, cfg):
        self.max_typos = int(cfg.get("max_typos_per_answer", 3))
        self.edit_cfg = cfg.get("edit_distance", {})
        self.protected = set(cfg.get("protected_tokens", []))
        self.spelling = cfg.get("spelling_variants", {})


def match_tokens(answer, key, tol):
    """Compare two normalized strings.

    Returns (typos, corrections) if they match within tolerance, else None.
    corrections = list of {"typed": ..., "expected": ...}.
    """
    if answer == key:
        return 0, []
    a, k = answer.split(), key.split()
    if len(a) == len(k):
        typos, corrections = 0, []
        for x, y in zip(a, k):
            if x == y:
                continue
            if x in tol.protected or y in tol.protected:
                return None
            if suffix_class(x) != suffix_class(y):
                return None
            if osa_distance(x, y) > allowed_edits(y, tol.edit_cfg):
                return None
            typos += 1
            corrections.append({"typed": x, "expected": y})
            if typos > tol.max_typos:
                return None
        return typos, corrections
    # space rule: exactly one missing / extra space
    if abs(len(a) - len(k)) == 1 and answer.replace(" ", "") == key.replace(" ", ""):
        return 1, [{"typed": answer, "expected": key, "kind": "space"}]
    return None


# ---------------------------------------------------------------- item checking

def check_answer(raw_answer, item, tol, scoring):
    """Check one student answer against one key item.

    item: dict with id, question, cue, source, model_answer,
          full: [text], partial: [{"text", "note"}], common_errors: [{"fragment", "feedback"}]
    """
    norm = normalize(raw_answer, tol.spelling)
    result = {
        "id": item["id"],
        "question": item["question"],
        "answer": raw_answer or "",
        "normalized": norm,
        "model_answer": item["model_answer"],
        "max_points": scoring.get("full", 2),
        "status": "empty",
        "points": 0,
        "typos": 0,
        "corrections": [],
        "matched": None,
        "note": None,
        "feedback": [],
    }
    if not norm:
        return result

    def best(candidates):
        found = None
        for text, note in candidates:
            m = match_tokens(norm, normalize(text, tol.spelling), tol)
            if m is not None and (found is None or m[0] < found[0][0]):
                found = (m, text, note)
                if m[0] == 0:
                    break
        return found

    hit = best((t, None) for t in item["full"])
    if hit:
        (typos, corr), text, _ = hit
        result.update(status="full", points=scoring.get("full", 2), typos=typos, corrections=corr, matched=text)
    else:
        hit = best((p["text"], p.get("note")) for p in item["partial"])
        if hit:
            (typos, corr), text, note = hit
            result.update(status="partial", points=scoring.get("partial", 1), typos=typos,
                          corrections=corr, matched=text, note=note)
        else:
            result.update(status="wrong", points=scoring.get("wrong", 0))

    for ce in item.get("common_errors", []):
        frag = normalize(ce["fragment"], tol.spelling)
        if frag and frag in norm:
            result["feedback"].append({"fragment": ce["fragment"], "feedback": ce["feedback"]})
    return result


def items_from_json(key_json):
    """Convert the raw writing_key.json structure into checker items keyed by (variant, question)."""
    out = {}
    for it in key_json["items"]:
        out[(int(it["variant"]), int(it["question"]))] = {
            "id": it["id"],
            "variant": int(it["variant"]),
            "question": int(it["question"]),
            "cue": it["cue"],
            "source": it["source"],
            "model_answer": it["model_answer"],
            "full": list(it.get("full_expanded", [])),
            "partial": [{"text": p["text"], "note": p.get("note")} for p in it.get("partial_expanded", [])],
            "common_errors": list(it.get("common_errors", [])),
        }
    return out
