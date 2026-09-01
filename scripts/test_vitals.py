#!/usr/bin/env python3
"""Regression tests for the whole-site vitals sweep.

Every case here is a bug this file actually shipped during its first hour, or a
distinction that silently produces a confident wrong answer about somebody's
server.

    python3 test_vitals.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vitals import (  # noqa: E402
    CONTROL_HTML, LIMITS, PageParser, probe_page, sweep, template_of,
)

FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:150]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    print("the parser is an HTMLParser, not a regex:")
    p = PageParser()
    p.feed(CONTROL_HTML)
    # 415 phantom images once shipped from a regex counting <img> inside an HTML
    # comment. The same bug had already been found in agentcheck. It is the
    # single most repeated mistake in this skill's history.
    check("a commented-out <img> is not an image",
          "/ghost.png" not in [i["src"] for i in p.images], str([i["src"] for i in p.images]))
    check("a commented-out <script src> does not block render",
          "/ghost.js" not in p.blocking_js)
    check("real images are still found", len(p.images) == 5, len(p.images))

    print("\nrender-blocking is a judgement, not a tag count:")
    check("a plain head stylesheet blocks", "/a.css" in p.blocking_css)
    check("media=print does NOT block", "/print.css" not in p.blocking_css,
          "the standard non-blocking pattern must not be reported as a defect")
    check("defer does not block", "/deferred.js" not in p.blocking_js)
    check("async does not block", "https://cdn.other.test/x.js" not in p.blocking_js)
    check("a bare head script does block", "/blocking.js" in p.blocking_js)

    print("\ntemplate grouping - too coarse and the sweep is one row; too fine and it")
    print("degenerates into the per-URL run it exists to replace:")
    check("one silo collapses to one template",
          template_of("https://x/maps/a") == template_of("https://x/maps/b") == "/maps/*")
    check("two silos stay apart",
          template_of("https://x/maps/a") != template_of("https://x/guides/a"))
    check("a locale prefix is factored out",
          template_of("https://x/zh/maps/a") == "/{locale}/maps/*")
    check("a locale silo is not the same as the root silo",
          template_of("https://x/zh/maps/a") != template_of("https://x/maps/a"))
    check("the homepage is its own template", template_of("https://x/") == "/")
    check("a bare locale root still groups", template_of("https://x/zh/") == "/{locale}/")

    print("\ntiming - the defect this file shipped, caught by its own control:")
    # Timing a plain urlopen reported 5,940ms for a server that answers in 119ms;
    # the rest was this container's DNS (1,446ms measured) and TLS. The finding
    # said "server/CDN work" and pointed at entirely the wrong system.
    doc = probe_page.__doc__ or ""
    check("the two-sample rule is documented with its measurement",
          "12,065" in doc and "4,188" in doc,
          "the cold-cache case and the reproducible case must both stay on record")
    src = (HERE / "vitals.py").read_text(encoding="utf-8")
    check("connect time is excluded from ttfb",
          "connect_ms" in src and "is NOT\\n             f\"counted here" in src
          or "NOT" in src and "connect_ms" in src)
    check("a network baseline is measured before the site",
          "network_baseline()" in src and "BASELINE_HOST" in src,
          "without it a slow local network reads as a slow site")

    print("\nrefusals - a sweep that read nothing is a broken reader, not a fast site:")
    r = sweep([])
    check("no URLs refuses rather than reporting a clean site",
          r.get("control_failed") is True, str(r)[:120])
    r2 = sweep(["https://255.255.255.255/nope"], per_template=1, workers=1, timeout=2)
    check("zero readable URLs refuses", r2.get("control_failed") is True, str(r2)[:160])
    check("the refusal names why", "could be read" in str(r2.get("reason", "")) and "fetcher" in str(r2.get("reason", "")),
          str(r2.get("reason"))[:120])

    print("\nthresholds are guidelines, not taste:")
    check("ttfb threshold is Google's 800ms", LIMITS["ttfb_ms"] == 800)
    check("dom threshold is near Lighthouse's warning", 1200 <= LIMITS["dom_nodes"] <= 1800)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all vitals tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
