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
    host = (host or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    two = {"co.uk", "com.au", "co.jp", "co.nz", "com.br", "co.in", "org.uk", "ac.uk"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


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

    rows, failures = [], []
    for i, kw in enumerate(keywords):
        cmd = [sys.executable, str(HERE / "serp.py"), kw, "--provider", provider,
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
            failures.append({"keyword": kw, "error": err})
        else:
            pos, url = position_of(data.get("results", []), domain)
            rows.append({
                "keyword": kw,
                "position": pos,
                "url": url,
                "provider": data.get("provider"),
                "depth_checked": len(data.get("results", [])),
                "ai_overview": (data.get("ai_overview") or {}).get("present"),
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
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
        "results": sorted(rows, key=lambda r: (r["position"] is None, r["position"] or 999)),
    }, indent=2, ensure_ascii=False))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
