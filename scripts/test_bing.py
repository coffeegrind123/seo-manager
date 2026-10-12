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
check("deduplicated on the way", sent["urlList"] == ["https://x.net/a"])
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

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("all bing tests passed")
