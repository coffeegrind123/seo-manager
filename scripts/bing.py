#!/usr/bin/env python3
"""Bing Webmaster Tools API - the only FREE source of real volume and backlinks.

Everything else in this skill either estimates demand or measures it indirectly.
This is different: for a site you have VERIFIED OWNERSHIP of, Bing hands over
real impression counts and real inbound links, free, with no rate limit worth
worrying about.

  sites      verified sites on this account. Also the auth control - run it
             first when anything else looks wrong.
  keyword    impressions for ONE query
  expand     related keywords WITH impressions - keyword research with numbers
  backlinks  inbound links, by count and by source URL
  queries    the queries this site actually appears for on Bing
  traffic    clicks / impressions / rank over time

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

Read references/data-sources.md before wiring this into a gate.

Stdlib only. Key from BING_WEBMASTER_API_KEY or ~/.bing_webmaster_key (0600).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

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
    common.add_argument("--days", type=int, default=90, help="lookback window (default 90)")
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
    ]:
        sub.add_parser(name, help=helptext, parents=[common])
    k = sub.add_parser("keyword", help="impressions for one query", parents=[common])
    k.add_argument("--q", required=True)
    e = sub.add_parser("expand", help="related keywords WITH impressions", parents=[common])
    e.add_argument("--seed", required=True)

    a = p.parse_args()
    key = read_key()
    if not key:
        print(json.dumps({"ok": False, "error": "no BING_WEBMASTER_API_KEY and no ~/.bing_webmaster_key"}))
        sys.exit(2)

    end = date.today()
    start = end - timedelta(days=a.days)
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
        out = {"backlinks": backlinks, "queries": queries, "traffic": traffic}[a.cmd](
            *( (key, site, a.limit) if a.cmd in ("backlinks", "queries") else (key, site) )
        )

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
