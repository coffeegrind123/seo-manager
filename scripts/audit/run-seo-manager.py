#!/usr/bin/env python3
"""
run-seo-manager.py — the full audit's advanced measurements, taken from
seo-manager's own instruments (scripts/, one directory up) instead of
re-implemented in the audit scripts.

The instruments carry the checks the original audit scripts lacked:
crawler-scoped directives, AI-crawler policy and AI-features eligibility,
field Core Web Vitals, hreflang reachability, retired schema types, AI-writing
tells, the internal link graph, Search Console / Bing index state and AI
citation. This script is the ONE place the audit talks to it: it runs each
instrument as a subprocess, and writes a single JSON with a STATE per
instrument, so the report can never show "pass" for something that did not
run.

    ok             the instrument ran and answered
    not_applicable it ran and the check does not apply (e.g. no hreflang)
    no_key         a credential is missing - nothing was measured
    cannot_ask     it ran and refused (unreachable, refused, quota, disabled API)
    error          it crashed or printed something that is not JSON
    timeout        it did not finish in time
    skipped        not requested (e.g. --no-live-apis)
    not_installed  seo-manager is not installed

Usage:
    python3 run-seo-manager.py <url> [--keyword "primary keyword"] [--out FILE]
                               [--max-pages 60] [--sample 40] [--no-live-apis]
                               [--gsc-property sc-domain:example.com]

Output: JSON to --out (default stdout). Exit 0 unless seo-manager is missing.
"""

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# The instruments are this skill's own scripts/, one directory up. SEO_MANAGER_DIR
# still points it at another checkout (a skill root, not its scripts/).
SEO = (Path(os.environ["SEO_MANAGER_DIR"]) / "scripts" if os.environ.get("SEO_MANAGER_DIR")
       else Path(__file__).resolve().parent.parent)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
     "Chrome/130.0 Safari/537.36"

# States an instrument reports itself with `"state": ...` when ok is false.
SELF_STATES = {"no_key": "no_key", "api_disabled": "cannot_ask", "no_access": "cannot_ask",
               "cannot_ask": "cannot_ask", "unknown": "cannot_ask", "failed": "cannot_ask"}


def run(name: str, argv: list, timeout: int) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-I", str(SEO / argv[0])] + argv[1:],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "seconds": timeout, "argv": argv}
    secs = round(time.time() - t0, 1)
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"state": "error", "exit": p.returncode, "seconds": secs, "argv": argv,
                "output_tail": (p.stdout or p.stderr or "")[-800:]}
    if data.get("ok") is False:
        st = SELF_STATES.get(str(data.get("state")), "cannot_ask")
        why = data.get("reason") or data.get("error") or data.get("detail")
        if isinstance(why, str) and ("no hreflang alternates" in why or "declares no" in why):
            st = "not_applicable"
        return {"state": st, "exit": p.returncode, "seconds": secs, "argv": argv,
                "reason": why, "data": data}
    return {"state": "ok", "exit": p.returncode, "seconds": secs, "argv": argv, "data": data}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(8 * 1024 * 1024).decode("utf-8", "replace")


def target_in_graph(graph_path: str, url: str) -> dict:
    """The audited page's own row in the crawl: inbound links, distinct
    linking pages, contextual vs boilerplate, crawl depth."""
    try:
        g = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"found": False, "error": str(e)}
    urls = g.get("urls") or []
    path = urllib.parse.urlsplit(url).path or "/"
    cands = {path, path.rstrip("/") or "/", (path.rstrip("/") + "/") if path != "/" else "/", url}
    idx = next((i for i, u in enumerate(urls) if u in cands), None)
    if idx is None:
        return {"found": False, "pages_in_graph": len(urls),
                "note": "the crawl did not reach this page - an orphan, or past --max-pages"}
    inbound, sources, boiler = 0, set(), 0
    adj = g.get("adj") or {}
    for src, edges in adj.items():
        for e in edges:
            if e[0] == idx and int(src) != idx:
                inbound += 1
                sources.add(int(src))
                boiler += 1 if (len(e) > 2 and e[2]) else 0
    # breadth-first depth from the crawl root (index 0)
    depth, frontier, seen = {0: 0}, [0], {0}
    while frontier:
        nxt = []
        for s in frontier:
            for e in adj.get(str(s), []):
                if e[0] not in seen:
                    seen.add(e[0])
                    depth[e[0]] = depth[s] + 1
                    nxt.append(e[0])
        frontier = nxt
    return {"found": True, "pages_in_graph": len(urls), "inbound_links": inbound,
            "linking_pages": len(sources), "boilerplate_links": boiler,
            "contextual_links": inbound - boiler, "click_depth": depth.get(idx)}


