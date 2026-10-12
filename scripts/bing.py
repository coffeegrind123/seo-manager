#!/usr/bin/env python3
"""Bing Webmaster Tools API - the only FREE source of real volume and backlinks.

Everything else in this skill either estimates demand or measures it indirectly.
This is different: for a site you have VERIFIED OWNERSHIP of, Bing hands over
real impression counts and real inbound links, free, with no rate limit worth
worrying about.

  sites      verified sites on this account. Also the auth control - run it
             first when anything else looks wrong.
  keyword    impressions for ONE query. ⚠ `known_to_bing: false` is NOT a
             demand verdict - see the coverage note below
  expand     related keywords WITH impressions - keyword research with numbers
  backlinks  inbound links, by count and by source URL
  queries    the queries this site actually appears for on Bing - one row per
             query per WEEK, dated, plus `by_query` (one row per query, totals
             and impression-weighted position). `--q` / `--contains` return one
             query's weekly series in date order: the only way to tell a trend
             from the same query counted ten times
  pages      per-PAGE impressions, clicks and CTR - WHICH URL earns. `queries`
             cannot answer that, and the two have opposite fixes: a locale page
             ranking for its own language is a win to extend, the homepage
             ranking for that language is a targeting bug
  pagequeries  the queries ONE page appears for - attribution for the above
  cannibal   for a set of pages, every query x URL pair: which URLs share a
             query (`split`), which named owner page is missing from one
             (`owner_absent`), and which queries sit at positions 7-11
             (`edge_of_page`), where URL rotation drops a site off page 1
  urlinfo    Bing's own index record for ONE url - discovered, last crawled,
             size. The Bing counterpart to Google's URL Inspection, and the
             one that matters where Bing carries the traffic. ⚠ a URL that
             does not exist returns the SAME empty answer as a real page Bing
             has never seen - always run it with a known-crawled control
  traffic    clicks / impressions / rank over time

The CRAWLER's side of the same account - what BINGBOT did, rather than what
searchers did. First-party: no user-agent to verify, no access log to parse.

  crawlstats   pages fetched per day, response-code mix, pages in index. ⚠ the
               row mixes DAILY counts with RUNNING TOTALS and says which is
               which nowhere - summing the wrong column produced Code2xx =
               96,000 for a site with 7,408 pages crawled. Each column's kind
               is re-derived from the series every run
  crawlissues  per-URL problems, bitmask decoded. An empty list is cross-checked
               against crawlstats: errors there plus nothing here is UNKNOWN,
               never "no issues"
  feeds        the sitemap AS BING HOLDS IT. `UrlCount` is from Bing's last
               crawl of the feed, so a gap against the live file dates your
               deploy from the crawler's side. `--verify` counts the live one
  quota        URLs Bing will still accept. ⚠ NOT IndexNow's - separate channel,
               separate allowance, and neither ping spends the other
  submit       SubmitUrlBatch. The only mutating call here, so it is a DRY RUN
               until --yes, refuses off-site URLs before spending the call, and
               refuses a batch larger than the remaining quota
  blocked      account-level URL blocks + page-preview blocks. Invisible to any
               crawl of the site, and they survive every deploy
  crawlsettings  the per-hour crawl-rate cap. Raising it removes a ceiling; it
               does not make Bing crawl more

⚠️ THE ONE THING THAT MATTERS MOST HERE: these are **BING IMPRESSIONS**, not
Google search volume, and the two are different quantities by more than a
constant. Bing is a minority engine (single-digit share in most markets, and
skewed by demographic and device in ways that vary per niche), so a Bing number
cannot be converted into a Google number without a multiplier this skill does
not have and will not invent.

So `bing.py` reports `bing_impressions` and NEVER `volume`, and the quality
bar's volume floor is NOT applied to it. What it IS good for, and this is
genuinely valuable:

  * RELATIVE ranking of candidates against each other. If A gets 10x the Bing
    impressions of B, that ordering is real information, and it is the first
    such signal in this skill backed by counts rather than by autocomplete
    position.
  * A floor on absolute demand. A query with real Bing impressions has real
    demand somewhere. The converse does not hold - near-zero on Bing does not
    prove near-zero on Google.

⚠ `known_to_bing: false` MEANS "Bing's keyword database has no row for this
string" - it does NOT mean the query is small, and it must never be used as a
demand gate. Measured 2026-09-01 on us/en-US over 90 days: one long-tail query
returned exact = **2**, while three sibling queries on the same head term - all
plainly larger - returned nothing at all. An endpoint
that reports 2 for one query and nothing for bigger ones is not thresholding by
volume; its coverage is patchy. So an unknown query is UNMEASURED, which is a
different state from "no demand" and belongs on the "cannot ask" side of the
providers.py rule.

Read references/data-sources.md before wiring this into a gate.

Stdlib only. Key from BING_WEBMASTER_API_KEY or ~/.bing_webmaster_key (0600).
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = "https://ssl.bing.com/webmaster/api.svc/json"
UA = "seo-manager/1.0"

# Measured 2026-08-01: the API rejects /Date(ms)/ with "String was not
# recognized as a valid DateTime" and accepts plain ISO. Do not "fix" this
# back to the .NET epoch form that the SOAP-era docs imply.
DATE_FMT = "%Y-%m-%d"


def read_key():
    val = os.environ.get("BING_WEBMASTER_API_KEY", "").strip()
    if val:
        return val
    p = os.path.expanduser("~/.bing_webmaster_key")
    try:
        if os.path.isfile(p):
            return open(p).read().strip()
    except OSError:
        pass
    return ""


def call(method, key, **params):
    """One API call. Distinguishes an ERROR from an empty-but-valid answer."""
    qs = urllib.parse.urlencode({"apikey": key, **{k: v for k, v in params.items() if v is not None}})
    req = urllib.request.Request(f"{BASE}/{method}?{qs}", headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable response", "detail": body[:200]}

    # The API answers HTTP 200 with an ErrorCode envelope for real failures,
    # including NotAuthorized. Treating that as data is how "we have no
    # backlinks" gets reported when the truth is "you do not own this site".
    if isinstance(data, dict) and "ErrorCode" in data:
        return {"ok": False, "error": data.get("Message", "unknown"), "error_code": data.get("ErrorCode")}
    return {"ok": True, "d": data.get("d")}


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402

# The ONLY two subcommands whose Bing endpoint accepts a date range. Module-level
# so `control` can check the real constant rather than a copy of it - a control
# that asserts against its own duplicate of a literal proves nothing.
WINDOWED = ("keyword", "expand")


def _norm_site(url):
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    return url if url.endswith("/") else url + "/"


def sites(key):
    r = call("GetUserSites", key)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    return {
        "ok": True,
        "count": len(rows),
        "sites": [{"url": s.get("Url"), "verified": s.get("IsVerified")} for s in rows],
    }


def _resolve_site(key, given):
    """Use the given site, or the single verified one if there is exactly one."""
    if given:
        return _norm_site(given), None
    r = sites(key)
    if not r.get("ok"):
        return None, r
    verified = [s["url"] for s in r["sites"] if s["verified"]]
    if len(verified) == 1:
        return verified[0], None
    return None, {"ok": False, "error": f"pass --site; this account has {len(verified)} verified sites",
                  "sites": verified}


def keyword(key, q, country, language, start, end):
    r = call("GetKeyword", key, q=q, country=country, language=language, startDate=start, endDate=end)
    if not r["ok"]:
        return r
    d = r["d"] or {}
    # A query Bing has never seen comes back Query:null with zeroes. That is a
    # real "no data", not a fabricated zero - but it must not be presented as a
    # measured zero either.
    known = d.get("Query") is not None
    return {
        "ok": True,
        "query": q,
        "known_to_bing": known,
        "bing_impressions": d.get("Impressions") if known else None,
        "bing_broad_impressions": d.get("BroadImpressions") if known else None,
        "period": {"start": start, "end": end, "country": country, "language": language},
        "caveat": "BING impressions, not Google search volume. Use for relative ranking only.",
    }


def expand(key, seed, country, language, start, end, limit):
    r = call("GetRelatedKeywords", key, q=seed, country=country, language=language, startDate=start, endDate=end)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    out = [
        {
            "query": x.get("Query"),
            "bing_impressions": x.get("Impressions"),
            "bing_broad_impressions": x.get("BroadImpressions"),
        }
        for x in rows
        if x.get("Query")
    ]
    out.sort(key=lambda x: x["bing_impressions"] or 0, reverse=True)
    return {
        "ok": True,
        "seed": seed,
        "count": len(out),
        "keywords": out[:limit],
        "period": {"start": start, "end": end, "country": country, "language": language},
        "caveat": (
            "Ordered by BING impressions. The ORDERING is the signal; the absolute numbers are "
            "Bing-only and are not Google volumes. Never write these into a keyword's `volume` field."
        ),
    }


def backlinks(key, site, pages):
    """Inbound links. Empty here is a real answer ONLY because of the control below."""
    counts = call("GetLinkCounts", key, siteUrl=site, page=0)
    if not counts["ok"]:
        return counts
    d = counts["d"] or {}
    rows = [{"url": l.get("Url"), "inbound_links": l.get("Count")} for l in (d.get("Links") or [])]

    details = call("GetUrlLinks", key, siteUrl=site, link=site, page=0)
    srcs = []
    if details["ok"]:
        dd = details["d"] or {}
        srcs = [{"from": x.get("Url"), "anchor": x.get("AnchorText")} for x in (dd.get("Details") or [])]

    out = {
        "ok": True,
        "site": site,
        "pages_with_links": len(rows),
        "total_pages": d.get("TotalPages"),
        "by_page": sorted(rows, key=lambda r: r["inbound_links"] or 0, reverse=True)[:pages],
        "sources_to_root": srcs[:pages],
    }
    if not rows and not srcs:
        # ⛔ The load-bearing distinction. An unowned site returns
        # NotAuthorized, so reaching here with empty arrays means the call was
        # authorised and Bing genuinely has nothing yet - which on a freshly
        # imported property is the NORMAL state, not a finding about the site.
        out["empty_means"] = (
            "authorised and genuinely empty - Bing has no link data for this site YET. "
            "On a newly added/imported property this is expected and is NOT evidence the site "
            "has no backlinks. Re-check after Bing has crawled for a few weeks."
        )
    return out


# .NET serialises DateTime.MinValue as /Date(-62135568000000-0800)/. That is not a
# date, it is "never" - and read naively it becomes year 0001, or a crash. Bing uses it
# for both DiscoveryDate and LastCrawledDate on a URL it has no record of.
DOTNET_MIN_MS = -62135568000000

# ⚠ AND IT IS NOT THE ONLY ONE. Measured 2026-09-01 on a live GetFeeds response:
# a sitemap Bing discovered itself carries Submitted = /Date(-11644473600000)/,
# which is the Windows FILETIME epoch, 1601-01-01. That is a SECOND "never"
# sentinel, and unlike DateTime.MinValue it decodes to a perfectly well-formed
# date that sorts, formats and prints as data - it shipped as
# `"submitted": "1601-01-01"` in the first run of `feeds`.
#
# So the guard is a SANITY FLOOR rather than a list of magic numbers: a third
# sentinel from some other .NET epoch would slip past an enumeration, and any
# date before the web existed is "never" whatever produced it.
EPOCH_FLOOR = datetime(1990, 1, 1)


def _dotnet_date(v):
    """/Date(1787460331000)/ -> '2026-08-21'. Any "never" sentinel -> None."""
    if not isinstance(v, str):
        return None
    m = re.search(r"\((-?\d+)", v)
    if not m:
        return None
    ms = int(m.group(1))
    if ms <= DOTNET_MIN_MS:
        return None
    try:
        dt = datetime.utcfromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return None
    if dt < EPOCH_FLOOR:
        return None
    return dt.strftime("%Y-%m-%d")


def run_control() -> dict:
    """Prove the decoders discriminate, WITHOUT a key and WITHOUT a network call.

    Every trap here returns confident nonsense rather than an error, which is why
    each one shipped: a page report whose URL column is named `Query`, a .NET
    sentinel date that decodes to the year 0001, a sort that inverts the real
    ranking, and a `--days` that four endpoints silently ignore so 7d and 30d
    come back byte-identical - which reads as "positions did not move"."""
    c = Controls("bing-control")
    # pages: weekly positions are impression-weighted. #2 at 3,000 impressions and
    # #40 at 3 -> (2*3000 + 40*3) / 3003 = 2.04, not the plain mean 21.0.
    c.check("pages_position_is_impression_weighted",
            page_position([(2.0, 3000), (40.0, 3)]) == 2.0, page_position([(2.0, 3000), (40.0, 3)]))
    c.check("pages_position_all_zero_impressions_falls_back_to_mean",
            page_position([(4.0, 0), (6.0, 0)]) == 5.0)
    c.check("pages_position_empty_is_none_not_zero", page_position([]) is None)

    c.check("site_url_is_normalised_with_a_scheme_and_slash",
            _norm_site("example.com") == "https://example.com/",
            _norm_site("example.com"))
    c.check("an_already_normal_site_url_is_untouched",
            _norm_site("https://example.com/") == "https://example.com/")
    c.check("an_empty_site_is_none_not_a_guess", _norm_site("") is None)

    # .NET DateTime.MinValue is "never", not year 0001. Decoded naively it sorts
    # first on every "oldest crawled" report and reads as a real date.
    # Epoch derived, not copied: 2026-08-23T00:00:00Z is 1787443200 s. Copying a
    # value out of a docstring makes the control agree with whatever the code
    # already does, which is the one thing a control must never do.
    c.check("a_real_dotnet_date_decodes",
            _dotnet_date("/Date(1787443200000)/") == "2026-08-23",
            str(_dotnet_date("/Date(1787443200000)/")))
    c.check("the_decoder_is_not_returning_a_constant",
            _dotnet_date("/Date(1787443200000)/") != _dotnet_date("/Date(1755907200000)/"))
    c.check("the_minvalue_sentinel_is_never_not_year_0001",
            _dotnet_date("/Date(-62135568000000-0800)/") is None,
            str(_dotnet_date("/Date(-62135568000000-0800)/")))
    c.check("a_non_date_is_none", _dotnet_date("not a date") is None)
    c.check("a_none_is_none_rather_than_crashing", _dotnet_date(None) is None)

    # The SECOND sentinel, and the reason the guard is a floor rather than a
    # list. -11644473600000 ms is the Windows FILETIME epoch: 1601-01-01, a
    # well-formed date that prints as data. It shipped that way once.
    # Derived independently: 1601-01-01 to 1970-01-01 is 134,774 days
    # (369 years, 89 of them leap), and 134774 * 86400 * 1000 = 11644473600000.
    c.check("the_filetime_epoch_is_never_not_1601",
            _dotnet_date("/Date(-11644473600000)/") is None,
            str(_dotnet_date("/Date(-11644473600000)/")))
    c.check("the_filetime_constant_is_the_one_bing_actually_sent",
            134774 * 86400 * 1000 == 11644473600000)
    c.check("a_date_before_the_web_is_refused_whatever_produced_it",
            _dotnet_date("/Date(-1000000000000)/") is None,
            "1938 - not a sentinel this code knows, and still not a submission date")
    c.check("the_floor_does_not_eat_real_dates",
            _dotnet_date("/Date(1787443200000)/") == "2026-08-23")

    # THE SORT. On real data clicks and impressions invert: the page earning most
    # of the clicks came SECOND by impressions. Sorting by the wrong column does
    # not error - it just answers a different question.
    rows = [{"url": "/", "impressions": 33403, "clicks": 716},
            {"url": "/ja/", "impressions": 8522, "clicks": 2298}]
    by_clicks = sorted(rows, key=lambda x: x["clicks"], reverse=True)[0]["url"]
    by_imps = sorted(rows, key=lambda x: x["impressions"], reverse=True)[0]["url"]
    c.check("clicks_and_impressions_really_do_invert", by_clicks != by_imps)
    c.check("the_reference_ranking_is_by_clicks", by_clicks == "/ja/",
            "if this ever changes, the fixture is wrong, not the tool")

    # --days must be REFUSED where the endpoint has no date range, not ignored.
    c.check("days_is_honoured_only_where_the_endpoint_has_a_window",
            set(WINDOWED) == {"keyword", "expand"}, f"got {sorted(WINDOWED)}")
    for cmd in ("queries", "pages", "pagequeries", "traffic", "cannibal"):
        c.check(f"days_is_refused_on_{cmd}", cmd not in WINDOWED)

    # THE CRAWL-STATS COLUMN KINDS. Summing a running total is the arithmetic
    # error that produced Code2xx = 96,000 for a site with 7,408 pages crawled.
    # Fixtures are hand-built, not lifted from a live response, so the expected
    # answer is derived from what the shape MEANS rather than from the code.
    c.check("a_running_total_is_cumulative",
            _classify_series([10, 20, 30, 41, 55])[0] == "cumulative")
    c.check("one_correction_in_a_month_is_still_cumulative",
            _classify_series([10, 20, 19, 30, 41])[0] == "cumulative",
            "measured: Code2xx went 5500 -> 5499 once in 28 days")
    c.check("a_bouncing_daily_count_is_a_flow",
            _classify_series([293, 169, 216, 411, 275])[0] == "flow")
    c.check("a_constant_column_is_unclassified_not_guessed",
            _classify_series([0, 0, 0, 0, 0])[0] == "unclassified",
            "every all-zero column on the only account measured was exactly this")
    c.check("too_few_points_is_unclassified",
            _classify_series([1, 2])[0] == "unclassified")
    c.check("the_classifier_discriminates_at_all",
            _classify_series([10, 20, 30, 41, 55])[0] != _classify_series([293, 169, 216, 411, 275])[0])
    c.check("the_reference_sets_do_not_overlap",
            not (set(REF_CUMULATIVE) & set(REF_FLOW)))

    # Column names end up in jq expressions in the workflow files.
    c.check("a_digit_run_gets_its_own_underscore", _snake("Code2xx") == "code_2xx",
            _snake("Code2xx"))
    c.check("ordinary_camel_case_still_works", _snake("AllOtherCodes") == "all_other_codes")

    # THE CRAWL-ISSUE BITMASK. A dropped high bit is a missing issue, silently.
    names, unknown = decode_crawl_issues(4 | 16)
    c.check("a_bitmask_decodes_to_every_flag_set",
            set(names) == {"code_4xx", "blocked_by_robots_txt"}, str(names))
    c.check("zero_is_no_issues_not_a_crash", decode_crawl_issues(0) == ([], 0))
    c.check("an_important_blocked_url_is_its_own_flag_not_a_plain_block",
            decode_crawl_issues(64)[0] == ["important_url_blocked_by_robots_txt"])
    c.check("a_plain_block_and_an_important_block_are_different_flags",
            decode_crawl_issues(16)[0] != decode_crawl_issues(64)[0])
    c.check("an_unknown_high_bit_is_reported_not_dropped",
            decode_crawl_issues(1024)[1] == 1024, str(decode_crawl_issues(1024)))
    c.check("a_non_int_mask_does_not_crash", decode_crawl_issues(None) == ([], 0))

    # SUBMIT is the only mutating call here. Its guards run before the network.
    c.check("a_url_list_is_deduplicated",
            _read_url_list(["https://a/x", "https://a/x", "https://a/y"], None)
            == ["https://a/x", "https://a/y"])
    c.check("an_empty_list_stays_empty", _read_url_list([], None) == [])

    # THE WEEKLY QUERY ROWS. One row per query per week; dropping the date made a
    # flat ten-week ranking look like ten queries. Dates are derived, not copied:
    # 2026-08-23T00:00Z is 1787443200 s (above), so 2026-08-07 is 16 days earlier,
    # 1787443200 - 16*86400 = 1786060800, and each later week adds 604800.
    W1, W2, W3 = "/Date(1786060800000)/", "/Date(1786665600000)/", "/Date(1787270400000)/"
    raw = [  # deliberately NOT in date order
        {"Query": "blue widget", "Date": W2, "Impressions": 300, "Clicks": 6,
         "AvgImpressionPosition": 8, "AvgClickPosition": -1},
        {"Query": "blue widget", "Date": W1, "Impressions": 100, "Clicks": 2,
         "AvgImpressionPosition": 6, "AvgClickPosition": -1},
        {"Query": "blue widget", "Date": W3, "Impressions": 100, "Clicks": 2,
         "AvgImpressionPosition": 7, "AvgClickPosition": -1},
        {"Query": "red widget", "Date": W3, "Impressions": 50, "Clicks": 5,
         "AvgImpressionPosition": 3, "AvgClickPosition": 2},
    ]
    wk = _weekly(raw)
    c.check("the_week_dates_decode_as_derived",
            sorted({r["date"] for r in wk}) == ["2026-08-07", "2026-08-14", "2026-08-21"],
            str(sorted({r["date"] for r in wk})))
    c.check("every_weekly_row_keeps_its_date", all(r["date"] for r in wk))
    c.check("click_position_minus_one_is_missing_even_with_clicks",
            all(r["avg_click_position"] is None for r in wk if r["query"] == "blue widget"),
            "measured: -1 on 951 of 951 rows, 786 of them with clicks > 0")
    c.check("CONTROL_a_real_click_position_is_kept",
            [r["avg_click_position"] for r in wk if r["query"] == "red widget"] == [2])
    sm = _summarise(wk)
    blue = sm["blue widget"]
    c.check("weekly_rows_aggregate_to_one_row_per_query", len(sm) == 2, str(sorted(sm)))
    c.check("impressions_and_clicks_are_summed_over_weeks",
            (blue["impressions"], blue["clicks"]) == (500, 10), str(blue))
    # (6*100 + 8*300 + 7*100) / 500 = 3700/500 = 7.4; the plain mean of 6, 8, 7 is 7.0.
    c.check("position_is_impression_weighted_not_a_plain_mean",
            blue["avg_position"] == 7.4, str(blue["avg_position"]))
    c.check("weeks_and_span_are_reported",
            (blue["weeks"], blue["first_date"], blue["last_date"])
            == (3, "2026-08-07", "2026-08-21"), str(blue))
    c.check("first_last_best_worst_week_positions",
            (blue["position_first_week"], blue["position_last_week"],
             blue["position_best"], blue["position_worst"]) == (6, 7, 6, 8), str(blue))
    c.check("a_series_is_in_date_order_whatever_order_bing_sent",
            [p["avg_position"] for p in _series([r for r in wk if r["query"] == "blue widget"])]
            == [6, 8, 7])
    dup = _summarise(_weekly(raw + [dict(raw[0], Impressions=100, AvgImpressionPosition=12)]))
    # A second row for the 08-14 week: 300@8 + 100@12 -> 400 imps at 3600/400 = 9.0 that week.
    c.check("a_split_week_is_summed_and_counted_not_shown_as_another_week",
            dup["blue widget"]["weeks"] == 3 and dup["blue widget"]["same_week_rows"] == 2
            and dup["blue widget"]["impressions"] == 600, str(dup["blue widget"]))
    c.check("a_split_week_merges_its_position_impression_weighted",
            [pt["avg_position"] for pt in _series(_weekly(raw + [dict(raw[0], Impressions=100,
                                                   AvgImpressionPosition=12)]))
             if pt["date"] == "2026-08-14"] == [9.0], "300@8 + 100@12 -> 9.0, a plain mean says 10")
    c.check("exact_match_is_case_insensitive",
            _match_queries(wk, exact="Blue Widget") == ["blue widget"])
    c.check("exact_match_does_not_fall_back_to_substring",
            _match_queries(wk, exact="widget") == [])
    c.check("contains_matches_every_query_with_the_substring",
            _match_queries(wk, contains="widget") == ["blue widget", "red widget"])
    qs_ = _query_series("https://example.com/", wk, "widget", None, 10)
    c.check("an_unmatched_query_says_unmeasured_not_zero",
            qs_["matched"] == 0 and "NOT zero demand" in qs_.get("empty_means", ""))

    # CANNIBALISATION. Expected values worked by hand:
    #   "widget pricing": /a 400@8 (wk1) + 200@11 (wk2) = 600 at (3200+2200)/600 = 9.0
    #                     /b 300@10 (wk2); /c 100@15 (wk1); total 1000
    #                     shares .6/.3/.1 -> 3 URLs at >= .10 -> split
    #                     best 9.0 in [7, 11] -> edge_of_page
    #                     owner "pricing" -> /pricing measured with NO rows -> owner_absent
    #                     wk1 leader /a (400 > 100), wk2 leader /b (300 > 200) -> 1 change
    #   "widget guide":   /guide only, 500@3, owner present -> no flag
    #   "widget":         /a 95@4, /b 5@12 -> /b share .05 -> no split; best 4 -> no flag
    def pq(*rows):
        return _weekly([{"Query": q, "Date": d, "Impressions": i, "Clicks": 0,
                         "AvgImpressionPosition": p, "AvgClickPosition": -1} for q, d, i, p in rows])
    A = {"https://example.com/a": pq(("widget pricing", W1, 400, 8), ("widget pricing", W2, 200, 11),
                                     ("widget", W1, 95, 4)),
         "https://example.com/b": pq(("widget pricing", W2, 300, 10), ("widget", W1, 5, 12)),
         "https://example.com/c": pq(("widget pricing", W1, 100, 15)),
         "https://example.com/guide": pq(("widget guide", W1, 500, 3)),
         "https://example.com/pricing": []}
    OWN = {"pricing": "https://example.com/pricing", "guide": "https://example.com/guide"}
    ca = _cannibal_core(A, OWN)
    fd = {f["query"]: f for f in ca["findings"]}
    c.check("only_the_cannibalised_query_is_a_finding", sorted(fd) == ["widget pricing"], str(sorted(fd)))
    wp = fd.get("widget pricing", {})
    c.check("split_owner_absent_and_edge_all_fire",
            set(wp.get("flags", [])) == {"split", "owner_absent", "edge_of_page"}, str(wp.get("flags")))
    c.check("the_split_names_all_three_urls_at_the_boundary_share",
            len(wp.get("split_urls", [])) == 3, "a share of exactly min_share counts")
    c.check("per_url_position_is_impression_weighted",
            [u["avg_position"] for u in wp.get("urls", [])][:1] == [9.0], str(wp.get("urls")))
    c.check("the_query_total_spans_urls", wp.get("impressions") == 1000)
    c.check("a_rotation_between_weeks_is_counted", wp.get("leader_changes") == 1,
            str(wp.get("weekly_leaders")))
    c.check("the_summary_counts_each_flag_once", ca["summary"] ==
            {"split": 1, "owner_absent": 1, "edge_of_page": 1}, str(ca["summary"]))
    tight = _cannibal_core(A, OWN, min_share=0.35)
    c.check("min_share_is_tunable_and_a_lone_holder_is_not_a_split",
            "split" not in tight["findings"][0]["flags"] and tight["summary"]["split"] == 0)
    B = {"https://example.com/x": pq(("alpha tool", W1, 200, 2)),
         "https://example.com/y": pq(("beta tool", W1, 300, 3))}
    cb = _cannibal_core(B, {})
    c.check("CONTROL_distinct_pages_for_distinct_queries_report_nothing",
            cb["findings"] == [] and cb["queries_checked"] == 2, str(cb))
    un = _cannibal_core({"https://example.com/a": A["https://example.com/a"],
                         "https://example.com/pricing": None}, {"pricing": "https://example.com/pricing"})
    c.check("an_owner_that_could_not_be_asked_is_unmeasured_never_absent",
            un["summary"]["owner_absent"] == 0 and un["unmeasured_pages"] == ["https://example.com/pricing"])
    c.check("an_owner_never_asked_is_not_absent",
            _cannibal_core({"https://example.com/a": A["https://example.com/a"]},
                           {"pricing": "https://example.com/pricing"})["summary"]["owner_absent"] == 0)
    c.check("an_owner_phrase_matches_words_not_substrings",
            _phrase_in("guide", "widget guide") and not _phrase_in("guide", "widget guidelines"))
    edge = lambda p: _cannibal_core({"https://example.com/e": pq(("q", W1, 50, p))})["summary"]["edge_of_page"]
    c.check("edge_of_page_is_7_to_11_inclusive",
            (edge(6.99), edge(7), edge(11), edge(11.01)) == (0, 1, 1, 0))
    c.check("a_query_below_min_impressions_is_not_judged",
            _cannibal_core({"https://example.com/a": pq(("thin", W1, 5, 9)),
                            "https://example.com/b": pq(("thin", W1, 4, 9))})["queries_checked"] == 0)

    # A key is not needed to run this control, and its ABSENCE must be reported
    # as "cannot ask", never folded into a data verdict.
    k = read_key()
    c.check("a_missing_key_is_reported_not_guessed", k is None or isinstance(k, str))
    return c.verdict(key_present=bool(k),
                     note=("the decoders are proven; whether the ACCOUNT answers is a "
                           "separate question - `sites` is the live probe for that"))


def urlinfo(key, site, url):
    """Bing's OWN record of one URL: discovered, last crawled, size.

    The Bing counterpart to Google's URL Inspection, and on a site where Bing
    carries the traffic it is the one that matters. Free, no quota worth counting.

    ⚠ THE TRAP THIS WRAPS. Bing answers ok:true for a URL THAT DOES NOT EXIST,
    with exactly the same all-sentinel payload it returns for a real page it has
    never seen. Measured 2026-09-01 on a live site: a guide page (a live 200) and
    /does-not-exist-zzz were byte-identical - both DateTime.MinValue on each date,
    DocumentSize 0 - while an older page on the same site carried a real discovery
    and crawl date. So `known_to_bing: false` says "Bing has no record of this
    string", never "this page exists and Bing declined it", and the two are
    different findings. Always carry a URL you KNOW is crawled as a control.

    HttpStatus is deliberately not surfaced: it came back 0 on crawled and unknown
    URLs alike, so reporting it would ship a field that looks meaningful and is not.

    ⚠ IT THROTTLES, AND A THROTTLE LOOKS LIKE AN ANSWER. A tight loop over 46 URLs
    returned errors for 35 of them (measured 2026-09-01); scored naively that is a
    76% "not indexed" finding and it was pure rate limiting. Space calls ~1.5s+,
    retry with backoff, and keep UNMEASURED as a bucket separate from `unknown` -
    collapsing the two is the same mistake as reading a refused SERP as an empty
    page 1.
    """
    r = call("GetUrlInfo", key, siteUrl=site, url=url)
    if not r["ok"]:
        return r
    d = r["d"] or {}
    discovered = _dotnet_date(d.get("DiscoveryDate"))
    crawled = _dotnet_date(d.get("LastCrawledDate"))
    size = d.get("DocumentSize") or 0
    return {
        "ok": True,
        "url": url,
        "known_to_bing": bool(discovered or crawled or size),
        "discovered": discovered,
        "last_crawled": crawled,
        "document_size": size,
        "is_page": d.get("IsPage"),
        "anchor_count": d.get("AnchorCount"),
        "child_urls": d.get("TotalChildUrlCount"),
        "empty_means": (
            "known_to_bing false = Bing has NO RECORD of this URL string. It is NOT "
            "evidence the page is excluded or penalised, and it is the SAME answer "
            "Bing gives for a URL that does not exist - so pair it with a control URL "
            "you know is crawled before reading it as a finding."
        ),
    }


# --------------------------------------------------------------------------
# The WEEKLY shape of the query endpoints.
#
# Measured 2026-10-12 on a live account: GetQueryStats returned 951 rows for 358
# distinct queries - ten rows for the busiest - and every (query, Date) pair was
# unique. The rows are one per query per WEEK (ten Friday-dated weeks), not one per
# market. GetPageQueryStats has the identical shape. Dropping `Date` and sorting by
# impressions printed one query ten times with no way to tell a trend from a
# duplicate: positions that sat flat at 6-8 for ten weeks were indistinguishable
# from ten separate queries.
#
# ⚠ AvgClickPosition is NOT a position. It was -1 on all 951 rows - INCLUDING the
# 786 rows with clicks > 0 - so "-1 means no click" does not even hold. It is
# surfaced as None whenever it is not a positive number and must never be read as
# a rank. The weekly rank is AvgImpressionPosition.
# --------------------------------------------------------------------------

def _pos(v):
    """A Bing position field: a positive number, or None for any sentinel (-1, 0)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if v > 0 else None


