#!/usr/bin/env python3
"""vitals.py - whole-site Core Web Vitals, by TEMPLATE rather than by URL.

`pagecheck.py vitals` measures one URL through PageSpeed Insights. That is the
right tool for one page and the wrong shape for a site: PSI takes ~20s per URL
and is quota-limited, so a 5,388-URL sitemap is not a sweep you can run, and
running it on a sample of arbitrary URLs answers a question nobody asked.

TWO THINGS MAKE A WHOLE-SITE SWEEP DIFFERENT FROM N SINGLE-PAGE CHECKS.

1. **On a generated site the unit is the TEMPLATE, not the page.** 2,234 map
   pages share one layout; measuring 30 of them 30 times reports the template 30
   times and calls it a site. So this groups URLs into templates by path shape,
   samples within each, and reports per-template - which is also the level at
   which a fix gets applied.

2. **CrUX field data is per-ORIGIN as well as per-URL, and most URLs have
   none.** CrUX needs enough real traffic to report, so on any large site the
   overwhelming majority of URLs come back with empty field data. Read naively
   that is a uniform result that looks like a finding and means "not enough
   traffic" - the same shape as the 44-of-44 slop verdict. The origin-level
   record is the one that always exists, and it is the ranking-relevant number,
   so it is fetched once and reported separately from the lab proxies.

KEYLESS BY DEFAULT. The lab proxies below need no credential and no browser:
TTFB, transfer size, compression, render-blocking CSS/JS in the head, image
dimension and lazy-loading hygiene, DOM size, subresource count and third-party
origins. They are PROXIES, not the metrics - LCP, CLS and INP cannot be
measured without rendering, and this says so rather than inventing them. PSI is
an opt-in enhancement (`--psi N`), spent on N representatives per template.

    vitals.py sweep --sitemap https://example.com/sitemap.xml
    vitals.py sweep --urls-file urls.txt --per-template 5 --psi 1
    vitals.py page https://example.com/       # one URL, keyless
    vitals.py origin https://example.com      # CrUX field data (needs a credential)
    vitals.py control                         # prove the parser discriminates

Stdlib only.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse, uniform_verdict  # noqa: E402
from providers import BROWSER_UA, http  # noqa: E402

# Thresholds. Deliberately few, and each one is a documented Google guideline or
# a measured proxy for one - not a taste preference. A tool that flags on taste
# produces a list nobody finishes.
LIMITS = {
    "ttfb_ms": 800,            # Google: "good" server response is under 800ms
    "html_kb": 100,            # transfer size of the document alone
    "render_blocking": 4,      # blocking CSS + JS in <head>
    "dom_nodes": 1500,         # Lighthouse warns above ~1,400
    "third_parties": 5,
}

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class PageParser(HTMLParser):
    """⚠ An HTMLParser, never a regex.

    An earlier image audit in this skill regexed raw HTML and reported 415
    images with no alt - every one of them the literal string `<img>` inside an
    HTML COMMENT explaining the aspect-ratio reasoning. The identical bug had
    already been found once in `agentcheck`. A comment, a `<script>` body and a
    `<template>` are not markup that ships, and only a parser knows that.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head = True
        self.depth_script = 0
        self.depth_style = 0
        self.nodes = 0
        self.images: list[dict] = []
        self.blocking_css: list[str] = []
        self.blocking_js: list[str] = []
        self.deferred_js = 0
        self.subresources: list[str] = []
        self.inline_css = 0
        self.inline_js = 0
        self.preloads: list[str] = []
        self.title = None
        self._in_title = False
        self.fonts: list[str] = []

    # -- helpers
    @staticmethod
    def _a(attrs):
        return {k.lower(): (v or "") for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self.nodes += 1
        a = self._a(attrs)
        if tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            self.depth_script += 1
            src = a.get("src")
            if src:
                self.subresources.append(src)
                # type=module is deferred by definition; async/defer say so.
                if self.in_head and not (
                        "async" in a or "defer" in a or a.get("type") == "module"):
                    self.blocking_js.append(src)
                else:
                    self.deferred_js += 1
        elif tag == "style":
            self.depth_style += 1
        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "")
            if "stylesheet" in rel and href:
                self.subresources.append(href)
                # media="print" + onload, or media that cannot match, is the
                # standard non-blocking pattern - not a defect.
                media = a.get("media", "all").lower()
                if self.in_head and media in ("", "all", "screen") and "onload" not in a:
                    self.blocking_css.append(href)
            elif "preload" in rel or "preconnect" in rel:
                self.preloads.append(f"{rel}:{a.get('as', '') or href}")
                if a.get("as") == "font" and href:
                    self.fonts.append(href)
        elif tag == "img":
            self.images.append({
                "src": a.get("src", "") or a.get("data-src", ""),
                "has_dims": bool(a.get("width")) and bool(a.get("height")),
                "lazy": a.get("loading", "").lower() == "lazy",
                "eager": a.get("loading", "").lower() == "eager"
                         or a.get("fetchpriority", "").lower() == "high",
                "alt": a.get("alt"),
            })
            if a.get("src"):
                self.subresources.append(a["src"])
        elif tag in ("iframe", "video", "audio", "source") and a.get("src"):
            self.subresources.append(a["src"])
        if tag in VOID:
            pass

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script":
            self.depth_script = max(0, self.depth_script - 1)
        elif tag == "style":
            self.depth_style = max(0, self.depth_style - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "head":
            self.in_head = False

    def handle_data(self, data):
        if self.depth_script:
            self.inline_js += len(data)
        elif self.depth_style:
            self.inline_css += len(data)
        elif self._in_title and self.title is None:
            self.title = data.strip()

    # Comments are NOT markup. Doing nothing here is the whole point.
    def handle_comment(self, data):
        return


def _timed_get(url: str, timeout: int = 25) -> dict:
    """Fetch with the phases timed SEPARATELY: connect, server, download.

    ⚠ THIS IS THE WHOLE REASON THIS FUNCTION EXISTS, and the first version got
    it wrong. Timing a plain `urlopen` measures DNS + TCP + TLS + server +
    download as one number and calls it TTFB. Measured 2026-09-01 from this
    container: `https://combatskirmish.net/` reported **5,940ms** and the tool
    duly flagged `slow_ttfb`, severity high, "server/CDN work". The control -
    the same probe against cloudflare.com and example.com, plus a raw socket
    breakdown - showed DNS alone took **1,446ms** here while the server itself
    answered in **240ms**. The site was fine; the container's resolver was not,
    and the finding pointed at the wrong system entirely.

    So: `connect_ms` (DNS+TCP+TLS) is reported and EXCLUDED from `ttfb_ms`,
    which is now server think-time plus one RTT - the thing a server or CDN
    change can actually move.
    """
    import http.client
    u = urllib.parse.urlsplit(url)
    host, scheme = u.netloc, (u.scheme or "https")
    path = (u.path or "/") + (("?" + u.query) if u.query else "")
    t0 = time.time()
    try:
        conn = (http.client.HTTPSConnection(host, timeout=timeout) if scheme == "https"
                else http.client.HTTPConnection(host, timeout=timeout))
        conn.connect()
        connect_ms = int((time.time() - t0) * 1000)

        t1 = time.time()
        conn.request("GET", path, headers={
            "Host": u.hostname or host, "User-Agent": BROWSER_UA,
            "Accept": "text/html,*/*", "Accept-Encoding": "gzip, deflate",
            "Connection": "close"})
        resp = conn.getresponse()
        ttfb = int((time.time() - t1) * 1000)

        t2 = time.time()
        raw = resp.read()
        download_ms = int((time.time() - t2) * 1000)
        status, hdrs = resp.status, {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "connect_ms": int((time.time() - t0) * 1000)}

    # A redirect is not the page. Follow one hop so `sweep` measures what a
    # visitor gets, and record that it happened.
    loc = hdrs.get("location")
    if status in (301, 302, 303, 307, 308) and loc:
        nxt = urllib.parse.urljoin(url, loc)
        if nxt != url:
            r2 = _timed_get(nxt, timeout)
            if r2.get("ok"):
                r2["redirected_from"] = url
                r2["redirect_status"] = status
            return r2

    enc = (hdrs.get("content-encoding") or "").lower()
    wire = len(raw)
    body = raw
    if "gzip" in enc:
        import gzip as _g
        try:
            body = _g.decompress(raw)
        except Exception:
            body = raw
    elif "deflate" in enc:
        import zlib as _z
        try:
            body = _z.decompress(raw)
        except Exception:
            body = raw
    return {"ok": True, "status": status, "connect_ms": connect_ms, "ttfb_ms": ttfb,
            "download_ms": download_ms, "total_ms": connect_ms + ttfb + download_ms,
            "wire_bytes": wire, "html_bytes": len(body),
            "compressed": bool(enc), "encoding": enc or None,
            "ctype": hdrs.get("content-type", ""),
            "body": body.decode("utf-8", "replace"), "final_url": url}


# A host that is known fast and is not ours. Measured alongside a sweep so a
# slow LOCAL network is visible as a network finding rather than as a verdict
# about the site - the mistake this file's own first version made.
BASELINE_HOST = "https://www.cloudflare.com/"


def network_baseline(timeout: int = 15) -> dict:
    """What does this machine's network cost, before the site is involved?"""
    r = _timed_get(BASELINE_HOST, timeout)
    if not r.get("ok"):
        return {"ok": False, "host": BASELINE_HOST, "error": r.get("error"),
                "note": "no baseline - treat every timing below as uncontrolled"}
    return {"ok": True, "host": BASELINE_HOST, "connect_ms": r["connect_ms"],
            "ttfb_ms": r["ttfb_ms"],
            "note": ("subtract nothing - just read it. If this connect_ms is itself "
                     "large, DNS/TLS on this machine is slow and no timing here is a "
                     "statement about the site.")}


def probe_page(url: str, timeout: int = 25, repeat: int = 2) -> dict:
    """Keyless lab PROXIES for one URL. Never claims to be LCP/CLS/INP.

    ⚠ TTFB IS SAMPLED TWICE, and the finding is raised on the FASTER run.

    One sample cannot tell a cold cache from a slow page, and the difference is
    the whole verdict. Measured 2026-09-01 on combatskirmish.net: `/leaderboard`
    returned **12,065ms** on the sweep's single sample and **70-94ms** on three
    repeats - it has a 5-minute server-side cache and the sweep happened to miss
    it. Reported as-is that is a false alarm on the site's own leaderboard.
    `/servers/de_dust2` returned **4,188 / 4,186 / 4,307ms** on the same
    treatment: reproducible, and a real finding on a 1,404-URL template.

    So `ttfb_ms` is the reproducible FLOOR and `ttfb_cold_ms` is the first
    sample. A large gap between them is a cache-miss cost, which is a different
    (and often acceptable) problem from a slow page - and it is reported as its
    own finding rather than folded into the headline."""
    r = _timed_get(url, timeout)
    if not r.get("ok"):
        return {"url": url, "ok": False, "error": r.get("error")}
    cold = r["ttfb_ms"]
    for _ in range(max(0, repeat - 1)):
        again = _timed_get(url, timeout)
        if again.get("ok"):
            r["ttfb_ms"] = min(r["ttfb_ms"], again["ttfb_ms"])
            r["connect_ms"] = min(r["connect_ms"], again["connect_ms"])
    r["ttfb_cold_ms"] = cold
    if "html" not in (r.get("ctype") or "").lower():
        return {"url": url, "ok": False, "status": r["status"],
                "error": f"not HTML ({r.get('ctype')}) - nothing to measure"}

    p = PageParser()
    try:
        p.feed(r["body"])
    except Exception as e:                                        # noqa: BLE001
        return {"url": url, "ok": False, "error": f"parse failed: {type(e).__name__}: {e}"}

    origin = urllib.parse.urlsplit(url).netloc.lower()
    third = sorted({urllib.parse.urlsplit(s).netloc.lower()
                    for s in p.subresources
                    if s.startswith(("http://", "https://"))
                    and urllib.parse.urlsplit(s).netloc.lower() not in ("", origin)})

    imgs = p.images
    # "Below the fold" is not knowable without rendering. The first few images
    # are the LCP candidates and SHOULD be eager; the rest should be lazy. Any
    # rule sharper than that would be inventing a viewport.
    late = imgs[3:]
    findings = []

    def flag(sev, rule, detail, fix):
        findings.append({"severity": sev, "rule": rule, "detail": detail, "fix": fix})

    if r["ttfb_ms"] > LIMITS["ttfb_ms"]:
        flag("high", "slow_ttfb",
             f"{r['ttfb_ms']}ms server response (guideline {LIMITS['ttfb_ms']}ms); "
             f"connect (DNS+TCP+TLS) was a separate {r['connect_ms']}ms and is NOT "
             f"counted here",
             "server/CDN work - caching, origin latency. Not a front-end fix.")
    if cold > max(LIMITS["ttfb_ms"], r["ttfb_ms"] * 4) and r["ttfb_ms"] <= LIMITS["ttfb_ms"]:
        flag("low", "cold_cache_cost",
             f"first request {cold}ms vs {r['ttfb_ms']}ms warm - a cache miss costs "
             f"{cold - r['ttfb_ms']}ms",
             "usually fine for users and expensive for a crawler, which arrives cold. "
             "Worth pre-warming only on templates a bot fetches in bulk.")
    if r["connect_ms"] > 1500:
        flag("info", "slow_connect_from_here",
             f"{r['connect_ms']}ms of DNS+TCP+TLS before the server was asked anything",
             "this is usually the MEASURING machine's resolver, not the site - compare "
             "against `network_baseline` before acting on it")
    if not r["compressed"]:
        flag("high", "html_not_compressed",
             f"{r['html_bytes'] // 1024}KB served uncompressed",
             "enable gzip/brotli for text/html at the edge")
    kb = r["wire_bytes"] // 1024
    if kb > LIMITS["html_kb"]:
        flag("medium", "heavy_document", f"{kb}KB on the wire for the HTML alone",
             "usually inlined CSS/JS or a very large DOM")
    blocking = len(p.blocking_css) + len(p.blocking_js)
    if blocking > LIMITS["render_blocking"]:
        flag("medium", "render_blocking_head",
             f"{len(p.blocking_css)} stylesheets + {len(p.blocking_js)} scripts block "
             f"the first paint",
             "defer/async the scripts; inline critical CSS and load the rest with "
             "media=print+onload")
    if p.nodes > LIMITS["dom_nodes"]:
        flag("low", "large_dom", f"~{p.nodes} elements",
             "Lighthouse warns above ~1,400; usually a very long list or table")
    if len(third) > LIMITS["third_parties"]:
        flag("medium", "third_party_origins", f"{len(third)}: {', '.join(third[:8])}",
             "each is a DNS+TLS handshake before anything it serves can render")
    no_dims = [i for i in imgs if not i["has_dims"]]
    if no_dims:
        flag("medium", "images_without_dimensions",
             f"{len(no_dims)} of {len(imgs)} <img> have no width/height",
             "set width/height (or an aspect-ratio box in CSS) or they shift the "
             "layout as they load - this is CLS")
    not_lazy = [i for i in late if not i["lazy"] and not i["eager"]]
    if not_lazy:
        flag("low", "late_images_not_lazy",
             f"{len(not_lazy)} images past the first 3 are not loading=lazy",
             "add loading=lazy below the fold; keep the LCP image eager")
    if imgs and not any(i["eager"] for i in imgs[:3]):
        flag("low", "no_lcp_candidate_prioritised",
             "none of the first 3 images is eager/fetchpriority=high",
             "mark the hero image fetchpriority=high so it is not queued behind "
             "lazy ones")

    return {
        "url": url, "ok": True, "status": r["status"], "final_url": r["final_url"],
        "connect_ms": r["connect_ms"], "ttfb_ms": r["ttfb_ms"],
        "ttfb_cold_ms": r["ttfb_cold_ms"],
        "download_ms": r["download_ms"], "total_ms": r["total_ms"],
        "timing_note": ("ttfb_ms is SERVER time plus one RTT; DNS/TCP/TLS is "
                        "connect_ms and is excluded. Conflating them is how a "
                        "1.4s local DNS lookup became a 'slow server' finding. "
                        "ttfb_ms is the faster of two samples - the reproducible "
                        "floor; ttfb_cold_ms is the first."),
        "wire_kb": round(r["wire_bytes"] / 1024, 1),
        "html_kb": round(r["html_bytes"] / 1024, 1),
        "compressed": r["compressed"], "encoding": r["encoding"],
        "dom_nodes": p.nodes,
        "render_blocking": {"css": len(p.blocking_css), "js": len(p.blocking_js),
                            "deferred_js": p.deferred_js},
        "inline_css_kb": round(p.inline_css / 1024, 1),
        "inline_js_kb": round(p.inline_js / 1024, 1),
        "images": {"total": len(imgs), "without_dimensions": len(no_dims),
                   "late_not_lazy": len(not_lazy),
                   "prioritised": sum(1 for i in imgs if i["eager"])},
        "subresources": len(p.subresources),
        "third_party_origins": third,
        "preloads": p.preloads[:10],
        "findings": findings,
        "verdict": ("fail" if any(f["severity"] == "high" for f in findings)
                    else "warn" if findings else "pass"),
    }


# ------------------------------------------------------------------ templates
_LOCALE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$", re.I)


def template_of(url: str) -> str:
    """Collapse a URL to the TEMPLATE it renders.

    A generated site has tens of URLs per layout and thousands per silo.
    Measuring 30 pages of one template reports the template 30 times, so the
    grouping has to happen before the sampling, not after."""
    path = urllib.parse.urlsplit(url).path or "/"
    segs = [s for s in path.strip("/").split("/") if s]
    if not segs:
        return "/"
    out = []
    if _LOCALE.match(segs[0]) and len(segs[0]) <= 5:
        out.append("{locale}")
        segs = segs[1:]
        if not segs:
            return "/{locale}/"
    out.append(segs[0])
    if len(segs) > 1:
        out.extend("*" for _ in segs[1:])
    return "/" + "/".join(out)


def _sitemap_urls(url: str, cap: int = 20000) -> tuple[list[str], str | None]:
    """URLs from a sitemap, following a sitemap INDEX one level."""
    r = http(url, timeout=60)
    if not r.ok:
        return [], f"sitemap {url} -> HTTP {r.get('status')} {r.get('error') or ''}".strip()
    body = r.text()
    if "<sitemapindex" in body[:2000]:
        children = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        urls: list[str] = []
        for c in children:
            got, _err = _sitemap_urls(c, cap)
            urls.extend(got)
            if len(urls) >= cap:
                break
        return urls[:cap], None
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)[:cap], None


