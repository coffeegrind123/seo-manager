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
  feeddetails  a sitemap INDEX child by child - count, status, last crawl
  submitfeed   SubmitFeed: make Bing re-read a sitemap. DRY RUN until --yes;
               the live feed must be 200 and parse first; read back after
  removefeed   RemoveFeed. Refused unless Bing holds the feed - a no-op answers
               the same null as a removal
  submitcontent  SubmitContent: push a page's full HTTP response. DRY RUN until
               --yes; the content-quota drop is the only proof it counted
  fetch        FetchUrl: bingbot fetches one URL now, and the response it got
               is parsed - status, noindex, canonical, truncation. --live diffs
               it against a normal fetch
  fetched      the fetch history, or one URL's last fetch parsed

Per-query views, the other direction from `pages`/`pagequeries`:

  keywordtrend  GetKeywordStats - one query's Bing demand, week by week
  querypages   EVERY url of the site that drew impressions for one query, with
               split / edge-of-page flags. `cannibal --query/--top-queries`
               uses it to see URLs nobody listed
  querypagedetail  the POSITION DISTRIBUTION for one query on one page - the
               shape an average position hides
  querytraffic  one query's clicks/impressions for the site, by DAY
  urltraffic   GetUrlTrafficInfo, cross-checked: measured returning 0 for pages
               with real traffic, so a zero is never read alone
  children     Index Explorer, one level - measured empty on every path tried,
               so an empty answer is cross-checked and reported unknown

Account CONFIGURATION (sites, roles, blocks, query parameters, regions, crawl
rate, connected pages, deep-link blocks) is `bingadmin.py`, not this script.

Every failure decodes Bing's ErrorCode (it arrives in an HTTP 400 body), a 404
means the METHOD is retired, and throttles are retried with backoff.

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
import base64
import json
import re
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
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


# Bing's ErrorCode enum (Microsoft.Bing.Webmaster.Api.ApiErrorCode).
ERROR_CODES = {
    0: "None", 1: "InternalError", 2: "UnknownError", 3: "InvalidApiKey",
    4: "ThrottleUser", 5: "ThrottleHost", 6: "UserBlocked", 7: "InvalidUrl",
    8: "InvalidParameter", 9: "TooManySites", 10: "UserNotFound", 11: "NotFound",
    12: "AlreadyExists", 13: "NotAllowed", 14: "NotAuthorized", 15: "UnexpectedState",
    16: "Deprecated",
}
THROTTLE_CODES = (4, 5)
THROTTLE_HTTP = (429, 503)
# Seconds before each retry. Measured 2026-10-18: ThrottleHost fired after ~20
# calls at 0.6s spacing and had cleared 20s later.
THROTTLE_BACKOFF_S = (3, 8, 20)
_sleep = time.sleep


def _envelope(status, body):
    """A failed response -> the standard error shape, with Bing's code decoded.

    Measured 2026-10-18: failures arrive as HTTP 400 with the ErrorCode envelope
    in the BODY (NotAuthorized, InvalidParameter, ThrottleHost alike), not as
    HTTP 200. Read only the status and every one of them is a bare "HTTP 400",
    so a throttle and "you do not own this site" become the same answer.

    An HTTP 404 with an empty body is a METHOD Bing no longer serves: a made-up
    method name returns exactly that, on GET and POST alike, which is the control
    that makes 404 mean "retired" rather than "this site has none"."""
    try:
        data = json.loads(body) if body.strip() else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "ErrorCode" in data:
        code = data.get("ErrorCode")
        return {"ok": False, "error": data.get("Message", "unknown"), "error_code": code,
                "error_name": ERROR_CODES.get(code, "unknown"), "http_status": status}
    if status == 404:
        return {"ok": False, "error": "method_not_served", "http_status": 404,
                "detail": ("Bing answered 404 for the METHOD - the same answer a method name "
                           "that never existed gets. The endpoint is retired or renamed; this "
                           "says nothing about the site.")}
    if status == 405:
        return {"ok": False, "error": "wrong_http_verb", "http_status": 405,
                "detail": "this method is served on the other verb (GET vs POST)"}
    return {"ok": False, "error": f"HTTP {status}", "http_status": status, "detail": body[:300]}


def _redact(res, req):
    """Scrub the API key out of every string in an error result.

    The key travels in the query string, so any exception text that quotes the
    URL would print it into JSON output, receipts and CI logs."""
    key = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query).get("apikey", [""])[0]
    if not key:
        return res
    return {k: (v.replace(key, "<redacted>") if isinstance(v, str) else v) for k, v in res.items()}


def _send(req, timeout):
    return _redact(_send_raw(req, timeout), req)


