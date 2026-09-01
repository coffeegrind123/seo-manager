#!/usr/bin/env python3
"""AI-writing tells, detected mechanically instead of asked about politely.

The sameness gate (`sameness.py`) catches a draft that is too similar to the
corpus. This catches a draft that is recognisably MACHINE-WRITTEN even when it
is perfectly unique - a different failure with the same cause, and the one a
model cannot self-assess, because the patterns below are exactly the ones it
reaches for without noticing.

  scan     count every tell in a draft, with line numbers and the fix
  corpus   scan a TIER of generated pages and separate the TEMPLATE from the
           prose. On a generated site this is the one to reach for: `scan` run
           page by page returned `warn` on 44 of 44 pages, because a generated
           page is mostly template and counting it per page multiplies one
           authoring decision by the page count
  diff     compare two drafts (did the rewrite actually remove them)
  rules    print the catalog

HTML IS DETECTED AND MASKED. The prose on a generated site exists only inside
built pages, and scanning those raw counts <title>, meta descriptions, JSON-LD,
<style> and comments as writing - 861 "words" against 252 of real copy on one
measured page. `--no-html` turns that off, and is mostly useful as the control
proving the masking is what changed the answer.

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
import collections
import json
import math
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


# A GENERATED SITE HAS NO DRAFT TO SCAN - the prose exists only inside built HTML,
# and scanning that raw counts text no reader ever sees: HTML and CSS comments,
# <script> (JSON-LD schema included), <style>, and the entire <head> with its
# <title>, meta description and schema headline.
#
# Measured on combatskirmish.net's weapon pages 2026-09-01: awp.html reports 861
# "words" raw against 252 of actual prose, and 44 of 44 pages came back `warn`.
# A uniform verdict across every sample is the signature of an instrument reading
# the wrong text, not of a corpus-wide defect - the same 44 split 12 pass / 32
# warn once the markup is masked. So HTML is detected and masked by default.
HTML_STRIP = [
    re.compile(r"(?s)<!--.*?-->"),
    re.compile(r"(?is)<script\b.*?</script\s*>"),
    re.compile(r"(?is)<style\b.*?</style\s*>"),
    # the HTML spelling of the markdown code-fence exclusion below: a shell flag
    # inside a <pre> sample is not a writing tell.
    re.compile(r"(?is)<pre\b.*?</pre\s*>"),
    re.compile(r"(?is)<code\b.*?</code\s*>"),
    re.compile(r"(?is)\A.*?<body\b[^>]*>"),   # the whole <head>
    re.compile(r"(?is)</body\s*>.*\Z"),
    re.compile(r"(?s)<[^>]+>"),
    re.compile(r"&[a-zA-Z#0-9]{1,8};"),
]

_HTML_HINT = re.compile(r"(?i)<!doctype\s+html|<(?:html|head|body|div|section|h[1-6]|p)\b")


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, uniform_verdict  # noqa: E402


CONTROL_CLEAN = """\
The AWP fires once and reloads. A hit above the waist kills at any range, which
is why the rifle costs 4750 and why buying one early ends a round if it misses.
Most players learn the angle before they learn the recoil.
"""

CONTROL_SLOPPY = """\
It's not just a rifle - it's a game-changer. In today's fast-paced landscape of
competitive shooters, this isn't merely about aim; it's about unlocking your
true potential. Let's dive in and explore the ever-evolving tapestry of tactics
that seamlessly elevate your gameplay to the next level. The bottom line? It's
not about luck - it's about mastery.
"""

# Markup whose NON-PROSE carries the tells and whose visible body does not.
# This is the 2026-09-01 bug in one file: read as prose it fires; masked it must
# not, and the word count must reflect the body rather than the markup.
CONTROL_HTML = """<!doctype html><html><head>
<title>It's not just a map - it's a game-changer</title>
<style>.x{content:"delve into the ever-evolving tapestry"}</style>
<script type="application/ld+json">{"desc":"unlock your true potential, seamlessly"}</script>
<!-- let's dive in and explore: this isn't merely about aim, it's about mastery -->
</head><body>
<p>Dust2 has two bomb sites and a mid corridor. The T spawn is closer to B,
which is why most rounds open with a smoke at the double doors.</p>
</body></html>
"""


def looks_like_html(text: str) -> bool:
    """Cheap sniff on the head of the input. Override with --html/--no-html."""
    return bool(_HTML_HINT.search(text[:4000]))


def _mask(text: str, *, html: bool = False) -> str:
    """Blank non-prose while preserving offsets, so line numbers stay true."""
    out = text
    if html:
        # HTML only. The markdown STRIP below must NOT run on it: its
        # "4-space indent = code block" rule matches almost every line of
        # generated HTML, which silently deleted two thirds of the prose
        # (awp.html measured 128 words instead of 252). <pre>/<code> above are
        # the HTML spelling of the same exclusion.
        for rx in HTML_STRIP:
            out = rx.sub(lambda m: re.sub(r"\S", " ", m.group(0)), out)
        return out
    for rx, _ in STRIP:
        out = rx.sub(lambda m: re.sub(r"\S", " ", m.group(0)), out)
    return out


def _front_matter_end(text: str) -> int:
    m = re.match(r"(?s)^---\n.*?\n---\n", text)
    return m.end() if m else 0


TOLERANCE = {r[0]: r[2] for r in RULES}


def _fire(body: str, masked: str) -> dict:
    """rule -> record with EVERY hit, uncapped. The shared core of scan and corpus."""
    line_starts, pos = [], 0
    for ln in body.splitlines():
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

    by_rule = {}
    for rule, sev, tol, rx, why, fix in COMPILED:
        found = []
        for m in rx.finditer(masked):
            frag = body[m.start():m.end()]
            # Context, not just the match. Several rules match a SINGLE character
            # (em_dash "-", unicode_arrow), so a bare match string is identical on
            # every page and every occurrence - which makes it useless both as a
            # report ({"match": "-"} tells the reader nothing) and as the identity
            # `corpus` groups on, where it collapsed an entire rule into one
            # "template" row and reported 0 pages over tolerance.
            # The ENCLOSING SENTENCE, sliced from MASKED. Two reasons it is not a
            # fixed character window. It reads as the prose the scanner actually
            # saw rather than as a soup of tags; and it is stable, which is what
            # `corpus` groups on - a +/-45 char window around a shared footer
            # bleeds into whatever page-specific sentence precedes it, so the
            # SAME template line keys differently on different pages and stops
            # being recognised as template.
            left = masked[max(0, m.start() - 120):m.start()]
            right = masked[m.end():m.end() + 120]
            cut = max(left.rfind("."), left.rfind("!"), left.rfind("?"), left.rfind("\n"))
            if cut >= 0:
                left = left[cut + 1:]
            ends = [i for i in (right.find("."), right.find("!"),
                                right.find("?"), right.find("\n")) if i >= 0]
            if ends:
                right = right[:min(ends) + 1]
            found.append({"line": line_of(m.start()),
                          "match": re.sub(r"\s+", " ", frag).strip()[:90],
                          "context": re.sub(r"\s+", " ", left + frag + right).strip()[:160]})
        if found:
            by_rule[rule] = {"rule": rule, "severity": sev, "count": len(found),
                             "tolerance": tol, "over_tolerance": len(found) > tol,
                             "why": why, "fix": fix, "hits": found}
    return by_rule


def _prepare(text: str, *, skip_front_matter: bool = True, html=None):
    if html is None:
        html = looks_like_html(text)
    start = _front_matter_end(text) if (skip_front_matter and not html) else 0
    body = text[start:]
    return body, _mask(body, html=html), html


def scan_text(text: str, *, skip_front_matter: bool = True, html=None) -> dict:
    body, masked, html = _prepare(text, skip_front_matter=skip_front_matter, html=html)
    by_rule = _fire(body, masked)

    flagged = [dict(v, hits=v["hits"][:12]) for v in by_rule.values() if v["over_tolerance"]]
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
        "html_mode": html,
        "note": ("No score is emitted on purpose - a slop score gets optimised instead "
                 "of the prose. `flagged` is the work list; `within_tolerance` is shown "
                 "so a rule that fired once is visible without being treated as a "
                 "defect. Code blocks, inline code and link targets are excluded."),
        **({"html_note": (
            "HTML detected: comments, <script>, <style> and the whole <head> are masked, "
            "so this counts prose rather than markup. It still counts VISIBLE CHROME - "
            "the h1, CTA labels, nav links and the footer are prose-shaped and repeat on "
            "every page of a generated site, so a single-page verdict here is partly a "
            "verdict on the template. Use `corpus` over the whole tier to separate the "
            "two."
        )} if html else {}),
    }


def corpus(paths, *, share_ratio: float = 0.6) -> dict:
    """Scan a TIER of generated pages and separate the template from the prose.

    WHY THIS EXISTS. Run `scan` over 44 generated pages and all 44 come back
    `warn`, because a generated page is mostly template: the same h1 shape, the
    same "Play now - free, no download" CTA, the same `All weapons ->` nav labels
    and the same footer on every page. Counting those per page multiplies ONE
    authoring decision by the page count, and a verdict that is identical for
    every page cannot rank anything - it is the corpus telling you about its
    layout, not about its writing.

    So: a match string that appears in at least `share_ratio` of the files is
    reported ONCE as `template` (fix it in the generator and it is fixed
    everywhere), and each page's verdict is recomputed on only the hits that are
    unique to it.

    Measured on combatskirmish.net 2026-09-01, 44 pages: every `unicode_arrow`
    hit on the site was a navigation label, and on awp.html only 3 of 9
    body-text hits were prose - the rest were the h1, a CTA, three nav links and
    the footer tagline.

    LIMIT, stated because it is invisible otherwise: template detection is by
    IDENTICAL string, so a templated line carrying an interpolated value
    ("AWP - Counter-Strike 1.6") is not recognised as shared even though its
    shape is. `files_hit` is reported per rule for exactly that reason - a rule
    firing on 18 of 18 files is templated whether or not the strings match.
    """
    scans, errors = [], []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except Exception as e:  # unreadable file is data, not a crash
            errors.append({"file": path, "error": str(e)})
            continue
        body, masked, html = _prepare(text)
        scans.append({"file": path, "html": html, "by_rule": _fire(body, masked),
                      "words": len([w for w in re.split(r"\s+", masked) if w.strip()])})
    n = len(scans)
    if not n:
        return {"ok": False, "check": "slop-corpus",
                "error": "no readable files", "errors": errors}

    match_files = collections.defaultdict(set)
    files_hit = collections.Counter()
    for sc in scans:
        for rule, v in sc["by_rule"].items():
            files_hit[rule] += 1
            for h in v["hits"]:
                match_files[(rule, h["context"])].add(sc["file"])

    threshold = max(2, math.ceil(share_ratio * n))
    shared = {k: len(v) for k, v in match_files.items() if len(v) >= threshold}

    pages = []
    for sc in scans:
        uniq = {}
        for rule, v in sc["by_rule"].items():
            keep = [h for h in v["hits"] if (rule, h["context"]) not in shared]
            if keep:
                uniq[rule] = keep
        over = sorted(r for r, hs in uniq.items() if len(hs) > TOLERANCE[r])
        pages.append({"file": sc["file"], "words": sc["words"],
                      "page_hits": sum(len(h) for h in uniq.values()),
                      "rules_over_tolerance": over,
                      "verdict": "warn" if over else "pass",
                      "hits": {r: hs[:6] for r, hs in uniq.items() if r in over}})

    # A whole tier agreeing is the tell that produced this function in the first
    # place. Now the tool says so itself instead of waiting for someone to notice.
    tell = uniform_verdict([p["verdict"] for p in pages], subject="pages")

    flagged_pages = [p for p in pages if p["rules_over_tolerance"]]
    flagged_pages.sort(key=lambda p: -(p["page_hits"] / max(p["words"], 1)))

    template = sorted(({"rule": r, "match": m, "files": c}
                       for (r, m), c in shared.items()),
                      key=lambda d: (-d["files"], d["rule"], d["match"]))

    return {
        "ok": True, "check": "slop-corpus", "files": n,
        "shared_in_at_least": threshold,
        "template": template,
        "per_rule": [{"rule": r, "files_hit": c, "of": n,
                      "every_file": c == n,
                      "shared_matches": sum(1 for (rr, _m) in shared if rr == r)}
                     for r, c in files_hit.most_common()],
        "uniform_verdict_tell": tell,
        "pages_over_tolerance": len(flagged_pages),
        "pages_clean": n - len(flagged_pages),
        "worst": flagged_pages[:15],
        "errors": errors,
        "reading": (
            "RUN THIS PER TIER, not across the whole site: the threshold is a "
            "fraction of the files given, so mixing 17 weapon pages into a 44-file "
            "corpus pushes their shared nav below it and hands them back as if they "
            "were per-page prose. "
            "`template` is one authoring decision repeated - fix it in the generator, "
            "not page by page, and it does not belong in any per-page count. `worst` is "
            "ranked on hits UNIQUE to each page. A rule with every_file true is "
            "templated even when its `shared_matches` is 0, because an interpolated "
            "value defeats identical-string matching - read that as layout, not prose."
        ),
    }


def run_control() -> dict:
    """Prove the reader still discriminates, on THIS run.

    Every check here is a way the scanner has actually been wrong, or a way it
    would be wrong without anyone being able to tell from the output."""
    c = Controls("slop-control")

    clean = scan_text(CONTROL_CLEAN, html=False)
    sloppy = scan_text(CONTROL_SLOPPY, html=False)
    c.check("clean_prose_passes", clean["verdict"] == "pass",
            f"got {clean['verdict']}, flagged {[f['rule'] for f in clean['flagged']]}")
    c.check("sloppy_prose_does_not_pass", sloppy["verdict"] != "pass")
    c.check("the_two_are_separated", sloppy["total_hits"] > clean["total_hits"])

    # THE 44-of-44 BUG. Read as prose the markup fires; masked it must not.
    as_prose = scan_text(CONTROL_HTML, html=False)
    as_html = scan_text(CONTROL_HTML, html=True)
    c.check("markup_read_as_prose_does_fire", as_prose["total_hits"] > 0,
            "if this is false the control is not exercising the masker at all")
    c.check("html_mode_masks_title_style_jsonld_and_comments",
            as_html["total_hits"] == 0,
            f"still firing: {[f['rule'] for f in as_html['flagged']]}")
    c.check("html_word_count_is_the_body_not_the_markup",
            as_html["words"] < as_prose["words"] / 2,
            f"html={as_html['words']} prose={as_prose['words']}")
    c.check("html_is_sniffed_without_a_flag", looks_like_html(CONTROL_HTML) is True)
    c.check("prose_is_not_sniffed_as_html", looks_like_html(CONTROL_SLOPPY) is False)

    # Markdown code must not be scanned as prose - a fenced block full of tells
    # must leave the verdict where the surrounding prose put it.
    fenced = scan_text(CONTROL_CLEAN + "\n```\n" + CONTROL_SLOPPY + "\n```\n", html=False)
    c.check("fenced_code_is_excluded", fenced["verdict"] == "pass",
            f"got {fenced['verdict']}")

    c.check("uniform_tell_fires_on_a_uniform_tier",
            (uniform_verdict(["warn"] * 44, subject="pages") or {}).get("population") == 44)
    c.check("uniform_tell_stays_quiet_on_a_mixed_tier",
            uniform_verdict(["warn"] * 30 + ["pass"] * 14, subject="pages") is None)

    c.check("every_rule_has_a_tolerance", all(r in TOLERANCE for r, *_ in RULES))
    c.check("catalog_is_not_empty", len(RULES) >= 20)
    return c.verdict(rules=len(RULES))


def scan_file(path: str, *, html=None) -> dict:
    p = Path(path)
    try:
        text = sys.stdin.read() if path == "-" else p.read_text(encoding="utf-8")
    except Exception as e:
        return {"ok": False, "check": "slop-scan", "file": path, "error": str(e)}
    out = scan_text(text, html=html)
    out["file"] = path
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="find AI writing tells in a draft")
    s.add_argument("file", help="path, or '-' for stdin")
    s.add_argument("--html", dest="html", action="store_true", default=None,
                   help="force HTML mode (mask markup, head, script, style)")
    s.add_argument("--no-html", dest="html", action="store_false",
                   help="force plain-text mode; scans markup as if it were prose")

    d = sub.add_parser("diff", help="did a rewrite actually remove them")
    d.add_argument("before")
    d.add_argument("after")

    c = sub.add_parser("corpus", help="scan a TIER: separate template hits from page hits")
    c.add_argument("files", nargs="+", help="paths (a shell glob is fine)")
    c.add_argument("--share-ratio", type=float, default=0.6,
                   help="a match in >= this fraction of files is template (default 0.6)")

    sub.add_parser("rules", help="print the catalog")
    sub.add_parser("control", help="prove the reader still discriminates")

    a = ap.parse_args()
    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "scan":
        out = scan_file(a.file, html=a.html)
    elif a.cmd == "corpus":
        out = corpus(a.files, share_ratio=a.share_ratio)
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