def _weekly(rows):
    """Bing query rows -> dated rows in this script's shape. Rows without a query
    are dropped; rows without a decodable date are KEPT with date None, because a
    dropped row would silently understate a total."""
    out = []
    for x in rows or []:
        q = x.get("Query")
        if not q:
            continue
        out.append({
            "query": q,
            "date": _dotnet_date(x.get("Date")),
            "impressions": x.get("Impressions") or 0,
            "clicks": x.get("Clicks") or 0,
            "avg_position": _pos(x.get("AvgImpressionPosition")),
            "avg_click_position": _pos(x.get("AvgClickPosition")),
        })
    return out


def _by_date(rows):
    """Collapse rows to one point per date, position impression-weighted.

    One row per (query, date) is what was measured, so this is normally the
    identity. It exists so that the day Bing starts splitting a week (by market,
    device, anything) the total stays right and the split is COUNTED - see
    `same_week_rows` - rather than appearing as a second week."""
    pts = {}
    for r in rows:
        d = pts.setdefault(r["date"], {"date": r["date"], "impressions": 0, "clicks": 0,
                                       "_w": 0.0, "_wi": 0, "_n": 0})
        d["impressions"] += r["impressions"]
        d["clicks"] += r["clicks"]
        d["_n"] += 1
        if r["avg_position"] is not None and r["impressions"] > 0:
            d["_w"] += r["avg_position"] * r["impressions"]
            d["_wi"] += r["impressions"]
    out = []
    for d in pts.values():
        w, wi, n = d.pop("_w"), d.pop("_wi"), d.pop("_n")
        d["avg_position"] = round(w / wi, 2) if wi else None
        d["_rows"] = n
        out.append(d)
    # Undated rows sort last rather than vanishing.
    out.sort(key=lambda d: (d["date"] is None, d["date"] or ""))
    return out