def main():
    ap = argparse.ArgumentParser(description="seo-manager instruments for one audited URL")
    ap.add_argument("url")
    ap.add_argument("--keyword", help="primary keyword - enables the AI-visibility check")
    ap.add_argument("--brand", help="brand name(s), comma-separated, for mention matching")
    ap.add_argument("--gsc-property", help="Search Console property (default: auto-match)")
    ap.add_argument("--max-pages", type=int, default=60, help="link-graph crawl budget")
    ap.add_argument("--sample", type=int, default=40, help="sitemap URLs to audit")
    ap.add_argument("--timeout", type=int, default=420, help="seconds per instrument")
    ap.add_argument("--no-live-apis", action="store_true",
                    help="skip Search Console, Bing and answer-engine calls (quota / paid keys)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    a = ap.parse_args()

    u = urllib.parse.urlsplit(a.url)
    if u.scheme not in ("http", "https") or not u.netloc:
        print(json.dumps({"ok": False, "error": f"not an absolute http(s) URL: {a.url}"}))
        sys.exit(2)
    origin = f"{u.scheme}://{u.netloc}"
    host = (u.hostname or "").lower()
    result = {"ok": True, "url": a.url, "origin": origin, "seo_manager": str(SEO.parent),
              "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "instruments": {}}
    if not (SEO / "sitecheck.py").exists():
        result.update(ok=False, state="not_installed",
                      note=f"seo-manager not found at {SEO.parent}; set SEO_MANAGER_DIR. Every "
                           "advanced section renders as NOT MEASURED, never as pass.")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        sys.exit(1)

    work = Path(tempfile.mkdtemp(prefix="audit-seo-"))
    page_dir = work / "page"
    page_dir.mkdir()
    page_file = page_dir / "index.html"
    try:
        page_file.write_text(fetch_html(a.url), encoding="utf-8")
        page_state = None
    except Exception as e:                                       # noqa: BLE001
        page_state = {"state": "cannot_ask", "reason": f"page fetch failed: {e}"}
    graph = str(work / "graph.json")
    # The page and the variants a link or a typed URL reaches it by: each must
    # land on the page in one permanent hop.
    other_host = host[4:] if host.startswith("www.") else "www." + host
    flip = (u.path[:-1] if u.path.endswith("/") and u.path != "/" else
            (u.path + "/" if u.path and u.path != "/" else u.path))
    variants = [a.url,
                urllib.parse.urlunsplit(("http", u.netloc, u.path, u.query, "")),
                urllib.parse.urlunsplit((u.scheme, other_host, u.path, u.query, "")),
                urllib.parse.urlunsplit((u.scheme, u.netloc, flip or "/", u.query, ""))]
    urls_file = work / "variants.txt"
    urls_file.write_text("\n".join(dict.fromkeys(variants)) + "\n", encoding="utf-8")

    T = a.timeout
    jobs = {
        "sitecheck": ["sitecheck.py", "all", origin, "--sample", str(a.sample)],
        "agentcheck": ["agentcheck.py", "all", origin, "--page", a.url],
        "hreflang_audit": ["hreflang.py", "audit", "--url", a.url, "--max-urls", "40"],
        "hreflang_redirects": ["hreflang.py", "redirects", a.url],
        "vitals_page": ["vitals.py", "page", a.url],
        # vitals.py reads a "/" path as the ORIGIN, which vitals_origin_history
        # already covers; only a deeper URL gets its own history query.
        **({"vitals_history": ["vitals.py", "history", a.url]}
           if (u.path or "/") != "/" else {}),
        "vitals_origin_history": ["vitals.py", "history", origin],
        "pagecheck": ["pagecheck.py", "all", a.url],
        "redirect_chain": ["sitecheck.py", "redirects", "--urls", str(urls_file)],
        "sitegraph_crawl": ["sitegraph.py", "crawl", "--url", origin, "--max-pages",
                            str(a.max_pages), "--delay", "0.3", "--out", graph],
    }
    if page_state is None:
        jobs["slop"] = ["slop.py", "scan", "--html", str(page_file)]
        jobs["extractable"] = ["geo.py", "extractable", "--root", str(page_dir)]
    else:
        result["instruments"]["slop"] = dict(page_state)
        result["instruments"]["extractable"] = dict(page_state)

    live = not a.no_live_apis
    gsc_prop = a.gsc_property
    if live and not gsc_prop:
        sites = run("gsc_sites", ["gsc.py", "sites"], 90)
        if sites["state"] == "ok":
            for p_ in sites["data"].get("properties") or []:
                pid = p_.get("property") or ""
                dom = pid.replace("sc-domain:", "")
                if pid.startswith("sc-domain:") and (host == dom or host.endswith("." + dom)):
                    gsc_prop = pid
                elif urllib.parse.urlsplit(pid).netloc.lower() == u.netloc.lower():
                    gsc_prop = gsc_prop or pid
        else:
            result["instruments"]["gsc_inspect"] = {**sites, "argv": ["gsc.py", "sites"]}
    if live and gsc_prop:
        end = date.today() - timedelta(days=1)
        jobs["gsc_inspect"] = ["gsc.py", "--property", gsc_prop, "inspect", "--url", a.url]
        jobs["gsc_queries"] = ["gsc.py", "--property", gsc_prop, "query",
                               "--start", str(end - timedelta(days=89)), "--end", str(end),
                               "--dimensions", "query", "--limit", "50",
                               "--filter", f"page:equals:{a.url}"]
    elif live and "gsc_inspect" not in result["instruments"]:
        result["instruments"]["gsc_inspect"] = {"state": "no_key",
                                                "reason": f"no Search Console property covers {host}"}
    if live:
        jobs["bing_urlinfo"] = ["bing.py", "urlinfo", "--url", a.url]
        jobs["bing_pagequeries"] = ["bing.py", "pagequeries", "--page", a.url, "--limit", "30"]
        if a.keyword:
            argv = ["geo.py", "ask", "--query", a.keyword, "--domain", host]
            if a.brand:
                argv += ["--brand", a.brand]
            jobs["geo_ask"] = argv
        else:
            result["instruments"]["geo_ask"] = {"state": "skipped",
                                                "reason": "no --keyword given"}
    else:
        for k in ("gsc_inspect", "gsc_queries", "bing_urlinfo", "bing_pagequeries", "geo_ask"):
            result["instruments"][k] = {"state": "skipped", "reason": "--no-live-apis"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run, k, v, T): k for k, v in jobs.items()}
        for f in concurrent.futures.as_completed(futs):
            result["instruments"][futs[f]] = f.result()

    crawl = result["instruments"].get("sitegraph_crawl", {})
    if crawl.get("state") == "ok" and Path(graph).exists():
        for k, argv in (("sitegraph_report", ["sitegraph.py", "report", "--graph", graph]),
                        ("sitegraph_broken", ["sitegraph.py", "broken", "--graph", graph]),
                        ("sitegraph_depth", ["sitegraph.py", "depth", "--graph", graph])):
            result["instruments"][k] = run(k, argv, T)
        result["instruments"]["sitegraph_target"] = {"state": "ok",
                                                     "data": target_in_graph(graph, a.url)}
    if gsc_prop:
        result["gsc_property"] = gsc_prop

    states = {}
    for v in result["instruments"].values():
        states[v["state"]] = states.get(v["state"], 0) + 1
    result["coverage"] = states
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "out": a.out, "coverage": states}))
    else:
        print(text)


if __name__ == "__main__":
    main()
