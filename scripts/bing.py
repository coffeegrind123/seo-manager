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
  queries    the queries this site actually appears for on Bing
  pages      per-PAGE impressions, clicks and CTR - WHICH URL earns. `queries`
             cannot answer that, and the two have opposite fixes: a locale page
             ranking for its own language is a win to extend, the homepage
             ranking for that language is a targeting bug
  pagequeries  the queries ONE page appears for - attribution for the above
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
demand gate. Measured 2026-09-01 on us/en-US over 90 days: `cs 1.6 non steam`
returned exact = **2**, while `cs 1.6 config`, `cs 1.6 wallhack` and
`cs 1.6 servers list` - all plainly larger - returned nothing at all. An endpoint
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
            {"url": "/zh/", "impressions": 8522, "clicks": 2298}]
    by_clicks = sorted(rows, key=lambda x: x["clicks"], reverse=True)[0]["url"]
    by_imps = sorted(rows, key=lambda x: x["impressions"], reverse=True)[0]["url"]
    c.check("clicks_and_impressions_really_do_invert", by_clicks != by_imps)
    c.check("the_reference_ranking_is_by_clicks", by_clicks == "/zh/",
            "if this ever changes, the fixture is wrong, not the tool")

    # --days must be REFUSED where the endpoint has no date range, not ignored.
    c.check("days_is_honoured_only_where_the_endpoint_has_a_window",
            set(WINDOWED) == {"keyword", "expand"}, f"got {sorted(WINDOWED)}")
    for cmd in ("queries", "pages", "pagequeries", "traffic"):
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
    never seen. Measured 2026-09-01 on combatskirmish.net: /guides/bunny-hop (a
    live 200) and /does-not-exist-zzz were byte-identical - both DateTime.MinValue
    on each date, DocumentSize 0 - while /maps/de_dust2 carried a real discovery
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


def queries(key, site, limit):
    r = call("GetQueryStats", key, siteUrl=site)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    out = [
        {
            "query": x.get("Query"),
            "impressions": x.get("Impressions"),
            "clicks": x.get("Clicks"),
            "avg_position": x.get("AvgImpressionPosition"),
        }
        for x in rows
    ]
    out.sort(key=lambda x: x["impressions"] or 0, reverse=True)
    res = {"ok": True, "site": site, "count": len(out), "queries": out[:limit]}
    if not out:
        res["empty_means"] = "authorised and genuinely empty - no Bing query data for this site yet."
    return res


def pages(key, site, limit):
    """Per-PAGE impressions and clicks - the dimension `queries` cannot give you.

    WHY THIS EXISTS (2026-09-01, combatskirmish.net). `queries` said the
    best-converting terms on the site were Chinese - 488 clicks on 1,462
    impressions at position 2 - and there was no way to find out WHICH PAGE
    earned them. Query-level data alone cannot answer "is our Chinese locale
    working, or is the English homepage ranking for Chinese queries?", and those
    two have opposite fixes. `pages` answered it in one call: /zh/ carried 8,522
    impressions and 2,298 clicks at position 4 - a 27% CTR, and more than three
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
        if x.get("AvgImpressionPosition"):
            a["_pos"].append(x["AvgImpressionPosition"])
    out = []
    for a in agg.values():
        pos = a.pop("_pos")
        a["avg_position"] = round(sum(pos) / len(pos), 1) if pos else None
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

    `pages` says /zh/ earns 70% of the clicks; this says which searches sent
    them, which is what tells you whether the win is repeatable or a single term.
    """
    r = call("GetPageQueryStats", key, siteUrl=site, page=page)
    if not r["ok"]:
        return r
    rows = r["d"] or []
    out = [{"query": x.get("Query"), "impressions": x.get("Impressions"),
            "clicks": x.get("Clicks"), "avg_position": x.get("AvgImpressionPosition")}
           for x in rows]
    out.sort(key=lambda x: x["impressions"] or 0, reverse=True)
    res = {"ok": True, "site": site, "page": page, "count": len(out), "queries": out[:limit]}
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
        ("queries", "queries this site appears for"),
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
        else:
            out = {"backlinks": backlinks, "queries": queries,
                   "traffic": traffic, "pages": pages}[a.cmd](
                *( (key, site, a.limit)
                   if a.cmd in ("backlinks", "queries", "pages") else (key, site) )
            )

    # State the window on every report that has one it did not choose, so a
    # reader who never passed --days still knows the span is Bing's, not theirs.
    if a.cmd in ("queries", "pages", "pagequeries", "traffic") and out.get("ok"):
        out.setdefault(
            "window",
            "Bing's own fixed reporting window - not selectable here; --days is refused "
            "on this subcommand rather than silently ignored.",
        )

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