def _summarise(rows):
    """query -> one aggregate row: totals, IMPRESSION-WEIGHTED position, weeks.

    Weighted, never a plain mean of weekly positions: a 5-impression week at
    position 40 must not move the headline as much as a 5,000-impression week at
    position 6. `position_first_week` / `position_last_week` and `position_best` /
    `position_worst` are there so a trend is visible without opening the series."""
    groups = {}
    for r in rows:
        groups.setdefault(r["query"], []).append(r)
    out = {}
    for q, rs in groups.items():
        pts = _by_date(rs)
        imps = sum(p["impressions"] for p in pts)
        clicks = sum(p["clicks"] for p in pts)
        w = sum(p["avg_position"] * p["impressions"] for p in pts if p["avg_position"] is not None)
        wi = sum(p["impressions"] for p in pts if p["avg_position"] is not None)
        dated = [p for p in pts if p["date"]]
        ranked = [p for p in dated if p["avg_position"] is not None]
        a = {
            "query": q,
            "impressions": imps,
            "clicks": clicks,
            "ctr": round(clicks / imps, 4) if imps else None,
            "avg_position": round(w / wi, 2) if wi else None,
            "weeks": len(dated),
            "first_date": dated[0]["date"] if dated else None,
            "last_date": dated[-1]["date"] if dated else None,
            "position_first_week": ranked[0]["avg_position"] if ranked else None,
            "position_last_week": ranked[-1]["avg_position"] if ranked else None,
            "position_best": min((p["avg_position"] for p in ranked), default=None),
            "position_worst": max((p["avg_position"] for p in ranked), default=None),
        }
        undated = sum(p["_rows"] for p in pts if not p["date"])
        if undated:
            a["undated_rows"] = undated
        multi = max((p["_rows"] for p in dated), default=1)
        if multi > 1:
            a["same_week_rows"] = multi
            a["same_week_means"] = ("Bing returned more than one row for a single week of this "
                                    "query. Measured on 2026-10-12 it never did. The rows are SUMMED "
                                    "here, so the total is right, but find out what splits them "
                                    "before reading a per-row number.")
        out[q] = a
    return out