def _median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def sweep(urls: list[str], per_template: int = 3, workers: int = 6,
          psi: int = 0, strategy: str = "mobile", timeout: int = 25,
          repeat: int = 2) -> dict:
    if not urls:
        return refuse("vitals-sweep", "no URLs given - pass --sitemap, --urls-file or --url")

    groups: dict[str, list[str]] = {}
    for u in urls:
        groups.setdefault(template_of(u), []).append(u)

    # Deterministic sampling: evenly spaced through each group rather than the
    # first N, because a sitemap is usually sorted and the first N of a silo are
    # its oldest or alphabetically-first pages, not a sample of it.
    sample: list[tuple[str, str]] = []
    for tpl, us in sorted(groups.items()):
        n = min(per_template, len(us))
        step = max(1, len(us) // n)
        sample.extend((tpl, us[i * step]) for i in range(n))

    # Measured BEFORE the site, so a slow local network is on the record rather
    # than distributed silently across every row as a verdict about the site.
    base = network_baseline()

    results: dict[str, list[dict]] = {t: [] for t in groups}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe_page, u, timeout, repeat): (t, u) for t, u in sample}
        for f in cf.as_completed(futs):
            t, u = futs[f]
            try:
                results[t].append(f.result())
            except Exception as e:                                # noqa: BLE001
                results[t].append({"url": u, "ok": False, "error": str(e)})

    # A sweep where NOTHING was readable is a broken reader, not a broken site.
    read = [r for rs in results.values() for r in rs if r.get("ok")]
    attempted = sum(len(rs) for rs in results.values())
    if attempted and not read:
        return refuse("vitals-sweep",
                      f"0 of {attempted} sampled URLs could be read - that is the fetcher "
                      f"or the host, not the site's performance",
                      examples=[r.get("error") for rs in results.values() for r in rs][:5])

    rows = []
    for tpl in sorted(groups):
        rs = [r for r in results[tpl] if r.get("ok")]
        failed = [r for r in results[tpl] if not r.get("ok")]
        if not rs:
            rows.append({"template": tpl, "urls_in_template": len(groups[tpl]),
                         "sampled": len(results[tpl]), "readable": 0,
                         "errors": [r.get("error") for r in failed][:3]})
            continue
        rules: dict[str, int] = {}
        for r in rs:
            for f in r["findings"]:
                rules[f["rule"]] = rules.get(f["rule"], 0) + 1
        # A row must explain its own verdict. A median next to a `fail` reads as
        # a tool bug when the slow sample is the one that fired the rule - and
        # on a template with per-page query cost (measured: /servers/* ranges
        # from 74ms to 4,300ms depending on the map) the median is the wrong
        # summary for a rule that triggers on the worst case.
        slowest = max(rs, key=lambda r: r["ttfb_ms"])
        rows.append({
            "template": tpl,
            "urls_in_template": len(groups[tpl]),
            "sampled": len(rs),
            "verdict": ("fail" if any(r["verdict"] == "fail" for r in rs)
                        else "warn" if any(r["verdict"] == "warn" for r in rs) else "pass"),
            "median_ttfb_ms": _median([r["ttfb_ms"] for r in rs]),
            "median_connect_ms": _median([r["connect_ms"] for r in rs]),
            "median_ttfb_cold_ms": _median([r["ttfb_cold_ms"] for r in rs]),
            "slowest_ttfb_ms": slowest["ttfb_ms"],
            "slowest_url": slowest["url"] if slowest["ttfb_ms"] > LIMITS["ttfb_ms"] else None,
            "ttfb_spread": ([r["ttfb_ms"] for r in sorted(rs, key=lambda x: x["ttfb_ms"])]
                            if slowest["ttfb_ms"] > LIMITS["ttfb_ms"] else None),
            "median_wire_kb": _median([r["wire_kb"] for r in rs]),
            "median_dom_nodes": _median([r["dom_nodes"] for r in rs]),
            "compressed": all(r["compressed"] for r in rs),
            "findings_by_rule": dict(sorted(rules.items(), key=lambda kv: -kv[1])),
            "example": rs[0]["url"],
            "unreadable": len(failed),
        })

    scored = [r for r in rows if r.get("verdict")]
    worst = sorted(scored, key=lambda r: (
        {"fail": 0, "warn": 1, "pass": 2}[r["verdict"]], -(r.get("slowest_ttfb_ms") or 0)))

    out = {
        "ok": True, "check": "vitals-sweep",
        "network_baseline": base,
        "urls_given": len(urls), "templates": len(groups),
        "sampled": len(sample), "readable": len(read),
        "per_template": per_template,
        "templates_failing": sum(1 for r in scored if r["verdict"] == "fail"),
        "templates_warning": sum(1 for r in scored if r["verdict"] == "warn"),
        "uniform_verdict_tell": uniform_verdict([r["verdict"] for r in scored],
                                                subject="templates"),
        "worst_first": worst,
        "reading": (
            "These are LAB PROXIES measured without rendering: TTFB, transfer size, "
            "compression, render-blocking resources in <head>, image dimension and "
            "lazy-loading hygiene, DOM size and third-party origins. They are NOT "
            "LCP, CLS or INP - those need a browser, and the ranking-relevant "
            "versions of them need REAL USERS. Run `vitals.py origin <origin>` for "
            "the CrUX field record, which is the number Google actually uses. "
            "Rows are per TEMPLATE because a fix is applied per template; "
            "`urls_in_template` is how many pages each finding is worth. "
            "READ `network_baseline` FIRST: ttfb_ms is server time only, but a large "
            "connect_ms there means this machine's DNS/TLS is slow and every timing "
            "below is uncontrolled."
        ),
    }
    if psi:
        out["psi"] = _psi_sample(worst, psi, strategy)
    return out


