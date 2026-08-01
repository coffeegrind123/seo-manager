#!/usr/bin/env python3
"""Regression tests for the SERP guards.

These are not synthetic. Every fixture in ../assets/fixtures/ is a REAL
response captured on 2026-07-30:

  bing-fr.json  bing-de.json   Bing, asked for "self hosted rank tracker"
                               through French and German residential exits,
                               returning clean b_algo blocks about Laposte.net
                               webmail and Universität Münster SelfService.
                               HTTP 200, no captcha, ten rows that parse.
  ddg-good.json                DuckDuckGo, same query, a genuinely good read.
  browser-google.json          Real Google via the browser MCP, same query,
                               captured while every HTTP provider was refusing.
                               Double-encoded, exactly as execute_js returns it.

The negative fixtures are the point. A guard tested only against good data
proves nothing - a control you know the answer to has to fail, and these are
the ones that fooled every naive check (status 200, ten results, and at least
one query token present in every single row).

    python3 test_guards.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from serp import (  # noqa: E402
    MIN_COVERAGE, MIN_HIT_RATE, registrable, score, shape_ok, verify_relevance,
)

FIXTURES = Path(__file__).resolve().parent.parent / "assets" / "fixtures"
QUERY = "self hosted rank tracker"


def load(name: str) -> list[dict]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if isinstance(data, str):  # execute_js hands back a JSON string
        data = json.loads(data)
    return data["results"]


def main() -> int:
    failures = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + detail) if detail else ''}")
        if not cond:
            failures.append(label)

    print(f"relevance thresholds: coverage >= {MIN_COVERAGE}, hit_rate >= {MIN_HIT_RATE}\n")

    print("NEGATIVE controls - real wrong-query responses that MUST be refused:")
    for name in ("bing-fr.json", "bing-de.json"):
        rows = load(name)
        rel = verify_relevance(QUERY, rows)
        check(f"{name} refused", not rel["pass"],
              f"coverage={rel['coverage']} hit_rate={rel['hit_rate']} rows={len(rows)}")
        # The trap these exist for: the naive rule passes them.
        naive = any(any(t in (r.get("title", "") + r.get("url", "")).lower()
                        for t in ("self", "hosted", "rank", "tracker")) for r in rows)
        check(f"{name} would have fooled a 'any query token' rule", naive,
              "which is exactly why coverage is measured over DISTINCT tokens")

    print("\nPOSITIVE controls - good reads of the SAME query that MUST pass:")
    for name in ("ddg-good.json", "browser-google.json"):
        rows = load(name)
        rel = verify_relevance(QUERY, rows)
        check(f"{name} accepted", rel["pass"],
              f"coverage={rel['coverage']} hit_rate={rel['hit_rate']} rows={len(rows)}")
        check(f"{name} shape ok", shape_ok(rows))

    print("\nSHAPE guard:")
    check("empty result set refused", not shape_ok([]))
    check("results with no http url refused", not shape_ok([{"title": "x", "url": ""}]))

    print("\nScoring sanity on the good Google read:")
    rows = load("browser-google.json")
    s = score(rows, "serpbear.com")
    check("github tagged as repo host, not weakness",
          all("repo/package-host" in r["signals"] for r in rows if r["domain"] == "github.com"))
    check("target domain located", s["target_position"] is not None,
          f"serpbear.com at position {s['target_position']}")
    check("authority count is reported as a ceiling",
          "CEILING" in s["verdict_note"])

    print("\nregistrable():")
    for host, want in [("www.example.com", "example.com"), ("a.b.example.co.uk", "example.co.uk"),
                       ("notexample.com", "notexample.com")]:
        check(f"{host} -> {want}", registrable(host) == want, registrable(host))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("all guard tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
