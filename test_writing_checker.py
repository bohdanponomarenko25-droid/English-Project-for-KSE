"""Run:  python test_writing_checker.py   (exit code 0 = all passed)"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from writing_checker import Tolerance, check_answer, items_from_json, normalize  # noqa: E402

KEY = json.load(open(os.path.join(HERE, "writing_key.json"), encoding="utf-8"))
TOL = Tolerance(KEY["tolerance"])
SCORING = KEY["scoring"]
ITEMS = items_from_json(KEY)

failures = []


def expect(variant, q, answer, status, typos=None, label=""):
    r = check_answer(answer, ITEMS[(variant, q)], TOL, SCORING)
    ok = r["status"] == status and (typos is None or r["typos"] == typos)
    if not ok:
        failures.append(f"V{variant} Q{q} {label!r}: {answer!r} -> {r['status']} ({r['typos']} typos), expected {status}"
                        + (f" ({typos} typos)" if typos is not None else ""))


# 1. Every expanded key string is stable under normalization and scores as expected.
for (v, q), it in ITEMS.items():
    for t in it["full"]:
        if normalize(t, TOL.spelling) != t:
            failures.append(f"{it['id']}: full key not normalized: {t!r} -> {normalize(t, TOL.spelling)!r}")
        expect(v, q, t, "full", 0, "full key")
    for p in it["partial"]:
        expect(v, q, p["text"], "partial", 0, "partial key")

# 2. Reference answers as a student would type them (capitals, punctuation, curly quotes, contractions).
reference = {
    (1, 1): "Julia was appointed Head of IT last month.",
    (1, 2): "Mark said that he was preparing the presentation then.",
    (1, 3): "If I were you, I would ask my manager for feedback.",
    (1, 4): "The handouts will be sent tomorrow.",
    (1, 5): "She asked me whether I had finished my CV.",
    (1, 6): "How about rehearsing the speech again?",
    (2, 1): "All applications are being reviewed now.",
    (2, 2): "She said she would apply for the internship the following day.",
    (2, 3): "If I were you, I'd check the compensation package carefully.",
    (2, 4): "Two candidates have already been hired.",
    (2, 5): "He asked me where I had completed my gap year.",
    (2, 6): "I advise you to improve your CV by adding voluntary roles.",
    (3, 1): "Reasonable prices are maintained by the company.",
    (3, 2): "The director said that they had reduced their carbon footprint.",
    (3, 3): "Why don’t you support local charities?",
    (3, 4): "The company’s sustainability report will be published next week.",
    (3, 5): "The auditor asked us if we obeyed the law.",
    (3, 6): "If I were you, I would acknowledge the customers’ concerns.",
    (4, 1): "The figures are being interpreted now.",
    (4, 2): "Anna said she couldn't attend the meeting that day.",
    (4, 3): "I recommend adding a hook to your presentation.",
    (4, 4): "The handouts had been distributed before the talk began.",
    (4, 5): "The manager asked why I was using that bar-chart.",
    (4, 6): "You ought to state the goals clearly.",
    (5, 1): "The report will have been finished by Friday.",
    (5, 2): "He said he had been studying the nervous system for two weeks.",
    (5, 3): "You'd better prepare carefully for the presentation.",
    (5, 4): "Signals are being transmitted through a network of nerves.",
    (5, 5): "The interviewer asked her if she had ever experienced that level of stress.",
    (5, 6): "My advice would be to practice the vocabulary before the speaking exam.",
}
for (v, q), a in reference.items():
    expect(v, q, a, "full", 0, "reference")

# 3. Typos that must be forgiven.
typo_ok = [
    (1, 1, "Julia was apointed Head of IT last month.", 1),
    (1, 2, "Mark said he was preparing the presentaion then", 1),
    (1, 6, "How about reherasing the speech again?", 1),
    (2, 4, "Two candidates have already been hried.", 1),
    (2, 6, "I advise you to improve your CV by adding voluntery roles.", 1),
    (3, 3, "Why dont you support local charities", 0),
    (3, 3, "Why don't you suport local charites?", 2),
    (4, 2, "Anna said she couldnt attend the meeting that day.", 0),
    (4, 2, "Anna said she could't attend the meeting that day.", 0),
    (4, 5, "The manager asked why I was using that barchart.", 1),
    (5, 3, "You had better prepare carefuly for the presentaton.", 2),
    (5, 4, "Signals are being transmited through a network of nerves.", 1),
    (5, 2, "He said he had been studing the nervous sytem for two weeks.", 2),
    (5, 5, "The interviewer asked her if she had ever experienced that levle of stres.", 2),
    (3, 6, "If I were you, I would acknowlege the customers concerns.", 1),
]
for v, q, a, n in typo_ok:
    expect(v, q, a, "full", n, "typo")

# 4. Grammar / content errors that must NOT be forgiven.
must_fail = [
    (1, 4, "The handouts will be send tomorrow."),                 # base form, protected
    (1, 5, "She asked me if I has finished my CV."),               # wrong auxiliary
    (1, 1, "Julia is appointed Head of IT last month."),           # tense
    (1, 1, "Julia has been appointed Head of IT last month."),     # tense
    (1, 3, "If I were you, I will ask my manager for feedback."),  # will vs would
    (2, 5, "He asked me where did I complete my gap year."),       # word order
    (3, 4, "The sustainability report will be publish next week."),  # base form
    (4, 4, "The handouts has been distributed before the talk began."),
    (5, 1, "The report will has been finished by Friday."),
    (5, 1, "The report will have been finish by Friday."),         # suffix class
    (2, 1, "All applications are being review now."),              # suffix class
    (1, 1, "Juila was apionted Haed of IT lsat mnoth."),           # > 3 typos
    (5, 3, "You had beter prepare carefully for the presentation."),  # "better" is a protected word in the key
    (4, 6, "You state the goals clearly."),                        # structure missing
    (5, 6, "My advice would be to learn the vocabulary before the speaking exam."),  # different verb
]
for v, q, a in must_fail:
    expect(v, q, a, "wrong", None, "must fail")

# 5. Partial credit with the right note.
partial_cases = [
    (1, 6, "How about rehearse the speech again?"),
    (2, 6, "I advice you to improve your CV by adding voluntary roles."),
    (1, 3, "If I was you, I would ask my manager for feedback."),
    (4, 2, "Anna said she could not attend the meeting today."),
    (2, 2, "She said she will apply for the internship the next day."),
    (2, 4, "Two candidates have been hired."),
    (4, 6, "You ought state the goals clearly."),
    (1, 6, "How about rehearse the speach again?"),                # partial + typo
]
for v, q, a in partial_cases:
    expect(v, q, a, "partial", None, "partial")

# 6. Common-error feedback is attached.
r = check_answer("Julia is appointed Head of IT last month.", ITEMS[(1, 1)], TOL, SCORING)
if not r["feedback"]:
    failures.append("common_errors feedback missing for 'julia is appointed'")

# 7. Empty answer.
expect(1, 1, "   ", "empty", None, "empty")

total = (sum(len(i["full"]) + len(i["partial"]) for i in ITEMS.values())
         + len(reference) + len(typo_ok) + len(must_fail) + len(partial_cases) + 2)
if failures:
    print(f"FAILED {len(failures)} of {total} checks:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"OK: {total} checks passed ({len(ITEMS)} items).")