def _psi_sample(rows: list[dict], n: int, strategy: str) -> dict:
    """Spend the PSI quota on the WORST templates, not on the first ones."""
    try:
        from pagecheck import check_vitals
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "error": f"cannot import pagecheck: {e}"}
    picked = [r for r in rows if r.get("example")][:n]
    out = []
    for r in picked:
        v = check_vitals(r["example"], strategy)
        out.append({"template": r["template"], "url": r["example"],
                    "ok": v.get("ok"), "performance_score": v.get("performance_score"),
                    "lab": v.get("lab"), "field_crux": v.get("field_crux"),
                    "error": v.get("error")})
    return {"ok": True, "strategy": strategy, "sampled_templates": len(out), "results": out,
            "note": "empty field_crux means the URL has too little traffic for CrUX to "
                    "report - that is not a score of zero. The ORIGIN record always "
                    "exists: `vitals.py origin <origin>`."}


def origin_field(origin: str, strategy: str = "mobile") -> dict:
    """The CrUX ORIGIN record - real-user data, and the one Google ranks on.

    Reported separately from everything else because it is a different KIND of
    measurement. Every lab number here is a proxy; this one is the metric."""
    try:
        from pagecheck import check_vitals
    except Exception as e:                                        # noqa: BLE001
        return refuse("vitals-origin", f"cannot import pagecheck: {e}")
    o = urllib.parse.urlsplit(origin)
    root = f"{o.scheme or 'https'}://{o.netloc or o.path}"
    v = check_vitals(root, strategy)
    if not v.get("ok"):
        return {"ok": False, "check": "vitals-origin", "origin": root,
                "error": v.get("error"), "how_to_fix": v.get("how_to_fix"),
                "note": "a missing credential is 'cannot ask', not a bad score"}
    field = v.get("field_crux") or {}
    have = [k for k, m in field.items() if (m or {}).get("p75") is not None]
    return {
        "ok": True, "check": "vitals-origin", "origin": root, "strategy": strategy,
        "field_crux": field,
        "metrics_reported": have,
        "has_field_data": bool(have),
        "performance_score": v.get("performance_score"),
        "lab": v.get("lab"),
        "note": ("field_crux is REAL-USER data and is the ranking-relevant number. "
                 "If metrics_reported is empty the ORIGIN has too little traffic for "
                 "CrUX - that is an unanswered question, not a failing score."),
    }


