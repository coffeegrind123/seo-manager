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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
            "how": "positions come from Search Console instead - use the search-console skill's "
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
            pos, url = None, None
            for r in data.get("results", []):
                host = registrable((r.get("url") or "").split("/")[2] if "://" in (r.get("url") or "") else "")
                if host == domain or host.endswith("." + domain):
                    pos, url = r.get("position"), r.get("url")
                    break
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