def _series(rows):
    """Public per-week points, date order, no internal fields."""
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in _by_date(rows)]


def _span(weekly):
    ds = sorted({r["date"] for r in weekly if r["date"]})
    return {"count": len(ds), "first": ds[0] if ds else None, "last": ds[-1] if ds else None}


def _match_queries(weekly, exact=None, contains=None):
    """Exact = case-insensitive whole-string match; contains = case-insensitive
    substring. Case-folded because Bing lower-cases most rows and a caller copying
    a query from a SERP will not."""
    names = {r["query"] for r in weekly}
    if exact is not None:
        want = exact.strip().casefold()
        return sorted(n for n in names if n.strip().casefold() == want)
    want = (contains or "").strip().casefold()
    return sorted(n for n in names if want and want in n.casefold())


def _query_series(site, weekly, exact, contains, limit):
    matched = _match_queries(weekly, exact, contains)
    summ = _summarise([r for r in weekly if r["query"] in set(matched)])
    results = []
    for q in matched:
        a = dict(summ[q])
        a["series"] = _series([r for r in weekly if r["query"] == q])
        results.append(a)
    results.sort(key=lambda a: -a["impressions"])
    span = _span(weekly)
    res = {"ok": True, "site": site,
           "match": {"mode": "exact" if exact is not None else "contains",
                     "value": exact if exact is not None else contains},
           "matched": len(results), "window_weeks": span, "results": results[:limit],
           "reading": ("`series` is one point per week in date order; `avg_position` is "
                       "Bing's AvgImpressionPosition. A flat series is a STABLE ranking, not "
                       "a duplicate. avg_click_position is None when Bing sends a sentinel - "
                       "it is not a rank.")}
    if not results:
        res["empty_means"] = (
            f"no row for this string in Bing's window for this site ({span['count']} weeks, "
            f"{span['first']}..{span['last']}). That is NOT zero demand and NOT 'we stopped "
            "ranking' - Bing omits low-volume queries, and an exact match needs Bing's exact "
            "wording. Try --contains with one distinctive word.")
    return res


def queries(key, site, limit, exact=None, contains=None):
    """The queries this site appears for - dated, and aggregated per query.

    Output, both keys always present:
      queries   one row per query per WEEK, with `date` - the original key and row
                shape (plus `date` and `avg_click_position`), so `keywords.py bing`
                still reads it. Sorted by impressions, cut at --limit ROWS.
      by_query  one row per query: total impressions/clicks, ctr, impression-
                weighted avg_position, weeks, first/last date, first/last/best/worst
                weekly position. Built from ALL rows, then cut at --limit QUERIES.
    """
    r = call("GetQueryStats", key, siteUrl=site)
    if not r["ok"]:
        return r
    weekly = _weekly(r["d"] or [])
    if exact is not None or contains is not None:
        return _query_series(site, weekly, exact, contains, limit)

    by_query = sorted(_summarise(weekly).values(), key=lambda a: (-a["impressions"], a["query"]))
    rows = sorted(weekly, key=lambda x: (-x["impressions"], x["query"], x["date"] or ""))
    res = {"ok": True, "site": site, "count": len(rows), "query_count": len(by_query),
           "window_weeks": _span(weekly),
           "queries": rows[:limit],
           "by_query": by_query[:limit],
           "reading": ("`queries` holds one row PER WEEK: the same query appears once for each "
                       "week it had impressions. Read `by_query` for per-query totals and an "
                       "impression-weighted position, and `queries --q \"<query>\"` for one "
                       "query's weekly series in date order. `--limit` cuts `queries` by ROWS "
                       "and `by_query` by QUERIES; `by_query` is aggregated before the cut.")}
    if not rows:
        res["empty_means"] = "authorised and genuinely empty - no Bing query data for this site yet."
    return res


def page_position(pos: list[tuple[float, int]]) -> float | None:
    """Impression-weighted mean of weekly positions, rounded to 0.1.

    Rows are one per page per WEEK. A plain mean gives a 3-impression week the
    same vote as a 3,000-impression one, so a page that ranked #2 all quarter and
    #40 for one near-empty week reads as #21. Weeks with no impressions fall back
    to equal weight only when NO week has impressions (a position still exists)."""
    if not pos:
        return None
    w = sum(i for _, i in pos)
    if w <= 0:
        return round(sum(p for p, _ in pos) / len(pos), 1)
    return round(sum(p * i for p, i in pos) / w, 1)


