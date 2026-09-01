#!/usr/bin/env python3
"""Controls for bing.py's PAGE dimension.

Every case is a way this returns a confident wrong answer instead of an error:

  1. GetPageStats puts the page URL in a field named `Query`. Reading it as a
     search term turns a page report into nonsense that still looks like data.
  2. Rows arrive one per date, so a page's real total only exists after
     aggregation - reading the first row understates every page differently.
  3. Sorting by impressions inverts the actual answer. Measured on
     combatskirmish.net 2026-09-01: /zh/ had a QUARTER of the homepage's
     impressions and THREE TIMES its clicks. Impressions-first ranking puts the
     page earning 68% of all clicks in second place.
  4. A page with impressions but no clicks must not raise on the CTR division.
  5. An empty result is EITHER "no data yet" or "you typed the URL wrong", and
     those are opposite findings - it must say so, not imply the page is dead.
  7. --days is accepted by the parser for every subcommand, but only `keyword`
     and `expand` reach an endpoint that takes a date range. Silently ignoring it
     elsewhere makes `--days 7` and `--days 30` return identical rows - measured
     on a live account 2026-09-01 - which reads as "positions did not move this
     week" and actually means the window was never applied.
"""
import json
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bing  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILS.append(name)


def stub(rows):
    """Replace the network with a fixed payload, in Bing's own row shape."""
    bing.call = lambda method, key, **kw: {"ok": True, "d": rows}


print("1-3. GetPageStats: URL in a field called `Query`, aggregated, click-sorted")
# Two date rows per page, exactly as the API returns them. The homepage wins on
# impressions and loses on clicks - the real 2026-09-01 shape, scaled down.
stub([
    {"Query": "https://x.net/",    "Impressions": 20000, "Clicks": 400, "AvgImpressionPosition": 7},
    {"Query": "https://x.net/",    "Impressions": 13403, "Clicks": 316, "AvgImpressionPosition": 7},
    {"Query": "https://x.net/zh/", "Impressions": 4000,  "Clicks": 1100, "AvgImpressionPosition": 4},
    {"Query": "https://x.net/zh/", "Impressions": 4522,  "Clicks": 1198, "AvgImpressionPosition": 4},
])
r = bing.pages("k", "https://x.net/", 10)
top = r["pages"][0]
check("the URL is read out of the `Query` field", top["url"].startswith("https://x.net/"))
check("rows for one page are aggregated, not truncated to the first",
      any(p["url"] == "https://x.net/" and p["impressions"] == 33403 for p in r["pages"]))
check("clicks aggregate too", top["clicks"] == 2298)
check("sorted by CLICKS, so /zh/ leads despite a quarter the impressions",
      top["url"] == "https://x.net/zh/")
check("the impressions-first order would have been wrong",
      max(r["pages"], key=lambda p: p["impressions"])["url"] == "https://x.net/")
check("ctr exposes the inversion", top["ctr"] > 0.25 and r["pages"][1]["ctr"] < 0.03)
check("totals cover every page", r["totals"]["clicks"] == 3014)

print("\n4. a page with impressions and no clicks does not raise")
stub([{"Query": "https://x.net/dead", "Impressions": 50, "Clicks": 0, "AvgImpressionPosition": 9},
      {"Query": "https://x.net/never", "Impressions": 0, "Clicks": 0, "AvgImpressionPosition": 0}])
r = bing.pages("k", "https://x.net/", 10)
check("zero-click page reports ctr 0.0, not a crash", 
      any(p["url"].endswith("/dead") and p["ctr"] == 0.0 for p in r["pages"]))
check("zero-IMPRESSION page reports ctr None rather than dividing by zero",
      any(p["url"].endswith("/never") and p["ctr"] is None for p in r["pages"]))

print("\n5. empty means two different things and must say so")
stub([])
r = bing.pages("k", "https://x.net/", 10)
check("empty pages report carries empty_means", "empty_means" in r)
check("and does not claim the site ranks for nothing", "NOT evidence" in r["empty_means"])
r = bing.pagequeries("k", "https://x.net/", "https://x.net/typo", 10)
check("empty pagequeries names the mistyped-URL possibility",
      "empty_means" in r and "does not match" in r["empty_means"])