def _send_raw(req, timeout):
    """urlopen with Bing's error envelope decoded and throttles retried.

    A throttle that is NOT retried reads as an answer - 35 of 46 urlinfo calls in a
    tight loop (2026-09-01) - so it is retried with backoff here, once, for every
    caller, and if it still throttles the result says `throttled` rather than
    passing as some other failure."""
    res = None
    for attempt in range(len(THROTTLE_BACKOFF_S) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            res = _envelope(e.code, e.read().decode("utf-8", "replace"))
        except Exception as e:
            return {"ok": False, "error": str(e)}
        else:
            if not body.strip():
                return {"ok": True, "d": None}
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return {"ok": False, "error": "unparseable response", "detail": body[:200]}
            # The envelope can also arrive on a 200. Treating it as data is how "we
            # have no backlinks" gets reported when the truth is "you do not own
            # this site".
            if isinstance(data, dict) and "ErrorCode" in data:
                res = _envelope(200, body)
            else:
                return {"ok": True, "d": data.get("d") if isinstance(data, dict) else data}

        if res.get("error_code") in THROTTLE_CODES or res.get("http_status") in THROTTLE_HTTP:
            if attempt < len(THROTTLE_BACKOFF_S):
                _sleep(THROTTLE_BACKOFF_S[attempt])
                continue
            res["throttled"] = True
            res["throttled_means"] = ("still throttled after every retry. This is CANNOT ASK, "
                                      "never an empty answer - re-run later.")
        return res
    return res


def call(method, key, **params):
    """One GET. Distinguishes an ERROR from an empty-but-valid answer."""
    qs = urllib.parse.urlencode({"apikey": key, **{k: v for k, v in params.items() if v is not None}})
    req = urllib.request.Request(f"{BASE}/{method}?{qs}", headers={"User-Agent": UA, "Accept": "application/json"})
    return _send(req, 45)


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

    # THE ERROR ENVELOPE. Measured 2026-10-18: failures are HTTP 400 with the code
    # in the body. Reading only the status made a throttle and NotAuthorized equal.
    e = _envelope(400, '{"ErrorCode":14,"Message":"ERROR!!! NotAuthorized"}')
    c.check("a_400_body_envelope_is_decoded", e.get("error_name") == "NotAuthorized", str(e))
    c.check("CONTROL_a_throttle_decodes_differently",
            _envelope(400, '{"ErrorCode":5,"Message":"ERROR!!! ThrottleHost"}').get("error_code") == 5)
    c.check("an_empty_404_is_a_retired_method", _envelope(404, "").get("error") == "method_not_served")
    c.check("a_non_envelope_400_keeps_its_status", _envelope(400, "<html>").get("error") == "HTTP 400")
    c.check("throttle_codes_are_user_and_host", set(THROTTLE_CODES) == {4, 5})

    # FETCH-AS-BINGBOT: three blocks, CRLF-separated, body already decoded.
    doc = ("Document-URL: https://x.test/a\r\nLast-DownLoad-Http-Code: 200\r\nTruncation: 0\r\n"
           "Robots-Rule: 0\r\nSFSDownloadDuration: 120\r\n\r\n"
           "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Encoding: br\r\n"
           "X-Robots-Tag: noindex\r\nLink: <https://x.test/a>; rel=\"canonical\"\r\n\r\n"
           "<html lang=\"de\"><head><title> Hallo  Welt </title>"
           "<link rel=\"canonical\" href=\"https://x.test/a\">"
           "<link rel=\"alternate\" hreflang=\"en\" href=\"https://x.test/en/a\">"
           "</head><body><h1>x</h1></body></html>")
    pf = parse_fetched(doc)
    c.check("the_fetched_document_parses_into_its_three_blocks",
            pf.get("parsed") and pf["crawler"]["http_code"] == 200 and pf["response"]["status"] == 200,
            str(pf)[:300])
    c.check("the_title_is_whitespace_normalised", pf["body"].get("title") == "Hallo Welt")
    c.check("an_x_robots_noindex_header_is_seen", pf["noindex"] is True)
    c.check("a_link_header_canonical_is_read", pf["response"]["link_canonical"] == ["https://x.test/a"])
    c.check("hreflang_and_lang_are_read", (pf["body"]["hreflang"], pf["body"]["lang"]) == (1, "de"))
    c.check("a_noindex_fetch_is_a_problem_verdict", _fetched_verdict(pf)[0] == "problem")
    clean = parse_fetched(doc.replace("X-Robots-Tag: noindex\r\n", ""))
    c.check("CONTROL_the_same_page_without_noindex_is_clean",
            _fetched_verdict(clean) == ("clean", []), str(_fetched_verdict(clean)))
    c.check("an_unrecognised_document_is_unparsed_not_clean",
            parse_fetched("garbage").get("parsed") is False)
    c.check("fetch_history_folds_a_trailing_slash",
            same_fetch_url("https://x.test/zh", "https://x.test/zh/"))
    c.check("CONTROL_different_paths_do_not_fold",
            not same_fetch_url("https://x.test/zh", "https://x.test/z"))
    live = {"status": 200, "x_robots_tag": [], "body": dict(clean["body"])}
    c.check("CONTROL_identical_views_do_not_differ", compare_views(clean, live)["differs"] is False)
    live["body"]["title"] = "Access denied"
    c.check("a_different_title_is_a_view_difference",
            [d["field"] for d in compare_views(clean, live)["differences"]] == ["title"])

    # SubmitContent's message: wire-encoding headers dropped, length recomputed.
    msg = build_http_message(200, "OK", [("Content-Type", "text/html"), ("Content-Encoding", "br"),
                                         ("Content-Length", "9")], b"<p>hi</p>!")
    c.check("the_content_message_drops_wire_encoding",
            b"Content-Encoding" not in msg and b"Content-Length: 10\r\n\r\n<p>hi</p>!" in msg, msg[:120])

    # Trends compare equal halves or nothing.
    c.check("a_trend_compares_the_last_window_with_the_one_before",
            _trend([{"v": x} for x in (1, 1, 2, 2)], "v", 2) == {
                "window": 2, "last": 4, "previous": 2, "change": 2, "change_pct": 1.0})
    c.check("a_short_series_has_no_trend_rather_than_a_half_one",
            _trend([{"v": 1}] * 3, "v", 2) is None)
    c.check("is_on_site_is_host_exact",
            is_on_site("https://x.test/", "https://x.test/a")
            and not is_on_site("https://x.test/", "https://www.x.test/a")
            and not is_on_site("https://x.test/", "ftp://x.test/a"))

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


def _add_query_side(key, site, per_page, queries, pace):
    """Merge GetQueryPageStats rows into the page-side set, so a URL nobody listed
    can still appear in a split. Returns (urls found only this way, errors).

    A page whose page-side call FAILED stays unmeasured: giving it the rows of a
    few queries would make it look measured for all of them."""
    found, errors = [], {}
    for i, q in enumerate(queries):
        if i and pace:
            _sleep(pace)
        rows, err = _query_rows(key, site, q)
        if err:
            errors[q] = err.get("error")
            continue
        for url, rs in rows.items():
            if url in per_page and per_page[url] is None:
                continue
            if url not in per_page:
                per_page[url] = []
                found.append(url)
            have = {(r["query"], r["date"]) for r in per_page[url]}
            per_page[url].extend(r for r in rs if (r["query"], r["date"]) not in have)
    return found, errors


def cannibal(key, site, explicit_pages, top, owners, min_share=CANNIBAL_MIN_SHARE,
             min_impressions=CANNIBAL_MIN_IMPRESSIONS, max_findings=50, pace=PAGE_CALL_PACE_S,
             queries=None, top_queries=0):
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

    qlist = list(queries or [])
    if top_queries and site_totals:
        qlist += sorted(site_totals, key=lambda q: (-site_totals[q], q))[:top_queries]
    qlist = list(dict.fromkeys(qlist))
    found_by_query, query_errors = (_add_query_side(key, site, per_page, qlist, pace)
                                    if qlist else ([], {}))

    core = _cannibal_core(per_page, owners, min_share, min_impressions, site_totals)
    out = {"ok": True, "site": site,
           "params": {"min_share": min_share, "min_impressions": min_impressions,
                      "edge_of_page": list(EDGE_OF_PAGE), "owners": owners,
                      "queries_asked": qlist},
           "pages_measured": [u for u in page_set if per_page[u] is not None],
           "pages_found_by_query": found_by_query,
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
               "cannot appear in a split - unless the query was in `queries_asked`, "
               "where every URL Bing showed was added (`pages_found_by_query`, measured "
               "for those queries only). `site_impressions` (per query) and "
               "`measured_page_impressions` (per URL appearance) are different counts "
               "and are not a coverage ratio.")}
    if query_errors:
        out["query_errors"] = query_errors
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
# AllOtherCodes moved to cumulative 2026-10-18: 486 -> 2,211 over 71 days, no dip
# above 6.5% of its peak, and summed as a flow it came to 1.96x the pages crawled -
# impossible for a per-day count of responses.
REF_CUMULATIVE = ("Code2xx", "InIndex", "InLinks", "BlockedByRobotsTxt", "AllOtherCodes")
REF_FLOW = ("CrawledPages", "Code4xx", "Code5xx", "CrawlErrors")

# Split before a capital AND before a digit run, so `Code2xx` reads `code_2xx`
# rather than `code2xx` - these names end up in jq expressions in the workflow
# files, and a reader who guesses the obvious spelling should be right.
_SNAKE = re.compile(r"(?<!^)(?=[A-Z])|(?<=[A-Za-z])(?=\d)")


def _snake(name):
    return _SNAKE.sub("_", name).lower()


# A stock may dip on at most this share of days, by at most this share of its peak.
STOCK_MAX_DIP_SHARE = 0.25
STOCK_MAX_DIP_SIZE = 0.15


def _classify_series(values):
    """flow vs cumulative, derived from the numbers rather than assumed.

    A stock dips rarely and SLIGHTLY; a daily count dips often and HARD. Both
    tests, because either alone fails on real data. Measured 2026-10-18 over 71
    steps: InIndex dipped 2 times, Code2xx 8, and neither dip exceeded 2.3% of
    the column's peak; CrawledPages dipped 34 times, by up to 46%. The old rule -
    at most ONE dip - called every stock with a second correction a flow and
    summed InIndex to 366,850 for an 8,442-page index.

    A column that never changes cannot be classified at all - and reporting that
    honestly is the point, because every all-zero column on the only account
    measured was exactly this case.
    """
    if len(values) < 3:
        return "unclassified", "fewer than 3 days of data"
    if len(set(values)) == 1:
        return "unclassified", f"constant at {values[0]} for the whole window"
    steps = len(values) - 1
    dips = [values[i - 1] - values[i] for i in range(1, len(values)) if values[i] < values[i - 1]]
    peak = max(values) or 1
    worst = max(dips, default=0) / peak
    basis = f"{len(dips)} decrease(s) in {steps} steps, largest {worst:.1%} of the peak"
    if len(dips) / steps <= STOCK_MAX_DIP_SHARE and worst <= STOCK_MAX_DIP_SIZE:
        return "cumulative", basis
    return "flow", basis


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


def _feed_row(x):
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
    return row


def feeds(key, site, verify):
    """The sitemap AS BING HOLDS IT - which is not necessarily the one you shipped.

    `UrlCount` is Bing's count from its LAST crawl of the feed, so a fresh
    sitemap that has not been re-crawled reads with the old total. That gap is
    the useful signal: it dates the deploy from the crawler's side.
    """
    r = call("GetFeeds", key, siteUrl=site)
    if not r["ok"]:
        return r
    out_rows = [_feed_row(x) for x in r["d"] or []]

    out = {"ok": True, "site": site, "count": len(out_rows), "feeds": out_rows}
    if not out_rows:
        out["empty_means"] = ("Bing knows of no sitemap for this site. Check robots.txt names one, "
                              "or submit it. This is a real finding, not an auth failure - `sites` "
                              "is the control that separates the two.")
        return out

    if verify:
        checks = []
        for row in out_rows:
            # "Sitemap" AND "Sitemap Index": _count_sitemap follows an index, and
            # skipping indexes made --verify return [] on a site whose only feed
            # is one (measured 2026-10-18).
            if row["type"] and not row["type"].lower().startswith("sitemap"):
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
    """POST half of the API. The mutating calls need a JSON body, not a query string.

    ⚠ Every write answers `{"d": null}` on success AND on a no-op - removing a
    feed, block or parameter that does not exist returns the identical null
    (measured 2026-10-18 on eight Remove* methods). A write is therefore only ever
    confirmed by reading the state back, never by its own response."""
    qs = urllib.parse.urlencode({"apikey": key})
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/{method}?{qs}", data=data,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json; charset=utf-8"})
    return _send(req, 60)


