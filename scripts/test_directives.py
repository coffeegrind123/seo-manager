#!/usr/bin/env python3
"""Regression tests for robots directives (meta robots + X-Robots-Tag).

The bugs these were written against, each measured on 2026-10-25:
  - `X-Robots-Tag: otherbot: noindex` read as a Google noindex by sitecheck and
    contract, raising a false `sitemap_url_noindex` and a false CRITICAL
    `noindex_added`. Google: a rule naming a crawler binds that crawler only.
  - contract tested `"none" in blob`, so `max-image-preview:none` was noindex.
  - sitecheck split on whitespace, so `follow,none` was NOT noindex.
  - contract ignored `<meta name="googlebot">`.
  - providers.http kept only the LAST of repeated headers, so a second
    X-Robots-Tag line hid a `noindex` on the first.

    python3 test_directives.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import directives as D  # noqa: E402
import sitecheck  # noqa: E402
import contract  # noqa: E402
import providers  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:200]}")
    if not cond:
        FAILS.append(name)


print("X-Robots-Tag scoping (developers.google.com/search/docs/crawling-indexing/robots-meta-tag)")
check("an unscoped noindex binds Google", D.noindex(header="noindex"))
check("`otherbot: noindex, nofollow` does not bind Google",
      not D.noindex(header="otherbot: noindex, nofollow"), D.applicable(header="otherbot: noindex, nofollow"))
check("`otherbot: none` does not bind Google", not D.noindex(header="otherbot: none"))
check("CONTROL: `googlebot: noindex` binds Google", D.noindex(header="googlebot: noindex"))
check("a scoped line does not leak to the next line",
      D.noindex(header="otherbot: nofollow\nnoindex")
      and D.noindex(header="noindex\notherbot: nofollow"))
check("the scope persists across commas in one line",
      D.applicable(header="otherbot: noindex, nofollow") == set())
check("unavailable_after's colon is a value, not a crawler name",
      "unavailable_after: 25 jun 2010 15:00:00 pst" in D.applicable(header="unavailable_after: 25 Jun 2010 15:00:00 PST"))
check("a value directive keeps its value and is never a crawler",
      D.applicable(header="max-snippet: 20") == {"max-snippet:20"}, D.applicable(header="max-snippet: 20"))

print("\ntokens, not substrings; commas, not whitespace")
check("`max-image-preview:none` is NOT noindex", not D.noindex(meta={"robots": "index, follow, max-image-preview:none"}))
check("`follow,none` IS noindex", D.noindex(meta={"robots": "follow,none"}))
check("`none` expands to noindex + nofollow", {"noindex", "nofollow"} <= D.applicable(meta={"robots": "none"}))
check("<meta name=googlebot> binds Google", D.noindex(meta={"googlebot": "noindex"}))
check("<meta name=bingbot> does not bind Google", not D.noindex(meta={"bingbot": "noindex"}))
check("CONTROL: <meta name=bingbot> binds Bing", D.noindex(meta={"bingbot": "noindex"}, agent="bingbot"))
check("CONTROL: plain `index, follow` is indexable", not D.noindex(meta={"robots": "index, follow"}, header=""))

print("\nthe consumers")
f = sitecheck.page_facts("<p>x</p>", {"x-robots-tag": "otherbot: noindex, nofollow"})
check("sitecheck: another crawler's header noindex is not noindex", f["noindex"] is False, f)
f = sitecheck.page_facts('<meta name="robots" content="follow,none">', {})
check("sitecheck: comma-separated none is noindex", f["noindex"] is True, f)
f = sitecheck.page_facts('<meta name="robots" content="max-image-preview:none">', {})
check("sitecheck: max-image-preview:none is indexable", f["noindex"] is False, f)
base = {"meta_robots": None, "header_robots": None}
check("contract: max-image-preview:none is indexable",
      contract._indexable(dict(base, meta_robots="index, follow, max-image-preview:none")))
check("contract: another crawler's header noindex is indexable",
      contract._indexable(dict(base, header_robots="otherbot: noindex, nofollow")))
check("contract: <meta name=googlebot content=noindex> is NOT indexable",
      not contract._indexable(dict(base, meta_googlebot="noindex")))
check("CONTROL contract: header noindex is not indexable", not contract._indexable(dict(base, header_robots="noindex")))

import bing  # noqa: E402


def _fetched(xrt: str, meta: str) -> str:
    return ("HEAD\r\n\r\nHTTP/1.1 200 OK\r\nContent-Type: text/html\r\n" + xrt +
            "\r\n<html><head>" + meta + "</head></html>")


def _bing_noindex(doc):
    r = bing.parse_fetched(doc)
    return next((v["noindex"] for v in r.values() if isinstance(v, dict) and "noindex" in v),
                r.get("noindex"))


check("bing: a googlebot-scoped header noindex does not bind bingbot",
      _bing_noindex(_fetched("X-Robots-Tag: googlebot: noindex\r\n", "")) is False)
check("bing: max-image-preview:none is not noindex",
      _bing_noindex(_fetched("", '<meta name="robots" content="max-image-preview:none">')) is False)
check("CONTROL bing: a bingbot-scoped header noindex binds bingbot",
      _bing_noindex(_fetched("X-Robots-Tag: bingbot: noindex\r\n", "")) is True)

print("\nrepeated response headers are all kept")
msg = __import__("email.message", fromlist=["Message"]).Message()
msg["X-Robots-Tag"] = "noindex"
msg["X-Robots-Tag"] = "googlebot: nofollow"
msg["Content-Type"] = "text/html"
h = providers.header_dict(msg)
check("both X-Robots-Tag lines survive, one per line", h.get("x-robots-tag") == "noindex\ngooglebot: nofollow", h)
check("CONTROL: a single header is unchanged", h.get("content-type") == "text/html", h)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all directives tests passed")
