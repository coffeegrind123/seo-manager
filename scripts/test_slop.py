#!/usr/bin/env python3
"""Regression tests for the AI-writing-tell detector.

Two failure modes matter here and they pull in opposite directions. A detector
that never fires is useless; a detector that fires on ordinary prose gets
ignored within a day, which is worse than not having one. So every rule is
fired AND a clean control passes with zero hits, and the exclusions (code
fences, inline code, link targets, front matter) are pinned - a `--leverage`
flag in a shell example is not a writing tell.

Run: python3 test_slop.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slop  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


def fired(text: str) -> set:
    r = slop.scan_text(text)
    return {v["rule"] for v in r["flagged"]} | {w["rule"] for w in r["within_tolerance"]}


print("catalog integrity")
check("every pattern compiles", len(slop.COMPILED) == len(slop.RULES))
check("no duplicate rule names",
      len({r[0] for r in slop.RULES}) == len(slop.RULES))
check("every rule has a why and a fix",
      all(r[4] and r[5] for r in slop.RULES))
check("severities are known",
      all(r[1] in slop.SEVERITY_ORDER for r in slop.RULES))

print("\nthe clean control must score ZERO (or the detector is just noisy)")
CLEAN = """# Asset streaming

The engine streams 294 MiB on a cold load and 19 MiB on a warm one, measured on
a Pixel 6a over 4G. Maps above 40 MB fall back to whole-file reads because a
ranged request addresses gzip bytes rather than file bytes.

