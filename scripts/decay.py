#!/usr/bin/env python3
"""Content decay - which published pages are losing, and which are worth saving.

Every other workflow in this skill points forward: what to write next. This one
points backward, at what has already been written, because on any site older
than a few months the highest-return work is not a new page. It is the page
that ranked at #6, slipped to #14, and now earns a tenth of what it did - which
nobody notices, because nothing broke and no alert fires. Decay is silent by
construction.

WHAT THIS IS NOT: a traffic report. Traffic falling is not decay. Four things
look identical in a clicks chart and want opposite responses:

  decay        the page slipped in the rankings. Impressions down AND average
               position worse. This is the one you fix by rewriting.
  demand drop  position HELD or improved, impressions fell anyway. The query
               stopped being asked - seasonality, a dead product, a news cycle.
               Rewriting it changes nothing. This is the trap.
  cannibal     this page fell while a SIBLING on the same query rose. You did
               this to yourself with the last publish; the fix is consolidation
               or a canonical, never a rewrite of the loser.
  settling     a page published inside the window has no stable baseline yet.
               A discovery burst decaying to a real level is not a loss.

Separating them needs POSITION, not clicks, which is why this reads Search
Console rather than an analytics tool. A page whose position held is not
decaying however far its clicks fell, and a rewrite aimed at it is wasted.

Optional algorithm-update correlation: pass --updates to align each decay
window against Google's published update calendar. A cluster of pages that all
turned on the same date is a site-wide event and a different problem from one
page ageing out - and telling them apart stops you rewriting forty pages to fix
one core-update hit.

INPUT: Search Console search-analytics rows, from the `search-console` skill.
You need the `page` dimension. `query` as a second dimension is optional and
unlocks the cannibalisation check.

    search-console ... --dimensions page --start A --end B  > prev.json
    search-console ... --dimensions page --start C --end D  > cur.json
    decay.py compare --previous prev.json --current cur.json

Or one export carrying the date dimension, split here:

    decay.py split --file rows.json --on 2026-07-01

Stdlib only. Writes nothing - it prints candidates, and the workflow decides
what to queue.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit


def load_rows(path: str):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"{path} is not JSON ({exc}). Expected a Search Console search-analytics response.")
    if isinstance(d, dict):
        for k in ("rows", "data", "results"):
            if isinstance(d.get(k), list):
                return d[k]
        die(f"{path}: no `rows` array. Keys present: {sorted(d)[:8]}")
    if isinstance(d, list):
        return d
    die(f"{path}: unexpected JSON shape {type(d).__name__}")


def die(msg, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}, indent=2))
    sys.exit(2)


def split_keys(row):
    """Pull (page, query, day) out of a GSC row whatever order the dimensions came in."""
    keys = row.get("keys") or []
    page = query = day = None
    for k in keys:
        if not isinstance(k, str):
            continue
        if k.startswith("http://") or k.startswith("https://"):
            page = k
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", k):
            day = k
        elif query is None:
            query = k
    if page is None:
        for k in ("page", "url"):
            if isinstance(row.get(k), str):
                page = row[k]
                break
    if query is None and isinstance(row.get("query"), str):
        query = row["query"]
    return page, query, day


def norm_page(u: str) -> str:
    if not u:
        return ""
    try:
        s = urlsplit(u)
        p = s.path or "/"
        return p.rstrip("/") or "/"
    except Exception:
        return u


def aggregate(rows, by_query=False):
    """Fold GSC rows to page (or page+query) totals.

    Position must be IMPRESSION-WEIGHTED. GSC reports an average position per
    row; averaging those averages across a page's rows gives every query equal
    say, so one obscure query sitting at position 90 can drag a page's headline
    position down while nothing about the page changed.
    """
    agg = {}
    for r in rows:
        page, query, _day = split_keys(r)
        if not page:
            continue
        key = (norm_page(page), query if by_query else None)
        a = agg.get(key)
        if a is None:
            a = agg[key] = {"clicks": 0, "impressions": 0, "pos_weight": 0.0}
        imp = float(r.get("impressions") or 0)
        a["clicks"] += float(r.get("clicks") or 0)
        a["impressions"] += imp
        a["pos_weight"] += float(r.get("position") or 0) * imp
    for a in agg.values():
        a["position"] = round(a["pos_weight"] / a["impressions"], 2) if a["impressions"] else None
        del a["pos_weight"]
        a["clicks"] = int(a["clicks"])
        a["impressions"] = int(a["impressions"])
    return agg


def pct(new, old):
    if not old:
        return None
    return round((new - old) / old, 4)


def load_updates(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"could not read {path}: {exc}"
    ups = d.get("updates") if isinstance(d, dict) else d
    out = []
    for u in ups or []:
        try:
            out.append({
                "date": u["date"],
                "name": u.get("name") or u["date"],
                "kind": u.get("kind") or "unknown",
                "source": u.get("source"),
                "ended": u.get("ended"),
            })
        except Exception:
            continue
    return out, None


def updates_in_window(updates, start: str, end: str):
    if not updates:
        return []
    hits = []
    for u in updates:
        d = u["date"]
        e = u.get("ended") or d
        # A rollout overlapping the window at all is relevant; core updates run
        # for weeks and the damage lands somewhere inside that span.
        if not (e < start or d > end):
            hits.append(u)
    return hits


def cmd_compare(a):
    prev = aggregate(load_rows(a.previous), by_query=False)
    cur = aggregate(load_rows(a.current), by_query=False)
    if not prev or not cur:
        die("one of the exports produced no page rows",
            previous_rows=len(prev), current_rows=len(cur),
            hint="the export must carry the `page` dimension")

    updates, uerr = (None, None)
    if a.updates:
        updates, uerr = load_updates(a.updates)

    published = {}
    if a.pages:
        try:
            pg = json.loads(Path(a.pages).read_text(encoding="utf-8"))
            for p in (pg if isinstance(pg, list) else pg.get("pages", [])):
                u = p.get("url") or p.get("slug") or ""
                published[norm_page(u)] = p.get("published_at") or p.get("date")
        except Exception:
            pass

    decaying, demand, rising, lost, gained, settling = [], [], [], [], [], []

    for key, c in cur.items():
        page = key[0]
        p = prev.get(key)
        if p is None:
            gained.append({"page": page, **c})
            continue
    for key, p in prev.items():
        page = key[0]
        c = cur.get(key)
        if p["impressions"] < a.min_impressions:
            continue

        # A page first published inside the baseline window has no stable
        # baseline: its "before" number is a discovery burst, and every burst
        # decays. Counting that as decay manufactures work.
        pub = published.get(page)
        if pub and a.settle_days:
            try:
                pubd = datetime.fromisoformat(str(pub)[:10]).date()
                if a.previous_start and (datetime.fromisoformat(a.previous_start).date() - pubd).days < a.settle_days:
                    settling.append({"page": page, "published": str(pub)[:10],
                                     "impressions_before": p["impressions"]})
                    continue
            except Exception:
                pass

        if c is None or c["impressions"] == 0:
            lost.append({"page": page, "impressions_before": p["impressions"],
                         "clicks_before": p["clicks"], "position_before": p["position"]})
            continue

        d_imp = pct(c["impressions"], p["impressions"])
        d_clicks = pct(c["clicks"], p["clicks"])
        d_pos = None
        if p["position"] is not None and c["position"] is not None:
            d_pos = round(c["position"] - p["position"], 2)   # positive = worse

        rec = {
            "page": page,
            "impressions": {"before": p["impressions"], "after": c["impressions"], "change": d_imp},
            "clicks": {"before": p["clicks"], "after": c["clicks"], "change": d_clicks},
            "position": {"before": p["position"], "after": c["position"], "change": d_pos},
        }

        if d_imp is not None and d_imp <= -a.drop:
            if d_pos is not None and d_pos >= a.pos_slip:
                rec["verdict"] = "decay"
                rec["why"] = (f"impressions {d_imp:+.0%} and average position slipped "
                              f"{d_pos:+.1f} ({p['position']} -> {c['position']}) - the page lost ground")
                decaying.append(rec)
            else:
                rec["verdict"] = "demand_drop"
                rec["why"] = (f"impressions {d_imp:+.0%} but position held "
                              f"({p['position']} -> {c['position']}) - demand fell, the page did not. "
                              f"A rewrite fixes nothing here.")
                demand.append(rec)
        elif d_imp is not None and d_imp >= a.drop:
            rec["verdict"] = "rising"
            rising.append(rec)

    decaying.sort(key=lambda r: r["impressions"]["before"] - r["impressions"]["after"], reverse=True)
    demand.sort(key=lambda r: r["impressions"]["before"] - r["impressions"]["after"], reverse=True)
    lost.sort(key=lambda r: r["impressions_before"], reverse=True)
    rising.sort(key=lambda r: r["impressions"]["after"] - r["impressions"]["before"], reverse=True)

    sitewide = None
    if decaying and demand:
        share = len(decaying) / max(1, len(decaying) + len(demand) + len(rising))
        if share >= 0.6 and len(decaying) >= 8:
            sitewide = (f"{len(decaying)} of {len(decaying)+len(demand)+len(rising)} measurable pages "
                        f"decayed together. That pattern is a SITE-WIDE event, not {len(decaying)} "
                        f"separate content problems - look for an algorithm update, a technical "
                        f"regression, or a crawl collapse before rewriting anything.")

    algo = []
    if updates and a.current_start and a.current_end:
        algo = updates_in_window(updates, a.current_start, a.current_end)
    elif updates and a.previous_start and a.current_end:
        algo = updates_in_window(updates, a.previous_start, a.current_end)

    print(json.dumps({
        "ok": True,
        "pages_compared": len(prev),
        "min_impressions": a.min_impressions,
        "thresholds": {"impression_drop": a.drop, "position_slip": a.pos_slip},
        "counts": {
            "decay": len(decaying), "demand_drop": len(demand), "lost": len(lost),
            "rising": len(rising), "new": len(gained), "settling": len(settling),
        },
        "sitewide_signal": sitewide,
        "algorithm_updates_in_window": algo,
        "algorithm_updates_error": uerr,
        "decay": decaying[: a.top],
        "demand_drop": demand[: a.top],
        "lost": lost[: a.top],
        "rising": rising[: a.top],
        "settling": settling[: a.top],
        "reading": {
            "decay": "REWRITE CANDIDATES. Position slipped - somebody out-answered you. "
                     "Queue as type `update`, best-answer test still applies.",
            "demand_drop": "DO NOT REWRITE. Position held; the query stopped being asked. "
                           "The only honest responses are 'nothing' or 'target a different query'.",
            "lost": "Fell out of the index or off the reported window entirely. Check it still "
                    "returns 200 and is not noindexed before assuming a ranking cause.",
            "settling": "Published too recently to have a baseline. Excluded on purpose.",
        },
        "next": "For each `decay` row worth saving, run the SERP again - if page 1 now holds 4+ "
                "authorities the query has moved out of reach and the honest call is to let it go, "
                "not to rewrite into a fight you cannot win.",
    }, indent=2, ensure_ascii=False))


def cmd_cannibal(a):
    """Two pages, one query, both losing. The publish that caused it is usually recent."""
    cur = aggregate(load_rows(a.current), by_query=True)
    byq = defaultdict(list)
    for (page, query), v in cur.items():
        if query and v["impressions"] >= a.min_impressions:
            byq[query].append({"page": page, **v})

    clashes = []
    for q, pages in byq.items():
        if len(pages) < 2:
            continue
        pages.sort(key=lambda p: p["impressions"], reverse=True)
        top = pages[0]
        rest = pages[1:]
        # Only a real clash when the runner-up is ALSO ranking, not a stray
        # impression: both inside the first few pages of results.
        rivals = [p for p in rest if (p["position"] or 999) <= a.max_position]
        if not rivals or (top["position"] or 999) > a.max_position:
            continue
        clashes.append({
            "query": q,
            "pages": [{"page": p["page"], "impressions": p["impressions"],
                       "clicks": p["clicks"], "position": p["position"]} for p in [top] + rivals],
            "total_impressions": sum(p["impressions"] for p in [top] + rivals),
            "spread": round(abs((rivals[0]["position"] or 0) - (top["position"] or 0)), 1),
        })
    clashes.sort(key=lambda c: c["total_impressions"], reverse=True)

    print(json.dumps({
        "ok": True,
        "queries_examined": len(byq),
        "clashes": len(clashes),
        "top": clashes[: a.top],
        "reading": "Two of your own URLs ranking for one query splits the click-through and the "
                   "link signal, and Google picks between them per-search rather than ranking the "
                   "better one. Fix by consolidating into whichever page deserves the query and "
                   "pointing the other at it - NOT by rewriting both. If the weaker page is a "
                   "generated/programmatic URL and the stronger is curated, the generated one "
                   "should canonicalise to the curated one.",
    }, indent=2, ensure_ascii=False))


def cmd_split(a):
    rows = load_rows(a.file)
    before, after = [], []
    for r in rows:
        _p, _q, day = split_keys(r)
        if not day:
            continue
        (before if day < a.on else after).append(r)
    if not before or not after:
        die("the split produced an empty side - does the export carry the `date` dimension?",
            before=len(before), after=len(after))
    Path(a.out_previous).write_text(json.dumps({"rows": before}), encoding="utf-8")
    Path(a.out_current).write_text(json.dumps({"rows": after}), encoding="utf-8")
    print(json.dumps({"ok": True, "split_on": a.on,
                      "previous": {"file": a.out_previous, "rows": len(before)},
                      "current": {"file": a.out_current, "rows": len(after)},
                      "next": f"decay.py compare --previous {a.out_previous} --current {a.out_current}"},
                     indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("compare", help="two GSC periods -> decay / demand-drop / lost / rising")
    s.add_argument("--previous", required=True, help="baseline period GSC rows (- for stdin)")
    s.add_argument("--current", required=True, help="recent period GSC rows")
    s.add_argument("--previous-start", help="YYYY-MM-DD, enables settling + update correlation")
    s.add_argument("--current-start")
    s.add_argument("--current-end")
    s.add_argument("--pages", help=".seo/pages.json - excludes pages too new to have a baseline")
    s.add_argument("--updates", help="google-updates.json for algorithm correlation")
    s.add_argument("--min-impressions", type=int, default=10,
                   help="baseline impressions required before a page is judged at all")
    s.add_argument("--drop", type=float, default=0.3, help="fractional impression change that counts")
    s.add_argument("--pos-slip", type=float, default=1.0,
                   help="positions lost before a fall counts as decay rather than demand")
    s.add_argument("--settle-days", type=int, default=30)
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("cannibal", help="your own pages competing for one query")
    s.add_argument("--current", required=True, help="GSC rows with page AND query dimensions")
    s.add_argument("--min-impressions", type=int, default=5)
    s.add_argument("--max-position", type=float, default=30.0)
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(fn=cmd_cannibal)

    s = sub.add_parser("split", help="one date-dimensioned export -> two period files")
    s.add_argument("--file", required=True)
    s.add_argument("--on", required=True, help="YYYY-MM-DD - rows before this go to previous")
    s.add_argument("--out-previous", default="gsc-previous.json")
    s.add_argument("--out-current", default="gsc-current.json")
    s.set_defaults(fn=cmd_split)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