check("and points at the command that resolves the ambiguity",
      "bing.py pages" in r["empty_means"])

print("\n6. an API error is never mistaken for an empty result")
bing.call = lambda method, key, **kw: {"ok": False, "error": "HTTP 401"}
r = bing.pages("k", "https://x.net/", 10)
check("a failed call propagates ok:false", r["ok"] is False)
check("and carries no pages array to be misread as zero", "pages" not in r)

print("8. urlinfo: .NET DateTime.MinValue is 'never', not year 0001")
MINV = "/Date(-62135568000000-0800)/"
check("the MinValue sentinel decodes to None", bing._dotnet_date(MINV) is None)
check("a real /Date(ms)/ decodes to a date", bing._dotnet_date("/Date(1787460331000)/") == "2026-05-19"
      or bing._dotnet_date("/Date(1787460331000)/").startswith("2026-"))
check("a missing/garbage value decodes to None",
      bing._dotnet_date(None) is None and bing._dotnet_date("nonsense") is None)

# A URL Bing has never seen: every field is the sentinel. This is ALSO exactly what a
# URL that does not exist returns, which is why the verdict must be phrased as
# "no record of this string" and never as "the page is excluded".
stub({"DiscoveryDate": MINV, "LastCrawledDate": MINV, "DocumentSize": 0, "IsPage": True})
r = bing.urlinfo("k", "https://x.net/", "https://x.net/never-seen")
check("an all-sentinel record is known_to_bing false", r["known_to_bing"] is False)
check("and reports no dates rather than year 0001",
      r["discovered"] is None and r["last_crawled"] is None)
check("and says a nonexistent URL answers identically",
      "does not exist" in r["empty_means"])

stub({"DiscoveryDate": "/Date(1784876400000-0700)/", "LastCrawledDate": "/Date(1787460331000)/",
      "DocumentSize": 18134, "IsPage": True})
r = bing.urlinfo("k", "https://x.net/", "https://x.net/real")
check("CONTROL a crawled URL is known_to_bing true", r["known_to_bing"] is True)
check("CONTROL and carries both dates", bool(r["discovered"]) and bool(r["last_crawled"]))
check("HttpStatus is not surfaced (it was 0 on crawled and unknown alike)",
      "http_status" not in r)

# an API error must never look like "Bing has no record"
bing.call = lambda method, key, **kw: {"ok": False, "error": "HTTP 401"}
r = bing.urlinfo("k", "https://x.net/", "https://x.net/real")
check("an API failure propagates ok:false", r["ok"] is False)
check("and carries no known_to_bing verdict to be misread", "known_to_bing" not in r)

print("\n7. a window that cannot be honoured is refused, not ignored")
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, BING_WEBMASTER_API_KEY="test-key-not-used")  # refusal precedes the network


def cli(*args):
    p = subprocess.run([sys.executable, os.path.join(HERE, "bing.py"), *args],
                       capture_output=True, text=True, env=ENV, timeout=60)
    try:
        return p.returncode, json.loads(p.stdout)
    except Exception:
        return p.returncode, {"_stdout": p.stdout[:200], "_stderr": p.stderr[:200]}


for cmd in ("queries", "pages", "traffic"):
    rc, out = cli(cmd, "--days", "7")
    check(f"{cmd} --days refuses", out.get("error") == "window_not_selectable")
    check(f"{cmd} --days exits non-zero", rc != 0)
    check(f"{cmd} --days names where a window IS real",
          "keyword" in (out.get("windowed_commands") or []))

# The control: the flag must still be accepted where the endpoint really takes
# one, or the guard has just broken the two commands it was meant to protect.
rc, out = cli("keyword", "--q", "zzz-control-query", "--days", "30")
check("CONTROL keyword --days is NOT refused", out.get("error") != "window_not_selectable")
rc, out = cli("expand", "--seed", "zzz-control-seed", "--days", "30")
check("CONTROL expand --days is NOT refused", out.get("error") != "window_not_selectable")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all bing tests passed")