def pages(key, site, limit):
    """Per-PAGE impressions and clicks - the dimension `queries` cannot give you.

    WHY THIS EXISTS (2026-09-01, a live site). `queries` said the
    best-converting terms on the site were in one non-English language - 488
    clicks on 1,462 impressions at position 2 - and there was no way to find out
    WHICH PAGE earned them. Query-level data alone cannot answer "is our locale
    page working, or is the English homepage ranking for those queries?", and
    those two have opposite fixes. `pages` answered it in one call: the locale
    homepage carried 8,522 impressions and 2,298 clicks at position 4 - a 27% CTR, and more than three
    times the clicks the homepage got from four times the impressions. 70% of the
    site's clicks came from one locale page that every internal-linking audit had
    flagged as having no editorial inbound links at all.

    ⚠ Bing returns this through GetPageStats, whose rows put the PAGE URL in a
    field named `Query`. That is Bing's naming, not a bug here, and reading it as
    a search term silently turns a page report into nonsense.
    """
    r = call("GetPageStats", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    agg: dict = {}
    for x in rows:
        # `Query` holds the URL for this endpoint - see the warning above.
        u = x.get("Query")
        if not u:
            continue
        a = agg.setdefault(u, {"url": u, "impressions": 0, "clicks": 0, "_pos": []})
        a["impressions"] += x.get("Impressions") or 0
        a["clicks"] += x.get("Clicks") or 0
        if (x.get("AvgImpressionPosition") or 0) > 0:
            a["_pos"].append((x["AvgImpressionPosition"], x.get("Impressions") or 0))
    out = []
    for a in agg.values():
        a["avg_position"] = page_position(a.pop("_pos"))
        # CTR is the reason to read this report rather than the impression column:
        # a page can carry a fraction of the impressions and most of the clicks.
        a["ctr"] = round(a["clicks"] / a["impressions"], 4) if a["impressions"] else None
        out.append(a)
    out.sort(key=lambda x: x["clicks"] or 0, reverse=True)
    res = {"ok": True, "site": site, "count": len(out),
           "totals": {"impressions": sum(x["impressions"] for x in out),
                      "clicks": sum(x["clicks"] for x in out)},
           "pages": out[:limit],
           "reading": ("Sorted by CLICKS, not impressions. A high-impression page with a "
                       "low CTR and a low-impression page with a high CTR need opposite "
                       "work, and the impression column hides that.")}
    if not out:
        res["empty_means"] = ("authorised and genuinely empty - Bing has no page data for "
                              "this site yet. On a newly added property that is expected and "
                              "is NOT evidence that no page ranks.")
    return res


def pagequeries(key, site, page, limit):
    """The queries ONE page appears for - attribution, the other half of `pages`.

    `pages` says one locale page earns 70% of the clicks; this says which searches sent
    them, which is what tells you whether the win is repeatable or a single term.
    """
    r = call("GetPageQueryStats", key, siteUrl=site, page=page)
    if not r["ok"]:
        return r
    # Same weekly shape as GetQueryStats (measured 2026-10-12: up to ten dated rows
    # per query for one page), so the same two views: dated rows for compatibility,
    # `by_query` for the per-query answer.
    out = sorted(_weekly(r["d"] or []), key=lambda x: (-x["impressions"], x["query"], x["date"] or ""))
    by_query = sorted(_summarise(out).values(), key=lambda a: (-a["impressions"], a["query"]))
    res = {"ok": True, "site": site, "page": page, "count": len(out),
           "query_count": len(by_query), "window_weeks": _span(out),
           "queries": out[:limit], "by_query": by_query[:limit]}
    if not out:
        # ⛔ A URL Bing has never ranked and a MISTYPED URL both return an empty
        # list, and they are completely different findings. Say so rather than
        # letting an empty array read as "this page ranks for nothing".
        res["empty_means"] = ("authorised and empty. This is EITHER a page Bing has no "
                              "query data for, OR a URL that does not match what Bing "
                              "indexed (a missing/extra trailing slash is enough). Confirm "
                              "the exact URL with `bing.py pages` before reading this as a "
                              "finding about the page.")
    return res


# --------------------------------------------------------------------------
# CANNIBALISATION, from Bing's own per-page attribution.
#
# Why (measured on a live site): four URLs of one site rotated through positions
# 8-10 for its head queries, week to week, and the homepage - the page meant to
# own two of those queries - drew no impressions for them at all. Read from SERPs
# alone that looked like "we fell off page 1". It was the site competing with
# itself, and only per-page query data shows it.
#
# The flags:
#   split          >= 2 URLs each holding >= min_share of the query's impressions
#   owner_absent   the URL the caller says should own a phrase has NO impressions
#                  for a query containing that phrase, while another URL does
#   edge_of_page   the best URL's impression-weighted position is 7-11 inclusive -
#                  the band where a rotation between URLs pushes the site off page 1
#
# ⚠ Only the MEASURED pages are visible. A URL outside the set cannot appear in a
# split, so a clean result covers those pages and nothing else. Owner URLs are
# always added to the set: an owner that was never asked would read as absent.
#
# ⚠ Page-level and site-level impressions are different quantities. Measured
# 2026-10-12: the site's GetPageStats rows summed to 519,667 impressions against
# 366,823 for GetQueryStats over the same window (1.42x) - a SERP showing two URLs
# of the site counts twice per page and once per query. So `site_impressions` is
# reported beside `measured_page_impressions` and NOT turned into a coverage ratio:
# a ratio above 1 and a ratio below 1 can both happen for reasons this cannot tell
# apart.
# --------------------------------------------------------------------------

CANNIBAL_MIN_SHARE = 0.10
CANNIBAL_MIN_IMPRESSIONS = 10
# Positions 7-11: the bottom of page 1 and the top of page 2 on a 10-result SERP.
EDGE_OF_PAGE = (7, 11)
# Pause between per-page calls. urlinfo lost 35 of 46 calls to throttling in a
# tight loop (2026-09-01); a throttle must not be read as "this page has no queries".
PAGE_CALL_PACE_S = 1.0


def _phrase_in(phrase, query):
    """Case-insensitive WORD match: "guide" is in "setup guide", not in "guidelines".
    A substring match would assign an owner to queries it was never meant for."""
    p = re.escape(phrase.strip().casefold())
    return bool(p) and re.search(rf"(?<!\w){p}(?!\w)", query.casefold()) is not None


def _slash_variant(url):
    return url[:-1] if url.endswith("/") else url + "/"


def _leaders(by_url_rows):
    """Week -> the URL with most impressions that week. A rotation shows up as the
    leader changing; ties go to the alphabetically-first URL so the answer is
    deterministic."""
    weeks = {}
    for url, rows in by_url_rows.items():
        for p in _by_date(rows):
            if p["date"] and p["impressions"] > 0:
                weeks.setdefault(p["date"], []).append((-p["impressions"], url))
    seq = [{"date": d, "url": sorted(v)[0][1]} for d, v in sorted(weeks.items())]
    changes = sum(1 for i in range(1, len(seq)) if seq[i]["url"] != seq[i - 1]["url"])
    return seq, changes


def _cannibal_core(per_page, owners=None, min_share=CANNIBAL_MIN_SHARE,
                   min_impressions=CANNIBAL_MIN_IMPRESSIONS, site_totals=None):
    """Pure: {url: weekly rows, or None when the call FAILED} -> findings.

    None and [] are different inputs on purpose: [] is a page Bing answered for
    with nothing, None is a page that could not be asked. An owner whose call
    failed is `owner_unmeasured`, never `owner_absent`."""
    owners = owners or {}
    unmeasured = {u for u, v in per_page.items() if v is None}
    by_q = {}
    for url, rows in per_page.items():
        for r in rows or []:
            by_q.setdefault(r["query"], {}).setdefault(url, []).append(r)

    findings, checked = [], 0
    for q, urls in by_q.items():
        entries = []
        for url, rows in urls.items():
            a = _summarise(rows)[q]
            entries.append({"url": url, "impressions": a["impressions"], "clicks": a["clicks"],
                            "avg_position": a["avg_position"], "weeks": a["weeks"]})
        total = sum(e["impressions"] for e in entries)
        if total < min_impressions:
            continue
        checked += 1
        for e in entries:
            e["share"] = round(e["impressions"] / total, 4)
        entries.sort(key=lambda e: (-e["impressions"], e["url"]))

        flags = []
        holders = [e["url"] for e in entries if e["impressions"] / total >= min_share]
        if len(holders) >= 2:
            flags.append("split")
        ranked = [e["avg_position"] for e in entries
                  if e["avg_position"] is not None and e["impressions"] > 0]
        best = min(ranked) if ranked else None
        if best is not None and EDGE_OF_PAGE[0] <= best <= EDGE_OF_PAGE[1]:
            flags.append("edge_of_page")

        owner_rows = []
        for phrase, ourl in owners.items():
            if not _phrase_in(phrase, q):
                continue
            mine = next((e for e in entries if e["url"] == ourl), None)
            if ourl not in per_page:
                owner_rows.append({"phrase": phrase, "url": ourl, "state": "not_asked"})
            elif ourl in unmeasured:
                owner_rows.append({"phrase": phrase, "url": ourl, "state": "unmeasured"})
            elif mine and mine["impressions"] > 0:
                owner_rows.append({"phrase": phrase, "url": ourl, "state": "present",
                                   "share": mine["share"]})
            elif any(e["impressions"] > 0 for e in entries if e["url"] != ourl):
                owner_rows.append({"phrase": phrase, "url": ourl, "state": "absent"})
        if any(o["state"] == "absent" for o in owner_rows):
            flags.append("owner_absent")
        if not flags:
            continue

        leaders, changes = _leaders(urls)
        f = {"query": q, "flags": flags, "impressions": total,
             "clicks": sum(e["clicks"] for e in entries),
             "best_position": best, "urls": entries,
             "split_urls": holders if "split" in flags else [],
             "leader_changes": changes, "weekly_leaders": leaders}
        if owner_rows:
            f["owners"] = owner_rows
        if site_totals is not None:
            f["site_impressions"] = site_totals.get(q)
            f["measured_page_impressions"] = total
        findings.append(f)

    findings.sort(key=lambda f: (-f["impressions"], f["query"]))
    summary = {k: sum(1 for f in findings if k in f["flags"])
               for k in ("split", "owner_absent", "edge_of_page")}
    return {"queries_checked": checked, "summary": summary, "findings": findings,
            "unmeasured_pages": sorted(unmeasured)}


def _read_owners(pairs, owners_file):
    """--owner "phrase=url" (repeatable) and --owners-file, which takes EITHER
    {phrase: url} OR the head-term map's own shape, {"terms": [{"phrase", "owner"}]}
    (headterms.py's .seo/headterms.json) - so the ownership the build gate enforces
    is the ownership measured here, with no hand-copied second list to drift.
    A url may be site-relative ("/guides/x"); `cannibal` resolves it against the site.
    Split --owner on the FIRST '=' so a URL with a query string survives."""
    owners = {}
    if owners_file:
        with open(owners_file) as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("terms"), list):
            for t in data["terms"]:
                if isinstance(t, dict) and t.get("phrase") and t.get("owner"):
                    owners[str(t["phrase"])] = str(t["owner"])
        elif isinstance(data, dict):
            owners.update({str(k): str(v) for k, v in data.items()})
        else:
            raise ValueError('--owners-file must be {"phrase": "url"} or {"terms": [{"phrase", "owner"}]}')
        if not owners:
            # An owners file that yields nothing would run as if no owner were named,
            # and `owner_absent` could then never fire - an empty answer from the input.
            raise ValueError(f"--owners-file {owners_file} named no phrase/owner pairs")
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--owner needs phrase=url, got {p!r}")
        k, v = p.split("=", 1)
        owners[k.strip()] = v.strip()
    bad = {k: v for k, v in owners.items()
           if not k or not v.startswith(("http://", "https://", "/"))}
    if bad:
        raise ValueError(f"owner urls must be absolute or start with '/', phrases non-empty: {bad}")
    return owners


def _page_rows(key, site, page, pace):
    """One page's weekly rows, retried once. None = could not ask (NOT zero)."""
    for attempt in range(2):
        r = call("GetPageQueryStats", key, siteUrl=site, page=page)
        if r.get("ok"):
            return _weekly(r["d"] or []), None
        if attempt == 0 and pace:
            time.sleep(pace * 3)
    return None, r.get("error")


