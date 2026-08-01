#!/usr/bin/env python3
"""Keyless demand + trend signals for the seo-manager skill.

Why this exists: the Google Trends API answers HTTP 429 from a datacenter IP
(see references/data-sources.md), so the trend radar had no quantitative
signal at all. Everything here was measured working from inside this
container on 2026-08-01, needs NO key, and needs NO browser.

  trending    Google Trends' "trending now" RSS feed. The RSS surface is NOT
              rate-limited the way the JSON API is - measured 200/21KB while
              the API was still 429ing. Carries approx_traffic bands.
  pageviews   Wikimedia pageviews for a specific article. REAL absolute counts
              (not an index like Trends), daily, with years of history. The
              best free proxy for "is interest in this topic growing?".
  wiki        Resolve a topic string to the Wikipedia article that pageviews
              needs, so you are not guessing at titles.
  discussions Hacker News (Algolia) + StackExchange. Where a niche argues
              about a problem, and how loudly, over time.

WHAT THESE ARE NOT: none of them is a keyword search volume. Wikipedia
pageviews measure interest in a TOPIC, not queries typed at Google, and the
Trends RSS feed reports whatever is spiking nationally, which on most days has
nothing to do with your niche. Both are ordinal, directional signals. Never
report either as a monthly search volume, and never let one satisfy the
quality bar's volume floor - that floor is a data gate that stays inapplicable
until you have real volume data.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TOOL_UA = "seo-manager/1.0 (+https://github.com/; SEO research)"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
HT_NS = "{https://trends.google.com/trending/rss}"


def fetch(url, *, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": TOOL_UA, "Accept": "*/*", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def _fail(where, status, body):
    """A refused read is a refused read. It is never an empty result set.

    The whole point of separating these: 'Trends found nothing in your niche'
    and 'Trends would not answer' lead to opposite decisions, and they look
    identical if you collapse them into an empty list.
    """
    return {
        "ok": False,
        "source": where,
        "status": status,
        "error": f"{where}: HTTP {status}",
        "detail": body[:200],
        "note": "REFUSED, not empty. Do not report this as 'no trends found'.",
    }


# ------------------------------------------------------------------ trending


def trending(geo="US", limit=25):
    """Google Trends 'trending now' RSS. Keyless, and not 429-gated like the API."""
    url = f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(geo)}"
    status, body = fetch(url, headers={"User-Agent": BROWSER_UA})
    if status != 200 or not body.lstrip().startswith("<?xml"):
        return _fail("google-trends-rss", status, body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return _fail("google-trends-rss", status, f"unparseable XML: {e}")

    items = []
    for it in root.findall(".//item")[:limit]:
        traffic = (it.findtext(f"{HT_NS}approx_traffic") or "").strip()
        news = [
            {"title": n.findtext(f"{HT_NS}news_item_title"), "url": n.findtext(f"{HT_NS}news_item_url")}
            for n in it.findall(f"{HT_NS}news_item")[:3]
        ]
        items.append(
            {
                "query": (it.findtext("title") or "").strip(),
                "approx_traffic": traffic or None,
                "traffic_floor": int(re.sub(r"[^0-9]", "", traffic) or 0) if traffic else None,
                "published": it.findtext("pubDate"),
                "picture": it.findtext(f"{HT_NS}picture"),
                "news": [n for n in news if n["title"]],
            }
        )
    return {
        "ok": True,
        "source": "google-trends-rss",
        "geo": geo,
        "count": len(items),
        "items": items,
        "caveat": (
            "This is NATIONAL trending-now, not your niche. Most entries will be sport, politics and "
            "celebrity noise. Filter against your facets before treating anything here as a signal, and "
            "remember approx_traffic is a floor band ('200+'), not a search volume for a keyword."
        ),
    }


# ----------------------------------------------------------------- pageviews


def wiki_search(topic, limit=5, lang="en"):
    """Resolve a topic to real article titles, so pageviews is not fed a guess."""
    qs = urllib.parse.urlencode({"action": "opensearch", "search": topic, "limit": limit, "format": "json"})
    status, body = fetch(f"https://{lang}.wikipedia.org/w/api.php?{qs}")
    if status != 200:
        return _fail("wikipedia-opensearch", status, body)
    try:
        data = json.loads(body)
        return {"ok": True, "source": "wikipedia-opensearch", "query": topic, "titles": data[1], "urls": data[3]}
    except (ValueError, IndexError) as e:
        return _fail("wikipedia-opensearch", status, f"unexpected shape: {e}")


def pageviews(article, days=90, lang="en", project=None):
    """Daily Wikimedia pageviews for one article. Real counts, not an index."""
    project = project or f"{lang}.wikipedia"
    end = datetime.now(timezone.utc).date() - timedelta(days=2)  # the feed lags ~1-2 days
    start = end - timedelta(days=days)
    title = urllib.parse.quote(article.replace(" ", "_"), safe="")
    url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"{project}/all-access/user/{title}/daily/"
        f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    )
    status, body = fetch(url)
    if status == 404:
        return {
            "ok": False,
            "source": "wikimedia-pageviews",
            "status": 404,
            "error": f"no such article '{article}' on {project}",
            "hint": "resolve the exact title first: trendfeeds.py wiki --topic '<topic>'",
        }
    if status != 200:
        return _fail("wikimedia-pageviews", status, body)
    try:
        items = json.loads(body)["items"]
    except (ValueError, KeyError) as e:
        return _fail("wikimedia-pageviews", status, f"unexpected shape: {e}")

    series = [{"date": i["timestamp"][:8], "views": i["views"]} for i in items]
    vals = [s["views"] for s in series]
    half = len(vals) // 2
    first, second = vals[:half], vals[half:]
    avg_a = sum(first) / len(first) if first else 0
    avg_b = sum(second) / len(second) if second else 0
    change = round((avg_b - avg_a) / avg_a * 100, 1) if avg_a else None
    return {
        "ok": True,
        "source": "wikimedia-pageviews",
        "article": article,
        "project": project,
        "days": len(series),
        "total_views": sum(vals),
        "avg_daily": round(sum(vals) / len(vals)) if vals else 0,
        "peak": max(series, key=lambda s: s["views"]) if series else None,
        "first_half_avg": round(avg_a),
        "second_half_avg": round(avg_b),
        "change_pct": change,
        "direction": ("rising" if change > 10 else "falling" if change < -10 else "flat") if change is not None else None,
        "series": series,
        "caveat": "Interest in a TOPIC, not search volume for a keyword.",
    }


# --------------------------------------------------------------- discussions


def discussions(query, limit=10, site="webmasters"):
    """Where a niche argues, and how loudly. HN + StackExchange, both keyless."""
    out = {"ok": True, "query": query, "sources": {}}

    qs = urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": limit})
    status, body = fetch(f"https://hn.algolia.com/api/v1/search?{qs}")
    if status != 200:
        out["sources"]["hackernews"] = _fail("hn-algolia", status, body)
    else:
        try:
            hits = json.loads(body).get("hits", [])
            out["sources"]["hackernews"] = {
                "ok": True,
                "count": len(hits),
                "items": [
                    {
                        "title": h.get("title"),
                        "points": h.get("points"),
                        "comments": h.get("num_comments"),
                        "created": h.get("created_at"),
                        "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                    }
                    for h in hits
                ],
            }
        except ValueError as e:
            out["sources"]["hackernews"] = _fail("hn-algolia", status, str(e))

    qs = urllib.parse.urlencode(
        {"order": "desc", "sort": "votes", "q": query, "site": site, "pagesize": limit, "filter": "default"}
    )
    status, body = fetch(f"https://api.stackexchange.com/2.3/search/advanced?{qs}")
    if status != 200:
        out["sources"]["stackexchange"] = _fail("stackexchange", status, body)
    else:
        try:
            data = json.loads(body)
            out["sources"]["stackexchange"] = {
                "ok": True,
                "site": site,
                "quota_remaining": data.get("quota_remaining"),
                "count": len(data.get("items", [])),
                "items": [
                    {
                        "title": i.get("title"),
                        "score": i.get("score"),
                        "views": i.get("view_count"),
                        "answered": i.get("is_answered"),
                        "tags": i.get("tags"),
                        "url": i.get("link"),
                    }
                    for i in data.get("items", [])
                ],
            }
        except ValueError as e:
            out["sources"]["stackexchange"] = _fail("stackexchange", status, str(e))

    ok = [s for s in out["sources"].values() if s.get("ok")]
    if not ok:
        out["ok"] = False
        out["error"] = "every discussion source refused - this is not evidence the niche is quiet"
    return out


def newsvolume(query, months=3, retries=2):
    """GDELT: how much NEWS COVERAGE a topic got, day by day, keyless.

    This fills the gap the rest of the file leaves. Google Trends' RSS answers
    "what is trending RIGHT NOW" and nothing else; Wikimedia pageviews answer
    "how much attention does this ARTICLE get" and only for topics with an
    article. GDELT answers "how much has the press written about this PHRASE,
    every day, for months" - which is the shape you need to tell a topic that
    is genuinely rising from one that had a single spike.

    ⚠ It is COVERAGE, not search demand. A press-driven spike and a
    people-are-searching spike are different things, and this measures the
    first. Treat a rise as a REASON to check demand, never as demand itself.

    ⚠ It 429s under shared-IP load and then answers fine seconds later
    (measured: first call 429, retry 200 with 86 datapoints). A single-shot
    call reports a permanent failure that is really a transient one, so this
    retries by default.
    """
    q = query if query.startswith('"') else f'"{query}"'
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?"
           + urllib.parse.urlencode({"query": q, "mode": "timelinevol",
                                     "format": "json", "timespan": f"{months}m"}))
    status = body = None
    for attempt in range(retries + 1):
        status, body = fetch(url, timeout=45)
        if status == 200:
            break
        if attempt < retries:
            time.sleep(5 * (attempt + 1))
    if status != 200:
        return _fail("gdelt", status, body)
    try:
        timeline = (json.loads(body) or {}).get("timeline") or []
    except json.JSONDecodeError:
        return _fail("gdelt", status, body)
    if not timeline:
        return {"ok": True, "source": "gdelt", "query": query, "points": 0, "series": [],
                "empty_means": "GDELT answered but has no coverage for this phrase in the "
                               "window. That is a real answer - a phrase the press has not "
                               "covered - not a failed read."}
    data = timeline[0].get("data") or []
    series = [{"date": (d.get("date") or "")[:10], "value": d.get("value")} for d in data]
    values = [d["value"] for d in series if isinstance(d.get("value"), (int, float))]
    half = len(values) // 2
    first_half = sum(values[:half]) / half if half else 0.0
    second_half = sum(values[half:]) / (len(values) - half) if len(values) - half else 0.0
    peak = max(series, key=lambda d: d.get("value") or 0) if series else None
    return {
        "ok": True, "source": "gdelt", "query": query, "months": months,
        "points": len(series),
        "peak": peak,
        "recent_vs_earlier": round(second_half - first_half, 4),
        "direction": ("rising" if second_half > first_half * 1.2 else
                      "falling" if second_half < first_half * 0.8 else "flat"),
        "series": series,
        "means": "share of monitored world news mentioning the phrase, per day. COVERAGE, "
                 "not search demand - use a rise as a prompt to check demand, never as demand.",
    }


def news(query, limit=25, hl="en-US", gl="US"):
    """Google News RSS: who is covering a topic right now, keyless.

    Two uses, both concrete. As a trend read it says whether a subject is live
    this week. As RESEARCH it names the publishers currently ranking in Google
    News for the phrase - which is a free look at who owns the topic, and a
    shortlist of outlets worth citing or pitching.
    """
    url = ("https://news.google.com/rss/search?"
           + urllib.parse.urlencode({"q": query, "hl": hl, "gl": gl, "ceid": f"{gl}:{hl.split('-')[0]}"}))
    status, body = fetch(url, headers={"User-Agent": BROWSER_UA}, timeout=30)
    if status != 200:
        return _fail("google-news", status, body)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return _fail("google-news", status, f"unparseable RSS: {e}")
    items, sources = [], {}
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        src = (src_el.text or "").strip() if src_el is not None else ""
        if src:
            sources[src] = sources.get(src, 0) + 1
        if len(items) < limit:
            items.append({"title": title, "published": pub, "source": src,
                          "link": (item.findtext("link") or "").strip()})
    return {
        "ok": True, "source": "google-news-rss", "query": query,
        "items_total": sum(sources.values()) or len(items),
        "top_publishers": sorted(({"publisher": k, "items": v} for k, v in sources.items()),
                                 key=lambda r: -r["items"])[:10],
        "items": items,
        "empty_means": "no items is a real answer (nothing in Google News for this phrase), "
                       "not a failed read - the feed parsed.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trending", help="Google Trends 'trending now' RSS (keyless)")
    t.add_argument("--geo", default="US", help="US, GB, DE, ... (default US)")
    t.add_argument("--limit", type=int, default=25)

    w = sub.add_parser("wiki", help="resolve a topic to Wikipedia article titles")
    w.add_argument("--topic", required=True)
    w.add_argument("--lang", default="en")

    v = sub.add_parser("pageviews", help="daily Wikimedia pageviews for an article")
    v.add_argument("--article", required=True)
    v.add_argument("--days", type=int, default=90)
    v.add_argument("--lang", default="en")

    d = sub.add_parser("discussions", help="HN + StackExchange chatter for a query")
    d.add_argument("--query", required=True)
    d.add_argument("--limit", type=int, default=10)
    d.add_argument("--site", default="webmasters", help="StackExchange site key (webmasters, gaming, stackoverflow...)")

    n = sub.add_parser("newsvolume", help="GDELT daily news-coverage timeline for a phrase (keyless)")
    n.add_argument("--query", required=True)
    n.add_argument("--months", type=int, default=3)

    g = sub.add_parser("news", help="Google News RSS - who is covering this topic now (keyless)")
    g.add_argument("--query", required=True)
    g.add_argument("--limit", type=int, default=25)
    g.add_argument("--hl", default="en-US")
    g.add_argument("--gl", default="US")

    a = p.parse_args()
    if a.cmd == "trending":
        out = trending(a.geo, a.limit)
    elif a.cmd == "wiki":
        out = wiki_search(a.topic, lang=a.lang)
    elif a.cmd == "pageviews":
        out = pageviews(a.article, a.days, a.lang)
    elif a.cmd == "newsvolume":
        out = newsvolume(a.query, a.months)
    elif a.cmd == "news":
        out = news(a.query, a.limit, a.hl, a.gl)
    else:
        out = discussions(a.query, a.limit, a.site)

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