def receipt(root, payload):
    """Append one line to <root>/.seo/bing.jsonl - only when <root>/.seo exists.

    Bing keeps no queryable history of submissions or account changes, so without
    this "did we submit that / who blocked that URL" has no answer. A directory
    that is not a managed site is not littered with a .seo/ it never had."""
    if not root:
        return None
    d = Path(root) / ".seo"
    if not d.is_dir():
        return None
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}
    try:
        with (d / "bing.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return str(d / "bing.jsonl")


def is_on_site(site, url):
    """True when url is http(s) on the verified site's host. Measured 2026-10-18:
    an off-site URL or feed is refused whole with AuthorizationFailed, so it is
    dropped before the call rather than costing it."""
    p = urllib.parse.urlparse(url or "")
    return p.scheme in ("http", "https") and p.netloc.lower() == urllib.parse.urlparse(site).netloc.lower()


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


def submit(key, site, urls, from_file, confirm, root=None):
    """SubmitUrl / SubmitUrlBatch - ask bingbot to come and look, now.

    DRY RUN unless --yes: it spends a finite quota, so the default is to show what
    would be sent. One URL goes through SubmitUrl, several through SubmitUrlBatch -
    same quota, same effect. The quota is read again afterwards, because the call
    itself returns null whatever happened and the drop in quota is the only
    evidence the submission was counted.
    """
    wanted = _read_url_list(urls, from_file)
    if not wanted:
        return {"ok": False, "error": "no urls", "detail": "pass --url (repeatable) or --from-file"}

    on_site, off_site = [], []
    for u in wanted:
        (on_site if is_on_site(site, u) else off_site).append(u)

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

    if len(on_site) == 1:
        method, r = "SubmitUrl", post("SubmitUrl", key, {"siteUrl": site, "url": on_site[0]})
    else:
        method, r = "SubmitUrlBatch", post("SubmitUrlBatch", key, {"siteUrl": site, "urlList": on_site})
    plan["method"] = method
    if not r["ok"]:
        plan["ok"] = False
        plan.update({k: v for k, v in r.items() if k != "ok"})
        return plan
    plan["submitted"] = True
    plan["urls"] = on_site[:50]
    after = quota(key, site)
    left = (after.get("url_submission") or {}).get("daily") if after.get("ok") else None
    plan["quota_daily_after"] = left
    if isinstance(daily, int) and isinstance(left, int):
        plan["quota_spent"] = daily - left
        plan["quota_means"] = ("the drop in remaining quota is the only evidence Bing COUNTED "
                               "the submission - the call itself returns null either way. "
                               f"{daily - left} spent for {len(on_site)} url(s).")
    plan["note"] = ("accepted for submission. Bing returns no per-URL result, so this is a "
                    "receipt for the REQUEST, not a crawl confirmation - verify with "
                    "`urlinfo --url <u>` in a few days, and `crawlstats` for the aggregate.")
    plan["receipt"] = receipt(root, {"action": method, "site": site, "urls": on_site,
                                     "quota_before": daily, "quota_after": left})
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


# --------------------------------------------------------------------------
# The rest of the read surface (2026-10-18). Every endpoint below was called on
# a live account before a line was written; what each one actually returned is
# in its docstring, because three of them answer confidently and wrongly.
# --------------------------------------------------------------------------

def _trend(points, field, window):
    """Sum of `field` over the last `window` points against the `window` before.
    None when the series cannot fill both halves - a half-window comparison is a
    different question, not a shorter answer to this one."""
    vals = [p.get(field) or 0 for p in points]
    if len(vals) < 2 * window:
        return None
    last, prev = sum(vals[-window:]), sum(vals[-2 * window:-window])
    return {"window": window, "last": last, "previous": prev, "change": last - prev,
            "change_pct": round((last - prev) / prev, 4) if prev else None}


def _dated(rows, fields):
    """Bing rows -> [{date, <fields>}] in date order, undated rows last (kept)."""
    pts = [{"date": _dotnet_date(x.get("Date")), **{k: x.get(src) or 0 for k, src in fields.items()}}
           for x in rows or []]
    pts.sort(key=lambda p: (p["date"] is None, p["date"] or ""))
    return pts


def keywordtrend(key, q, country, language):
    """GetKeywordStats - one query's Bing demand, week by week.

    Measured 2026-10-18: 24 weekly rows (about six months) for a head query, each
    with `Impressions` (exact) and `BroadImpressions` (the query inside longer
    ones). The window is Bing's own - there is no date parameter - so --days is
    refused. Same caveat as `keyword`: Bing impressions, never Google volume, and
    an empty answer is UNMEASURED, not zero demand.
    """
    r = call("GetKeywordStats", key, q=q, country=country, language=language)
    if not r["ok"]:
        return r
    pts = _dated(r["d"], {"impressions": "Impressions", "broad_impressions": "BroadImpressions"})
    out = {"ok": True, "query": q, "market": {"country": country, "language": language},
           "known_to_bing": bool(pts), "weeks": len(pts), "series": pts,
           "caveat": "BING impressions, not Google search volume. Use the SHAPE, not the size."}
    if not pts:
        out["empty_means"] = ("Bing's keyword database has no weekly row for this exact string in "
                              "this market. That is UNMEASURED, not zero demand - see `keyword`'s "
                              "coverage note.")
        return out
    dated = [p for p in pts if p["date"]]
    peak = max(dated, key=lambda p: p["impressions"]) if dated else None
    out.update({
        "range": {"first": dated[0]["date"], "last": dated[-1]["date"]} if dated else None,
        "totals": {"impressions": sum(p["impressions"] for p in pts),
                   "broad_impressions": sum(p["broad_impressions"] for p in pts)},
        "peak_week": peak,
        "trend_4w": _trend(dated, "impressions", 4),
        "trend_12w": _trend(dated, "impressions", 12),
        "reading": ("`impressions` is the exact query, `broad_impressions` the query inside "
                    "longer ones. trend_Nw compares the last N weeks with the N before; a "
                    "null trend means the series is too short to fill both halves."),
    })
    return out


def _url_entries(per_url, total=None):
    """{url: weekly rows for ONE query} -> per-URL totals, share, weighted position."""
    entries = []
    for url, rows in per_url.items():
        imps = sum(r["impressions"] for r in rows)
        clicks = sum(r["clicks"] for r in rows)
        w = [(r["avg_position"], r["impressions"]) for r in rows if r["avg_position"] is not None]
        entries.append({"url": url, "impressions": imps, "clicks": clicks,
                        "ctr": round(clicks / imps, 4) if imps else None,
                        "avg_position": page_position(w),
                        "weeks": len({r["date"] for r in rows if r["date"]})})
    total = total if total is not None else sum(e["impressions"] for e in entries)
    for e in entries:
        e["share"] = round(e["impressions"] / total, 4) if total else None
    entries.sort(key=lambda e: (-e["impressions"], e["url"]))
    return entries


def _query_rows(key, site, q):
    """GetQueryPageStats -> ({url: weekly rows with query=q}, error). The page URL
    arrives in a field named `Query`, exactly as in GetPageStats."""
    r = call("GetQueryPageStats", key, siteUrl=site, query=q)
    if not r["ok"]:
        return None, r
    per_url = {}
    for row in _weekly(r["d"] or []):
        per_url.setdefault(row["query"], []).append(dict(row, query=q))
    return per_url, None


def querypages(key, site, q, limit, min_share=CANNIBAL_MIN_SHARE):
    """GetQueryPageStats - EVERY URL of the site that drew impressions for one query.

    The other direction from `pagequeries`, and the one `cannibal` was missing:
    `cannibal` can only see the pages it was told to measure, so a URL outside the
    set never appears in a split. This asks the query and gets every URL back.

    Measured 2026-10-18: 47 weekly rows for a head query, the page URL in a field
    named `Query` (Bing's naming, same trap as GetPageStats).
    """
    per_url, err = _query_rows(key, site, q)
    if err:
        return err
    entries = _url_entries(per_url)
    out = {"ok": True, "site": site, "query": q, "url_count": len(entries),
           "impressions": sum(e["impressions"] for e in entries),
           "clicks": sum(e["clicks"] for e in entries),
           "urls": entries[:limit]}
    if not entries:
        out["empty_means"] = ("no URL of this site drew impressions for this exact string in "
                              "Bing's window. NOT 'we do not rank' - Bing omits low-volume "
                              "queries and matches the string exactly. Find Bing's wording "
                              "with `queries --contains <word>`.")
        return out
    core = _cannibal_core(per_url, None, min_share, 1)
    finding = core["findings"][0] if core["findings"] else None
    leaders, changes = _leaders(per_url)
    out.update({
        "flags": finding["flags"] if finding else [],
        "split_urls": finding["split_urls"] if finding else [],
        "best_position": min((e["avg_position"] for e in entries if e["avg_position"]), default=None),
        "leader_changes": changes, "weekly_leaders": leaders,
        "window_weeks": _span([r for rows in per_url.values() for r in rows]),
        "reading": ("every URL Bing showed for this query, by impressions. `split` = two or "
                    f"more URLs at >= {min_share:.0%} share each; `edge_of_page` = the best "
                    f"URL sits at positions {EDGE_OF_PAGE[0]}-{EDGE_OF_PAGE[1]}; "
                    "`leader_changes` counts weeks where a different URL led."),
    })
    return out


def querypagedetail(key, site, q, page):
    """GetQueryPageDetailStats - the POSITION DISTRIBUTION for one query on one page.

    Every other endpoint gives an average position, which hides the shape: a page
    that is #3 half the time and #15 the other half averages #9 and reads as
    "edge of page 1" when it is really a page-1 result that keeps falling off.
    Measured 2026-10-18: 39 rows for a query x page, several rows per DAY - one per
    position the page was shown at, each with its own impressions and clicks.
    """
    r = call("GetQueryPageDetailStats", key, siteUrl=site, query=q, page=page)
    if not r["ok"]:
        return r
    rows = [{"date": _dotnet_date(x.get("Date")), "position": _pos(x.get("Position")),
             "impressions": x.get("Impressions") or 0, "clicks": x.get("Clicks") or 0}
            for x in r["d"] or []]
    out = {"ok": True, "site": site, "query": q, "page": page, "rows": len(rows)}
    if not rows:
        out["empty_means"] = ("no detail rows for this query on this page. Either the page did "
                              "not show for it, or either string differs from Bing's record "
                              "(a trailing slash is enough) - confirm both with `querypages`.")
        return out

    hist, days = {}, {}
    for x in rows:
        if x["position"] is not None:
            b = int(round(x["position"]))
            h = hist.setdefault(b, {"position": b, "impressions": 0, "clicks": 0})
            h["impressions"] += x["impressions"]
            h["clicks"] += x["clicks"]
        d = days.setdefault(x["date"], {"date": x["date"], "impressions": 0, "clicks": 0, "_w": []})
        d["impressions"] += x["impressions"]
        d["clicks"] += x["clicks"]
        if x["position"] is not None:
            d["_w"].append((x["position"], x["impressions"]))
    series = []
    for d in sorted(days.values(), key=lambda d: (d["date"] is None, d["date"] or "")):
        w = d.pop("_w")
        d["avg_position"] = page_position(w)
        d["best"] = min((p for p, _ in w), default=None)
        d["worst"] = max((p for p, _ in w), default=None)
        series.append(d)

    ranked = sum(h["impressions"] for h in hist.values())
    on_p1 = sum(h["impressions"] for b, h in hist.items() if b <= 10)
    top3 = sum(h["impressions"] for b, h in hist.items() if b <= 3)
    out.update({
        "impressions": sum(x["impressions"] for x in rows),
        "clicks": sum(x["clicks"] for x in rows),
        "avg_position": page_position([(x["position"], x["impressions"]) for x in rows
                                       if x["position"] is not None]),
        "page1_share": round(on_p1 / ranked, 4) if ranked else None,
        "top3_share": round(top3 / ranked, 4) if ranked else None,
        "histogram": [hist[b] for b in sorted(hist)],
        "days": len([d for d in series if d["date"]]),
        "series": series,
        "reading": ("`histogram` is impressions by position across the window; `page1_share` the "
                    "fraction shown at 1-10. A page-1 average with a low page1_share is a page "
                    "that keeps dropping off, which the average alone cannot show."),
    })
    return out


def querytraffic(key, site, q):
    """GetQueryTrafficStats - one query's clicks and impressions for the whole SITE, by DAY.

    The daily counterpart to `queries --q`, which is weekly. Measured 2026-10-18:
    76 daily rows for a head query, Clicks and Impressions only (no position - use
    `querypagedetail` for that). Daily is what dates an algorithm update or a
    deploy to the day rather than to the week.
    """
    r = call("GetQueryTrafficStats", key, siteUrl=site, query=q)
    if not r["ok"]:
        return r
    pts = _dated(r["d"], {"impressions": "Impressions", "clicks": "Clicks"})
    out = {"ok": True, "site": site, "query": q, "days": len(pts), "series": pts}
    if not pts:
        out["empty_means"] = ("no daily rows for this exact string. NOT zero traffic - Bing "
                              "omits low-volume queries and matches exactly. Find Bing's "
                              "wording with `queries --contains <word>`.")
        return out
    dated = [p for p in pts if p["date"]]
    imps = sum(p["impressions"] for p in pts)
    clicks = sum(p["clicks"] for p in pts)
    out.update({
        "range": {"first": dated[0]["date"], "last": dated[-1]["date"]} if dated else None,
        "totals": {"impressions": imps, "clicks": clicks,
                   "ctr": round(clicks / imps, 4) if imps else None},
        "trend_7d": {"impressions": _trend(dated, "impressions", 7),
                     "clicks": _trend(dated, "clicks", 7)},
        "trend_28d": {"impressions": _trend(dated, "impressions", 28),
                      "clicks": _trend(dated, "clicks", 28)},
        "reading": ("one point per day Bing has data for - days with no impressions are "
                    "ABSENT, not zero-filled, so `days` is days with data, not calendar days."),
    })
    return out


def urltraffic(key, site, url):
    """GetUrlTrafficInfo - Bing's per-URL clicks/impressions, cross-examined.

    ⚠ MEASURED 2026-10-18: it returned Clicks 0 / Impressions 0 for the homepage
    and a locale homepage - while GetPageStats and GetQueryPageStats both showed
    those exact URLs with hundreds of impressions a week. A zero here is NOT a
    traffic verdict on its own, so every answer is checked against GetPageStats
    and a disagreement is reported as one rather than as "no traffic".
    """
    r = call("GetUrlTrafficInfo", key, siteUrl=site, url=url)
    if not r["ok"]:
        return r
    d = r["d"] or {}
    out = {"ok": True, "site": site, "url": url, "is_page": d.get("IsPage"),
           "clicks": d.get("Clicks"), "impressions": d.get("Impressions")}
    ps = pages(key, site, 10 ** 9)
    if not ps.get("ok"):
        out["verdict"] = "uncorroborated"
        out["why"] = "the GetPageStats cross-check failed, so a zero cannot be read either way"
        return out
    row = next((p for p in ps["pages"] if p["url"] == url), None)
    out["page_stats"] = ({"impressions": row["impressions"], "clicks": row["clicks"]}
                         if row else None)
    mine = d.get("Impressions") or 0
    theirs = row["impressions"] if row else 0
    if theirs and not mine:
        out["verdict"] = "disagrees"
        out["why"] = ("GetPageStats shows impressions for this exact URL and GetUrlTrafficInfo "
                      "says zero. The same disagreement was measured on 2026-10-18 for every "
                      "URL tried. Read `pages` / `querypages`, not this, for traffic.")
    elif mine and not theirs:
        out["verdict"] = "disagrees"
        out["why"] = "this endpoint reports traffic GetPageStats does not show for the same string"
    elif not row:
        out["verdict"] = "no_page_stats_row"
        out["why"] = ("GetPageStats has no row for this string: either no impressions in its "
                      "window or a URL spelled differently from Bing's record")
    else:
        out["verdict"] = "consistent"
        out["why"] = ("both endpoints report traffic. Their windows differ and are not "
                      "documented, so compare them as ordering, not as equal totals.")
    return out


CHILD_HTTP = {"any": 0, "2xx": 1, "3xx": 2, "301": 4, "302": 8, "4xx": 16, "5xx": 32, "other": 64}
CHILD_CRAWLED = {"any": 0, "week": 1, "2weeks": 2, "3weeks": 4}
CHILD_DISCOVERED = {"any": 0, "week": 1, "month": 2}
CHILD_FLAGS = {"any": 0, "robots": 1, "malware": 2}
CHILD_MAX_PAGES = 20


def children(key, site, url, traffic=False, http="any", crawled="any", discovered="any",
             flags="any", max_pages=CHILD_MAX_PAGES, pace=PAGE_CALL_PACE_S):
    """GetChildrenUrlInfo / GetChildrenUrlTrafficInfo - Bing's Index Explorer, one level.

    The bulk counterpart to `urlinfo`: every child URL Bing holds under a path,
    filterable by HTTP code, crawl date, discovery date and robots/malware flag.

    ⚠ MEASURED 2026-10-18: both returned [] for the site root and for every
    subpath tried, with and without filters, on a site Bing holds thousands of
    pages for - and GetUrlInfo on the root reported TotalChildUrlCount 0. The
    endpoint answers (no error) and says nothing. So an empty result is checked
    against crawlstats' in_index and reported UNKNOWN when Bing holds pages, never
    as "nothing under this path". GetChildrenUrlInfo is POST-only (405 on GET).
    """
    filters = {"CrawlDateFilter": CHILD_CRAWLED[crawled],
               "DiscoveredDateFilter": CHILD_DISCOVERED[discovered],
               "DocFlagsFilters": CHILD_FLAGS[flags],
               "HttpCodeFilters": CHILD_HTTP[http]}
    rows, seen, read, stopped = [], set(), 0, None
    for page in range(max_pages):
        if page and pace:
            _sleep(pace)
        if traffic:
            r = call("GetChildrenUrlTrafficInfo", key, siteUrl=site, url=url, page=page)
        else:
            r = post("GetChildrenUrlInfo", key, {"siteUrl": site, "url": url, "page": page,
                                                 "filterProperties": filters})
        if not r["ok"]:
            if page == 0:
                return r
            stopped = {"page": page, **{k: v for k, v in r.items() if k != "ok"}}
            break
        read += 1
        batch = [x for x in r["d"] or [] if x.get("Url") and x["Url"] not in seen]
        if not batch:
            break
        for x in batch:
            seen.add(x["Url"])
            if traffic:
                rows.append({"url": x["Url"], "is_page": x.get("IsPage"),
                             "impressions": x.get("Impressions"), "clicks": x.get("Clicks")})
            else:
                rows.append({"url": x["Url"], "is_page": x.get("IsPage"),
                             "discovered": _dotnet_date(x.get("DiscoveryDate")),
                             "last_crawled": _dotnet_date(x.get("LastCrawledDate")),
                             "document_size": x.get("DocumentSize"),
                             "anchor_count": x.get("AnchorCount"),
                             "child_urls": x.get("TotalChildUrlCount")})
    else:
        stopped = {"page": max_pages, "error": "max_pages reached - raise --max-pages for more"}

    filtered = {k: v for k, v in {"http": http, "crawled": crawled, "discovered": discovered,
                                   "flags": flags}.items() if v != "any"}
    out = {"ok": True, "site": site, "url": url, "kind": "traffic" if traffic else "index",
           "filters": filtered, "pages_read": read, "count": len(rows), "children": rows}
    if stopped:
        out["stopped"] = stopped
    if rows:
        return out

    stats = crawlstats(key, site)
    # The LAST row's value, not latest_stock: the column-kind classifier can call
    # in_index a flow (measured 2026-10-18: 2 dips in 71 days), which drops it from
    # latest_stock - and the latest point-in-time value is right either way.
    held = ((stats.get("series") or [{}])[-1].get("in_index")) if stats.get("ok") else None
    if held and not filtered:
        out["verdict"] = "unknown"
        out["why_not_none"] = (f"Bing holds {held} pages for this site (crawlstats in_index) and "
                               "this endpoint returned no children with no filter applied. Measured "
                               "2026-10-18: the same empty answer for every path tried. The "
                               "instrument is not answering; use `urlinfo` per URL or `feeds` "
                               "for coverage.")
    elif held:
        out["verdict"] = "empty_under_filter"
        out["why"] = ("nothing matched the filter. The endpoint also returns nothing UNFILTERED "
                      "on the account measured, so re-run without filters before reading this "
                      "as 'no URL has that property'.")
    else:
        out["verdict"] = "unknown" if not stats.get("ok") else "empty"
        out["why"] = ("Bing reports no indexed pages for this site, so an empty child list "
                      "agrees" if stats.get("ok") else "the in_index cross-check failed")
    return out


def feeddetails(key, site, feed):
    """GetFeedDetails - a sitemap INDEX as Bing holds it, child by child.

    `feeds` shows the top-level feed. This shows each child sitemap Bing read
    from it, with its own count, status and last crawl - which is where a stale
    or failing child hides. Measured 2026-10-18: 14 rows for a sitemap index,
    the index itself first (UrlCount 8,299) then each child (1,000 each).
    """
    r = call("GetFeedDetails", key, siteUrl=site, feedUrl=feed)
    if not r["ok"]:
        return r
    rows = [_feed_row(x) for x in r["d"] or []]
    out = {"ok": True, "site": site, "feed": feed, "count": len(rows)}
    if not rows:
        out["empty_means"] = ("Bing holds no feed at this exact URL. Compare against `feeds`: "
                              "if it lists the feed, the string differs; if not, Bing has never "
                              "read it - `submitfeed` hands it over.")
        return out
    index = next((x for x in rows if x["url"] == feed), None)
    kids = [x for x in rows if x["url"] != feed]
    out["feed_row"] = index
    out["children"] = kids
    if kids:
        total = sum(x["url_count_per_bing"] or 0 for x in kids)
        crawled = sorted(x["last_crawled"] for x in kids if x["last_crawled"])
        out["children_summary"] = {
            "count": len(kids), "url_count_sum": total,
            "not_success": [x["url"] for x in kids if (x["status"] or "").lower() != "success"],
            "oldest_crawl": crawled[0] if crawled else None,
            "newest_crawl": crawled[-1] if crawled else None,
        }
        if index and isinstance(index.get("url_count_per_bing"), int):
            out["children_summary"]["index_url_count"] = index["url_count_per_bing"]
            out["children_summary"]["index_vs_children_delta"] = index["url_count_per_bing"] - total
        out["reading"] = ("`oldest_crawl` is the child Bing has gone longest without re-reading - "
                          "a URL added there is invisible to Bing until it does. A non-zero "
                          "index_vs_children_delta means the index count and its children were "
                          "read at different times.")
    return out


def _held_feed(key, site, feed):
    """(row, error) - the feed as Bing holds it, top-level or as a child of an index."""
    r = call("GetFeeds", key, siteUrl=site)
    if not r["ok"]:
        return None, r
    for x in r["d"] or []:
        if x.get("Url") == feed:
            return _feed_row(x), None
    for x in r["d"] or []:
        if (x.get("Type") or "").lower().startswith("sitemap index"):
            d = call("GetFeedDetails", key, siteUrl=site, feedUrl=x.get("Url"))
            for y in (d.get("d") or []) if d.get("ok") else []:
                if y.get("Url") == feed:
                    return dict(_feed_row(y), parent=x.get("Url")), None
    return None, None


def _live_feed(url):
    """(status, kind, error) for the live feed: kind is urlset / sitemapindex / rss / None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            status, body = r.status, r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, None, None
    except Exception as e:
        return None, None, str(e)
    kind = next((k for k in ("sitemapindex", "urlset", "rss", "feed") if f"<{k}" in body), None)
    return status, kind, None


def submitfeed(key, site, feed, confirm, root=None):
    """SubmitFeed - hand Bing a sitemap and ask it to re-read it.

    The sitemap counterpart of `submit`, and the Bing half of what `gsc.py
    sitemap-submit` does for Google. IndexNow announces URLs; this makes Bing
    re-read the FEED, which is what updates `feeds`' UrlCount.

    Guards, all before the call: off-site feed refused (measured: Bing refuses it
    with AuthorizationFailed), and the live feed must answer 200 and parse as a
    sitemap - submitting a 404 inside a deploy window points Bing at a dead feed.
    DRY RUN until --yes. Confirmed by reading GetFeeds back.
    """
    if not is_on_site(site, feed):
        return {"ok": False, "error": "feed_off_site",
                "detail": f"{feed} is not on {site}; Bing refuses it whole (AuthorizationFailed)"}
    status, kind, err = _live_feed(feed)
    plan = {"ok": True, "site": site, "feed": feed, "submitted": False,
            "live": {"status": status, "kind": kind, **({"error": err} if err else {})}}
    if status != 200 or kind is None:
        plan["ok"] = False
        plan["error"] = "feed_not_healthy"
        plan["detail"] = ("the live feed must return 200 and parse as a sitemap/feed before Bing "
                          "is pointed at it")
        return plan
    before, e = _held_feed(key, site, feed)
    if e:
        return e
    plan["held_before"] = before
    if not confirm:
        plan["dry_run"] = True
        plan["what_to_do"] = "re-run with --yes to submit"
        return plan

    r = post("SubmitFeed", key, {"siteUrl": site, "feedUrl": feed})
    if not r["ok"]:
        plan["ok"] = False
        plan.update({k: v for k, v in r.items() if k != "ok"})
        return plan
    plan["submitted"] = True
    after, _ = _held_feed(key, site, feed)
    plan["held_after"] = after
    today = datetime.now(timezone.utc).strftime(DATE_FMT)
    if after and after.get("submitted") == today:
        plan["verified"] = "confirmed"
        plan["verified_means"] = "GetFeeds now carries today's submission date for this feed"
    elif after:
        plan["verified"] = "held_not_redated"
        plan["verified_means"] = ("Bing holds the feed but its submission date did not move to "
                                  "today. The call returns null whatever happened, so re-check "
                                  "`feeds` in a few minutes before re-submitting.")
    else:
        plan["verified"] = "not_held"
        plan["verified_means"] = ("the call succeeded and Bing does not list the feed yet. It may "
                                  "lag; re-check `feeds` before re-submitting.")
    plan["receipt"] = receipt(root, {"action": "SubmitFeed", "site": site, "feed": feed,
                                     "verified": plan["verified"]})
    return plan


def removefeed(key, site, feed, confirm, root=None):
    """RemoveFeed - tell Bing to forget a sitemap.

    Refused unless Bing currently holds the feed: RemoveFeed returns null for a
    feed it never had (measured 2026-10-18), so "removed" would be unprovable. A
    feed still referenced from robots.txt will be re-discovered - removing it
    here is only half the job. DRY RUN until --yes; confirmed by reading back.
    """
    if not is_on_site(site, feed):
        return {"ok": False, "error": "feed_off_site", "detail": f"{feed} is not on {site}"}
    before, e = _held_feed(key, site, feed)
    if e:
        return e
    if not before:
        return {"ok": False, "error": "feed_not_held",
                "detail": ("Bing holds no feed at this exact URL, and RemoveFeed answers a no-op "
                           "with the same null as a removal - nothing to remove, nothing sent.")}
    plan = {"ok": True, "site": site, "feed": feed, "removed": False, "held_before": before}
    if before.get("parent"):
        plan["warning"] = (f"this is a CHILD of {before['parent']}. Bing will re-read it from the "
                           "index; remove it from the index file instead.")
    if not confirm:
        plan["dry_run"] = True
        plan["what_to_do"] = "re-run with --yes to remove"
        return plan
    r = post("RemoveFeed", key, {"siteUrl": site, "feedUrl": feed})
    if not r["ok"]:
        plan["ok"] = False
        plan.update({k: v for k, v in r.items() if k != "ok"})
        return plan
    plan["removed"] = True
    after, _ = _held_feed(key, site, feed)
    plan["verified"] = "confirmed" if after is None else "still_held"
    plan["robots_note"] = ("a Sitemap: line in robots.txt re-introduces it on Bing's next read "
                           "of robots.txt")
    plan["receipt"] = receipt(root, {"action": "RemoveFeed", "site": site, "feed": feed,
                                     "verified": plan["verified"]})
    return plan


# dynamicServing, per Bing's Content Submission API documentation.
DYNAMIC_SERVING = {"none": 0, "pc": 1, "mobile": 2, "amp": 3, "tablet": 4, "nonvisual": 5}
# Headers that describe the WIRE encoding of the fetched bytes. urllib hands back
# the body decoded, so passing these through would tell Bing the body is still
# compressed or chunked when it is not.
WIRE_HEADERS = {"content-encoding", "transfer-encoding", "content-length", "connection",
                "keep-alive", "alt-svc", "report-to", "nel", "set-cookie"}


def build_http_message(status, reason, headers, body):
    """The raw HTTP response SubmitContent wants, before base64: status line,
    headers (wire-encoding ones dropped, Content-Length recomputed), blank line, body."""
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{k}: {v}" for k, v in headers if k.lower() not in WIRE_HEADERS]
    lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace") + body


def submitcontent(key, site, url, dynamic, confirm, root=None):
    """SubmitContent - push a page's full HTTP response to Bing, no crawl needed.

    The live page is fetched here and sent as Bing would have received it. merj's
    client marks the method deprecated; measured 2026-10-18 the endpoint still
    answers (NotAuthorized for an unowned site, not 404) and the content quota
    reads 100/day. DRY RUN until --yes. There is no read-back for submitted
    content, so the quota drop is the only proof it was counted.
    """
    if not is_on_site(site, url):
        return {"ok": False, "error": "url_off_site", "detail": f"{url} is not on {site}"}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            status, reason = r.status, r.reason
            headers, body = list(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "page_not_200", "status": e.code,
                "detail": "only a live 200 is worth submitting as content"}
    except Exception as e:
        return {"ok": False, "error": "page_unreachable", "detail": str(e)}
    msg = build_http_message(status, reason, headers, body)
    q = quota(key, site)
    left = (q.get("content_submission") or {}).get("daily") if q.get("ok") else None
    plan = {"ok": True, "site": site, "url": url, "submitted": False,
            "status": status, "body_bytes": len(body), "message_bytes": len(msg),
            "dynamic_serving": dynamic, "content_quota_daily": left}
    if left == 0:
        plan["ok"] = False
        plan["error"] = "content_quota_exhausted"
        return plan
    if not confirm:
        plan["dry_run"] = True
        plan["headers_sent"] = [k for k, _ in headers if k.lower() not in WIRE_HEADERS]
        plan["what_to_do"] = "re-run with --yes to submit"
        return plan
    r = post("SubmitContent", key, {"siteUrl": site, "url": url,
                                    "httpMessage": base64.b64encode(msg).decode("ascii"),
                                    "structuredData": "",
                                    "dynamicServing": DYNAMIC_SERVING[dynamic]})
    if not r["ok"]:
        plan["ok"] = False
        plan.update({k: v for k, v in r.items() if k != "ok"})
        return plan
    plan["submitted"] = True
    a = quota(key, site)
    after = (a.get("content_submission") or {}).get("daily") if a.get("ok") else None
    plan["content_quota_after"] = after
    if isinstance(left, int) and isinstance(after, int):
        plan["quota_spent"] = left - after
    plan["receipt"] = receipt(root, {"action": "SubmitContent", "site": site, "url": url,
                                     "bytes": len(msg), "quota_before": left,
                                     "quota_after": after})
    return plan


class _Facts(HTMLParser):
    """The SEO-relevant facts of one HTML document - what bingbot was served."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.f = {"title": None, "lang": None, "meta_robots": [], "canonical": [],
                  "hreflang": 0, "h1": 0, "meta_description": False}
        self._in_title = False
        self._title = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.f["lang"] = a["lang"]
        elif tag == "title" and self.f["title"] is None:
            self._in_title = True
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name in ("robots", "bingbot"):
                self.f["meta_robots"].append(f"{name}: {a.get('content', '')}")
            elif name == "description" and a.get("content", "").strip():
                self.f["meta_description"] = True
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if "canonical" in rel:
                self.f["canonical"].append(a.get("href"))
            if "alternate" in rel and a.get("hreflang"):
                self.f["hreflang"] += 1
        elif tag == "h1":
            self.f["h1"] += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.f["title"] = " ".join("".join(self._title).split())

    def handle_data(self, data):
        if self._in_title:
            self._title.append(data)