Server entries refresh every 30 seconds. A map change resets the per-round
score, so the public board aggregates with MAX instead of SUM.
"""
r = slop.scan_text(CLEAN)
check("clean prose produces no hits at all", r["total_hits"] == 0,
      str([v["rule"] for v in r["flagged"]]))
check("clean prose verdict is pass", r["verdict"] == "pass")

print("\nevery rule must fire on its own trigger")
CASES = {
    "throat_clearing": "Here's the thing: it works.",
    "emphasis_crutch": "It is fast. Let that sink in.",
    "pedagogical": "Let's break this down for a moment.",
    "binary_contrast": "It's not about speed. It's about consistency. "
                       "The answer isn't caching. It's routing.",
    "negative_listing": "Not a bug. Not a feature. A design flaw.",
    "rhetorical_question": "The result? Devastating. The worst part? Nobody knew. ",
    "magic_adverb": "It quietly and deeply and fundamentally changed things.",
    "ai_vocabulary": "We leverage a robust framework to streamline delivery.",
    "serves_as_dodge": "The station serves as a reminder of the past.",
    "landscape_noun": "Navigating the landscape of modern browser gaming. "
                      "The ecosystem of tools is broad.",
    "participle_analysis": "It shipped in June, highlighting its importance to users. "
                           "It grew, underscoring their commitment.",
    "stakes_inflation": "This revolutionizes the way you play.",
    "signposted_conclusion": "In conclusion, it is good.",
    "bold_first_bullet": "- **Speed:** fast\n- **Cost:** free\n- **Size:** small\n",
    "unicode_arrow": "Click A → B.",
    "vague_attribution": "Experts say this is common.",
    "lazy_extreme": "Every single player always wins.",
    "fragment_drama": "Speed. That's it. That's the tradeoff.",
    "em_dash": "a—b—c—d—e",
    "tricolon": "It is fast, cheap, and simple. It runs here, there, and everywhere. "
                "We build, test, and ship. ",
}
for rule, text in CASES.items():
    check(f"{rule} fires", rule in fired(text), f"got {sorted(fired(text))}")

print("\nexclusions - these must NOT fire (a noisy detector gets ignored)")
check("fenced code is not prose",
      slop.scan_text("# T\n\n```sh\nleverage --utilize --streamline --robust\n```\n"
                     )["total_hits"] == 0)
check("inline code is not prose",
      slop.scan_text("Pass `--leverage` and `--utilize` to the tool.")["total_hits"] == 0)
check("link targets are not prose",
      slop.scan_text("See [the docs](https://x.test/landscape-of-things).")["total_hits"] == 0)
check("indented code blocks are not prose",
      slop.scan_text("Text.\n\n    leverage utilize streamline robust\n")["total_hits"] == 0)

fm = "---\ntitle: Here's the thing about leverage\n---\n\nNormal prose here.\n"
check("YAML front matter is skipped", slop.scan_text(fm)["total_hits"] == 0,
      str(slop.scan_text(fm)))

print("\ntolerance: once is rhetoric, repeatedly is a fingerprint")
one = "It is not fast. It's careful."
many = one + " The answer isn't X. It's Y. It's not this. It's that."
r1, r2 = slop.scan_text(one), slop.scan_text(many)
check("one binary contrast is within tolerance",
      "binary_contrast" not in {v["rule"] for v in r1["flagged"]}, str(r1["flagged"]))
check("several binary contrasts are flagged",
      "binary_contrast" in {v["rule"] for v in r2["flagged"]},
      str([v['rule'] for v in r2['flagged']]))
check("a within-tolerance rule is still reported, not hidden",
      "binary_contrast" in {w["rule"] for w in r1["within_tolerance"]})

print("\nbinary_contrast is the highest-value and highest-risk rule - pin both sides")
CONTRAST_POS = [
    "It is not fast. It's careful.",
    "The answer isn't X. It's Y.",
    "It's not this. It's that.",
    "It's not about speed. It's about consistency.",
    "The problem isn't latency. It's jitter.",
    "Not because it is slow, but because it is wrong.",
]
CONTRAST_NEG = [
    "The engine is not documented. Its behaviour is odd.",
    "This is not supported on mobile. Chrome ships it in 150.",
    "The map is not cached. The CDN strips the query string.",
    "It is not clear whether the header is honoured.",
    "Latency is not the only factor we measured this week.",
    "The header is not set. That header controls indexing.",
]
_rx = dict((r[0], r[3]) for r in slop.COMPILED)["binary_contrast"]
check("every genuine contrast form matches",
      all(_rx.search(t) for t in CONTRAST_POS),
      str([t for t in CONTRAST_POS if not _rx.search(t)]))
check("ordinary negation is NOT a contrast (no false positives)",
      not any(_rx.search(t) for t in CONTRAST_NEG),
      str([t for t in CONTRAST_NEG if _rx.search(t)]))

print("\nline numbers must survive masking")
text = "line one\nline two\n\nHere's the thing: no.\n"
r = slop.scan_text(text, skip_front_matter=False)
hit = [v for v in r["flagged"] if v["rule"] == "throat_clearing"][0]
check("reported line number is correct", hit["hits"][0]["line"] == 4,
      str(hit["hits"]))

print("\nno score is emitted (a score gets optimised instead of the prose)")
check("no numeric score field", not any(
    k in slop.scan_text(CLEAN) for k in ("score", "grade", "density")))

print("\n" + "=" * 60)
print("HTML mode - a generated site has no draft, only built pages")

HTML_PAGE = """<!doctype html>
<html><head>
  <title>AWP \u2014 CS 1.6 Weapon Guide</title>
  <meta name="description" content="The AWP \u2014 price, damage and how to play it.">
  <script type="application/ld+json">{"headline":"AWP \u2014 Counter-Strike 1.6"}</script>
  <style>.cta{color:red} /* the button \u2014 orange on hover */</style>
</head><body>
  <!-- the did-you-mean redirect \u2014 only on variant pages -->
  <main>
    <p>It costs $4750 and it is bolt-action, so a miss leaves you helpless.</p>
    <pre><code>./run.sh --leverage 3 --delve deep</code></pre>
  </main>
