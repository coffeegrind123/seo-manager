#!/usr/bin/env python3
"""Controls for bing.py's PAGE dimension.

Every case is a way this returns a confident wrong answer instead of an error:

  1. GetPageStats puts the page URL in a field named `Query`. Reading it as a
     search term turns a page report into nonsense that still looks like data.
  2. Rows arrive one per date, so a page's real total only exists after
     aggregation - reading the first row understates every page differently.
  3. Sorting by impressions inverts the actual answer. Measured on a live
     site 2026-09-01: a locale homepage had a QUARTER of the homepage's
     impressions and THREE TIMES its clicks. Impressions-first ranking puts the
     page earning 68% of all clicks in second place.
  4. A page with impressions but no clicks must not raise on the CTR division.
  5. An empty result is EITHER "no data yet" or "you typed the URL wrong", and
     those are opposite findings - it must say so, not imply the page is dead.
  12. GetQueryStats / GetPageQueryStats return one row per query per WEEK.
      Without the date a flat ten-week ranking reads as ten queries.
  13. `cannibal`: a split, a missing owner page and a page-1 edge position, each
      with a control that must NOT fire.
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
    {"Query": "https://x.net/ja/", "Impressions": 4000,  "Clicks": 1100, "AvgImpressionPosition": 4},
    {"Query": "https://x.net/ja/", "Impressions": 4522,  "Clicks": 1198, "AvgImpressionPosition": 4},
])
r = bing.pages("k", "https://x.net/", 10)
top = r["pages"][0]
check("the URL is read out of the `Query` field", top["url"].startswith("https://x.net/"))
check("rows for one page are aggregated, not truncated to the first",
      any(p["url"] == "https://x.net/" and p["impressions"] == 33403 for p in r["pages"]))
check("clicks aggregate too", top["clicks"] == 2298)
check("sorted by CLICKS, so /ja/ leads despite a quarter the impressions",
      top["url"] == "https://x.net/ja/")
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

# --------------------------------------------------------------------------
# The CRAWLER side. Every case below is a way the crawl/submission surface
# returns a confident wrong answer rather than an error.
# --------------------------------------------------------------------------

def stub_by_method(table, default=None):
    """Different payload per endpoint - crawlissues reads two of them."""
    def _call(method, key, **kw):
        if method in table:
            return {"ok": True, "d": table[method]}
        return default if default is not None else {"ok": True, "d": []}
    bing.call = _call


print("\n8. crawl stats: a running total must never be summed over days")
# Two columns, deliberately opposite in shape. CrawledPages bounces (a daily
# count); InIndex only rises (a stock). Summing the second produced InIndex =
# 82,767 on a live account whose index holds 4,809 pages.
days = [
    {"Date": "/Date(1787443200000)/", "CrawledPages": 293, "InIndex": 1025, "Code4xx": 1},
    {"Date": "/Date(1787529600000)/", "CrawledPages": 169, "InIndex": 1268, "Code4xx": 2},
    {"Date": "/Date(1787616000000)/", "CrawledPages": 216, "InIndex": 1541, "Code4xx": 1},
    {"Date": "/Date(1787702400000)/", "CrawledPages": 411, "InIndex": 1665, "Code4xx": 3},
    {"Date": "/Date(1787788800000)/", "CrawledPages": 275, "InIndex": 1795, "Code4xx": 0},
]
stub_by_method({"GetCrawlStats": days})
r = bing.crawlstats("k", "https://x.net/")
check("a bouncing daily count is summed", r["window_totals"]["crawled_pages"] == 293 + 169 + 216 + 411 + 275)
check("a monotonic stock is NOT summed", "in_index" not in r["window_totals"])
check("and it is named in refused_to_sum", "in_index" in r["refused_to_sum"]["columns"])
check("the stock reports its LATEST value", r["latest_stock"]["in_index"]["latest"] == 1795)
check("and its movement over the window", r["latest_stock"]["in_index"]["change_over_window"] == 770)
check("the two column kinds really do differ",
      r["column_kinds"]["in_index"]["kind"] != r["column_kinds"]["crawled_pages"]["kind"])
check("the measured kind agrees with the reference for in_index",
      "kind_disagrees_with_reference" not in r)

# CONTROL: hand it a stock shaped like a flow and the disagreement must SHOW,
# not be silently overridden by the hardcoded reference table.
flipped = [dict(d, InIndex=v) for d, v in zip(days, [1025, 900, 1541, 1100, 1795])]
stub_by_method({"GetCrawlStats": flipped})
r2 = bing.crawlstats("k", "https://x.net/")
check("CONTROL a reference/measurement disagreement is reported",
      any(x["column"] == "in_index" for x in r2.get("kind_disagrees_with_reference", [])))
check("CONTROL and the MEASURED kind wins", r2["column_kinds"]["in_index"]["kind"] == "flow")

# Measured 2026-10-18 over 71 steps: InIndex 1,025 -> 8,442 with 2 small dips
# (largest 2.3% of its peak), Code2xx with 8 (largest 1.6%). Allowing ONE dip
# called both flows, and InIndex was summed to 366,850 for an 8,442-page index.
# Daily counts dip on about half the days and by a third or more of their peak.
rise = [1000 + 100 * i for i in range(72)]
for i in (20, 45):
    rise[i] = rise[i - 1] - 150                       # two corrections, ~2% of the peak
c2xx = [1100 + 90 * i for i in range(72)]
for i in (5, 12, 19, 26, 33, 40, 47, 54):
    c2xx[i] = c2xx[i - 1] - 100                       # eight corrections, ~1.4%
bounce = [300 + (250 if i % 2 else 0) + 5 * i for i in range(72)]   # a real daily count
long_days = [{"Date": f"/Date({1785715200000 + i * 86400000})/", "InIndex": rise[i],
              "Code2xx": c2xx[i], "CrawledPages": bounce[i]} for i in range(72)]
stub_by_method({"GetCrawlStats": long_days})
r3 = bing.crawlstats("k", "https://x.net/")
check("a stock with two small corrections is cumulative",
      r3["column_kinds"]["in_index"]["kind"] == "cumulative")
check("a stock with eight small corrections is cumulative",
      r3["column_kinds"]["code_2xx"]["kind"] == "cumulative")
check("so neither is summed", "in_index" not in r3["window_totals"] and "code_2xx" not in r3["window_totals"])
check("CONTROL a count that dips every other day is still a flow",
      r3["column_kinds"]["crawled_pages"]["kind"] == "flow")

print("\n9. an empty crawl-issue list is UNKNOWN when the stats disagree")
stub_by_method({"GetCrawlIssues": [],
                "GetCrawlStats": [dict(d, CrawlErrors=e) for d, e in zip(days, [2, 2, 1, 4, 3])]})
r = bing.crawlissues("k", "https://x.net/", 20)
check("errors in stats + nothing here is unknown, not none", r["verdict"] == "unknown")
check("and it says which reading contradicts it", "12" in r["why_not_none"] or "crawl errors" in r["why_not_none"])

# CONTROL: with no errors anywhere, empty really is empty - the guard must not
# refuse every zero, only the unsupported ones.
stub_by_method({"GetCrawlIssues": [],
                "GetCrawlStats": [dict(d, CrawlErrors=0) for d in days]})
r = bing.crawlissues("k", "https://x.net/", 20)
check("CONTROL a corroborated zero is reported as none", r["verdict"] == "none")

stub_by_method({"GetCrawlIssues": [{"Url": "https://x.net/a", "HttpCode": 404,
                                    "Issues": 4 | 64, "InLinks": 3}]})
r = bing.crawlissues("k", "https://x.net/", 20)
check("a bitmask row decodes both of its flags",
      set(r["issues"][0]["issues"]) == {"code_4xx", "important_url_blocked_by_robots_txt"})
check("and keeps the raw mask so a decoder bug is auditable", r["issues"][0]["issues_raw"] == 68)

print("\n10. GetFeeds carries a SECOND never-sentinel that decodes to a real date")
stub_by_method({"GetFeeds": [{"Url": "https://x.net/sitemap.xml", "Type": "Sitemap",
                              "Status": "Success", "UrlCount": 6127,
                              "LastCrawled": "/Date(1788155102000)/",
                              "Submitted": "/Date(-11644473600000)/",
                              "FileSize": 0, "Compressed": False}]})
r = bing.feeds("k", "https://x.net/", False)
f = r["feeds"][0]
check("the FILETIME epoch is 'never', not 1601-01-01", f["submitted"] is None)
check("and it says so rather than leaving a bare null", "submitted_means" in f)
check("CONTROL a real crawl date is still read", f["last_crawled"] == "2026-08-31")
check("Bing's held url count is reported as BING's, not as the truth",
      f["url_count_per_bing"] == 6127)

print("\n11. submit: the guards run before the network, not after")
bing.call = lambda method, key, **kw: {
    "ok": True, "d": {"DailyQuota": 2, "MonthlyQuota": 3000}}
sent = {}
bing.post = lambda method, key, payload: (sent.update(payload) or {"ok": True, "d": None})

r = bing.submit("k", "https://x.net/", ["https://x.net/a", "https://evil.example/b"], None, False)
check("an off-site url is dropped before sending", r["on_site"] == 1)
check("and named, so a typo is visible", r["off_site"] == ["https://evil.example/b"])
check("no --yes means nothing was submitted", r["submitted"] is False and r["dry_run"] is True)
check("and post() was never reached", sent == {})

r = bing.submit("k", "https://x.net/", ["https://x.net/a", "https://x.net/b", "https://x.net/c"], None, True)
check("a batch over the daily quota is refused", r["ok"] is False)
check("and says so rather than truncating silently", r["error"] == "batch exceeds the daily quota")
check("CONTROL the refusal did not send a partial batch", sent == {})

r = bing.submit("k", "https://x.net/", ["https://x.net/a", "https://x.net/a"], None, True)
check("CONTROL a within-quota batch DOES send", r["submitted"] is True)
check("deduplicated on the way, and ONE url goes through SubmitUrl",
      sent.get("url") == "https://x.net/a" and r["method"] == "SubmitUrl")
sent.clear()
r = bing.submit("k", "https://x.net/", ["https://x.net/a", "https://x.net/b"], None, True)
check("CONTROL several urls go through SubmitUrlBatch",
      sent.get("urlList") == ["https://x.net/a", "https://x.net/b"] and r["method"] == "SubmitUrlBatch")
check("and the receipt does not claim a crawl happened", "not a crawl confirmation" in r["note"])

print("\n12. weekly query rows keep their date, and aggregate per query")
# 2026-08-07 = 1786060800 s (1787443200, i.e. 2026-08-23, minus 16 days); +604800 per week.
W1, W2, W3 = "/Date(1786060800000)/", "/Date(1786665600000)/", "/Date(1787270400000)/"
weekly_payload = [
    {"Query": "blue widget", "Date": W3, "Impressions": 100, "Clicks": 1, "AvgImpressionPosition": 7, "AvgClickPosition": -1},
    {"Query": "blue widget", "Date": W1, "Impressions": 100, "Clicks": 1, "AvgImpressionPosition": 6, "AvgClickPosition": -1},
    {"Query": "blue widget", "Date": W2, "Impressions": 300, "Clicks": 3, "AvgImpressionPosition": 8, "AvgClickPosition": -1},
    {"Query": "green widget", "Date": W2, "Impressions": 40, "Clicks": 0, "AvgImpressionPosition": 15, "AvgClickPosition": -1},
]
stub(weekly_payload)
r = bing.queries("k", "https://example.com/", 50)
check("every dated row carries `date`", all(x.get("date") for x in r["queries"]))
check("`queries` stays one row per week (backward-compatible key and length)",
      r["count"] == 4 and len(r["queries"]) == 4)
check("the old row keys survive for keywords.py bing",
      all({"query", "impressions", "clicks", "avg_position"} <= set(x) for x in r["queries"]))
bq = {x["query"]: x for x in r["by_query"]}
check("by_query has one row per query", r["query_count"] == 2 and len(bq) == 2)
# (100*7 + 100*6 + 300*8) / 500 = 3700/500 = 7.4
check("by_query position is impression-weighted (7.4, not the plain mean 7.0)",
      bq["blue widget"]["avg_position"] == 7.4)
check("by_query carries weeks and the date span",
      (bq["blue widget"]["weeks"], bq["blue widget"]["first_date"], bq["blue widget"]["last_date"])
      == (3, "2026-08-07", "2026-08-21"))
check("by_query is sorted by total impressions", r["by_query"][0]["query"] == "blue widget")
check("the window is reported in weeks", r["window_weeks"] == {"count": 3, "first": "2026-08-07", "last": "2026-08-21"})
r1 = bing.queries("k", "https://example.com/", 1)
check("--limit cuts by_query AFTER aggregation, not the rows it is built from",
      len(r1["by_query"]) == 1 and r1["by_query"][0]["impressions"] == 500)

r = bing.queries("k", "https://example.com/", 50, exact="BLUE widget")
check("--q returns one query's series", r["matched"] == 1)
check("--q series is in DATE order regardless of payload order",
      [p["date"] for p in r["results"][0]["series"]] == ["2026-08-07", "2026-08-14", "2026-08-21"])
check("and its weekly positions are Bing's impression positions",
      [p["avg_position"] for p in r["results"][0]["series"]] == [6, 8, 7])
check("AvgClickPosition -1 never reaches a row as a rank",
      all(x["avg_click_position"] is None for x in bing._weekly(weekly_payload)))
r = bing.queries("k", "https://example.com/", 50, contains="widget")
check("--contains matches every query holding the substring", r["matched"] == 2)
r = bing.queries("k", "https://example.com/", 50, exact="widget")
check("CONTROL --q is exact: a substring does not match", r["matched"] == 0)
check("and an empty match is not reported as zero demand", "NOT zero demand" in r["empty_means"])

rc, out = cli("queries", "--q", "a", "--contains", "b")
check("--q with --contains is refused before the network", out.get("error") == "conflicting_match" and rc != 0)

stub(weekly_payload)
r = bing.pagequeries("k", "https://example.com/", "https://example.com/p", 50)
check("pagequeries rows are dated too", all(x.get("date") for x in r["queries"]))
check("and pagequeries aggregates per query", len(r["by_query"]) == 2)

print("\n13. cannibal: split / owner_absent / edge_of_page, each with a control")
SITE = "https://example.com/"
A, B, C, G, P = (SITE + "a", SITE + "b", SITE + "c", SITE + "guide", SITE + "pricing")


def row(q, d, imp, pos):
    return {"Query": q, "Date": d, "Impressions": imp, "Clicks": 0,
            "AvgImpressionPosition": pos, "AvgClickPosition": -1}


PAGE_STATS = [{"Query": u, "Impressions": n, "Clicks": 0, "AvgImpressionPosition": 5}
              for u, n in ((A, 695), (B, 305), (C, 100), (G, 500), (P, 20))]
PER_PAGE = {
    A: [row("widget pricing", W1, 400, 8), row("widget pricing", W2, 200, 11), row("widget", W1, 95, 4)],
    B: [row("widget pricing", W2, 300, 10), row("widget", W1, 5, 12)],
    C: [row("widget pricing", W1, 100, 15)],
    G: [row("widget guide", W1, 500, 3)],
    P: [],
}
SITE_Q = [row("widget pricing", W1, 700, 9), row("widget", W1, 100, 4), row("widget guide", W1, 500, 3)]
calls = []


def cannibal_stub(fail=()):
    def _call(method, key, **kw):
        calls.append((method, kw.get("page")))
        if method == "GetPageStats":
            return {"ok": True, "d": PAGE_STATS}
        if method == "GetQueryStats":
            return {"ok": True, "d": SITE_Q}
        if method == "GetPageQueryStats":
            if kw["page"] in fail:
                return {"ok": False, "error": "HTTP 429"}
            return {"ok": True, "d": PER_PAGE.get(kw["page"], [])}
        return {"ok": True, "d": []}
    bing.call = _call


cannibal_stub()
calls.clear()
r = bing.cannibal("k", SITE, [A, B, C], 0, {"pricing": P, "guide": G}, pace=0)
fd = {f["query"]: f for f in r["findings"]}
check("an owner URL is measured even when not passed as a page",
      ("GetPageQueryStats", P) in calls and P in r["pages_measured"])
check("exactly one cannibalised query", sorted(fd) == ["widget pricing"])
wp = fd["widget pricing"]
check("split + owner_absent + edge_of_page on it",
      set(wp["flags"]) == {"split", "owner_absent", "edge_of_page"})
# /a: (400*8 + 200*11)/600 = 9.0; shares 600/1000, 300/1000, 100/1000
check("per-URL impressions, shares and weighted position",
      [(u["url"], u["impressions"], u["share"], u["avg_position"]) for u in wp["urls"]]
      == [(A, 600, 0.6, 9.0), (B, 300, 0.3, 10.0), (C, 100, 0.1, 15.0)])
check("the owner row says absent", wp["owners"] == [{"phrase": "pricing", "url": P, "state": "absent"}])
check("the rotation is visible week by week",
      [x["url"] for x in wp["weekly_leaders"]] == [A, B] and wp["leader_changes"] == 1)
check("site-level and page-level impressions are both reported, not divided",
      wp["site_impressions"] == 700 and wp["measured_page_impressions"] == 1000
      and "coverage" not in json.dumps(wp))
check("CONTROL the owned query (owner present, one URL) is not a finding", "widget guide" not in fd)
check("CONTROL a 95/5 split under min_share is not a finding", "widget" not in fd)

r = bing.cannibal("k", SITE, [A, B, C], 0, {"pricing": P}, min_share=0.35, pace=0)
check("--min-share is honoured", r["summary"]["split"] == 0 and r["summary"]["owner_absent"] == 1)

cannibal_stub(fail=(P,))
r = bing.cannibal("k", SITE, [A, B, C], 0, {"pricing": P}, pace=0)
check("a FAILED owner call is unmeasured, never absent",
      r["summary"]["owner_absent"] == 0 and r["unmeasured_pages"] == [P] and "unmeasured_means" in r)
check("and the owner row says so",
      r["findings"][0]["owners"] == [{"phrase": "pricing", "url": P, "state": "unmeasured"}])

cannibal_stub()
r = bing.cannibal("k", SITE, [], 2, {}, pace=0)
check("auto mode measures the top N pages BY IMPRESSIONS", r["pages_measured"] == [A, G])

r = bing.cannibal("k", SITE, [A], 0, {"pricing": SITE + "pricing/"}, pace=0)
check("an owner URL differing from Bing's by a slash is refused, not reported absent",
      r["ok"] is False and r["error"] == "owner_url_not_as_bing_reports")

cannibal_stub(fail=(A, B))
r = bing.cannibal("k", SITE, [A, B], 0, {}, pace=0)
check("every page failing is a refusal, not 'no cannibalisation'",
      r["ok"] is False and "findings" not in r)

PER_PAGE_CLEAN = {A: [row("alpha tool", W1, 200, 2)], B: [row("beta tool", W1, 300, 3)]}
_saved = dict(PER_PAGE)
PER_PAGE.clear(); PER_PAGE.update(PER_PAGE_CLEAN)
cannibal_stub()
r = bing.cannibal("k", SITE, [A, B], 0, {}, pace=0)
check("CONTROL a site with no shared queries reports NO finding",
      r["ok"] is True and r["findings"] == [] and r["queries_checked"] == 2)
check("and the empty result says it covers the measured pages only", "measured pages" in r["empty_means"])
PER_PAGE.clear(); PER_PAGE.update(_saved)

import tempfile
with tempfile.TemporaryDirectory() as td:
    hp = os.path.join(td, "headterms.json")
    with open(hp, "w") as fh:
        json.dump({"terms": [{"phrase": "pricing", "owner": "/pricing", "allow": []},
                             {"phrase": "guide", "owner": G}]}, fh)
    check("the head-term map's own shape is read as owners",
          bing._read_owners([], hp) == {"pricing": "/pricing", "guide": G})
    with open(hp, "w") as fh:
        json.dump({"terms": []}, fh)
    try:
        bing._read_owners([], hp)
        check("an owners file naming nobody is refused", False)
    except ValueError:
        check("an owners file naming nobody is refused", True)
cannibal_stub()
r = bing.cannibal("k", SITE, [A, B, C], 0, {"pricing": "/pricing"}, pace=0)
check("a site-relative owner resolves against the site and is measured",
      P in r["pages_measured"] and r["summary"]["owner_absent"] == 1)

rc, out = cli("cannibal", "--min-share", "0.6")
check("a --min-share at which a split is impossible is refused", out.get("error") == "min_share_out_of_range")
rc, out = cli("cannibal", "--owner", "pricing")
check("a malformed --owner is refused", out.get("error") == "bad_cannibal_input")
rc, out = cli("cannibal", "--days", "7")
check("cannibal --days refuses like the other fixed-window commands", out.get("error") == "window_not_selectable")

# --------------------------------------------------------------------------
# The rest of the API surface (2026-10-18).
# --------------------------------------------------------------------------
import io  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

print("\n14. transport: the error envelope lives in an HTTP 400 body, and throttles retry")
REAL_URLOPEN = urllib.request.urlopen
import importlib  # noqa: E402
fresh = importlib.reload(bing)   # the module's own call()/post(), not a stub from above
slept = []
fresh._sleep = slept.append


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def answers(*seq):
    """urlopen that returns each answer in turn: (status, body)."""
    q = list(seq)

    def _open(req, timeout=None):
        status, body = q.pop(0)
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body.encode()))
        return _Resp(body.encode())
    return _open


urllib.request.urlopen = answers((400, '{"ErrorCode":14,"Message":"ERROR!!! NotAuthorized"}'))
r = fresh.call("GetCrawlStats", "k", siteUrl="https://x.net/")
check("a 400 envelope is decoded to its NAME, not 'HTTP 400'",
      r["ok"] is False and r["error_name"] == "NotAuthorized" and r["error_code"] == 14)
check("and an auth failure is not retried", slept == [])

urllib.request.urlopen = answers((400, '{"ErrorCode":5,"Message":"ERROR!!! ThrottleHost"}'),
                                 (200, '{"d":[1,2]}'))
r = fresh.call("GetCrawlStats", "k", siteUrl="https://x.net/")
check("a ThrottleHost is retried and the retry's answer returned", r == {"ok": True, "d": [1, 2]})
check("after the first backoff", slept == [fresh.THROTTLE_BACKOFF_S[0]])

slept.clear()
urllib.request.urlopen = answers(*[(400, '{"ErrorCode":4,"Message":"ERROR!!! ThrottleUser"}')] * 4)
r = fresh.call("GetCrawlStats", "k", siteUrl="https://x.net/")
check("a throttle that never clears is `throttled`, never data",
      r["ok"] is False and r.get("throttled") is True and len(slept) == len(fresh.THROTTLE_BACKOFF_S))

urllib.request.urlopen = answers((404, ""))
r = fresh.call("GetSiteMoves", "k", siteUrl="https://x.net/")
check("an empty 404 is a RETIRED method", r["error"] == "method_not_served")
urllib.request.urlopen = answers((200, '{"ErrorCode":14,"Message":"x"}'))
check("CONTROL the envelope is still caught on a 200",
      fresh.call("GetUserSites", "k").get("error_name") == "NotAuthorized")
urllib.request.urlopen = answers((200, ""))
check("a write's empty body is ok with d None", fresh.post("SubmitFeed", "k", {}) == {"ok": True, "d": None})
SECRET = "k3y-that-must-never-print"


def _boom(req, timeout=None):
    raise ValueError(f"cannot open {req.full_url}")


urllib.request.urlopen = _boom
r = fresh.call("GetUserSites", SECRET)
check("an exception quoting the URL never prints the API key",
      SECRET not in json.dumps(r) and "<redacted>" in r["error"])
urllib.request.urlopen = answers((400, f"bad key {SECRET}"))
check("nor does an error BODY that echoes it",
      SECRET not in json.dumps(fresh.post("SubmitFeed", SECRET, {})))
urllib.request.urlopen = REAL_URLOPEN
bing = fresh
bing._sleep = lambda s: None
SITE = "https://x.net/"
D = lambda iso: f"/Date({int(__import__('calendar').timegm(__import__('time').strptime(iso, '%Y-%m-%d'))) * 1000})/"

print("\n15. keywordtrend: weekly, date-ordered, trend only when both halves exist")
stub([{"Date": D(f"2026-0{m}-0{d}"), "Impressions": i, "BroadImpressions": i * 2, "Query": "q"}
      for (m, d, i) in [(5, 9, 40), (5, 2, 30), (4, 4, 10), (4, 1, 20)]])
r = bing.keywordtrend("k", "q", "us", "en-US")
check("weeks come back in date order whatever order Bing sent",
      [p["date"] for p in r["series"]] == ["2026-04-01", "2026-04-04", "2026-05-02", "2026-05-09"])
check("totals keep exact and broad apart", r["totals"] == {"impressions": 100, "broad_impressions": 200})
check("a 4-week trend needs 8 weeks, so it is null here", r["trend_4w"] is None)
stub([])
r = bing.keywordtrend("k", "q", "us", "en-US")
check("an empty answer is unmeasured, not zero demand",
      r["known_to_bing"] is False and "UNMEASURED" in r["empty_means"])

print("\n16. querypages: the URL arrives in `Query`, and a split across URLs is flagged")
W1, W2 = D("2026-08-07"), D("2026-08-14")
stub([{"Query": "https://x.net/a", "Date": W1, "Impressions": 600, "Clicks": 6, "AvgImpressionPosition": 8},
      {"Query": "https://x.net/b", "Date": W2, "Impressions": 400, "Clicks": 1, "AvgImpressionPosition": 9},
      {"Query": "https://x.net/c", "Date": W2, "Impressions": 5, "Clicks": 0, "AvgImpressionPosition": 30}])
r = bing.querypages("k", SITE, "widget", 10)
check("rows are keyed by the URL from the `Query` field", [u["url"] for u in r["urls"]][:2] ==
      ["https://x.net/a", "https://x.net/b"])
check("two URLs over min_share are a split", "split" in r["flags"] and len(r["split_urls"]) == 2)
check("a best position of 8 is edge-of-page", "edge_of_page" in r["flags"])
check("the leader changed between the two weeks", r["leader_changes"] == 1)
stub([{"Query": "https://x.net/a", "Date": W1, "Impressions": 600, "Clicks": 6, "AvgImpressionPosition": 2}])
r = bing.querypages("k", SITE, "widget", 10)
check("CONTROL one URL at #2 raises no flag", r["flags"] == [])
stub([])
check("an empty answer says NOT 'we do not rank'",
      "NOT" in bing.querypages("k", SITE, "widget", 10)["empty_means"])

print("\n17. querypagedetail: the position distribution an average hides")
stub([{"Date": W1, "Position": 3, "Impressions": 50, "Clicks": 5},
      {"Date": W1, "Position": 15, "Impressions": 50, "Clicks": 0},
      {"Date": W2, "Position": 3, "Impressions": 100, "Clicks": 9}])
r = bing.querypagedetail("k", SITE, "q", "https://x.net/a")
check("the histogram sums impressions per position",
      r["histogram"] == [{"position": 3, "impressions": 150, "clicks": 14},
                         {"position": 15, "impressions": 50, "clicks": 0}])
check("page1_share is the fraction shown at 1-10", r["page1_share"] == 0.75)
check("each day keeps its best and worst", (r["series"][0]["best"], r["series"][0]["worst"]) == (3, 15))
check("the average is impression-weighted: (3*150 + 15*50)/200 = 6.0", r["avg_position"] == 6.0)

print("\n18. querytraffic: daily, with 7-day halves")
stub([{"Date": D(f"2026-08-{d:02d}"), "Impressions": 10 if d <= 7 else 20, "Clicks": 1}
      for d in range(1, 15)])
r = bing.querytraffic("k", SITE, "q")
check("14 days give a 7-day trend", r["trend_7d"]["impressions"]["change"] == 70)
check("and no 28-day one", r["trend_28d"]["impressions"] is None)

print("\n19. urltraffic: a zero is cross-examined against GetPageStats")
stub_by_method({"GetUrlTrafficInfo": {"Clicks": 0, "Impressions": 0, "IsPage": True, "Url": SITE},
                "GetPageStats": [{"Query": SITE, "Impressions": 900, "Clicks": 20, "AvgImpressionPosition": 5}]})
r = bing.urltraffic("k", SITE, SITE)
check("zero here and traffic in page stats is `disagrees`, not 'no traffic'", r["verdict"] == "disagrees")
stub_by_method({"GetUrlTrafficInfo": {"Clicks": 3, "Impressions": 90, "IsPage": True, "Url": SITE},
                "GetPageStats": [{"Query": SITE, "Impressions": 900, "Clicks": 20, "AvgImpressionPosition": 5}]})
check("CONTROL both reporting traffic is consistent",
      bing.urltraffic("k", SITE, SITE)["verdict"] == "consistent")

print("\n20. children: an empty Index Explorer is UNKNOWN while Bing holds pages")
stats = [{"Date": D(f"2026-08-{d:02d}"), "InIndex": 1000 + d, "CrawledPages": d % 3}
         for d in range(1, 8)]
bing.post = lambda m, k, p: {"ok": True, "d": []}
stub_by_method({"GetCrawlStats": stats})
r = bing.children("k", SITE, SITE, pace=0)
check("no children + pages in index = unknown", r["verdict"] == "unknown" and "1007" in r["why_not_none"])
pages_seen = []


def _kids(m, k, p):
    pages_seen.append(p["page"])
    return {"ok": True, "d": [{"Url": "https://x.net/a"}, {"Url": "https://x.net/b"}]}


bing.post = _kids
r = bing.children("k", SITE, SITE, pace=0)
check("a page that adds no NEW url stops the paging", r["count"] == 2 and pages_seen == [0, 1])

print("\n21. feeddetails: an index and its children")
stub([{"Url": SITE + "sitemap.xml", "Type": "Sitemap Index", "Status": "Success", "UrlCount": 1500,
       "LastCrawled": D("2026-10-15"), "Submitted": "/Date(-11644473600000)/"},
      {"Url": SITE + "s1.xml", "Type": "Sitemap", "Status": "Success", "UrlCount": 1000,
       "LastCrawled": D("2026-10-01"), "Submitted": "/Date(-11644473600000)/"},
      {"Url": SITE + "s2.xml", "Type": "Sitemap", "Status": "Error", "UrlCount": 400,
       "LastCrawled": D("2026-10-14"), "Submitted": "/Date(-11644473600000)/"}])
r = bing.feeddetails("k", SITE, SITE + "sitemap.xml")
cs_ = r["children_summary"]
check("children are separated from the index row", cs_["count"] == 2 and r["feed_row"]["url_count_per_bing"] == 1500)
check("a failing child is named", cs_["not_success"] == [SITE + "s2.xml"])
check("the stalest child is reported", cs_["oldest_crawl"] == "2026-10-01")
check("index vs children delta is computed, not assumed zero", cs_["index_vs_children_delta"] == 100)

print("\n22. submitfeed / removefeed: guards before the network, read-back after")
posted = []
bing.post = lambda m, k, p: posted.append((m, p)) or {"ok": True, "d": None}
r = bing.submitfeed("k", SITE, "https://evil.example/sitemap.xml", True)
check("an off-site feed is refused before anything is sent", r["error"] == "feed_off_site" and posted == [])
bing._live_feed = lambda u: (404, None, None)
r = bing.submitfeed("k", SITE, SITE + "sitemap.xml", True)
check("a feed that is not live is refused - the deploy-window case",
      r["error"] == "feed_not_healthy" and posted == [])
bing._live_feed = lambda u: (200, "urlset", None)
stub_by_method({"GetFeeds": []})
r = bing.submitfeed("k", SITE, SITE + "sitemap.xml", False)
check("no --yes is a dry run that sends nothing", r.get("dry_run") is True and posted == [])
today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
stub_by_method({"GetFeeds": [{"Url": SITE + "sitemap.xml", "Type": "Sitemap", "Status": "Success",
                              "UrlCount": 5, "Submitted": D(today), "LastCrawled": D(today)}]})
r = bing.submitfeed("k", SITE, SITE + "sitemap.xml", True)
check("--yes sends SubmitFeed with siteUrl + feedUrl",
      posted == [("SubmitFeed", {"siteUrl": SITE, "feedUrl": SITE + "sitemap.xml"})])
check("and today's submission date read back is `confirmed`", r["verified"] == "confirmed")
posted.clear()
stub_by_method({"GetFeeds": []})
r = bing.removefeed("k", SITE, SITE + "gone.xml", True)
check("removing a feed Bing does not hold is refused - its null would prove nothing",
      r["error"] == "feed_not_held" and posted == [])

print("\n22b. feeds --verify: a sitemap INDEX is verified, not skipped")
# Measured 2026-10-18: Bing types the site's only feed "Sitemap Index", and the
# verify filter accepted only "Sitemap" - so --verify returned [] and read as
# "nothing to compare".
stub([{"Url": SITE + "sitemap.xml", "Type": "Sitemap Index", "Status": "Success", "UrlCount": 8299,
       "LastCrawled": D("2026-10-15"), "Submitted": "/Date(-11644473600000)/"},
      {"Url": SITE + "feed.rss", "Type": "RSS", "Status": "Success", "UrlCount": 20,
       "LastCrawled": D("2026-10-15"), "Submitted": "/Date(-11644473600000)/"}])
REAL_COUNT = bing._count_sitemap
bing._count_sitemap = lambda url, depth=0, seen=None: (8300, [])
r = bing.feeds("k", SITE, True)
bing._count_sitemap = REAL_COUNT
check("a Sitemap Index row is verified", [v["url"] for v in r["verify"]] == [SITE + "sitemap.xml"])
check("and its delta against the live count is computed", r["verify"][0].get("delta") == 1)
check("CONTROL an RSS feed is still not counted as a sitemap", len(r["verify"]) == 1)

print("\n23. fetch: UnknownError retried once, slash-folded history accepted")
calls = []


def _fetch_post(m, k, p):
    calls.append(m)
    return ({"ok": False, "error": "x", "error_code": 2} if len(calls) == 1 else {"ok": True, "d": None})


bing.post = _fetch_post
now_ms = int(__import__("time").time() * 1000)
doc = ("Last-DownLoad-Http-Code: 200\r\nTruncation: 0\r\nRobots-Rule: 0\r\n\r\n"
       "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<html><title>t</title></html>")
stub_by_method({"GetFetchedUrls": [{"Url": "https://x.net/zh", "Fetched": True, "Date": f"/Date({now_ms})/"}],
                "GetFetchedUrlDetails": {"Status": "Completed", "Date": f"/Date({now_ms})/", "Document": doc}})
r = bing.fetch("k", SITE, "https://x.net/zh/", wait=30)
check("an UnknownError on FetchUrl is retried once", calls == ["FetchUrl", "FetchUrl"])
check("/zh in the history completes a fetch of /zh/", r["ok"] and r["verdict"] == "clean")
stub_by_method({"GetFetchedUrls": [], "GetFetchedUrlDetails": None})
bing.time.time = (lambda t0=[0]: (t0.__setitem__(0, t0[0] + 50) or t0[0]))  # each poll costs 50s
r = bing.fetch("k", SITE, "https://x.net/p", wait=60)
bing.time.time = __import__("time").time
check("a fetch that never completes is `fetch_pending`, not a result", r.get("error") == "fetch_pending")
r = bing.fetch("k", SITE, "https://evil.example/", wait=1)
check("an off-site fetch is refused", r.get("error") == "url_off_site")

print("\n24. cannibal --query: a URL nobody listed can join a split")
A_, B_ = "https://x.net/a", "https://x.net/b"


def _cn_call(method, key, **kw):
    if method == "GetPageStats":
        return {"ok": True, "d": [{"Query": A_, "Impressions": 500, "Clicks": 5, "AvgImpressionPosition": 8}]}
    if method == "GetPageQueryStats":
        return {"ok": True, "d": [{"Query": "widget", "Date": W1, "Impressions": 500, "Clicks": 5,
                                   "AvgImpressionPosition": 8}]}
    if method == "GetQueryPageStats":
        return {"ok": True, "d": [{"Query": A_, "Date": W1, "Impressions": 500, "Clicks": 5, "AvgImpressionPosition": 8},
                                  {"Query": B_, "Date": W1, "Impressions": 400, "Clicks": 2, "AvgImpressionPosition": 9}]}
    return {"ok": True, "d": []}


bing.call = _cn_call
r = bing.cannibal("k", SITE, [A_], 0, {}, pace=0)
check("CONTROL page-side alone cannot see /b", r["summary"]["split"] == 0)
r = bing.cannibal("k", SITE, [A_], 0, {}, pace=0, queries=["widget"])
check("query-side finds /b", r["pages_found_by_query"] == [B_])
check("and the split now fires", r["summary"]["split"] == 1)
check("without double-counting /a's own row",
      next(u for u in r["findings"][0]["urls"] if u["url"] == A_)["impressions"] == 500)

print("\n25. CLI: every new fixed-window command refuses --days")
for cmd, extra in [("keywordtrend", ["--q", "x"]), ("querypages", ["--q", "x"]),
                   ("querytraffic", ["--q", "x"]), ("children", []), ("urltraffic", ["--url", "x"])]:
    rc, out = cli(cmd, *extra, "--days", "7")
    check(f"{cmd} --days refuses", out.get("error") == "window_not_selectable")
rc, out = cli("querypages", "--q", "x", "--min-share", "0.9")
check("querypages refuses an impossible --min-share", out.get("error") == "min_share_out_of_range")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all bing tests passed")