def html_facts(body):
    p = _Facts()
    try:
        p.feed(body or "")
        p.close()
    except Exception as e:
        p.f["parse_error"] = str(e)
    p.f["body_chars"] = len(body or "")
    return p.f


def _header_lines(block):
    out = []
    for ln in block.split("\r\n"):
        if ":" in ln:
            k, v = ln.split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


def parse_fetched(doc):
    """GetFetchedUrlDetails.Document -> crawler record, HTTP response, body facts.

    Measured 2026-10-18, the Document is three blocks separated by blank lines:
      1. the crawler's own record - Last-DownLoad-Http-Code, IP-Address,
         Truncation, Robots-Rule, Download-Status, SFSSendUserAgent, ...
      2. the HTTP status line and response headers bingbot received
      3. the body, already DECODED even where the headers say Content-Encoding: br
    """
    if not isinstance(doc, str) or not doc:
        return {"parsed": False, "why": "empty document"}
    parts = doc.split("\r\n\r\n", 2)
    if len(parts) < 2 or not parts[1].startswith("HTTP/"):
        return {"parsed": False, "why": "unrecognised shape", "head": doc[:200]}
    meta = dict(_header_lines(parts[0]))
    status_line, _, hblock = parts[1].partition("\r\n")
    headers = _header_lines(hblock)
    body = parts[2] if len(parts) > 2 else ""
    m = re.match(r"HTTP/\S+\s+(\d+)", status_line)
    hv = lambda name: [v for k, v in headers if k.lower() == name]
    facts = html_facts(body) if "html" in " ".join(hv("content-type")).lower() or "<html" in body[:2000].lower() else {}
    x_robots = hv("x-robots-tag")
    robots_all = " ".join(x_robots + facts.get("meta_robots", [])).lower()
    link_canon = [re.search(r"<([^>]+)>", v).group(1) for v in hv("link")
                  if "canonical" in v.lower() and re.search(r"<([^>]+)>", v)]
    return {
        "parsed": True,
        "crawler": {
            "http_code": int(meta["Last-DownLoad-Http-Code"]) if meta.get("Last-DownLoad-Http-Code", "").isdigit() else meta.get("Last-DownLoad-Http-Code"),
            "ip": meta.get("IP-Address"),
            "user_agent": meta.get("SFSSendUserAgent"),
            "download_ms": int(meta["SFSDownloadDuration"]) if meta.get("SFSDownloadDuration", "").isdigit() else None,
            "document_length": int(meta["Document-Length"]) if meta.get("Document-Length", "").isdigit() else None,
            "truncated": meta.get("Truncation") not in (None, "0"),
            "robots_rule": meta.get("Robots-Rule"),
            "download_status": meta.get("Download-Status"),
            "crawl_region": meta.get("SFSCrawlRegion"),
            "redirect_policy": meta.get("SFSRedirectPolicy"),
        },
        "response": {
            "status": int(m.group(1)) if m else None,
            "status_line": status_line,
            "content_type": (hv("content-type") or [None])[0],
            "location": (hv("location") or [None])[0],
            "x_robots_tag": x_robots,
            "link_canonical": link_canon,
            "headers": [k for k, _ in headers],
        },
        "body": facts,
        "noindex": "noindex" in robots_all,
        "nofollow": "nofollow" in robots_all,
    }