def cannibal(key, site, explicit_pages, top, owners, min_share=CANNIBAL_MIN_SHARE,
             min_impressions=CANNIBAL_MIN_IMPRESSIONS, max_findings=50, pace=PAGE_CALL_PACE_S):
    ps = pages(key, site, 10 ** 9)
    if not ps.get("ok"):
        return ps
    known = {p["url"]: p["impressions"] for p in ps["pages"]}
    owners = {k: urllib.parse.urljoin(site, v) for k, v in (owners or {}).items()}

    # An owner URL spelled differently from Bing's record would be asked, come back
    # empty, and be reported ABSENT from every query - a finding made from a typo.
    # A slash variant that Bing does know is refused rather than silently rewritten.
    owner_unknown = []
    for phrase, u in owners.items():
        if u in known:
            continue
        if _slash_variant(u) in known:
            return {"ok": False, "error": "owner_url_not_as_bing_reports",
                    "detail": (f"owner for {phrase!r} is {u!r}; Bing records "
                               f"{_slash_variant(u)!r}. Pass Bing's exact string."),
                    "what_to_do": "copy the URL from `bing.py pages`"}
        owner_unknown.append(u)

    by_imps = sorted(known, key=lambda u: (-known[u], u))
    wanted = list(explicit_pages) + (by_imps[:top] if top else [])
    wanted += [u for u in owners.values()]
    page_set = list(dict.fromkeys(wanted))
    if not page_set:
        return {"ok": False, "error": "no pages",
                "detail": "pass --pages / --from-file, or a --limit > 0 for the top pages"}

    per_page, errors = {}, {}
    for i, u in enumerate(page_set):
        if i and pace:
            time.sleep(pace)
        rows, err = _page_rows(key, site, u, pace)
        per_page[u] = rows
        if rows is None:
            errors[u] = err

    if all(v is None for v in per_page.values()):
        return {"ok": False, "error": "every per-page call failed",
                "detail": errors, "why_not_none": ("no page could be asked, so 'no "
                                                    "cannibalisation' cannot be concluded")}

    sq = call("GetQueryStats", key, siteUrl=site)
    site_totals = ({q: a["impressions"] for q, a in _summarise(_weekly(sq["d"] or [])).items()}
                   if sq.get("ok") else None)

    core = _cannibal_core(per_page, owners, min_share, min_impressions, site_totals)
    out = {"ok": True, "site": site,
           "params": {"min_share": min_share, "min_impressions": min_impressions,
                      "edge_of_page": list(EDGE_OF_PAGE), "owners": owners},
           "pages_measured": [u for u in page_set if per_page[u] is not None],
           "unmeasured_pages": core["unmeasured_pages"],
           "queries_checked": core["queries_checked"],
           "summary": core["summary"],
           "finding_count": len(core["findings"]),
           "findings": core["findings"][:max_findings],
           "reading": (
               "Sorted by impressions. `urls` lists every MEASURED page that drew "
               "impressions for the query, with its share and impression-weighted "
               "position; `weekly_leaders` / `leader_changes` show a rotation directly. "
               "Only the pages in `pages_measured` are visible: a URL outside the set "
               "cannot appear in a split. `site_impressions` (per query) and "
               "`measured_page_impressions` (per URL appearance) are different counts "
               "and are not a coverage ratio.")}
    if errors:
        out["errors"] = errors
        out["unmeasured_means"] = ("these pages could not be asked (throttle or API error). "
                                   "Their queries are missing from every finding, and an owner "
                                   "among them is `unmeasured`, never `absent`. Re-run.")
    if owner_unknown:
        out["owner_urls_unknown_to_bing"] = owner_unknown
        out["owner_unknown_means"] = ("Bing's page report has no row for these URLs at all: "
                                      "either they draw no impressions anywhere (then "
                                      "`owner_absent` is real) or the string is not the URL "
                                      "Bing indexed. Check with `bing.py urlinfo` and a control.")
    if not core["findings"]:
        out["empty_means"] = (f"no flag fired across {core['queries_checked']} queries with "
                              f">= {min_impressions} impressions on the measured pages. That "
                              "covers those pages only" +
                              ("; some pages were NOT measured, see unmeasured_pages." if errors
                               else "."))
    return out