# -------------------------------------------------------------------- control
CONTROL_HTML = """<!doctype html><html><head>
<title>T</title>
<!-- <img src="/ghost.png"> a commented image, and <script src="/ghost.js"></script> -->
<link rel="stylesheet" href="/a.css">
<link rel="stylesheet" href="/print.css" media="print">
<link rel="preload" as="image" href="/hero.webp">
<script src="/blocking.js"></script>
<script src="/deferred.js" defer></script>
<script src="https://cdn.other.test/x.js" async></script>
<style>.a{color:red}</style>
<script>var x = 1;</script>
</head><body>
<img src="/hero.webp" width="800" height="600" fetchpriority="high" alt="hero">
<img src="/a.png" width="10" height="10" alt="a">
<img src="/b.png" width="10" height="10" alt="b">
<img src="/c.png" alt="c">
<img src="/d.png" alt="d" loading="lazy">
<p>Body text.</p>
</body></html>"""


def run_control() -> dict:
    """Prove the parser and the template grouper discriminate.

    The comment in CONTROL_HTML is load-bearing: it contains a well-formed
    `<img>` and a well-formed `<script src>`. A regex-based reader counts both,
    and that exact bug has now been found twice in this skill - 415 phantom
    images in an image audit, and structural regexes running against raw HTML in
    `agentcheck`."""
    c = Controls("vitals-control")
    p = PageParser()
    p.feed(CONTROL_HTML)

    c.check("commented_out_markup_is_not_counted",
            len(p.images) == 5 and "/ghost.png" not in [i["src"] for i in p.images],
            f"{len(p.images)} images: {[i['src'] for i in p.images]}")
    c.check("a_commented_script_is_not_render_blocking",
            "/ghost.js" not in p.blocking_js, str(p.blocking_js))

    c.check("a_head_stylesheet_blocks", "/a.css" in p.blocking_css, str(p.blocking_css))
    c.check("a_print_stylesheet_does_not_block",
            "/print.css" not in p.blocking_css,
            "media=print is the standard non-blocking pattern, not a defect")
    c.check("a_bare_head_script_blocks", "/blocking.js" in p.blocking_js)
    c.check("a_deferred_script_does_not_block", "/deferred.js" not in p.blocking_js)
    c.check("an_async_script_does_not_block",
            "https://cdn.other.test/x.js" not in p.blocking_js)
    c.check("deferred_scripts_are_still_counted", p.deferred_js == 2, str(p.deferred_js))

    c.check("missing_dimensions_are_detected",
            sum(1 for i in p.images if not i["has_dims"]) == 2,
            str([i["src"] for i in p.images if not i["has_dims"]]))
    c.check("sized_images_are_not_flagged",
            any(i["has_dims"] for i in p.images))
    c.check("an_lcp_candidate_is_recognised", p.images[0]["eager"] is True)
    c.check("lazy_is_read", any(i["lazy"] for i in p.images))

    c.check("inline_css_is_measured", p.inline_css > 0)
    c.check("inline_js_is_measured", p.inline_js > 0)
    c.check("inline_script_text_is_not_prose", p.title == "T", str(p.title))
    c.check("preloads_are_read", any("preload" in x for x in p.preloads), str(p.preloads))
    c.check("dom_nodes_are_counted", p.nodes > 10, str(p.nodes))

    # THE TEMPLATE GROUPER. Too coarse and every page is one template (the sweep
    # reports one row for the site); too fine and every URL is its own (the
    # sweep degenerates into the per-URL run this exists to replace).
    c.check("pages_of_one_silo_share_a_template",
            template_of("https://x.test/maps/de_dust2")
            == template_of("https://x.test/maps/cs_office") == "/maps/*",
            template_of("https://x.test/maps/de_dust2"))
    c.check("different_silos_do_not_collide",
            template_of("https://x.test/maps/a") != template_of("https://x.test/guides/a"))
    c.check("a_locale_prefix_is_recognised",
            template_of("https://x.test/zh/maps/a") == "/{locale}/maps/*",
            template_of("https://x.test/zh/maps/a"))
    c.check("a_locale_and_the_root_silo_are_distinct",
            template_of("https://x.test/zh/maps/a") != template_of("https://x.test/maps/a"))
    c.check("the_homepage_is_its_own_template", template_of("https://x.test/") == "/")
    c.check("depth_is_reflected",
            template_of("https://x.test/a/b/c") != template_of("https://x.test/a/b"))
    c.check("a_two_letter_silo_is_not_mistaken_for_a_locale_forever",
            template_of("https://x.test/zh/") == "/{locale}/",
            "a bare locale root must still group, not vanish")

    c.check("the_thresholds_are_present", set(LIMITS) >= {"ttfb_ms", "dom_nodes"})

    # THE TIMING SPLIT - the defect this file shipped and its control caught.
    # A local fetch of a file:// URL is not possible through http.client, so the
    # split is proven structurally: the three phases must be distinct keys, and
    # ttfb must never be the sum.
    probe = _timed_get(BASELINE_HOST, timeout=20)
    if probe.get("ok"):
        c.check("connect_is_timed_separately_from_the_server",
                {"connect_ms", "ttfb_ms", "download_ms"} <= set(probe),
                str(sorted(probe)))
        c.check("ttfb_is_not_the_total",
                probe["ttfb_ms"] <= probe["total_ms"]
                and probe["total_ms"] == probe["connect_ms"] + probe["ttfb_ms"]
                + probe["download_ms"],
                f"connect={probe['connect_ms']} ttfb={probe['ttfb_ms']} "
                f"dl={probe['download_ms']} total={probe['total_ms']}")
        c.check("a_known_fast_host_does_not_look_slow_on_the_server_metric",
                probe["ttfb_ms"] < 2000,
                f"{probe['ttfb_ms']}ms from {BASELINE_HOST} - if this is large the "
                f"NETWORK is slow, and no timing this tool reports is about your site")
    else:
        c.check("the_network_baseline_is_reachable", False, str(probe.get("error")))
    c.check("a_single_sample_cannot_separate_cold_from_slow",
            "ttfb_cold_ms" in probe_page.__doc__ and "12,065" in probe_page.__doc__,
            "the measured case that motivates two samples must stay documented")
    c.check("the_uniform_tell_is_wired",
            (uniform_verdict(["warn"] * 12, subject="templates") or {}).get("population") == 12)
    return c.verdict(images_parsed=len(p.images))