</body></html>
"""

r_html = slop.scan_text(HTML_PAGE)
r_raw = slop.scan_text(HTML_PAGE, html=False)
check("html is auto-detected", r_html["html_mode"] is True)
check("--no-html is honoured", r_raw["html_mode"] is False)

em_html = sum(v["count"] for v in slop._fire(*slop._prepare(HTML_PAGE)[:2]).values()
              if v["rule"] == "em_dash")
em_raw = sum(v["count"] for v in
             slop._fire(HTML_PAGE, slop._mask(HTML_PAGE, html=False)).values()
             if v["rule"] == "em_dash")
check("head, script, style and comments are not counted as prose", em_html == 0,
      f"html={em_html} raw={em_raw}")
check("CONTROL the same input DOES count them without html masking", em_raw >= 4,
      f"raw={em_raw}")
check("CONTROL so the masking, not the input, changed the answer", em_raw > em_html)

check("word count drops to the visible prose", r_html["words"] < r_raw["words"] / 2,
      f"{r_html['words']} vs {r_raw['words']}")
check("html mode says a single-page verdict is partly the template",
      "corpus" in r_html.get("html_note", ""))
check("plain markdown gets no html_note", "html_note" not in slop.scan_text(CLEAN))

# REGRESSION. The markdown STRIP rule "4-space indent = code block" matches almost
# every line of generated HTML. Running it after the html mask deleted two thirds of
# the prose (252 words measured as 128), silently, while still looking like a result.
INDENTED = ("<!doctype html><html><body>\n"
            "        <p>The economy rewards a team that saves together, and it "
            "punishes the one that half-buys after a loss.</p>\n</body></html>")
check("indented HTML prose survives the markdown code-block rule",
      slop.scan_text(INDENTED)["words"] > 15,
      f"words={slop.scan_text(INDENTED)['words']}")
check("CONTROL <pre>/<code> is still excluded, as the contract promises",
      "leverage" not in slop._prepare(HTML_PAGE)[1])

print("\ncorpus - separating one authoring decision from many pages")

def _tmp_corpus(pages):
    d = tempfile.mkdtemp()
    paths = []
    for i, body in enumerate(pages):
        f = Path(d) / f"p{i}.html"
        f.write_text("<!doctype html><html><body>" + body + "</body></html>",
                     encoding="utf-8")
        paths.append(str(f))
    return paths

FOOTER = "<footer><p>Combat Skirmish \u2014 play CS 1.6 free in your browser.</p></footer>"
# five pages sharing a footer; one of them ALSO has three em dashes of its own.
shared_only = [f"<p>Page {i} says something ordinary about maps.</p>{FOOTER}" for i in range(4)]
noisy = ("<p>It costs $4750 \u2014 most of a buy \u2014 and it is slow, "
         "so a miss \u2014 any miss \u2014 leaves you helpless.</p>" + FOOTER)
res = slop.corpus(_tmp_corpus(shared_only + [noisy]))
check("corpus runs over the tier", res["ok"] and res["files"] == 5)
tmpl = [t for t in res["template"] if "Combat Skirmish" in t["match"]]
check("the shared footer is reported ONCE as template", len(tmpl) == 1, str(res["template"]))
check("and is attributed to every file", tmpl and tmpl[0]["files"] == 5)
check("pages carrying ONLY template chrome come back clean",
      res["pages_clean"] == 4, f"clean={res['pages_clean']}")
check("CONTROL the page with its own em dashes is still flagged",
      res["pages_over_tolerance"] == 1 and "p4" in res["worst"][0]["file"],
      str([w["file"] for w in res["worst"]]))

# REGRESSION. em_dash's match string is the bare character, so keying template
# identity on `match` collapsed the whole rule into one shared row and reported
# every page clean - the mirror image of the bug this command exists to fix.
check("template identity keys on context, not the bare match",
      all(t["rule"] != "em_dash" or "Combat Skirmish" in t["match"]
          for t in res["template"]),
      str(res["template"]))
check("hits carry context so a '-' hit is readable at all",
      all("context" in h and len(h["context"]) > 5
          for w in res["worst"] for hs in w["hits"].values() for h in hs))

per_rule = {r["rule"]: r for r in res["per_rule"]}
check("a rule firing on every file is reported as such",
      per_rule["em_dash"]["every_file"] is True and per_rule["em_dash"]["of"] == 5)
check("corpus refuses an empty file set rather than inventing a verdict",
      slop.corpus([])["ok"] is False)
check("an unreadable file is data, not a crash",
      slop.corpus(_tmp_corpus(["<p>ok</p>"]) + ["/nonexistent/zzz.html"])["errors"])

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all slop tests passed")
