#!/usr/bin/env python3
"""SERP drift - what changed on page 1, and whether it changed everywhere at once.

`rankcheck.py` tracks ONE number per keyword: where this site sits. That is the
number you report, and it is a bad early-warning system, because by the time
your own position moves the cause is weeks old. The SERP around you moves first.

This snapshots the WHOLE page 1 per keyword and diffs two snapshots, which
surfaces three things a position history cannot:

  new entrants   A domain that was not on page 1 and now is. If the same
                 domain appears across several of your keywords in one diff,
                 a competitor has started publishing into your space and you
                 are seeing it while you still outrank them.

  feature change An AI Overview appearing on a query is the single largest
                 click-through event that can happen to it - your position is
                 unchanged and your traffic is not. Reading a click drop as a
                 ranking problem, when the ranking never moved, sends you
                 rewriting a page that is fine.

  volatility     How much page 1 churned, per keyword and site-wide. This is
                 the number that tells you whether YOUR page moved or whether
                 EVERYTHING moved. They demand opposite responses and look
                 identical from a single keyword's history.

Then the correlation that makes volatility actionable: `--updates` aligns the
window against Google's published update calendar (`assets/google-updates.json`).

READ THAT CORRELATION CAREFULLY. A core update overlapping a bad week is not
evidence the update caused it - core updates run for weeks and something always
overlaps. The calendar's real job is the opposite one: to stop you inventing a
content explanation for a fortnight when the whole index was moving, and to
stop you rewriting forty pages because of a week Google had already announced.
An update in the window RAISES the bar for a content diagnosis; it never
supplies one on its own.

Usage:
    drift.py snapshot --keywords-from .seo/keywords.json --out .seo/drift/2026-08-01.json
    drift.py compare --before .seo/drift/2026-07-01.json --after .seo/drift/2026-08-01.json \
        --updates ~/.claude/skills/seo-manager/assets/google-updates.json

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def die(msg, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}, indent=2))
    sys.exit(2)


def host_of(url: str) -> str:
    try:
        h = urlsplit(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def load_keywords(a) -> list[str]:
    kws: list[str] = []
    for k in a.keywords or []:
        kws.append(k.strip())
    if a.keywords_from:
        try:
            raw = json.loads(Path(a.keywords_from).read_text(encoding="utf-8"))
        except Exception as exc:
            die(f"cannot read {a.keywords_from}: {exc}")
        rows = raw if isinstance(raw, list) else raw.get("keywords", [])
        for r in rows:
            k = r.get("keyword") if isinstance(r, dict) else r
            if isinstance(k, str) and k.strip():
                kws.append(k.strip())
    if a.file:
        raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
        kws.extend(x.strip() for x in raw.splitlines() if x.strip())
    return list(dict.fromkeys(kws))


def cmd_snapshot(a):
    kws = load_keywords(a)
    if not kws:
        die("no keywords", hint="--keywords-from .seo/keywords.json, --file, or --keywords")

    body = json.dumps({"queries": kws, "depth": a.depth, "view": "full"}).encode()
    req = urllib.request.Request(a.daemon.rstrip("/") + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=max(180, 12 * len(kws))) as r:
            payload = json.loads(r.read().decode())
    except Exception as exc:
        die(f"serpd batch failed: {type(exc).__name__}: {exc}",
            hint="python3 seodoctor.py --hard, then serpd.py --start (NO trailing &)")

    snap = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "depth": a.depth, "keywords": {}, "unread": []}
    for r in payload.get("results", []):
        q = r.get("query")
        if not q:
            continue
        if not r.get("ok"):
            # A refused read is NOT an empty page 1. Recording it as one would
            # make the next diff report a total wipeout of page 1 and a total
            # re-entry the week after.
            snap["unread"].append({"keyword": q, "error": r.get("error") or "refused read"})
            continue
        rows = r.get("results") or []
        snap["keywords"][q] = {
            "results": [{"position": x.get("position"), "url": x.get("url"),
                         "domain": x.get("domain") or host_of(x.get("url") or ""),
                         "title": (x.get("title") or "")[:120]} for x in rows[: a.depth]],
            "ai_overview": bool((r.get("ai_overview") or {}).get("present")),
            "people_also_ask": (r.get("people_also_ask") or [])[:6],
        }

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True, "out": str(out), "keywords_captured": len(snap["keywords"]),
        "unread": len(snap["unread"]), "unread_detail": snap["unread"][:10],
        "note": "Unread keywords are EXCLUDED from the snapshot, not stored empty. "
                "Re-run them before the next comparison or they will look like a "
                "page-1 wipeout followed by a full recovery.",
    }, indent=2, ensure_ascii=False))


def load_updates(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"cannot read {path}: {exc}"
    return (d.get("updates") if isinstance(d, dict) else d) or [], None


def cmd_compare(a):
    try:
        before = json.loads(Path(a.before).read_text(encoding="utf-8"))
        after = json.loads(Path(a.after).read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"cannot read a snapshot: {exc}")

    bk, ak = before.get("keywords", {}), after.get("keywords", {})
    common = [k for k in ak if k in bk]
    if not common:
        die("the two snapshots share no keywords",
            before_keywords=len(bk), after_keywords=len(ak))

    per_kw = []
    entrant_counter = Counter()
    exit_counter = Counter()
    churns = []
    feature_changes = []
    target = (a.target or "").lower().lstrip("www.")

    for k in common:
        b = bk[k]
        c = ak[k]
        bdom = [r["domain"] for r in b["results"] if r.get("domain")]
        adom = [r["domain"] for r in c["results"] if r.get("domain")]
        bset, aset = set(bdom), set(adom)
        entrants = sorted(aset - bset)
        exits = sorted(bset - aset)
        held = bset & aset
        churn = round(1 - (len(held) / max(1, len(bset | aset))), 3)
        churns.append(churn)
        for d in entrants:
            entrant_counter[d] += 1
        for d in exits:
            exit_counter[d] += 1

        moves = []
        bpos = {r["domain"]: r["position"] for r in b["results"] if r.get("domain")}
        apos = {r["domain"]: r["position"] for r in c["results"] if r.get("domain")}
        for d in held:
            if bpos.get(d) is not None and apos.get(d) is not None:
                delta = bpos[d] - apos[d]           # positive = moved UP
                if abs(delta) >= a.move_threshold:
                    moves.append({"domain": d, "from": bpos[d], "to": apos[d], "delta": delta})
        moves.sort(key=lambda m: -abs(m["delta"]))

        fc = None
        if b.get("ai_overview") != c.get("ai_overview"):
            fc = "gained" if c.get("ai_overview") else "lost"
            feature_changes.append({"keyword": k, "ai_overview": fc})

        row = {
            "keyword": k,
            "churn": churn,
            "entrants": entrants,
            "exits": exits,
            "moves": moves[:5],
            "ai_overview_change": fc,
        }
        if target:
            row["target"] = {"before": bpos.get(target), "after": apos.get(target)}
        per_kw.append(row)

    per_kw.sort(key=lambda r: -r["churn"])
    mean_churn = round(sum(churns) / len(churns), 3)
    high = [r for r in per_kw if r["churn"] >= a.volatile]

    updates, uerr = ([], None)
    if a.updates:
        updates, uerr = load_updates(a.updates)
    window_start = (before.get("taken_at") or "")[:10]
    window_end = (after.get("taken_at") or "")[:10]
    in_window = []
    for u in updates or []:
        d = u.get("date", "")
        e = u.get("ended") or d
        if window_start and window_end and not (e < window_start or d > window_end):
            in_window.append(u)

    if mean_churn >= a.volatile:
        verdict = "SITE-WIDE VOLATILITY"
        note = (f"mean page-1 churn {mean_churn:.0%} across {len(common)} keywords. That is the "
                f"index moving, not your pages failing. Do not start rewriting on this evidence.")
    elif high:
        verdict = "LOCALISED MOVEMENT"
        note = (f"mean churn {mean_churn:.0%} is calm, but {len(high)} keyword(s) churned hard. "
                f"Those are individual SERPs being re-decided - look at them one at a time.")
    else:
        verdict = "STABLE"
        note = f"mean churn {mean_churn:.0%}. Page 1 is broadly the same set of domains."

    print(json.dumps({
        "ok": True,
        "window": {"from": window_start, "to": window_end},
        "keywords_compared": len(common),
        "keywords_only_in_before": sorted(set(bk) - set(ak))[:20],
        "keywords_only_in_after": sorted(set(ak) - set(bk))[:20],
        "mean_churn": mean_churn,
        "verdict": verdict,
        "note": note,
        "recurring_entrants": [{"domain": d, "keywords": n}
                               for d, n in entrant_counter.most_common(10) if n > 1],
        "recurring_exits": [{"domain": d, "keywords": n}
                            for d, n in exit_counter.most_common(10) if n > 1],
        "ai_overview_changes": feature_changes,
        "algorithm_updates_in_window": in_window,
        "algorithm_updates_error": uerr,
        "per_keyword": per_kw[: a.top],
        "reading": {
            "recurring_entrants": "A domain entering page 1 on SEVERAL of your keywords in one "
                                  "window is a competitor moving into the space. One keyword is "
                                  "noise; three is a strategy.",
            "ai_overview_changes": "`gained` means clicks will fall at an unchanged position. "
                                   "Measure the CTR change before attributing it to the page.",
            "algorithm_updates_in_window": "An overlap RAISES the bar for a content explanation. "
                                           "It does not prove causation - core updates run for "
                                           "weeks and something always overlaps.",
        },
    }, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="capture whole page 1 for every tracked keyword")
    s.add_argument("--keywords", action="append")
    s.add_argument("--keywords-from", help=".seo/keywords.json")
    s.add_argument("--file", help="newline-delimited keywords, or -")
    s.add_argument("--out", required=True)
    s.add_argument("--depth", type=int, default=10)
    s.add_argument("--daemon", default="http://127.0.0.1:8791")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("compare", help="diff two snapshots + correlate with algorithm updates")
    s.add_argument("--before", required=True)
    s.add_argument("--after", required=True)
    s.add_argument("--updates", help="assets/google-updates.json")
    s.add_argument("--target", help="your domain, to track your own position through the diff")
    s.add_argument("--volatile", type=float, default=0.4, help="churn that counts as volatile")
    s.add_argument("--move-threshold", type=int, default=2)
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_compare)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
