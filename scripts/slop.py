#!/usr/bin/env python3
"""AI-writing tells, detected mechanically instead of asked about politely.

The sameness gate (`sameness.py`) catches a draft that is too similar to the
corpus. This catches a draft that is recognisably MACHINE-WRITTEN even when it
is perfectly unique - a different failure with the same cause, and the one a
model cannot self-assess, because the patterns below are exactly the ones it
reaches for without noticing.

  scan     count every tell in a draft, with line numbers and the fix
  diff     compare two drafts (did the rewrite actually remove them)
  rules    print the catalog

WHY A SCRIPT AND NOT A CHECKLIST. Every other SEO tool ships these as prose for
the model to bear in mind while writing. A model bearing its own tells in mind
is the same instrument grading itself, and it scores well - I would. Regexes do
not have that problem. `binary_contrast` either matched on line 42 or it did
not, and the count is the same on every run.

WHAT IT DELIBERATELY DOES NOT DO: judge. A score is not emitted, because
"slop density 0.7" invites optimising the number rather than the prose. It
returns located hits and the reason each is a tell; the rewrite is a human
(or model) act performed with that list open. Several patterns are legitimate
ONCE - one binary contrast is rhetoric, ten is a fingerprint - so the catalog
carries a per-rule `tolerance` and the report says which rules exceeded it.

The catalog is a curated list of patterns that mark prose as machine-written.
See `references/deslop.md` for how to read a report and why there is no score.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# (rule, severity, tolerance, regex, why, fix)
# `tolerance` = occurrences allowed before it is reported. 0 = never fine.
RULES = [
    ("throat_clearing", "high", 0,
     # `^[ \t]*`, never `^\s*`: \s matches the preceding newline, so the match
     # starts on the BLANK line above and every reported line number is one low.
     r"(?im)^[ \t]*(?:here'?s (?:the thing|what|why|the kicker|the deal|where it gets)"
     r"|the (?:uncomfortable )?truth is|let me be clear|i'?m going to be honest"
     r"|can we talk about|it turns out that)\b",
     "an opener that announces a point instead of making it",
     "delete the opener and start with the point"),

    ("emphasis_crutch", "high", 0,
     r"(?i)\b(?:full stop\.|period\.|let that sink in|make no mistake"
     r"|here'?s why that matters|this matters because)\b",
     "emphasis asserted rather than earned",
     "cut it; if the fact is not striking on its own, the sentence is the problem"),

    ("pedagogical", "high", 0,
     r"(?i)\b(?:let'?s (?:break this down|unpack|explore|dive in|delve into)"
     r"|think of it (?:as|like)|imagine a world where)\b",
     "teacher voice at a reader who did not ask to be taught",
     "state the fact; trust the reader"),

    ("binary_contrast", "high", 1,
     # The negation and the assertion sit in DIFFERENT sentences, so the gap
     # class must not exclude `.` - excluding it silently missed the most
     # common form of the tell ("The answer isn't X. It's Y."). The closing
     # alternation demands a real apostrophe so that "Its behaviour is odd"
     # after an unrelated negation is not swept up.
     r"(?i)(?:\b(?:it|this|that|the\s+\w+)\s*(?:'s|\s+is|\s+was)?\s*"
     r"(?:not|isn't|is not|wasn't)\s+(?:about\s+)?[^.!?;]{1,60}[.,;]\s*"
     r"(?:it's|that's|it is|that is)\s"
     r"|\bnot because\b[^.!?]{2,80}\bbut because\b"
     r"|\bisn'?t\s+the\s+\w+[.,]\s*\w+\s+is\b)",
     "the 'not X, it's Y' reframe - the single most-cited AI tell",
     "state Y directly and drop the negation"),

    ("negative_listing", "medium", 0,
     r"(?i)\bnot\s+(?:a|an)?\s*[\w-]+\.\s*not\s+(?:a|an)?\s*[\w-]+\.",
     "a dramatic countdown through what the thing is not",
     "name the thing; the runway is not needed"),

    ("rhetorical_question", "medium", 1,
     r"(?i)(?:\bthe (?:result|worst part|catch|kicker|problem)\?\s|\bwhat if\b[^.?!]{5,70}\?"
     r"|\bthink about it:|\bhere'?s what i mean:)",
     "a question nobody asked, answered for effect",
     "make the point"),

    ("magic_adverb", "medium", 2,
     r"(?i)\b(?:quietly|deeply|fundamentally|remarkably|arguably|profoundly|seamlessly)\b",
     "an adverb doing the work the noun should do",
     "delete it, or replace the whole clause with the concrete fact"),

    ("ai_vocabulary", "high", 0,
     r"(?i)\b(?:delve|delving|tapestry|synergy|paradigm shift|game-?changer"
     r"|leverage(?:s|d|ing)?\b(?!\s+ratio)|utilis?e(?:s|d)?|streamline(?:s|d)?"
     r"|robust(?:ly)?|harness(?:es|ing)?\b|ever-evolving|cutting-edge)\b",
     "vocabulary that reads as machine-written on sight",
     "use the plain word: use, simplify, strong, field"),

    ("serves_as_dodge", "medium", 0,
     r"(?i)\b(?:serves? as|stands? as|represents? a|marks? a) (?:a |an |the )?"
     r"(?:reminder|testament|pivotal|key|crucial|significant)\b",
     "a pompous substitute for 'is'",
     "use 'is'"),

    ("landscape_noun", "medium", 1,
     r"(?i)\b(?:the )?(?:landscape|ecosystem|realm|sphere) of\b|\bnavigat(?:e|ing) the\b",
     "an ornate noun standing in for the actual subject",
     "name the field: 'in browser gaming', not 'in the gaming landscape'"),

    ("participle_analysis", "medium", 1,
     r"(?i),\s+(?:highlighting|underscoring|showcasing|emphasi[sz]ing|reflecting|"
     r"demonstrating|cementing|solidifying)\s+(?:its|their|his|her|the)\b",
     "a trailing participle that restates the sentence as significance",
     "delete the clause; the sentence already said it"),

    ("stakes_inflation", "medium", 0,
     r"(?i)\b(?:revolutioni[sz](?:e|es|ing|ed)|transform(?:s|ing|ed)? the way|redefin(?:e|es|ing|ed)"
     r"|the future of \w+ (?:is|depends)|nothing short of|a new era)\b",
     "grandiosity where a measurement belongs",
     "replace with the number or the concrete change"),

    ("signposted_conclusion", "high", 0,
     r"(?im)(?:^[ \t]*(?:in conclusion|to sum up|in summary|to wrap up|at the end of the day)\b"
     r"|\bdespite these challenges\b)",
     "a summary of what was just said",
     "stop when the last point is made"),

    ("bold_first_bullet", "medium", 2,
     r"(?m)^\s*[-*+]\s+\*\*[^*]{2,60}(?:\*\*\s*[:—-]|:\*\*)",
     "every list item opening with a bolded keyword - a strong formatting tell",
     "write the item as a sentence, or use a real table"),

    ("em_dash", "low", 3,
     r"—",
     "em dashes at density read as machine-set punctuation",
     "a comma, a full stop, or brackets"),

    ("unicode_arrow", "low", 0,
     r"[→⇒➡]",
     "decorative arrows in prose",
     "use a word"),

    ("vague_attribution", "high", 0,
     r"(?i)\b(?:experts?|researchers?|studies|analysts?|many people) (?:say|argue|agree|"
     r"suggest|have shown|believe)\b",
     "a citation-shaped phrase with no citation in it",
     "name the source, or delete the claim - this one also fails the "
     "information-gain requirement outright"),

    ("lazy_extreme", "medium", 2,
     r"(?i)\b(?:every single|always|never|all of|none of) (?:player|user|site|time|game)s?\b",
     "an absolute standing in for a measurement",
     "give the actual proportion"),

    ("tricolon", "low", 2,
     r"(?i)\b(\w+), (\w+),? and (\w+)\.\s",
     "the three-item list, used reflexively",
     "two items are usually enough and read less rehearsed"),

    ("fragment_drama", "medium", 1,
     r"(?i)(?:\.\s+That'?s it\.\s+That'?s|\b\w+\.\s+Openly\.\s|\.\s+And that'?s okay\.)",
     "sentence fragments deployed for manufactured weight",
     "complete sentences"),
]

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Compile at import so a malformed pattern is a loud ImportError at the top of
# the run, not a traceback in the middle of scanning someone's draft. (Two
# patterns in this catalog shipped with a mid-expression `(?i)` during
# development; Python rejects that only at compile time, and compiling lazily
# meant the failure surfaced per-invocation instead of once.)
try:
    COMPILED = [(r, sv, tol, re.compile(p), why, fix)
                for r, sv, tol, p, why, fix in RULES]
except re.error as _e:                                        # pragma: no cover
    raise SystemExit(f"slop.py: bad pattern in RULES: {_e}")

# Fenced code, inline code and link targets are not prose and must not be
# scanned - a `--leverage` CLI flag or a URL containing "landscape" is not a
# writing tell, and flagging it trains the writer to ignore the report.
STRIP = [
    (re.compile(r"(?s)```.*?```"), " "),
    (re.compile(r"(?s)~~~.*?~~~"), " "),
    (re.compile(r"`[^`\n]+`"), " "),
    (re.compile(r"\]\([^)]*\)"), "] "),
    (re.compile(r"(?m)^\s{4,}\S.*$"), " "),
]


def _mask(text: str) -> str:
    """Blank non-prose while preserving offsets, so line numbers stay true."""
    out = text
    for rx, _ in STRIP:
        out = rx.sub(lambda m: re.sub(r"\S", " ", m.group(0)), out)
    return out


def _front_matter_end(text: str) -> int:
    m = re.match(r"(?s)^---\n.*?\n---\n", text)
    return m.end() if m else 0


def scan_text(text: str, *, skip_front_matter: bool = True) -> dict:
    start = _front_matter_end(text) if skip_front_matter else 0
    body = text[start:]
    masked = _mask(body)
    lines = body.splitlines()
    line_starts, pos = [], 0
    for ln in lines:
        line_starts.append(pos)
        pos += len(ln) + 1

    def line_of(off: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    hits, by_rule = [], {}
    for rule, sev, tol, rx, why, fix in COMPILED:
        found = []
        for m in rx.finditer(masked):
            frag = body[m.start():m.end()]
            found.append({"line": line_of(m.start()),
                          "match": re.sub(r"\s+", " ", frag).strip()[:90]})
        if found:
            by_rule[rule] = {"rule": rule, "severity": sev, "count": len(found),
                             "tolerance": tol, "over_tolerance": len(found) > tol,
                             "why": why, "fix": fix, "hits": found[:12]}
            hits.extend(found)

    flagged = [v for v in by_rule.values() if v["over_tolerance"]]
    flagged.sort(key=lambda v: (SEVERITY_ORDER.get(v["severity"], 9), -v["count"]))
    within = [{"rule": v["rule"], "count": v["count"], "tolerance": v["tolerance"]}
              for v in by_rule.values() if not v["over_tolerance"]]

    words = len([w for w in re.split(r"\s+", masked) if w.strip()])
    return {
        "ok": True, "check": "slop-scan", "words": words,
        "verdict": ("fail" if any(v["severity"] == "high" for v in flagged)
                    else "warn" if flagged else "pass"),
        "total_hits": sum(v["count"] for v in by_rule.values()),
        "rules_over_tolerance": len(flagged),
        "flagged": flagged,
        "within_tolerance": within,
        "note": ("No score is emitted on purpose - a slop score gets optimised instead "
                 "of the prose. `flagged` is the work list; `within_tolerance` is shown "
                 "so a rule that fired once is visible without being treated as a "
                 "defect. Code blocks, inline code and link targets are excluded."),
    }


def scan_file(path: str) -> dict:
    p = Path(path)
    try:
        text = sys.stdin.read() if path == "-" else p.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "check": "slop-scan", "file": path, "error": str(e)}
    out = scan_text(text)
    out["file"] = path
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="find AI writing tells in a draft")
    s.add_argument("file", help="path, or '-' for stdin")

    d = sub.add_parser("diff", help="did a rewrite actually remove them")
    d.add_argument("before")
    d.add_argument("after")

    sub.add_parser("rules", help="print the catalog")

    a = ap.parse_args()
    if a.cmd == "scan":
        out = scan_file(a.file)
    elif a.cmd == "rules":
        out = {"ok": True, "check": "slop-rules",
               "rules": [{"rule": r, "severity": sv, "tolerance": t, "why": w, "fix": f}
                         for r, sv, t, _p, w, f in RULES]}
    else:
        b, af = scan_file(a.before), scan_file(a.after)
        if not (b.get("ok") and af.get("ok")):
            out = {"ok": False, "check": "slop-diff", "before": b, "after": af}
        else:
            bc = {v["rule"]: v["count"] for v in b["flagged"]}
            ac = {v["rule"]: v["count"] for v in af["flagged"]}
            out = {"ok": True, "check": "slop-diff",
                   "before_hits": b["total_hits"], "after_hits": af["total_hits"],
                   "removed": sorted(set(bc) - set(ac)),
                   "introduced": sorted(set(ac) - set(bc)),
                   "still_present": {k: {"before": bc[k], "after": ac[k]}
                                     for k in sorted(set(bc) & set(ac))},
                   "verdict": af["verdict"],
                   "note": "`introduced` is the one to read - a rewrite that removes a "
                           "binary contrast by adding three magic adverbs has not "
                           "improved anything."}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