def _read_urls(a) -> tuple[list[str], str | None]:
    urls = list(a.url or [])
    if a.urls_file:
        try:
            urls += [ln.strip() for ln in Path(a.urls_file).read_text(
                encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        except OSError as e:
            return [], f"cannot read {a.urls_file}: {e}"
    if a.sitemap:
        got, err = _sitemap_urls(a.sitemap)
        if err:
            return [], err
        urls += got
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep", help="whole site, sampled per template (keyless)")
    s.add_argument("--sitemap", help="sitemap or sitemap-index URL")
    s.add_argument("--urls-file", help="one URL per line")
    s.add_argument("--url", action="append", help="a URL (repeatable)")
    s.add_argument("--per-template", type=int, default=3)
    s.add_argument("--workers", type=int, default=6)
    s.add_argument("--timeout", type=int, default=25)
    s.add_argument("--repeat", type=int, default=2,
                   help="TTFB samples per URL; the finding uses the fastest (default 2)")
    s.add_argument("--psi", type=int, default=0,
                   help="also run PageSpeed Insights on the N worst templates")
    s.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])

    g = sub.add_parser("page", help="one URL, keyless lab proxies")
    g.add_argument("url")
    g.add_argument("--timeout", type=int, default=25)
    g.add_argument("--repeat", type=int, default=2,
                   help="TTFB samples; the finding uses the fastest (default 2)")

    o = sub.add_parser("origin", help="CrUX ORIGIN field data (needs a PSI credential)")
    o.add_argument("origin")
    o.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])

    sub.add_parser("control", help="prove the parser and grouper discriminate")

    a = ap.parse_args()
    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "page":
        out = probe_page(a.url, a.timeout, a.repeat)
    elif a.cmd == "origin":
        out = origin_field(a.origin, a.strategy)
    else:
        urls, err = _read_urls(a)
        out = (refuse("vitals-sweep", err) if err else
               sweep(urls, a.per_template, a.workers, a.psi, a.strategy, a.timeout,
                     a.repeat))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