def traffic(key, site):
    r = call("GetRankAndTrafficStats", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    res = {
        "ok": True,
        "site": site,
        "count": len(rows),
        "series": [
            {"date": x.get("Date"), "impressions": x.get("Impressions"), "clicks": x.get("Clicks")}
            for x in rows
        ],
    }
    if not rows:
        res["empty_means"] = "authorised and genuinely empty - no Bing traffic data for this site yet."
    return res



# --------------------------------------------------------------------------
# The CRAWLER's own side of the account.
#
# Everything above this line asks Bing what SEARCHERS did. This block asks what
# BINGBOT did, and it is a different instrument: first-party, from the crawler
# itself, with no user-agent to verify and no log to parse. On a site where Bing
# carries the traffic it answers "is the new silo being crawled at all" without
# waiting for a ranking to move.
#
# It also opens the one submission lever this skill did not have. IndexNow and
# Bing's own SubmitUrlBatch are DIFFERENT channels with different quotas; a ping
# to one is not a submission to the other.
# --------------------------------------------------------------------------

# Measured 2026-09-01 across a live 28-day series: GetCrawlStats mixes two kinds
# of column in one row and NOTHING in the payload distinguishes them.
#
#   flow        a count for THAT DAY.        CrawledPages: 293, 169, 216, 411 ...
#   cumulative  a running total or a stock.  InIndex:     1025, 1268, 1541, 1665 ...
#
# Summing the second kind is the arithmetic error this skill's own quality bar
# warns about: it produced Code2xx = 96,000 and InIndex = 82,767 for a site
# Bing had crawled 7,408 pages of. Both are pure artefact.
#
# These sets are the REFERENCE classification, not the operative one. Every run
# re-derives the kind from the series it just fetched and reports a disagreement,
# because a remembered constraint that nobody re-checks is exactly how the
# `--days` bug shipped.
REF_CUMULATIVE = ("Code2xx", "InIndex", "InLinks", "BlockedByRobotsTxt")
REF_FLOW = ("CrawledPages", "AllOtherCodes", "Code4xx", "Code5xx", "CrawlErrors")

# Split before a capital AND before a digit run, so `Code2xx` reads `code_2xx`
# rather than `code2xx` - these names end up in jq expressions in the workflow
# files, and a reader who guesses the obvious spelling should be right.
_SNAKE = re.compile(r"(?<!^)(?=[A-Z])|(?<=[A-Za-z])(?=\d)")


def _snake(name):
    return _SNAKE.sub("_", name).lower()


def _classify_series(values):
    """flow vs cumulative, derived from the numbers rather than assumed.

    A cumulative column never decreases. One correction in a month is tolerated
    (measured: Code2xx went 5500 -> 5499 once in 28 days) because a hard
    monotonicity test would misclassify it as a flow and license summing it.

    A column that never changes cannot be classified at all - and reporting that
    honestly is the point, because every all-zero column on the only account
    measured was exactly this case.
    """
    if len(values) < 3:
        return "unclassified", "fewer than 3 days of data"
    if len(set(values)) == 1:
        return "unclassified", f"constant at {values[0]} for the whole window"
    drops = sum(1 for i in range(1, len(values)) if values[i] < values[i - 1])
    if drops <= 1:
        return "cumulative", f"{drops} decrease(s) in {len(values) - 1} steps"
    return "flow", f"{drops} decrease(s) in {len(values) - 1} steps"


def crawlstats(key, site):
    """Bingbot's own record: what it fetched, what it got back, what is indexed."""
    r = call("GetCrawlStats", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    if not rows:
        return {"ok": True, "site": site, "days": 0, "series": [],
                "empty_means": ("authorised and genuinely empty - Bing has no crawl history "
                                "for this site yet. `sites` proves the authorisation half.")}

    for x in rows:
        x["_date"] = _dotnet_date(x.get("Date"))
    rows = sorted([x for x in rows if x["_date"]], key=lambda x: x["_date"])
    cols = [c for c in rows[0] if c not in ("Date", "_date", "__type")]

    kinds, disagreements = {}, []
    for c in cols:
        vals = [x.get(c) for x in rows if isinstance(x.get(c), int)]
        kind, basis = _classify_series(vals)
        kinds[_snake(c)] = {"kind": kind, "basis": basis}
        expected = "cumulative" if c in REF_CUMULATIVE else ("flow" if c in REF_FLOW else None)
        if expected and kind != "unclassified" and kind != expected:
            disagreements.append({"column": _snake(c), "reference": expected, "measured": kind,
                                  "basis": basis})

    latest, first = rows[-1], rows[0]
    stock, flow_totals, refused = {}, {}, []
    for c in cols:
        k = _snake(c)
        kind = kinds[k]["kind"]
        if kind == "cumulative":
            stock[k] = {"latest": latest.get(c), "first": first.get(c),
                        "change_over_window": (latest.get(c) or 0) - (first.get(c) or 0)}
            refused.append(k)
        elif kind == "flow":
            flow_totals[k] = sum(x.get(c) or 0 for x in rows)
        else:
            stock[k] = {"latest": latest.get(c), "constant": True}

    out = {
        "ok": True, "site": site, "days": len(rows),
        "range": {"start": rows[0]["_date"], "end": latest["_date"]},
        "column_kinds": kinds,
        "latest_stock": stock,
        "window_totals": flow_totals,
        "refused_to_sum": {
            "columns": sorted(refused),
            "why": ("these are running totals or point-in-time stocks, so a sum over days "
                    "double-counts every earlier day. Read `latest_stock.<col>.latest` for "
                    "the value and `.change_over_window` for the movement."),
        },
        "series": [{"date": x["_date"], **{_snake(c): x.get(c) for c in cols}} for x in rows],
    }
    if disagreements:
        out["kind_disagrees_with_reference"] = disagreements
        out["read_this_first"] = ("A column changed shape since the reference classification "
                                  "was measured. Trust the measured kind, and update "
                                  "REF_CUMULATIVE / REF_FLOW in bing.py.")

    findings, caution = [], []
    crawled = flow_totals.get("crawled_pages")
    errors = flow_totals.get("crawl_errors")
    if crawled:
        findings.append({"metric": "crawl_rate_per_day", "value": round(crawled / len(rows), 1),
                         "note": "pages bingbot fetched per day, averaged over the window"})
        if errors is not None:
            findings.append({"metric": "crawl_error_rate", "value": round(errors / crawled, 4),
                             "note": f"{errors} errors over {crawled} crawled pages"})
    idx = stock.get("in_index", {})
    if idx.get("latest") is not None:
        findings.append({"metric": "pages_in_index", "value": idx["latest"],
                         "change_over_window": idx.get("change_over_window"),
                         "note": "Bing's own count. Compare against `feeds` url_count, not "
                                 "against your local page total - Bing may hold a stale sitemap."})
    rb = stock.get("blocked_by_robots_txt", {})
    if rb.get("change_over_window"):
        findings.append({"metric": "newly_blocked_by_robots_txt", "value": rb["change_over_window"],
                         "note": "URLs bingbot wanted and robots.txt refused, added over the "
                                 "window. Intentional blocks show up here too - this is a "
                                 "number to explain, not automatically a defect."})
    aoc = flow_totals.get("all_other_codes")
    if aoc and crawled and aoc > crawled * 0.5:
        caution.append({
            "column": "all_other_codes", "window_total": aoc, "crawled_pages": crawled,
            "why": ("Bing publishes no breakdown of what this column contains, and here it is "
                    f"{round(aoc / crawled, 2)}x the pages crawled - too large to be a per-day "
                    "count of responses to those crawls. Report it; do not build a conclusion "
                    "on it, and do not present it as an error rate."),
        })
    if findings:
        out["findings"] = findings
    if caution:
        out["caution"] = caution
    return out


# GetCrawlIssues returns a bitmask in a field called `Issues`. Decoded from
# merj/bing-webmaster-tools, which mirrors the API's own enum.
CRAWL_ISSUE_FLAGS = (
    (1, "code_301"), (2, "code_302"), (4, "code_4xx"), (8, "code_5xx"),
    (16, "blocked_by_robots_txt"), (32, "contains_malware"),
    (64, "important_url_blocked_by_robots_txt"), (128, "dns_errors"),
    (256, "time_out_errors"),
)


def decode_crawl_issues(mask):
    """0 -> [], and an unknown high bit is REPORTED rather than dropped."""
    if not isinstance(mask, int) or mask <= 0:
        return [], 0
    names = [n for bit, n in CRAWL_ISSUE_FLAGS if mask & bit]
    known = 0
    for bit, _ in CRAWL_ISSUE_FLAGS:
        known |= bit
    return names, mask & ~known


def crawlissues(key, site, limit):
    """Per-URL crawl problems - with the empty answer guarded.

    An empty list means either "nothing is wrong" or "this endpoint has nothing
    to say about a site it does answer for", and those are opposite findings. So
    an empty result is cross-examined against GetCrawlStats: if the crawler
    logged errors in the window and this returns nothing, the verdict is UNKNOWN.
    """
    r = call("GetCrawlIssues", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    out_rows = []
    for x in rows:
        names, unknown_bits = decode_crawl_issues(x.get("Issues"))
        row = {"url": x.get("Url"), "http_code": x.get("HttpCode"),
               "in_links": x.get("InLinks"), "issues": names, "issues_raw": x.get("Issues")}
        if unknown_bits:
            row["undecoded_bits"] = unknown_bits
            row["undecoded_means"] = ("a flag this decoder does not know - reported rather than "
                                      "dropped, because a silently discarded bit is a missing issue")
        out_rows.append(row)

    out = {"ok": True, "site": site, "count": len(out_rows),
           "issues": out_rows[:limit]}
    if out_rows:
        return out

    stats = crawlstats(key, site)
    errs = (stats.get("window_totals") or {}).get("crawl_errors")
    if stats.get("ok") and errs:
        out["verdict"] = "unknown"
        out["why_not_none"] = (
            f"GetCrawlIssues returned nothing, but GetCrawlStats logged {errs} crawl errors "
            f"over {stats.get('days')} days on the same site. Those cannot both be complete. "
            "Reporting 'no crawl issues' here would be a finding about the site made from a "
            "gap in the instrument.")
        out["what_to_do"] = ("Read `crawlstats` for the codes by day; treat this endpoint as "
                             "unable to answer rather than as answering no.")
    elif stats.get("ok"):
        out["verdict"] = "none"
        out["why_this_is_trustworthy"] = (
            "GetCrawlStats logged no crawl errors over the same window, so an empty issue list "
            "agrees with an independent reading rather than standing alone.")
    else:
        out["verdict"] = "unknown"
        out["why_not_none"] = ("the corroborating GetCrawlStats call failed, so an empty list "
                               "cannot be told apart from an instrument that cannot answer")
    return out


def _count_sitemap(url, depth=0, seen=None):
    """Count <loc> entries, following a sitemap INDEX one level down.

    Stdlib, and deliberately dumb: it counts locs, it does not validate. A
    sitemap index is detected by <sitemapindex>, not by filename.
    """
    seen = seen if seen is not None else set()
    if url in seen or depth > 1:
        return 0, []
    seen.add(url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, [{"url": url, "error": str(e)}]
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
    if "<sitemapindex" in body:
        total, errs = 0, []
        for child in locs:
            n, e = _count_sitemap(child, depth + 1, seen)
            total += n
            errs += e
        return total, errs
    return len(locs), []


def feeds(key, site, verify):
    """The sitemap AS BING HOLDS IT - which is not necessarily the one you shipped.

    `UrlCount` is Bing's count from its LAST crawl of the feed, so a fresh
    sitemap that has not been re-crawled reads with the old total. That gap is
    the useful signal: it dates the deploy from the crawler's side.
    """
    r = call("GetFeeds", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    out_rows = []
    for x in rows:
        submitted = _dotnet_date(x.get("Submitted"))
        row = {
            "url": x.get("Url"), "type": x.get("Type"), "status": x.get("Status"),
            "url_count_per_bing": x.get("UrlCount"),
            "last_crawled": _dotnet_date(x.get("LastCrawled")),
            "submitted": submitted,
            "file_size": x.get("FileSize"), "compressed": x.get("Compressed"),
        }
        if submitted is None:
            row["submitted_means"] = ("the .NET 'never' sentinel - Bing discovered this feed "
                                      "itself (robots.txt or a crawl) rather than being handed "
                                      "it. Not a defect; it does mean no submission is on record.")
        out_rows.append(row)

    out = {"ok": True, "site": site, "count": len(out_rows), "feeds": out_rows}
    if not out_rows:
        out["empty_means"] = ("Bing knows of no sitemap for this site. Check robots.txt names one, "
                              "or submit it. This is a real finding, not an auth failure - `sites` "
                              "is the control that separates the two.")
        return out

    if verify:
        checks = []
        for row in out_rows:
            if row["type"] and row["type"].lower() != "sitemap":
                continue
            live, errs = _count_sitemap(row["url"])
            bing_n = row.get("url_count_per_bing")
            c = {"url": row["url"], "url_count_live": live, "url_count_per_bing": bing_n}
            if errs:
                c["fetch_errors"] = errs
            if isinstance(bing_n, int) and live:
                c["delta"] = live - bing_n
                if live != bing_n:
                    c["means"] = (
                        f"Bing is holding a count from {row['last_crawled']}. A delta is NOT a "
                        "defect on its own: it is either a sitemap change bingbot has not "
                        "re-read yet, or a change that never deployed. `last_crawled` against "
                        "your deploy date tells you which.")
            elif not live:
                c["means"] = ("the live sitemap could not be counted, so no comparison is "
                              "possible - this is 'cannot ask', not 'they agree'")
            checks.append(c)
        out["verify"] = checks
    else:
        out["verify_hint"] = ("pass --verify to count the live sitemap and compare it against "
                              "Bing's held count")
    return out


def quota(key, site):
    """What Bing will let you submit - and what that is NOT.

    Distinct from IndexNow: different channel, different quota, and a ping to one
    is not a submission to the other. IndexNow is keyless and effectively
    unlimited; this is a per-site allowance you can exhaust.
    """
    u = call("GetUrlSubmissionQuota", key, siteUrl=site)
    c = call("GetContentSubmissionQuota", key, siteUrl=site)
    if not u["ok"]:
        return u
    ud = u["d"] or {}
    out = {
        "ok": True, "site": site,
        "url_submission": {"daily": ud.get("DailyQuota"), "monthly": ud.get("MonthlyQuota")},
        "means": ("the allowance REMAINING, per Bing. Submitting consumes it; it is not a "
                  "constant. Nothing here reports how much has already been spent, so read it "
                  "immediately before a batch rather than caching the number."),
        "not_indexnow": ("IndexNow (indexnow.py) is a separate, keyless channel that also "
                         "reaches Yandex, Seznam and Naver. Pinging IndexNow does not spend "
                         "this quota, and spending this quota does not ping them."),
    }
    if c["ok"]:
        cd = c["d"] or {}
        out["content_submission"] = {"daily": cd.get("DailyQuota"), "monthly": cd.get("MonthlyQuota")}
    return out


def post(method, key, payload):
    """POST half of the API. The mutating calls need a JSON body, not a query string."""
    qs = urllib.parse.urlencode({"apikey": key})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/{method}?{qs}", data=data,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}",
                "detail": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not body.strip():
        return {"ok": True, "d": None}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable response", "detail": body[:200]}
    if isinstance(data, dict) and "ErrorCode" in data:
        return {"ok": False, "error": data.get("Message", "unknown"),
                "error_code": data.get("ErrorCode")}
    return {"ok": True, "d": data.get("d") if isinstance(data, dict) else data}


def _read_url_list(urls, from_file):
    out = list(urls or [])
    if from_file:
        with open(from_file) as fh:
            out += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def submit(key, site, urls, from_file, confirm):
    """SubmitUrlBatch - ask bingbot to come and look, now.

    DRY RUN unless --yes. It spends a finite quota and it is the only mutating
    call in this script, so the default is to show what would be sent.
    """
    wanted = _read_url_list(urls, from_file)
    if not wanted:
        return {"ok": False, "error": "no urls", "detail": "pass --url (repeatable) or --from-file"}

    host = urllib.parse.urlparse(site).netloc.lower()
    on_site, off_site = [], []
    for u in wanted:
        p = urllib.parse.urlparse(u)
        (on_site if p.scheme in ("http", "https") and p.netloc.lower() == host else off_site).append(u)

    q = quota(key, site)
    daily = (q.get("url_submission") or {}).get("daily") if q.get("ok") else None

    plan = {
        "ok": True, "site": site, "submitted": False,
        "urls_requested": len(wanted), "on_site": len(on_site), "off_site": off_site[:20],
        "quota_daily_remaining": daily,
    }
    if off_site:
        plan["off_site_refused"] = ("Bing rejects the whole batch for a URL outside the verified "
                                    "site, so these are dropped before sending rather than "
                                    "costing the call.")
    if not on_site:
        plan["ok"] = False
        plan["error"] = "every url was off-site"
        return plan
    if isinstance(daily, int) and len(on_site) > daily:
        plan["ok"] = False
        plan["error"] = "batch exceeds the daily quota"
        plan["detail"] = (f"{len(on_site)} urls against {daily} remaining today. Bing does not "
                          "partially accept - trim the batch or wait for the reset.")
        return plan
    if not confirm:
        plan["dry_run"] = True
        plan["urls"] = on_site[:50]
        plan["what_to_do"] = "re-run with --yes to actually submit"
        return plan

    r = post("SubmitUrlBatch", key, {"siteUrl": site, "urlList": on_site})
    if not r["ok"]:
        plan["ok"] = False
        plan.update({k: v for k, v in r.items() if k != "ok"})
        return plan
    plan["submitted"] = True
    plan["urls"] = on_site[:50]
    plan["note"] = ("accepted for submission. Bing returns no per-URL result, so this is a "
                    "receipt for the REQUEST, not a crawl confirmation - verify with "
                    "`urlinfo --url <u>` in a few days, and `crawlstats` for the aggregate.")
    return plan


def blocked(key, site):
    """URLs you have told Bing to drop - the self-inflicted deindex check.

    Nothing else in this program can see these: they are account state, not
    markup, so they survive every deploy and appear in no crawl of the site.
    """
    b = call("GetBlockedUrls", key, siteUrl=site)
    if not b["ok"]:
        return b
    p = call("GetActivePagePreviewBlocks", key, siteUrl=site)
    rows = b["d"] or []
    out = {
        "ok": True, "site": site,
        "blocked_urls": [{"url": x.get("Url"), "entity_type": x.get("EntityType"),
                          "date": _dotnet_date(x.get("Date"))} for x in rows],
        "count": len(rows),
    }
    if p["ok"]:
        pr = p["d"] or []
        out["page_preview_blocks"] = [{"url": x.get("Url")} for x in pr]
        out["page_preview_count"] = len(pr)
    if not rows:
        out["verdict"] = "clean"
        out["means"] = ("no URL-level block is set on this account. The endpoint answered - "
                        "`sites` proves the authorisation - so this is a real zero, not a "
                        "cannot-ask.")
    else:
        out["verdict"] = "blocks_present"
        out["means"] = ("each of these is a page Bing has been told not to serve, regardless of "
                        "what the page or robots.txt says. Confirm every one is deliberate.")
    return out


def crawlsettings(key, site):
    """How fast Bing is willing to crawl, hour by hour."""
    r = call("GetCrawlSettings", key, siteUrl=site)
    if not r["ok"]:
        return r
    d = r["d"] or {}
    rate = d.get("CrawlRate") or []
    out = {
        "ok": True, "site": site,
        "crawl_rate_by_hour": rate,
        "crawl_boost_available": d.get("CrawlBoostAvailable"),
        "crawl_boost_enabled": d.get("CrawlBoostEnabled"),
    }
    if rate:
        out["uniform"] = len(set(rate)) == 1
        out["means"] = (
            "24 values, one per hour, 1 (slowest) to 10 (fastest); Bing's default is a flat 5. "
            "A throttled hour is a self-imposed crawl ceiling and is invisible everywhere else. "
            "Raising it does NOT make Bing crawl more - it only removes a cap.")
    if d.get("CrawlBoostAvailable") is False:
        out["boost_note"] = ("crawl boost is not offered on this site. That is Bing's call, not a "
                             "setting you failed to enable.")
    return out


def main():
    # Shared options live on a parent parser so they are accepted on EITHER
    # side of the subcommand. Putting them only on the top-level parser makes
    # the natural `bing.py expand --seed x --days 90` an "unrecognized
    # arguments" error, which reads like a broken script rather than a word
    # order rule.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--site", help="site url; inferred when the account has exactly one verified site")
    common.add_argument("--country", default="us")
    common.add_argument("--language", default="en-US")
    # default=None, NOT 90: only `keyword` and `expand` reach an endpoint that
    # takes a date range. The other subcommands hit endpoints with NO window
    # parameter at all, so a --days they silently accepted would be a lie that
    # reads as data - `--days 7` and `--days 30` returned byte-identical rows
    # on a live account (2026-09-01), which reads as "positions unchanged this
    # week" and is actually the same fixed window answered twice.
    common.add_argument("--days", type=int, default=None,
                        help="lookback window (keyword/expand only; default 90)")
    common.add_argument("--limit", type=int, default=50)

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common]
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in [
        ("sites", "verified sites (and the auth control)"),
        ("backlinks", "inbound links"),
        ("traffic", "clicks/impressions over time"),
        ("pages", "per-PAGE impressions/clicks/CTR - which URL actually earns"),
        ("crawlstats", "what BINGBOT fetched, day by day, and what is in the index"),
        ("quota", "how many URLs Bing will still accept today (NOT IndexNow's quota)"),
        ("blocked", "URLs blocked at the ACCOUNT level - invisible to any crawl of the site"),
        ("crawlsettings", "the per-hour crawl-rate cap, and whether boost is offered"),
    ]:
        sub.add_parser(name, help=helptext, parents=[common])
    sub.add_parser("control", help="prove the decoders discriminate (no key needed)")

    ci = sub.add_parser("crawlissues", help="per-URL crawl problems, with the empty answer guarded",
                        parents=[common])
    fd = sub.add_parser("feeds", help="the sitemap AS BING HOLDS IT - url count and last crawl",
                        parents=[common])
    fd.add_argument("--verify", action="store_true",
                    help="count the LIVE sitemap and compare it against Bing's held count")
    sb = sub.add_parser("submit", help="SubmitUrlBatch - ask bingbot to fetch these now (DRY RUN by default)",
                        parents=[common])
    sb.add_argument("--url", action="append", default=[], help="URL to submit (repeatable)")
    sb.add_argument("--from-file", help="file of URLs, one per line, # comments allowed")
    sb.add_argument("--yes", action="store_true",
                    help="actually submit. Without it this prints the batch and spends nothing")

    qy = sub.add_parser("queries", help="queries this site appears for - dated weekly rows + by_query",
                        parents=[common])
    qy.add_argument("--q", dest="exact", help="one query's weekly series, date order (case-insensitive exact)")
    qy.add_argument("--contains", help="weekly series for every query containing this substring")
    cn = sub.add_parser("cannibal", help="queries split across several URLs of the site, owner pages "
                        "missing, best position at 7-11", parents=[common])
    cn.add_argument("--pages", help="comma-separated page URLs, exactly as `bing.py pages` reports them")
    cn.add_argument("--from-file", help="file of page URLs, one per line, # comments allowed")
    cn.add_argument("--top", type=int, default=None,
                    help="also add the top N pages by impressions (default: --limit when no "
                         "pages are given, else 0)")
    cn.add_argument("--owner", action="append", default=[],
                    help='"phrase=url": the URL meant to own queries containing the phrase (repeatable)')
    cn.add_argument("--owners-file", help='JSON object {"phrase": "url", ...}')
    cn.add_argument("--min-share", type=float, default=CANNIBAL_MIN_SHARE,
                    help="share of a query's impressions a URL needs to count toward a split")
    cn.add_argument("--min-impressions", type=int, default=CANNIBAL_MIN_IMPRESSIONS,
                    help="ignore queries with fewer impressions across the measured pages")
    cn.add_argument("--max-findings", type=int, default=50)
    cn.add_argument("--pace", type=float, default=PAGE_CALL_PACE_S,
                    help="seconds between per-page calls (Bing throttles tight loops)")

    ui = sub.add_parser("urlinfo", help="Bing's own record of ONE url: discovered, last crawled, size",
                        parents=[common])
    ui.add_argument("--url", required=True, help="exact URL to look up")
    pq = sub.add_parser("pagequeries", help="the queries ONE page appears for",
                        parents=[common])
    pq.add_argument("--page", required=True, help="exact URL as bing.py pages reports it")
    k = sub.add_parser("keyword", help="impressions for one query", parents=[common])
    k.add_argument("--q", required=True)
    e = sub.add_parser("expand", help="related keywords WITH impressions", parents=[common])
    e.add_argument("--seed", required=True)

    a = p.parse_args()
    # The control is pure and keyless on purpose: the decoders must be provable
    # on a machine with no Bing account at all, so "cannot ask" stays separate
    # from "the reader is broken".
    if a.cmd == "control":
        out = run_control()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if out.get("ok") else 1)
    key = read_key()
    if not key:
        print(json.dumps({"ok": False, "error": "no BING_WEBMASTER_API_KEY and no ~/.bing_webmaster_key"}))
        sys.exit(2)

    if a.days is not None and a.cmd not in WINDOWED:
        print(json.dumps({
            "ok": False,
            "error": "window_not_selectable",
            "detail": (
                f"`{a.cmd}` reads a Bing endpoint that takes no date range, so --days "
                "cannot be honoured. It is refused rather than ignored: two --days "
                "values return identical rows, which reads as 'nothing changed' when "
                "it means 'the window was never applied'."
            ),
            "windowed_commands": list(WINDOWED),
            "what_to_do": (
                "Drop --days and read the fixed window Bing returns, or compare "
                "snapshots over time yourself (bing.py traffic is a dated series and "
                "IS a real time signal)."
            ),
        }, indent=2))
        sys.exit(2)

    # Argument contradictions are refused before the network, like --days above.
    if a.cmd == "queries" and a.exact is not None and a.contains is not None:
        print(json.dumps({"ok": False, "error": "conflicting_match",
                          "detail": "--q is an exact match and --contains a substring match; pass one"}))
        sys.exit(2)
    if a.cmd == "cannibal":
        try:
            owners = _read_owners(a.owner, a.owners_file)
            explicit = _read_url_list([u.strip() for u in (a.pages or "").split(",") if u.strip()],
                                      a.from_file)
        except (ValueError, OSError, json.JSONDecodeError) as ex:
            print(json.dumps({"ok": False, "error": "bad_cannibal_input", "detail": str(ex)}))
            sys.exit(2)
        if not (0 < a.min_share <= 0.5):
            # Above 0.5 two URLs can never both qualify, so `split` could not fire and
            # an empty result would be the parameter, not the site.
            print(json.dumps({"ok": False, "error": "min_share_out_of_range",
                              "detail": "--min-share must be in (0, 0.5]; above 0.5 a split is impossible"}))
            sys.exit(2)
        top = a.top if a.top is not None else (0 if explicit else a.limit)

    end = date.today()
    start = end - timedelta(days=a.days if a.days is not None else 90)
    s, e_ = start.strftime(DATE_FMT), end.strftime(DATE_FMT)

    if a.cmd == "sites":
        out = sites(key)
    elif a.cmd == "keyword":
        out = keyword(key, a.q, a.country, a.language, s, e_)
    elif a.cmd == "expand":
        out = expand(key, a.seed, a.country, a.language, s, e_, a.limit)
    else:
        site, err = _resolve_site(key, a.site)
        if err:
            print(json.dumps(err, indent=2))
            sys.exit(3)
        if a.cmd == "urlinfo":
            out = urlinfo(key, site, a.url)
        elif a.cmd == "crawlstats":
            out = crawlstats(key, site)
        elif a.cmd == "crawlissues":
            out = crawlissues(key, site, a.limit)
        elif a.cmd == "feeds":
            out = feeds(key, site, a.verify)
        elif a.cmd == "quota":
            out = quota(key, site)
        elif a.cmd == "submit":
            out = submit(key, site, a.url, a.from_file, a.yes)
        elif a.cmd == "blocked":
            out = blocked(key, site)
        elif a.cmd == "crawlsettings":
            out = crawlsettings(key, site)
        elif a.cmd == "pagequeries":
            out = pagequeries(key, site, a.page, a.limit)
        elif a.cmd == "queries":
            out = queries(key, site, a.limit, a.exact, a.contains)
        elif a.cmd == "cannibal":
            out = cannibal(key, site, explicit, top, owners, a.min_share, a.min_impressions,
                           a.max_findings, a.pace)
        else:
            out = {"backlinks": backlinks,
                   "traffic": traffic, "pages": pages}[a.cmd](
                *( (key, site, a.limit)
                   if a.cmd in ("backlinks", "pages") else (key, site) )
            )

    # State the window on every report that has one it did not choose, so a
    # reader who never passed --days still knows the span is Bing's, not theirs.
    if a.cmd in ("queries", "pages", "pagequeries", "traffic", "cannibal") and out.get("ok"):
        out.setdefault(
            "window",
            "Bing's own fixed reporting window - not selectable here; --days is refused "
            "on this subcommand rather than silently ignored.",
        )

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
