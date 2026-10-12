#!/usr/bin/env python3
"""claims.py - does the published copy say anything the product's own facts contradict,
or anything that hands the reader to a competitor?

`factcheck.py` checks the NUMBERS in a draft against the sources it cites. That
covers claims about the world. It does nothing for claims about the PRODUCT -
which is where a content program does the most damage, because the writer
(human or agent) works from positioning copy, and positioning copy is where the
aspirational claim lives.

Measured on a real site (2026-10-12): one aspirational sentence about the
product's technology ("it runs the original engine, not a reimplementation")
had been copied into ~15 English passages, the homepage FAQ, its JSON-LD, the
AI-facing summary file and roughly 400 translated strings across 21 locales.
The source code said otherwise in its README's first line. Three more claims
had gone stale as the product shipped features (mobile support, a VR mode in a
second game, self-hosting), so the site was underselling itself in ~20 places.
And comparison pages written by an earlier run of this skill told readers that
named competitors were "the obvious choice" and "the right answer".

None of that is visible to a slop, sameness or schema check. It needs a record
of what is TRUE, and a scan against it. The record is `.seo/claims.json`:

    {
      "version": 1,
      "facts": [
        {"id": "platforms", "statement": "Runs on desktop and mobile browsers; native apps for X and Y.",
         "source": "docs/platforms.md:12"}
      ],
      "never": [
        {"id": "desktop-only", "pattern": "\\\\bdesktop[- ]only\\\\b",
         "why": "mobile has been supported since 2026-09", "source": "docs/platforms.md:12",
         "except": "historically"}
      ],
      "competitors": ["Rival App", "rival.example"]
    }

`facts` is for WRITERS - read it before writing positioning copy. This script
does not interpret it. `never` is enforced: a regex (case-insensitive) matched
per sentence of visible text and JSON-LD prose; `except` is a regex that, found
in the same sentence, clears it (historical mentions, quotations).

Competitor FRAMING is reported for REVIEW, never as a verdict: a sentence that
names a listed competitor AND carries a preference cue ("the obvious choice",
"the right answer", "best", "you should use"). Whether a mention is fair is a
human reading of the verbatim sentence - this narrows the reading to the
sentences worth reading. The cue list is English; other languages report the
mention count only, and the output says so rather than implying they were read.

    claims.py scan --root public/ [--glob '**/*.html'] [--fail-on-finding]
    claims.py scan --urls urls.txt
    claims.py lint            # the ledger itself: every pattern compiles, ids unique
    claims.py control

Refusals: no ledger / an empty `never` list AND no competitors (nothing was
asked), and a scan that read zero documents.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402
from headterms import parse_page  # noqa: E402

DEFAULT_LEDGER = ".seo/claims.json"

# English preference cues. Each is a phrase a fair comparison rarely needs: a
# sentence can state a competitor's features, prices and limits without any of
# these. Kept deliberately narrow - a cue list that fires on every mention turns
# review into skimming, which is the uniform-verdict failure in another form.
PREFERENCE_CUES = [
    r"\bobvious choice\b", r"\bright (answer|choice|pick)\b", r"\bbest (option|choice|pick|bet|place)\b",
    r"\b(is|are) (the )?better\b", r"\bbetter (option|choice|experience|than (this|us|ours))\b",
    r"\bgo[- ]to\b", r"\b(we )?recommend(ed|s)?\b", r"\byou should (use|try|go)\b",
    r"\buse (it|them) instead\b", r"\binstead,? (use|try|go to)\b", r"\bgood (game|choice|option) in its own right\b",
    r"\bin its own right\b", r"\b(the )?(largest|biggest|most popular)\b", r"\bby a wide margin\b",
    r"\b(wins?|beats?) (us|this)\b", r"\bif you want\b.{0,80}\b(it is|use|try|go with)\b",
]
_CUES = [re.compile(c, re.I) for c in PREFERENCE_CUES]
_SENT = re.compile(r"(?<=[.!?。！？])\s+")


def sentences(blocks: list[str]) -> list[str]:
    out = []
    for b in blocks:
        out.extend(x.strip() for x in _SENT.split(b) if x.strip())
    return out


def load_ledger(path: str) -> tuple[dict | None, str | None]:
    p = Path(path)
    if not p.is_file():
        return None, f"no claims ledger at {path} - record one (see the module docstring)"
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"cannot read {path}: {exc}"
    errs = lint(led)
    if errs:
        return None, f"ledger does not lint: {errs[:5]}"
    if not led.get("never") and not led.get("competitors"):
        return None, ("ledger has no `never` rules and no `competitors` - a scan would report "
                      "'clean' without having asked anything")
    return led, None


def lint(led: dict) -> list[str]:
    errs, ids = [], set()
    for i, r in enumerate(led.get("never") or []):
        rid = r.get("id") or f"#{i}"
        if rid in ids:
            errs.append(f"duplicate id {rid}")
        ids.add(rid)
        for k in ("pattern", "except"):
            if r.get(k):
                try:
                    re.compile(r[k], re.I)
                except re.error as exc:
                    errs.append(f"{rid}.{k}: {exc}")
        if not r.get("pattern"):
            errs.append(f"{rid}: no pattern")
        if not r.get("why"):
            errs.append(f"{rid}: no `why` - a rule nobody can justify gets deleted, not followed")
    return errs


def _competitor_res(names: list[str]) -> list[tuple[str, re.Pattern]]:
    return [(n, re.compile(r"(?<![\w.-])" + re.escape(n) + r"(?![\w-])", re.I)) for n in names if n.strip()]


def scan_doc(url: str, text: str, led: dict, *, html: bool = True) -> dict:
    if html:
        pg = parse_page(text)
        if not pg["ok"]:
            return {"url": url, "error": pg["error"]}
        sents = sentences(pg["blocks"] + pg["jsonld"])
        lang_en = True
        m = re.search(r'<html[^>]*\blang="([^"]+)"', text, re.I)
        if m and not m.group(1).lower().startswith("en"):
            lang_en = False
    else:
        sents = sentences([ln for ln in text.splitlines() if ln.strip()])
        lang_en = True
    rules = [(r, re.compile(r["pattern"], re.I), re.compile(r["except"], re.I) if r.get("except") else None)
             for r in (led.get("never") or [])]
    comps = _competitor_res(led.get("competitors") or [])
    findings, review, mentions = [], [], 0
    for s in dict.fromkeys(sents):                        # JSON-LD often repeats visible text
        for r, rx, ex in rules:
            if rx.search(s) and not (ex and ex.search(s)):
                findings.append({"rule": r.get("id"), "sentence": s[:400], "why": r.get("why")})
        named = [n for n, rx in comps if rx.search(s)]
        if named:
            mentions += 1
            if lang_en:
                cues = [c.pattern for c in _CUES if c.search(s)]
                if cues:
                    review.append({"competitors": named, "cues": cues, "sentence": s[:400]})
    return {"url": url, "findings": findings, "review": review, "competitor_mentions": mentions,
            "cues_read": lang_en}


def _scan(docs: list[tuple[str, str]], led: dict) -> dict:
    if not docs:
        return refuse("claims-scan", "read 0 documents - a wrong --root or empty URL list reads "
                      "exactly like clean copy")
    rows = []
    for url, text in docs:
        rows.append(scan_doc(url, text, led, html=not url.endswith((".md", ".txt"))))
    findings = [dict(f, url=r["url"]) for r in rows for f in r.get("findings", [])]
    review = [dict(v, url=r["url"]) for r in rows for v in r.get("review", [])]
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    not_read = sum(1 for r in rows if r.get("competitor_mentions") and not r.get("cues_read"))
    return {"ok": not findings, "check": "claims-scan", "documents": len(rows),
            "findings": findings[:500], "findings_total": len(findings), "findings_by_rule": by_rule,
            "review": review[:300], "review_total": len(review),
            "competitor_mentions": sum(r.get("competitor_mentions", 0) for r in rows),
            "non_english_docs_with_mentions_not_cue_read": not_read,
            "errors": [r for r in rows if r.get("error")][:10],
            "reading": ("findings are deterministic: the sentence matches a `never` rule. review "
                        "items are CANDIDATES - read each verbatim sentence; a competitor named "
                        "next to a preference cue is usually, not always, the reader being handed "
                        "to them.")}


def _docs_from_root(root: str, globs: list[str]) -> list[tuple[str, str]]:
    base = Path(root)
    seen, out = set(), []
    for g in globs:
        for f in sorted(base.glob(g)):
            if f.is_file() and f not in seen:
                seen.add(f)
                out.append(("/" + f.relative_to(base).as_posix(), f.read_text(encoding="utf-8", errors="replace")))
    return out


def _docs_from_urls(file: str, timeout: int) -> list[tuple[str, str]]:
    from providers import http, BROWSER_UA  # noqa: E402 - network only when asked
    out = []
    for u in Path(file).read_text(encoding="utf-8").split():
        r = http(u, ua=BROWSER_UA, timeout=timeout)
        if r.ok:
            out.append((u, (r.get("body") or b"").decode("utf-8", "replace")))
    return out


# ------------------------------------------------------------------ control
_LED = {
    "never": [
        {"id": "desktop-only", "pattern": r"\bdesktop[- ]only\b|\brequires a desktop\b",
         "why": "mobile is supported", "except": r"\bhistorically\b|\bused to\b"},
        {"id": "original-engine", "pattern": r"\bthe original engine\b", "why": "it is a compatible engine"},
    ],
    "competitors": ["Rival App", "rival.example"],
}
_DOC_BAD = ('<html lang="en"><head><title>T</title>'
            '<script type="application/ld+json">{"@type":"FAQPage","mainEntity":[{"@type":"Question",'
            '"name":"Do I need a PC?","acceptedAnswer":{"@type":"Answer","text":"It requires a desktop browser today."}}]}</script>'
            '</head><body><h1>H</h1><p>It runs the original engine in a tab.</p>'
            '<p>For a phone, Rival App is the obvious choice.</p>'
            '<p>Rival App costs $5 a month and supports two platforms.</p>'
            '<p>The game was historically desktop-only, before touch controls shipped.</p>'
            '<script>var x = "requires a desktop";</script><!-- desktop-only --></body></html>')
# Carries an English cue ("go-to") so that reading cues on a non-English page would
# put it in review: without one, the "not cue-read" check passes whether or not it holds.
_DOC_DE = ('<html lang="de"><body><p>Rival App ist die go-to Wahl.</p></body></html>')


def run_control() -> dict:
    c = Controls("claims-control")
    r = _scan([("/a.html", _DOC_BAD), ("/de/a.html", _DOC_DE)], _LED)
    rules = sorted(f["rule"] for f in r.get("findings", []))
    c.check("visible never-claim found", "original-engine" in rules, rules)
    c.check("JSON-LD answer is scanned (a rich result IS published copy)",
            any(f["rule"] == "desktop-only" and "desktop browser" in f["sentence"] for f in r.get("findings", [])))
    c.check("`except` context clears a historical mention",
            not any("historically" in f["sentence"] for f in r.get("findings", [])))
    c.check("script and comment text are not copy", rules.count("desktop-only") == 1, rules)
    rev = r.get("review", [])
    c.check("competitor + preference cue goes to review", any("obvious choice" in v["sentence"] for v in rev))
    c.check("a neutral factual mention is NOT flagged", not any("$5 a month" in v["sentence"] for v in rev))
    c.check("non-English mention is counted but not cue-read, and says so",
            r.get("non_english_docs_with_mentions_not_cue_read") == 1 and not any(v["url"] == "/de/a.html" for v in rev))
    c.check("findings make ok false", r.get("ok") is False)
    c.check("zero documents refuses", _scan([], _LED).get("control_failed") is True)
    # x: bad regex AND no why (2); y: no why (1); z: valid (0) -> 3
    c.check("lint catches a bad regex and a missing why",
            len(lint({"never": [{"id": "x", "pattern": "("}, {"id": "y", "pattern": "a"},
                                {"id": "z", "pattern": "b", "why": "w"}]})) == 3)
    clean = _scan([("/c.html", '<html lang="en"><body><p>Works on phones.</p></body></html>')], _LED)
    c.check("clean copy passes", clean.get("ok") is True and not clean.get("review"))
    return c.verdict()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("control", help="prove the scan finds planted claims and ignores non-copy")
    s = sub.add_parser("lint", help="validate the ledger")
    s.add_argument("--ledger", default=DEFAULT_LEDGER)
    s = sub.add_parser("scan", help="scan built pages or live URLs against the ledger")
    s.add_argument("--root")
    s.add_argument("--glob", action="append", help="default: **/*.html **/*.md **/*.txt")
    s.add_argument("--urls")
    s.add_argument("--timeout", type=int, default=20)
    s.add_argument("--ledger", default=DEFAULT_LEDGER)
    s.add_argument("--fail-on-finding", action="store_true", help="exit 1 on any finding (CI)")
    a = p.parse_args()

    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "lint":
        try:
            led = json.loads(Path(a.ledger).read_text(encoding="utf-8"))
        except Exception as exc:
            out = refuse("claims-lint", f"cannot read {a.ledger}: {exc}")
        else:
            errs = lint(led)
            out = {"ok": not errs, "check": "claims-lint", "errors": errs,
                   "never_rules": len(led.get("never") or []), "facts": len(led.get("facts") or []),
                   "competitors": len(led.get("competitors") or [])}
    else:
        led, err = load_ledger(a.ledger)
        if err:
            out = refuse("claims-scan", err)
        elif a.root:
            out = _scan(_docs_from_root(a.root, a.glob or ["**/*.html", "**/*.md", "**/*.txt"]), led)
        elif a.urls:
            out = _scan(_docs_from_urls(a.urls, a.timeout), led)
        else:
            out = refuse("claims-scan", "pass --root DIR or --urls FILE")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if a.cmd == "scan" and a.fail_on_finding and out.get("findings"):
        sys.exit(1)


if __name__ == "__main__":
    main()
