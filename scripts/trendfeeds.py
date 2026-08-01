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

    a = p.parse_args()
    if a.cmd == "trending":
        out = trending(a.geo, a.limit)
    elif a.cmd == "wiki":
        out = wiki_search(a.topic, lang=a.lang)
    elif a.cmd == "pageviews":
        out = pageviews(a.article, a.days, a.lang)
    else:
        out = discussions(a.query, a.limit, a.site)

    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 3)


if __name__ == "__main__":
    main()
