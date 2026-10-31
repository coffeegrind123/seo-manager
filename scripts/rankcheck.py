#!/usr/bin/env python3
"""Rank tracking run for the seo-manager skill.

Takes the tracked keywords out of `.seo/keywords.json`, checks each one through
the configured SERP provider, and appends the results to `.seo/ranks.jsonl`.
That append-only file is what `seostate.py rankings` reads to draw trends, and
what the research workflow's step-0 learning step grades its own targeting
against.

Honest about position depth: a provider that only returns page 1 can tell you
"not in the top 10", never "not in the top 100". The recorded row carries
`depth_checked` so a future run never mistakes one for the other.

    rankcheck.py --all                    # every tracked keyword
    rankcheck.py --keyword "rank tracker" # one
    rankcheck.py --all --provider serpapi --depth 100
    rankcheck.py --all --dry-run          # show what would be checked

Stdlib only. Delegates fetching to serp.py in the same directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
from providers import registrable as _shared_registrable  # noqa: E402


def run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not proc.stdout.strip():
        return {"ok": False, "error": (proc.stderr or "no output").strip()[:300]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output: {proc.stdout[:200]}"}


def state(root: str | None, *args) -> dict:
    cmd = [sys.executable, str(HERE / "seostate.py")]
    if root:
        cmd += ["--root", root]
    return run_json(cmd + list(args))


def registrable(host: str) -> str:
    return _shared_registrable(host)


def position_of(results: list[dict], domain: str) -> tuple:
    """(position, url) of the first result belonging to `domain`, or (None, None).

    Extracted from main() so it can be CONTROLLED. The distinction it carries is
    the whole point of a rank check: "not in the results we saw" is not a
    position, and `notexample.com` is not `example.com`."""
    for r in results or []:
        u = r.get("url") or ""
        host = registrable(u.split("/")[2] if "://" in u else "")
        if host == domain or host.endswith("." + domain):
            return r.get("position"), r.get("url")
    return None, None


# A FALL worth a second look (rankme.fast's rule): out of the top 10, five or
# more places down, or gone from a depth the old position sat inside.
TOP = 10
FALL = 5
# A second read within this many places of the OLD position says the first
# read was noise, not a drop.
VOLATILE_WITHIN = 2


def rank_state(position, depth_checked: int) -> str:
    if position is not None:
        return "ranked"
    if not depth_checked:
        return "not_measured"
    return "out_of_range"


def classify_move(prev, now, depth: int) -> str | None:
    """'drop', 'unknown_depth', or None. Previously unranked is never a drop:
    there was no position to lose."""
    if prev is None:
        return None
    if now is None:
        # Gone from the read - but only a drop if the read went deep enough to
        # have seen the old position.
        return "drop" if depth >= prev else "unknown_depth"
    if now <= prev:
        return None
    if (prev <= TOP < now) or (now - prev >= FALL):
        return "drop"
    return None


def settle_drop(*, prev, first, second, depth: int) -> str:
    """A candidate drop after a SECOND read: confirmed / volatile / unconfirmed.
    A failed second read never confirms anything."""
    if second == "failed":
        return "unconfirmed"
    if second is not None and abs(second - prev) <= VOLATILE_WITHIN:
        return "volatile"
    return "confirmed" if classify_move(prev, second, depth) == "drop" else "volatile"


def run_control() -> dict:
    """Prove the domain matcher discriminates - offline, no SERP call.

    A matcher that is too loose records a competitor's position as yours; one
    that is too tight records "not ranking" for a page sitting at #3. Both are
    silent, and both survive every subsequent report."""
    from controls import Controls
    c = Controls("rankcheck-control")
    rows = [
        {"position": 1, "url": "https://notexample.com/a"},
        {"position": 2, "url": "https://blog.example.com/b"},
        {"position": 3, "url": "https://example.com/c"},
    ]
    c.check("a_lookalike_domain_does_not_match",
            position_of([rows[0]], "example.com") == (None, None),
            "notexample.com must never be recorded as example.com")
    c.check("a_subdomain_matches", position_of([rows[1]], "example.com")[0] == 2)
    c.check("the_apex_matches", position_of([rows[2]], "example.com")[0] == 3)
    c.check("the_FIRST_match_wins", position_of(rows, "example.com")[0] == 2,
            "a rank check reports the best position, not the last one seen")
    c.check("the_url_is_returned_with_the_position",
            position_of(rows, "example.com")[1] == "https://blog.example.com/b")
    c.check("absent_is_none_not_zero", position_of(rows, "other.test") == (None, None),
            "position 0 would sort first on every report")
    c.check("an_empty_result_set_is_absent_not_a_crash",
            position_of([], "example.com") == (None, None))
    c.check("a_malformed_url_does_not_crash",
            position_of([{"position": 1, "url": "not a url"}], "example.com") == (None, None))
    c.check("registrable_folds_www", registrable("www.example.com") == "example.com")
    c.check("registrable_does_not_over_fold",
            registrable("notexample.com") == "notexample.com")
    c.check("a_null_with_depth_is_out_of_range_not_a_position", rank_state(None, 20) == "out_of_range")
    c.check("unranked_before_is_never_a_drop", classify_move(None, None, 20) is None)
    c.check("a_fall_out_of_the_top_10_is_a_drop", classify_move(7, 14, 20) == "drop")
    c.check("gone_from_a_read_too_shallow_to_see_it_is_unknown", classify_move(15, None, 10) == "unknown_depth")
    c.check("a_failed_second_read_never_confirms", settle_drop(prev=7, first=14, second="failed",
                                                              depth=20) == "unconfirmed")
    return c.verdict(note="the matcher is proven offline; whether the PROVIDER answers is a "
                          "separate question - `serp.py --control` proves the SERP guards")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--control", action="store_true",
                   help="prove the domain matcher discriminates, offline")
    p.add_argument("--root", help="repo root (defaults to the nearest .seo/)")
    p.add_argument("--all", action="store_true", help="check every tracked keyword")
    p.add_argument("--keyword", action="append", help="check just these (repeatable)")
    p.add_argument("--provider", help="override the project's serp_provider")
    p.add_argument("--depth", type=int, default=20, help="how deep to look (ddg pages are 10 each)")
    p.add_argument("--delay", type=float, default=6.0,
                   help="seconds between checks. The keyless ddg provider goes into blanket 202 "
                        "refusal under load, and no proxy or endpoint change clears it, so the "
                        "default is deliberately slow. A proxy does NOT let you lower it.")
    p.add_argument("--limit", type=int, help="stop after N keywords (budget guard)")
    p.add_argument("--proxy-country", metavar="CC",
                   help="pin the residential exit country (see serp.py --help for the verified pool). "
                        "Use it to check how the site ranks FROM a market, not as a throttle workaround.")
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-confirm", action="store_true",
                   help="skip the second read on drop candidates (and the history lookup)")
    p.add_argument("--confirm-provider",
                   help="provider for the second read (default: the same ladder again)")
    a = p.parse_args()

    if a.control:
        out = run_control()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if out.get("ok") else 1)

    cfg = state(a.root, "config")
    if not cfg.get("ok"):
        print(json.dumps(cfg, indent=2))
        sys.exit(1)
    config = cfg["config"]
    domain = registrable(config.get("domain") or "")
    provider = a.provider or config.get("serp_provider") or "ddg"

    if provider == "browser":
        print(json.dumps({
            "ok": False,
            "error": "the browser provider cannot run unattended from this script - it needs the "
                     "agent to drive the browser MCP.",
            "how": "for each keyword run `serp.py <kw> --provider browser`, follow the printed steps, "
                   "then record with `seostate.py record-rank --json '[{...}]'`. For a bulk run, "
                   "switch to --provider ddg (keyless) or serpapi.",
        }, indent=2))
        sys.exit(2)
    if provider == "none":
        print(json.dumps({
            "ok": False,
            "error": "this project has no SERP provider (GSC-only mode).",
            "how": "positions come from Search Console instead - use `gsc.py query`'s "
                   "search-analytics query with dimension=query, and feed it to "
                   "`keywords.py gsc`. That is a real configuration, not a failure.",
        }, indent=2))
        sys.exit(2)

    if a.keyword:
        keywords = [k.strip().lower() for k in a.keyword if k.strip()]
    elif a.all:
        kws = state(a.root, "keywords")
        keywords = [k["keyword"] for k in kws.get("keywords", [])]
    else:
        p.error("pass --all or --keyword")

    if a.limit:
        keywords = keywords[: a.limit]
    if not keywords:
        print(json.dumps({"ok": True, "checked": 0, "note": "no tracked keywords - track some first"}, indent=2))
        return
    if a.dry_run:
        print(json.dumps({"ok": True, "would_check": keywords, "provider": provider,
                          "domain": domain, "depth": a.depth}, indent=2))
        return

    prev = {}
    if not a.no_confirm:
        hist = state(a.root, "rankings", "--days", "3650")
        prev = {r["keyword"]: r.get("latest") for r in hist.get("rankings", [])}

    def read(kw: str, prov: str) -> dict:
        cmd = [sys.executable, str(HERE / "serp.py"), kw, "--provider", prov,
               "--count", str(a.depth), "--target-domain", domain, "--fallback", "--raw"]
        if a.proxy_country:
            cmd += ["--proxy-country", a.proxy_country]
        if a.no_proxy:
            cmd.append("--no-proxy")
        data = run_json(cmd)
        if not data.get("ok"):
            rel = data.get("relevance") or {}
            err = data.get("error") or next(iter((data.get("errors") or {}).values()), "unknown")
            if rel and rel.get("pass") is False:
                err = (f"results were not for this query (coverage {rel.get('coverage')}, "
                       f"hit_rate {rel.get('hit_rate')}) - refused rather than recording a "
                       "position off somebody else's SERP")
            return {"ok": False, "error": err}
        pos, url = position_of(data.get("results", []), domain)
        return {"ok": True, "position": pos, "url": url, "provider": data.get("provider"),
                "depth": len(data.get("results", [])),
                "ai_overview": (data.get("ai_overview") or {}).get("present")}

    rows, failures, drops = [], [], []
    for i, kw in enumerate(keywords):
        got = read(kw, provider)
        if not got["ok"]:
            failures.append({"keyword": kw, "error": got["error"]})
        else:
            row = {
                "keyword": kw,
                "position": got["position"],
                "url": got["url"],
                "provider": got["provider"],
                "depth_checked": got["depth"],
                "rank_state": rank_state(got["position"], got["depth"]),
                # Unpinned is recorded as such: a position from an exit nobody
                # chose is a local observation, never a global fact.
                "exit_country": a.proxy_country or ("none" if a.no_proxy else "unpinned"),
                "ai_overview": got["ai_overview"],
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            move = classify_move(prev.get(kw), got["position"], got["depth"])
            if move == "unknown_depth":
                row["drop_status"] = "unknown_depth"
            elif move == "drop":
                # ONE read is one sample of a personalised, A/B-tested page.
                # A drop is reported only after a second read agrees.
                time.sleep(a.delay)
                again = read(kw, a.confirm_provider or provider)
                second = again["position"] if again["ok"] else "failed"
                row["drop_status"] = settle_drop(prev=prev[kw], first=got["position"],
                                                 second=second, depth=got["depth"])
                drops.append({"keyword": kw, "from": prev[kw], "to": got["position"],
                              "second_read": second, "status": row["drop_status"],
                              "second_provider": again.get("provider")})
            rows.append(row)
        if i < len(keywords) - 1:
            time.sleep(a.delay)

    if rows:
        res = state(a.root, "record-rank", "--json", json.dumps(rows))
        if not res.get("ok"):
            print(json.dumps({"ok": False, "error": "recording failed", "detail": res}, indent=2))
            sys.exit(1)

    ranked = [r for r in rows if r["position"] is not None]
    print(json.dumps({
        "ok": not failures,
        "provider": provider,
        "domain": domain,
        "checked": len(rows),
        "ranking_in_depth": len(ranked),
        "not_found": len(rows) - len(ranked),
        "depth_caveat": f"'not found' means outside the top {a.depth} this provider returned - "
                        "not necessarily outside the top 100. Raise --depth (or use serpapi, which "
                        "buys the full 100 in one credit) before reading it as a drop.",
        "failures": failures,
        "drops": {st: [d for d in drops if d["status"] == st]
                  for st in ("confirmed", "volatile", "unconfirmed")},
        "drop_rule": (f"a candidate is a fall out of the top {TOP}, a fall of {FALL}+ places, or "
                      f"vanishing from a read deep enough to have seen the old position; it is "
                      f"CONFIRMED only when a second read agrees. Report confirmed drops; "
                      f"volatile ones are noise, unconfirmed ones are unknown."),
        "exit_country": a.proxy_country or "unpinned - every position here is from an exit "
                                           "nobody chose; name it as such",
        "results": sorted(rows, key=lambda r: (r["position"] is None, r["position"] or 999)),
    }, indent=2, ensure_ascii=False))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
