# KSE CalmTest B2: Writing checking service

A small local server for the module test. It serves the test page, stores the Writing Task 5 answer key in an SQLite database, checks Writing answers with typo tolerance, and saves every finished test for the instructor.

You only need Python 3.8 or newer. There is nothing to install.

## Start

- **Windows:** double-click `start_server.bat`
- **macOS / Linux:** run `./start_server.sh` (or `python3 server.py`)

Then open **http://localhost:8000** in a browser.

On first start the server creates `kse_tests.db` and imports `writing_key.json` into it.

| Command | What it does |
|---|---|
| `python server.py` | This computer only |
| `python server.py --host 0.0.0.0` | Students on the same Wi-Fi open `http://<teacher's IP>:8000` |
| `python server.py --port 9000` | Use another port |
| `python server.py --reload-key` | Re-import `writing_key.json` after you edit the key |

## How Writing is checked

The rules come from `Writing_Task5_Key_HTML.pdf` (schema `be-writing-key/1.0`) and live in `writing_checker.py`.

1. **Normalization.** The student answer and the key go through the same steps: lowercase, quotes and punctuation removed, `dont` → `don't` → `do not`, `bar-chart` = `bar chart`, US spelling turned into UK (`practice` → `practise`).
2. **Word-by-word comparison** with Damerau–Levenshtein distance. A swap of two neighbouring letters counts as one typo.
   - Words of 3 letters or fewer must match exactly. Words of 4–6 letters may have 1 typo, and words of 7 or more may have 2.
   - At most 3 typos per sentence. One missing or extra space counts as 1 typo.
3. **What is never treated as a typo:**
   - **Protected words** must match exactly: auxiliaries and modals, pronouns, if/whether, now/then/tomorrow, advise/advice, irregular forms like send/sent. So `will be send` is wrong, not a typo.
   - **The ending class** must match: -ing / -ed / -s / none. So `finish` ≠ `finished`, and `rehearse` ≠ `rehearsing`.
4. **Scoring.** A full match earns the whole sentence, a partial match half the sentence with an explanation, and anything else 0. The `common_errors` entries in the key add feedback to the report but never change the score.

In the test, Writing is worth 2.0 points, so each sentence is worth 1/3 point: a full match gives 0.33, a partial match 0.17.

**If the server is not running,** the page still works. Writing falls back to exact matching, and any mismatch is marked "?" for the instructor to check by hand.

## Instructor endpoints

| Address | Returns |
|---|---|
| `GET /api/results.csv` | All saved tests for Excel (one row per student: score per task, Writing full/partial/wrong) |
| `GET /api/results` | The same data as JSON, with the per-sentence Writing verdicts |
| `GET /api/health` | Server status and key version |
| `POST /api/writing/check` | `{"variant": 1, "answers": ["…" ×6]}` → a verdict per sentence |

## Files

| File | Purpose |
|---|---|
| `server.py` | HTTP server, SQLite database, API |
| `writing_checker.py` | Normalization and typo-tolerant matching (pure Python) |
| `writing_key.json` | The answer key extracted from the PDF |
| `static/index.html` | The test page |
| `test_writing_checker.py` | `python test_writing_checker.py`: 326 checks covering every key variant, typos to forgive, and grammar errors to reject |

## Known limitation

The page still contains the answer keys for Listening, Grammar and the offline Writing fallback. A student who opens the page source can find them. Moving all grading to the server would close this gap.
