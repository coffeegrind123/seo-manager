#!/usr/bin/env python3
"""Regression tests for the browser SERP read (serp.BROWSER_EXTRACT/READY).

The bug these were written against (2026-10-25): BROWSER_EXTRACT deduped on
the RESOLVED url, and two /goto-wrapped threads on one site both resolve to
`https://reddit.com/`. On openserp's captured Google page
(google/testdata/search_results_wrapped_links.html) 10 result headings gave 9
results; with a same-site pair mid-page every later position moves up one and
`target_position` reads better than reality. The JS itself was verified in
headless Chrome over CDP (10 of 10 after the fix); this suite pins the
Python-side guard that refuses a read whose headings do not add up.

    python3 test_serp_browser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serp  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:200]}")
    if not cond:
        FAILS.append(name)


def rows(n):
    return [{"position": i + 1, "url": f"https://s{i}.test/", "title": "t"} for i in range(n)]


print("every result heading is extracted or skipped for a named reason")
lost = serp.extract_accounted({"results": rows(9), "accounting": {"headings": 10, "no_link": 0,
                                                                  "search_link": 0, "duplicate_href": 0}})
check("one heading unaccounted for is a refused read", bool(lost) and "1 lost" in lost, lost)
check("CONTROL: named skips add up", serp.extract_accounted(
    {"results": rows(1), "accounting": {"headings": 10, "no_link": 0, "search_link": 0,
                                        "duplicate_href": 9}}) is None)
check("CONTROL: an older payload with no accounting is not refused",
      serp.extract_accounted({"results": rows(3)}) is None)

print("\nthe extractor dedupes on the anchor, and the readiness probe knows the consent wall")
check("BROWSER_EXTRACT keys its dedupe on the anchor href",
      "seen[href]" in serp.BROWSER_EXTRACT and "seen[u]" not in serp.BROWSER_EXTRACT)
check("BROWSER_EXTRACT reports its accounting", "accounting:acct" in serp.BROWSER_EXTRACT)
check("BROWSER_READY names Google's consent wall", "'consent'" in serp.BROWSER_READY)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all serp browser tests passed")