def _fetched_verdict(p):
    problems = []
    if not p.get("parsed"):
        return "unreadable", ["the fetched document did not parse"]
    st = p["response"]["status"]
    if st != 200:
        problems.append(f"bingbot got HTTP {st}")
    if p["noindex"]:
        problems.append("noindex served to bingbot (meta robots or X-Robots-Tag)")
    if p["crawler"]["truncated"]:
        problems.append("Bing truncated the document")
    if p["crawler"]["robots_rule"] not in (None, "0"):
        problems.append(f"Robots-Rule {p['crawler']['robots_rule']} (only 0 has been measured, "
                        "on an allowed page)")
    canon = p["body"].get("canonical") or [] if p.get("body") else []
    if len(set(canon)) > 1:
        problems.append(f"{len(set(canon))} different canonical tags")
    return ("problem" if problems else "clean"), problems


def _live_view(url):
    """The page as an ordinary client sees it, for comparison with bingbot's copy.
    Not a bingbot user agent on purpose: a CDN's verified-bot check blocks a forged
    one, and that block would read as the SITE cloaking."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            return {"status": r.status, "x_robots_tag": r.headers.get_all("X-Robots-Tag") or [],
                    "body": html_facts(body)}
    except urllib.error.HTTPError as e:
        return {"status": e.code}
    except Exception as e:
        return {"error": str(e)}


def compare_views(bot, live):
    """Structural differences between bingbot's copy and the live one. Body length
    is compared as a ratio with a wide band, because a nonce or a timestamp makes
    two fetches of the same page differ in bytes."""
    if "error" in live:
        return {"compared": False, "why": live["error"]}
    diffs = []
    if bot["response"]["status"] != live.get("status"):
        diffs.append({"field": "status", "bingbot": bot["response"]["status"], "live": live.get("status")})
    b, l_ = bot.get("body") or {}, live.get("body") or {}
    for f in ("title", "lang", "canonical", "hreflang", "h1", "meta_robots", "meta_description"):
        if b.get(f) != l_.get(f):
            diffs.append({"field": f, "bingbot": b.get(f), "live": l_.get(f)})
    if bot["response"]["x_robots_tag"] != live.get("x_robots_tag", []):
        diffs.append({"field": "x_robots_tag", "bingbot": bot["response"]["x_robots_tag"],
                      "live": live.get("x_robots_tag")})
    bc, lc = b.get("body_chars") or 0, l_.get("body_chars") or 0
    if bc and lc and not (0.8 <= bc / lc <= 1.25):
        diffs.append({"field": "body_chars", "bingbot": bc, "live": lc,
                      "note": "more than 20% apart - a different template, an error page or a block"})
    return {"compared": True, "differs": bool(diffs), "differences": diffs}


FETCH_POLL_S = 5
FETCH_WAIT_S = 90
FETCH_RETRY_CODES = (1, 2)


def same_fetch_url(held, asked):
    """Bing's fetch history is keyed slash-insensitively. Measured 2026-10-18:
    fetching /zh/ then /zh left ONE row, `/zh`, and GetFetchedUrlDetails for
    /zh/ returned that /zh fetch. An exact match would wait forever for a row
    that was folded into its twin."""
    return bool(held) and held.rstrip("/") == (asked or "").rstrip("/")


def fetched(key, site, url=None):
    """GetFetchedUrls / GetFetchedUrlDetails - the fetch-as-bingbot history.

    With no url: every URL this account asked Bing to fetch, and whether it did.
    With a url: what bingbot was served on the latest fetch, parsed. Measured
    2026-10-18: details for a URL never fetched come back d: null - reported as
    `never_fetched`, not as an empty page.
    """
    if not url:
        r = call("GetFetchedUrls", key, siteUrl=site)
        if not r["ok"]:
            return r
        rows = [{"url": x.get("Url"), "date": _dotnet_date(x.get("Date")),
                 "fetched": x.get("Fetched"), "expired": x.get("Expired")} for x in r["d"] or []]
        out = {"ok": True, "site": site, "count": len(rows), "fetched": rows}
        if not rows:
            out["empty_means"] = "this account has not asked Bing to fetch anything (or it expired)"
        return out
    r = call("GetFetchedUrlDetails", key, siteUrl=site, url=url)
    if not r["ok"]:
        return r
    d = r["d"]
    if not d:
        return {"ok": True, "site": site, "url": url, "state": "never_fetched",
                "what_to_do": "run `bing.py fetch --url <url>` to have bingbot fetch it now"}
    p = parse_fetched(d.get("Document"))
    verdict, problems = _fetched_verdict(p)
    return {"ok": True, "site": site, "url": url, "state": d.get("Status"),
            "fetched_at": _dotnet_date(d.get("Date")), "verdict": verdict,
            "problems": problems, **{k: v for k, v in p.items() if k != "parsed"}}


def fetch(key, site, url, wait=FETCH_WAIT_S, live=False, root=None):
    """FetchUrl - have bingbot fetch one URL NOW, then read what it was served.

    The Bing counterpart of a live URL Inspection, and the only way to see the
    page as bingbot gets it: a CDN, a bot rule or a geo redirect can serve it
    something different from what a browser gets. Measured 2026-10-18: the fetch
    completed within 10 seconds, from a US proxy, as `bingbot/2.0`. It changes no
    index state, so it is not gated behind --yes. `--live` also fetches the page
    normally and lists the structural differences.
    """
    if not is_on_site(site, url):
        return {"ok": False, "error": "url_off_site", "detail": f"{url} is not on {site}"}
    asked = time.time()
    r = post("FetchUrl", key, {"siteUrl": site, "url": url})
    if not r["ok"] and r.get("error_code") in FETCH_RETRY_CODES:
        # Measured 2026-10-18: one UnknownError on a URL the same call accepted a
        # minute later. A fetch changes nothing, so repeating it is safe.
        _sleep(FETCH_POLL_S)
        r = post("FetchUrl", key, {"siteUrl": site, "url": url})
    if not r["ok"]:
        return r
    receipt(root, {"action": "FetchUrl", "site": site, "url": url})
    deadline = asked + wait
    done = False
    while time.time() < deadline:
        _sleep(FETCH_POLL_S)
        lst = call("GetFetchedUrls", key, siteUrl=site)
        for x in (lst.get("d") or []) if lst.get("ok") else []:
            m = re.search(r"\((-?\d+)", x.get("Date") or "")
            # Bing's clock and ours: two minutes of slack, so a fresh fetch is not
            # mistaken for a stale one and vice versa.
            fresh = m and int(m.group(1)) / 1000 >= asked - 120
            if same_fetch_url(x.get("Url"), url) and x.get("Fetched") and fresh:
                done = True
        if done:
            break
    if not done:
        return {"ok": False, "error": "fetch_pending", "site": site, "url": url,
                "detail": (f"Bing accepted the fetch request but had not completed it after "
                           f"{wait}s. Read it later with `bing.py fetched --url {url}`.")}
    out = fetched(key, site, url)
    if out.get("ok") and live and out.get("response"):
        out["live_comparison"] = compare_views(out, _live_view(url))
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
    sb.add_argument("--root", default=".", help="site repo; a receipt goes to <root>/.seo/bing.jsonl if .seo/ exists")
    sf = sub.add_parser("submitfeed", help="SubmitFeed - make Bing re-read a sitemap (DRY RUN by default)",
                        parents=[common])
    sf.add_argument("--feed", help="absolute sitemap URL (default: <site>sitemap.xml)")
    sf.add_argument("--yes", action="store_true", help="actually submit")
    sf.add_argument("--root", default=".", help="site repo, for the receipt")
    rf = sub.add_parser("removefeed", help="RemoveFeed - make Bing forget a sitemap (DRY RUN by default)",
                        parents=[common])
    rf.add_argument("--feed", required=True, help="absolute sitemap URL, exactly as `feeds` lists it")
    rf.add_argument("--yes", action="store_true", help="actually remove")
    rf.add_argument("--root", default=".", help="site repo, for the receipt")
    fdd = sub.add_parser("feeddetails", help="a sitemap INDEX as Bing holds it, child by child",
                         parents=[common])
    fdd.add_argument("--feed", help="absolute sitemap URL (default: <site>sitemap.xml)")
    sc = sub.add_parser("submitcontent", help="SubmitContent - push a page's HTTP response to Bing "
                        "(DRY RUN by default)", parents=[common])
    sc.add_argument("--url", required=True)
    sc.add_argument("--dynamic-serving", choices=sorted(DYNAMIC_SERVING), default="none",
                    help="which device class this response is for (default: none = all)")
    sc.add_argument("--yes", action="store_true", help="actually submit")
    sc.add_argument("--root", default=".", help="site repo, for the receipt")
    ft = sub.add_parser("fetch", help="FetchUrl - bingbot fetches the URL now; show what it was served",
                        parents=[common])
    ft.add_argument("--url", required=True)
    ft.add_argument("--wait", type=int, default=FETCH_WAIT_S, help="seconds to wait for the fetch")
    ft.add_argument("--live", action="store_true",
                    help="also fetch the page normally and list structural differences")
    ft.add_argument("--root", default=".", help="site repo, for the receipt")
    fh = sub.add_parser("fetched", help="fetch-as-bingbot history, or one URL's last fetch parsed",
                        parents=[common])
    fh.add_argument("--url", help="show what bingbot was served for this URL")
    fh.add_argument("--live", action="store_true", help="with --url: compare against a normal fetch")
    kt = sub.add_parser("keywordtrend", help="one query's Bing impressions week by week", parents=[common])
    kt.add_argument("--q", required=True)
    qp = sub.add_parser("querypages", help="EVERY url of the site that drew impressions for a query",
                        parents=[common])
    qp.add_argument("--q", required=True)
    qp.add_argument("--min-share", type=float, default=CANNIBAL_MIN_SHARE)
    qd = sub.add_parser("querypagedetail", help="position distribution for one query on one page",
                        parents=[common])
    qd.add_argument("--q", required=True)
    qd.add_argument("--page", required=True, help="exact URL as `querypages` reports it")
    qt = sub.add_parser("querytraffic", help="one query's clicks/impressions for the site, by DAY",
                        parents=[common])
    qt.add_argument("--q", required=True)
    ut = sub.add_parser("urltraffic", help="GetUrlTrafficInfo, cross-checked against GetPageStats",
                        parents=[common])
    ut.add_argument("--url", required=True)
    ch = sub.add_parser("children", help="Index Explorer: child URLs Bing holds under a path",
                        parents=[common])
    ch.add_argument("--url", help="parent path (default: the site root)")
    ch.add_argument("--traffic", action="store_true", help="clicks/impressions per child instead of index facts")
    ch.add_argument("--http", choices=sorted(CHILD_HTTP), default="any")
    ch.add_argument("--crawled", choices=sorted(CHILD_CRAWLED), default="any")
    ch.add_argument("--discovered", choices=sorted(CHILD_DISCOVERED), default="any")
    ch.add_argument("--flags", choices=sorted(CHILD_FLAGS), default="any")
    ch.add_argument("--max-pages", type=int, default=CHILD_MAX_PAGES)
    ch.add_argument("--pace", type=float, default=PAGE_CALL_PACE_S)

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
    cn.add_argument("--query", action="append", default=[],
                    help="also ask Bing for EVERY url ranking for this query (repeatable)")
    cn.add_argument("--top-queries", type=int, default=0,
                    help="also ask that for the site's top N queries by impressions")

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
        print(json.dumps({"ok": False, "state": "no_key",
                          "error": "no BING_WEBMASTER_API_KEY and no ~/.bing_webmaster_key"}))
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

    if a.cmd == "querypages" and not (0 < a.min_share <= 0.5):
        print(json.dumps({"ok": False, "error": "min_share_out_of_range",
                          "detail": "--min-share must be in (0, 0.5]; above 0.5 a split is impossible"}))
        sys.exit(2)

    if a.cmd == "sites":
        out = sites(key)
    elif a.cmd == "keywordtrend":
        out = keywordtrend(key, a.q, a.country, a.language)
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
            out = submit(key, site, a.url, a.from_file, a.yes, a.root)
        elif a.cmd == "submitfeed":
            out = submitfeed(key, site, a.feed or site + "sitemap.xml", a.yes, a.root)
        elif a.cmd == "removefeed":
            out = removefeed(key, site, a.feed, a.yes, a.root)
        elif a.cmd == "feeddetails":
            out = feeddetails(key, site, a.feed or site + "sitemap.xml")
        elif a.cmd == "submitcontent":
            out = submitcontent(key, site, a.url, a.dynamic_serving, a.yes, a.root)
        elif a.cmd == "fetch":
            out = fetch(key, site, a.url, a.wait, a.live, a.root)
        elif a.cmd == "fetched":
            out = fetched(key, site, a.url)
            if a.live and a.url and out.get("response"):
                out["live_comparison"] = compare_views(out, _live_view(a.url))
        elif a.cmd == "querypages":
            out = querypages(key, site, a.q, a.limit, a.min_share)
        elif a.cmd == "querypagedetail":
            out = querypagedetail(key, site, a.q, a.page)
        elif a.cmd == "querytraffic":
            out = querytraffic(key, site, a.q)
        elif a.cmd == "urltraffic":
            out = urltraffic(key, site, a.url)
        elif a.cmd == "children":
            out = children(key, site, a.url or site, a.traffic, a.http, a.crawled, a.discovered,
                           a.flags, a.max_pages, a.pace)
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
                           a.max_findings, a.pace, a.query, a.top_queries)
        else:
            out = {"backlinks": backlinks,
                   "traffic": traffic, "pages": pages}[a.cmd](
                *( (key, site, a.limit)
                   if a.cmd in ("backlinks", "pages") else (key, site) )
            )

    # State the window on every report that has one it did not choose, so a
    # reader who never passed --days still knows the span is Bing's, not theirs.
    if a.cmd in ("queries", "pages", "pagequeries", "traffic", "cannibal", "keywordtrend",
                 "querypages", "querypagedetail", "querytraffic") and out.get("ok"):
        out.setdefault(
            "window",
            "Bing's own fixed reporting window - not selectable here; --days is refused "
            "on this subcommand rather than silently ignored.",
        )

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
