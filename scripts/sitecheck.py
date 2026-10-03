#!/usr/bin/env python3
"""sitecheck.py - the site-level technical checks the health workflow depends on.

`workflow-health.md` said to "always run" canonical-tag-audit, sitemap-audit
and redirect-audit, because each one's failure silently invalidates other work.
None of those skills was installed (checked 2026-10-03), so the three checks
the workflow called non-negotiable could not run. This is them, plus the cheap
host-level probes that sit next to them, in the skill's own idiom: stdlib,
redirects never followed silently, one row per (rule, url) with its evidence,
and a refusal rather than a verdict when the instrument cannot see.

  hosts      http->https and www<->apex: one PERMANENT hop to one canonical host
  soft404    does an unknown URL answer 404, or a 200 that indexes as a page
  redirects  hop-by-hop chains for a URL set: >1 hop, loops, temporary hops
  sitemap    every listed URL (or a sample): redirects, errors, noindex,
             canonical elsewhere - each one a URL the sitemap asks Google to
             index and the page itself refuses
  links      from a sitegraph.py graph: internal links that land on a redirect
             or an error, and indexable pages missing from the sitemap
  canonicals canonical targets that are not a 200 (a canonical to a 404 or a
             redirect makes the page unindexable while it looks fine)
  headers    HSTS and the three security headers, plus mixed ACTIVE content
  variants   trailing-slash and tracking-parameter twins served as duplicates
  all        hosts + soft404 + headers + sitemap sample + canonicals + variants
  diff       two saved runs -> new / fixed / persisting, keyed (rule, url)
  control    offline: every classifier fired both ways on synthetic input

    sitecheck.py all https://example.com --sample 150 --save
    sitecheck.py links --graph .seo/graph.json --origin https://example.com --sitemap https://example.com/sitemap.xml
    sitecheck.py diff --before .seo/sitecheck/a.json --after .seo/sitecheck/b.json

WHAT THIS IS NOT: a crawler (sitegraph.py is), a page-content audit (pagecheck,
contract), or a speed audit (vitals). Each finding names what to change; none
of them is scored, and no total is computed.

Stdlib only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import html as htmlmod
import json
import re
import secrets
import sys
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, BROWSER_UA  # noqa: E402
from controls import Controls, refuse  # noqa: E402

MAX_HOPS = 10
PERMANENT = (301, 308)
TEMPORARY = (302, 303, 307)
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Query keys that only ever identify a click source. A page served 200 under
# one of these with no canonical back to the clean URL is a duplicate that
# every shared link mints.
TRACKING_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                 "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid")

NOT_FOUND_WORDING = re.compile(
    r"\b(404|not found|page (?:does not|doesn't) exist|no longer available|"
    r"page (?:cannot|can't) be found|nothing (?:was )?found)\b", re.I)


def _f(sev, rule, url, detail, fix=None, **evidence):
    row = {"severity": sev, "rule": rule, "url": url, "detail": detail}
    if fix:
        row["fix"] = fix
    if evidence:
        row["evidence"] = evidence
    return row


def _sorted(findings):
    return sorted(findings, key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["rule"], f["url"]))


# ------------------------------------------------------------------ fetching


def hop(url: str, timeout: int = 20) -> dict:
    """One request, redirects NOT followed. The first response is the fact."""
    r = http(url, timeout=timeout, ua=BROWSER_UA, follow=False, retries=1,
             retry_on=(502, 503, 504))
    st = r.get("status")
    loc = r.get("location") or (r.get("headers") or {}).get("location")
    return {"url": url, "status": st, "location": urllib.parse.urljoin(url, loc) if loc else None,
            "headers": r.get("headers") or {}, "body": r.text() if st == 200 else "",
            "ctype": (r.get("ctype") or "").lower(), "error": r.get("error")}


def follow_chain(url: str, max_hops: int = MAX_HOPS, fetch=hop) -> list[dict]:
    """Hop by hop until a non-redirect, a loop, or max_hops. Each element is a
    hop with its status and Location, so the evidence is the chain itself."""
    chain, seen = [], set()
    cur = url
    for _ in range(max_hops + 1):
        h = fetch(cur)
        chain.append({k: h.get(k) for k in ("url", "status", "location", "error")})
        if h.get("status") not in PERMANENT + TEMPORARY or not h.get("location"):
            chain[-1]["_final"] = h
            return chain
        if h["location"] in seen or h["location"] == cur:
            chain.append({"url": h["location"], "status": None, "location": None,
                          "error": "loop"})
            return chain
        seen.add(cur)
        cur = h["location"]
    chain.append({"url": cur, "status": None, "location": None, "error": "max_hops"})
    return chain


def classify_chain(url: str, chain: list[dict]) -> list[dict]:
    out = []
    hops = [c for c in chain if c.get("status") in PERMANENT + TEMPORARY]
    last = chain[-1]
    ev = {"chain": [{"url": c["url"], "status": c["status"]} for c in chain]}
    if last.get("error") == "loop":
        return [_f("high", "redirect_loop", url, "the redirect chain loops back on itself",
                   "Break the cycle; a looping URL is unreachable for users and crawlers.", **ev)]
    if last.get("error") == "max_hops":
        return [_f("high", "redirect_chain_unterminated", url,
                   f"still redirecting after {MAX_HOPS} hops", None, **ev)]
    if len(hops) >= 2:
        out.append(_f("medium", "redirect_chain", url,
                      f"{len(hops)} redirects before the final URL",
                      "Point the first hop straight at the final URL. Each extra hop costs "
                      "crawl budget and delays discovery.", **ev))
    temp = [c for c in hops if c["status"] in TEMPORARY]
    if temp:
        out.append(_f("medium", "temporary_redirect", url,
                      f"{len(temp)} temporary ({', '.join(str(c['status']) for c in temp)}) "
                      f"hop(s) in the chain",
                      "Use 301/308 for a move that is meant to stay; a temporary redirect "
                      "keeps the OLD URL as the one Google indexes.", **ev))
    if hops and last.get("status") not in (200, None):
        out.append(_f("high", "redirect_to_error", url,
                      f"the chain ends in HTTP {last.get('status')}", None, **ev))
    return out


# ------------------------------------------------------------------ parsing


class _Head(HTMLParser):
    """canonical, meta robots, and every ACTIVE subresource URL - comments are
    never parsed as markup (the regex-over-raw-HTML bug this skill has hit
    twice)."""

    ACTIVE = {("script", "src"), ("iframe", "src"), ("embed", "src"), ("object", "data"),
              ("link", "href"), ("img", "src"), ("audio", "src"), ("video", "src"),
              ("source", "src"), ("track", "src")}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical, self.robots, self.titles, self.resources = None, "", 0, []
        self.lang, self.viewport = None, False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or None
        if tag == "title":
            self.titles += 1
        if tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href", "").strip()
        if tag == "meta":
            name = a.get("name", "").lower()
            if name in ("robots", "googlebot"):
                self.robots += " " + a.get("content", "").lower()
            if name == "viewport":
                self.viewport = True
        for t, attr in self.ACTIVE:
            if tag == t and a.get(attr):
                if tag == "link" and "stylesheet" not in a.get("rel", "").lower() and \
                        "preload" not in a.get("rel", "").lower():
                    continue
                self.resources.append((tag, a[attr].strip()))


def page_facts(html: str, headers: dict | None = None) -> dict:
    p = _Head()
    try:
        p.feed(html or "")
    except Exception:
        pass
    hdr = (headers or {}).get("x-robots-tag", "").lower()
    robots = (p.robots + " " + hdr).strip()
    return {"canonical": p.canonical, "noindex": "noindex" in robots or "none" in robots.split(),
            "robots": robots or None, "x_robots_tag": hdr or None, "titles": p.titles,
            "lang": p.lang, "viewport": p.viewport, "resources": p.resources}


def parse_sitemap(body: str) -> dict:
    """urlset or sitemapindex -> locs with lastmod. `&amp;` in a <loc> is XML,
    and an unescaped reader requests a URL that does not exist."""
    kind = ("index" if re.search(r"<sitemapindex\b", body[:4000], re.I) else
            "urlset" if re.search(r"<urlset\b", body[:4000], re.I) else None)
    rows = []
    for block in re.findall(r"<(?:url|sitemap)\b[^>]*>(.*?)</(?:url|sitemap)>", body, re.S | re.I):
        m = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S | re.I)
        if not m:
            continue
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S | re.I)
        loc = htmlmod.unescape(re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip()))
        rows.append({"loc": loc, "lastmod": lm.group(1).strip() if lm else None})
    return {"kind": kind, "entries": rows}


def read_sitemaps(src: str, limit: int = 50000) -> dict:
    """Follow an index (any depth, each file once). Failed files are LISTED -
    a sitemap that could not be read is not a sitemap with nothing in it."""
    todo, seen, urls, files, failed = [src], set(), [], [], []
    while todo and len(urls) < limit:
        cur = todo.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        r = http(cur, timeout=45, ua=BROWSER_UA, retries=1)
        raw = r.get("body") or b""
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        if r.get("status") != 200:
            failed.append({"sitemap": cur, "status": r.get("status"), "error": r.get("error")})
            continue
        doc = parse_sitemap(body)
        if doc["kind"] is None:
            failed.append({"sitemap": cur, "status": 200,
                           "error": "200 but neither <urlset> nor <sitemapindex> - an HTML page?"})
            continue
        files.append({"sitemap": cur, "kind": doc["kind"], "entries": len(doc["entries"])})
        if doc["kind"] == "index":
            todo.extend(e["loc"] for e in doc["entries"])
        else:
            urls.extend(e for e in doc["entries"] if len(urls) < limit)
    return {"urls": urls[:limit], "files": files, "failed": failed, "truncated": len(urls) >= limit}


def sitemaps_for(origin: str) -> list[str]:
    r = http(origin.rstrip("/") + "/robots.txt", timeout=20, ua=BROWSER_UA)
    found = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text()) if r.get("status") == 200 else []
    return found or [origin.rstrip("/") + "/sitemap.xml"]


def norm_url(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    host = (p.hostname or "").lower()
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    return urllib.parse.urlunsplit((p.scheme.lower(), host + port, p.path or "/", p.query, ""))


# ------------------------------------------------------------------- checks


def check_hosts(origin: str, fetch=hop) -> dict:
    """Every scheme x host variant must reach ONE canonical origin through one
    PERMANENT hop. Two variants both serving 200 is the whole site twice."""
    p = urllib.parse.urlsplit(origin)
    host = p.hostname or ""
    bare = host[4:] if host.startswith("www.") else host
    variants = [f"{s}://{h}/" for s in ("https", "http") for h in (bare, "www." + bare)]
    rows, finals = [], {}
    for v in variants:
        ch = follow_chain(v, fetch=fetch)
        final = ch[-1]
        rows.append({"variant": v, "chain": [{"url": c["url"], "status": c["status"]} for c in ch]})
        if final.get("status") == 200:
            finals[v] = final["url"]
    out = {"ok": True, "check": "sitecheck-hosts", "origin": origin, "variants": rows,
           "findings": []}
    answered = [r for r in rows if r["chain"][0]["status"] is not None]
    if not answered:
        return refuse("sitecheck-hosts", "no host variant answered at all - unreachable, not a finding")
    serving = sorted({v for v, f in finals.items() if f == v})
    if len(serving) > 1:
        out["findings"].append(_f(
            "high", "host_duplicate", origin,
            f"{len(serving)} variants each serve the site directly: {serving}",
            "Pick one and 301 the others to it; otherwise every page exists twice.",
            serving=serving))
    targets = sorted(set(finals.values()))
    if len(targets) > 1 and len(serving) <= 1:
        out["findings"].append(_f("high", "host_split", origin,
                                  f"variants end on different URLs: {targets}", None))
    for r in rows:
        ch = r["chain"]
        if ch[0]["status"] is None:
            continue
        sts = [c["status"] for c in ch if c["status"] in PERMANENT + TEMPORARY]
        if any(s in TEMPORARY for s in sts):
            out["findings"].append(_f(
                "medium", "host_temporary_redirect", r["variant"],
                f"host redirect uses {sts} - temporary", "Use 301 or 308.", chain=ch))
        if len(sts) >= 2:
            out["findings"].append(_f(
                "low", "host_redirect_chain", r["variant"],
                f"{len(sts)} hops to the canonical host (e.g. http->https->www)",
                "Redirect each variant straight to the final origin.", chain=ch))
        if r["variant"].startswith("http://") and len(ch) == 1 and ch[0]["status"] == 200:
            out["findings"].append(_f("high", "http_serves_content", r["variant"],
                                      "plain HTTP serves the page instead of redirecting", None))
    out["canonical_origin"] = targets[0] if len(targets) == 1 else None
    out["findings"] = _sorted(out["findings"])
    return out


def is_soft404(status, body: str, home_body: str = "") -> tuple[bool, str]:
    if status in (404, 410):
        return False, f"HTTP {status}"
    if status != 200:
        return False, f"HTTP {status} - not a soft 404, but not a clean 404 either"
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(
        r"<(script|style)\b.*?</\1>", " ", body or "", flags=re.S | re.I)))
    if home_body and body and body == home_body:
        return True, "200 with the HOMEPAGE's exact body - a catch-all"
    if NOT_FOUND_WORDING.search(text[:4000]):
        return True, "200 carrying not-found wording"
    return True, "200 for a URL that cannot exist"


def check_soft404(origin: str, fetch=hop) -> dict:
    o = origin.rstrip("/")
    probe = f"{o}/{secrets.token_hex(6)}-sitecheck-probe"
    h = fetch(probe)
    if h.get("status") is None:
        return refuse("sitecheck-soft404", f"the probe got no answer ({h.get('error')})")
    if h["status"] in PERMANENT + TEMPORARY:
        ch = follow_chain(probe, fetch=fetch)
        end = ch[-1]
        soft = end.get("status") == 200
        out = {"ok": True, "check": "sitecheck-soft404", "probe": probe,
               "status": h["status"], "redirects_to": h.get("location"),
               "findings": [_f("high", "unknown_url_redirects_to_200", probe,
                               f"an unknown URL {h['status']}s to {end['url']}, which answers 200 - "
                               f"Google treats a mass redirect to a live page as a soft 404",
                               "Answer unknown URLs with 404 or 410.")] if soft else []}
        return out
    home = fetch(o + "/")
    soft, why = is_soft404(h["status"], h.get("body", ""), home.get("body", ""))
    return {"ok": True, "check": "sitecheck-soft404", "probe": probe, "status": h["status"],
            "verdict": why,
            "findings": [_f("high", "soft_404", probe, why,
                            "Return a real 404 (or 410) status for unknown URLs. A catch-all "
                            "200 lets every typo and every dead link index as a page, and "
                            "makes optional files (llms.txt, /.well-known/*) read as present.")]
            if soft else []}


SECURITY_HEADERS = {
    "strict-transport-security": "HSTS - without it the first request of every visit can be "
                                 "downgraded to HTTP",
    "x-content-type-options": "nosniff",
    "referrer-policy": "how much of the URL leaks to other sites",
}


def header_findings(url: str, headers: dict) -> list[dict]:
    h = {k.lower(): v for k, v in (headers or {}).items()}
    out = []
    if url.startswith("https://"):
        for name, why in SECURITY_HEADERS.items():
            if name not in h:
                out.append(_f("low" if name != "strict-transport-security" else "medium",
                              f"missing_{name.replace('-', '_')}", url, f"no {name} header ({why})"))
        hsts = h.get("strict-transport-security", "")
        m = re.search(r"max-age\s*=\s*(\d+)", hsts)
        if hsts and (not m or int(m.group(1)) < 15552000):
            out.append(_f("low", "hsts_short_max_age", url,
                          f"HSTS max-age is {m.group(1) if m else 'missing'}; under 180 days "
                          f"(15552000s) gives little protection", None))
    csp = h.get("content-security-policy", "")
    if "x-frame-options" not in h and "frame-ancestors" not in csp:
        out.append(_f("low", "no_framing_policy", url,
                      "neither X-Frame-Options nor CSP frame-ancestors - the page can be framed"))
    return out


def mixed_content(url: str, resources: list[tuple[str, str]]) -> list[dict]:
    if not url.startswith("https://"):
        return []
    bad = [(t, r) for t, r in resources if r.lower().startswith("http://")]
    if not bad:
        return []
    return [_f("high" if any(t in ("script", "iframe", "link", "object", "embed") for t, _ in bad)
               else "medium", "mixed_content", url,
               f"{len(bad)} subresource(s) over plain HTTP on an HTTPS page "
               f"(browsers block active ones outright)",
               "Serve them over HTTPS or as protocol-relative paths.",
               resources=[f"{t}: {r}" for t, r in bad[:10]])]


def check_headers(url: str, fetch=hop) -> dict:
    h = fetch(url)
    if h.get("status") != 200:
        return refuse("sitecheck-headers", f"{url} answered HTTP {h.get('status')} - "
                      f"headers of an error page are not the site's headers")
    facts = page_facts(h["body"], h["headers"])
    f = header_findings(url, h["headers"]) + mixed_content(url, facts["resources"])
    if not facts["lang"]:
        f.append(_f("low", "html_lang_missing", url, "<html> has no lang attribute"))
    if not facts["viewport"]:
        f.append(_f("medium", "viewport_missing", url,
                    "no <meta name=viewport> - mobile-first indexing renders it as desktop"))
    if facts["titles"] > 1:
        f.append(_f("medium", "multiple_titles", url, f"{facts['titles']} <title> elements"))
    return {"ok": True, "check": "sitecheck-headers", "url": url, "findings": _sorted(f)}


def audit_url(url: str, fetch=hop) -> list[dict]:
    """A URL the sitemap asks Google to index: is the page itself agreeing?"""
    h = fetch(url)
    st = h.get("status")
    if st is None:
        return [_f("info", "sitemap_url_unreachable", url,
                   f"no answer ({h.get('error')}) - unknown, not an error finding")]
    if st in PERMANENT + TEMPORARY:
        return [_f("high", "sitemap_url_redirects", url,
                   f"listed URL answers {st} -> {h.get('location')}",
                   "List the FINAL URL. A sitemap entry that redirects is a URL you told "
                   "Google to index and then refused.", location=h.get("location"))]
    if st != 200:
        return [_f("high", "sitemap_url_error", url, f"listed URL answers HTTP {st}",
                   "Remove dead URLs from the sitemap - 4xx/5xx entries cost trust in the rest.")]
    facts = page_facts(h["body"], h["headers"])
    out = []
    if facts["noindex"]:
        out.append(_f("high", "sitemap_url_noindex", url,
                      f"listed URL says noindex ({facts['robots']})",
                      "Either index it or drop it from the sitemap; listing a noindex page is a "
                      "contradiction Search Console reports as 'Submitted URL marked noindex'."))
    c = facts["canonical"]
    if c:
        target = norm_url(urllib.parse.urljoin(url, c))
        if target != norm_url(url):
            out.append(_f("medium", "sitemap_url_canonical_elsewhere", url,
                          f"listed URL canonicalises to {target}",
                          "A sitemap should list canonical URLs only.", canonical=target))
    return out


def _pool(fn, items, workers: int):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(fn, items))


def sample(items: list, n: int) -> list:
    if n <= 0 or len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def check_sitemap(src: str, n: int, workers: int, fetch=hop) -> dict:
    sm = read_sitemaps(src)
    if not sm["files"]:
        return refuse("sitecheck-sitemap", "no sitemap could be read - nothing was audited, "
                      "which is not the same as nothing being wrong", failed=sm["failed"])
    entries = sm["urls"]
    picked = sample(entries, n)
    rows = _pool(lambda e: audit_url(e["loc"], fetch), picked, workers)
    findings = [f for r in rows for f in r]
    lastmod_share = (sum(1 for e in entries if e["lastmod"]) / len(entries)) if entries else 0
    dupes = len(entries) - len({e["loc"] for e in entries})
    if dupes:
        findings.append(_f("low", "sitemap_duplicate_entries", src, f"{dupes} duplicate <loc> entries"))
    if entries and lastmod_share < 0.5:
        findings.append(_f("low", "sitemap_lastmod_sparse", src,
                           f"only {lastmod_share:.0%} of entries carry <lastmod>",
                           "An accurate lastmod is how a changed page gets recrawled sooner."))
    for f in sm["failed"]:
        findings.append(_f("high", "sitemap_file_unreadable", f["sitemap"],
                           f"listed sitemap could not be read ({f.get('status')} {f.get('error') or ''})"))
    return {"ok": True, "check": "sitecheck-sitemap", "source": src, "files": sm["files"],
            "entries": len(entries), "audited": len(picked),
            "sampled": len(picked) < len(entries), "truncated": sm["truncated"],
            "findings": _sorted(findings),
            "note": ("A SAMPLE, spread evenly across the file - a clean sample bounds the error "
                     "rate, it does not prove zero.") if len(picked) < len(entries) else None}


def check_canonicals(urls: list[str], workers: int, fetch=hop) -> dict:
    def one(u):
        h = fetch(u)
        if h.get("status") != 200:
            return None
        c = page_facts(h["body"], h["headers"])["canonical"]
        if not c:
            return {"url": u, "canonical": None, "findings": [
                _f("low", "canonical_missing", u, "no rel=canonical",
                   "A self-canonical removes the guess for parameter and case variants.")]}
        tgt = urllib.parse.urljoin(u, c)
        if norm_url(tgt) == norm_url(u):
            return {"url": u, "canonical": tgt, "findings": []}
        t = fetch(tgt)
        if t.get("status") in PERMANENT + TEMPORARY:
            return {"url": u, "canonical": tgt, "findings": [_f(
                "high", "canonical_redirects", u, f"canonical {tgt} answers {t['status']}",
                "Point the canonical at the final URL; a canonical to a redirect is ignored.")]}
        if t.get("status") != 200:
            return {"url": u, "canonical": tgt, "findings": [_f(
                "critical", "canonical_broken", u, f"canonical {tgt} answers HTTP {t.get('status')}",
                "This page is unindexable as it stands. Fix the canonical target.")]}
        return {"url": u, "canonical": tgt, "findings": []}
    rows = [r for r in _pool(one, urls, workers) if r]
    return {"ok": True, "check": "sitecheck-canonicals", "checked": len(rows),
            "unreadable": len(urls) - len(rows),
            "findings": _sorted([f for r in rows for f in r["findings"]])}


def variant_urls(url: str) -> dict:
    p = urllib.parse.urlsplit(url)
    path = p.path or "/"
    out = {}
    if path != "/":
        flipped = path[:-1] if path.endswith("/") else path + "/"
        out["trailing_slash"] = urllib.parse.urlunsplit((p.scheme, p.netloc, flipped, p.query, ""))
    q = (p.query + "&" if p.query else "") + "utm_source=sitecheck"
    out["tracking_param"] = urllib.parse.urlunsplit((p.scheme, p.netloc, path, q, ""))
    return out


def check_variants(urls: list[str], workers: int, fetch=hop) -> dict:
    def one(u):
        base = fetch(u)
        if base.get("status") != 200:
            return []
        res = []
        for kind, v in variant_urls(u).items():
            h = fetch(v)
            if h.get("status") != 200:
                continue
            c = page_facts(h["body"], h["headers"])["canonical"]
            if c and norm_url(urllib.parse.urljoin(v, c)) == norm_url(u):
                continue
            res.append(_f("medium", f"duplicate_{kind}", u,
                          f"{v} also answers 200 without a canonical back to {u}",
                          "301 the variant to the clean URL, or canonicalise it there.",
                          variant=v, variant_canonical=c))
        return res
    rows = _pool(one, urls, workers)
    return {"ok": True, "check": "sitecheck-variants", "checked": len(urls),
            "findings": _sorted([f for r in rows for f in r])}


def check_links(graph_path: str, origin: str, sitemap: str | None, n: int, workers: int,
                fetch=hop) -> dict:
    """Uses a sitegraph.py graph: which internal link TARGETS redirect or fail
    (sitegraph's live crawl follows redirects, so it cannot see this), and
    which indexable pages the sitemap omits."""
    from sitegraph import load
    g, _info = load(graph_path)
    o = origin.rstrip("/")
    inbound: dict[int, int] = {}
    for src, edges in g.adj.items():
        for dst, _a, _b in edges:
            if dst != src:
                inbound[dst] = inbound.get(dst, 0) + 1
    targets = sorted(inbound, key=lambda t: -inbound[t])
    picked = sample(targets, n)

    def full(u):
        return u if u.startswith("http") else o + (u if u.startswith("/") else "/" + u)

    def one(t):
        u = full(g.urls[t])
        h = fetch(u)
        st = h.get("status")
        if st in PERMANENT + TEMPORARY:
            return _f("medium", "internal_link_to_redirect", u,
                      f"{inbound[t]} internal link(s) point at a URL that answers {st} -> "
                      f"{h.get('location')}", "Link to the final URL directly.",
                      inbound=inbound[t], location=h.get("location"))
        if st is not None and st >= 400:
            return _f("high", "internal_link_to_error", u,
                      f"{inbound[t]} internal link(s) point at HTTP {st}", None, inbound=inbound[t])
        return None
    findings = [f for f in _pool(one, picked, workers) if f]
    missing = []
    if sitemap:
        sm = read_sitemaps(sitemap)
        if sm["files"]:
            listed = {norm_url(e["loc"]) for e in sm["urls"]}
            for uid, meta in g.meta.items():
                if not meta.get("exists", True) or "noindex" in (meta.get("robots") or ""):
                    continue
                u = full(g.urls[uid])
                c = meta.get("canonical")
                if c and norm_url(full(c)) != norm_url(u):
                    continue
                if norm_url(u) not in listed:
                    missing.append(u)
            if missing:
                findings.append(_f("medium", "indexable_not_in_sitemap", sitemap,
                                   f"{len(missing)} indexable, self-canonical page(s) are not in the "
                                   f"sitemap", "List every page you want indexed.",
                                   examples=missing[:15]))
        else:
            findings.append(_f("info", "sitemap_unreadable_for_coverage", sitemap,
                               "coverage could not be compared - the sitemap was not readable"))
    return {"ok": True, "check": "sitecheck-links", "link_targets": len(targets),
            "probed": len(picked), "not_in_sitemap": len(missing),
            "findings": _sorted(findings)}


# -------------------------------------------------------------- run storage


def diff_runs(before: dict, after: dict) -> dict:
    def keyed(run):
        return {(f["rule"], f["url"]): f for f in run.get("findings", [])}
    b, a = keyed(before), keyed(after)
    return {"new": [a[k] for k in sorted(a.keys() - b.keys())],
            "fixed": [b[k] for k in sorted(b.keys() - a.keys())],
            "persisting": [a[k] for k in sorted(a.keys() & b.keys())]}


def collect(parts: list[dict]) -> list[dict]:
    return _sorted([f for p in parts for f in (p.get("findings") or [])])


# ------------------------------------------------------------------ control


def run_control() -> dict:
    c = Controls("sitecheck-control")
    # A fake network: url -> (status, location, body, headers)
    net = {
        "https://x.test/": (200, None, "<html lang=en><title>Home</title></html>", {}),
        "http://x.test/": (301, "https://x.test/", "", {}),
        "http://www.x.test/": (302, "http://x.test/", "", {}),
        "https://www.x.test/": (200, None, "<html>dup</html>", {}),
        "https://x.test/a": (301, "https://x.test/b", "", {}),
        "https://x.test/b": (301, "https://x.test/c", "", {}),
        "https://x.test/c": (200, None, "<html></html>", {}),
        "https://x.test/loop1": (301, "https://x.test/loop2", "", {}),
        "https://x.test/loop2": (301, "https://x.test/loop1", "", {}),
        "https://x.test/tmp": (302, "https://x.test/c", "", {}),
        "https://x.test/noidx": (200, None, '<meta name="robots" content="noindex">', {}),
        "https://x.test/hdrnoidx": (200, None, "<p>x</p>", {"x-robots-tag": "noindex"}),
        "https://x.test/canon": (200, None, '<link rel="canonical" href="/c">', {}),
        "https://x.test/canon-dead": (200, None, '<link rel="canonical" href="/gone">', {}),
        "https://x.test/gone": (404, None, "", {}),
        "https://x.test/comment": (200, None, '<!-- <meta name="robots" content="noindex"> -->', {}),
    }

    def fake(u):
        st, loc, body, hdrs = net.get(u, (404, None, "", {}))
        return {"url": u, "status": st, "location": loc, "body": body if st == 200 else "",
                "headers": hdrs, "ctype": "text/html", "error": None}

    ch = follow_chain("https://x.test/a", fetch=fake)
    rules = {f["rule"] for f in classify_chain("https://x.test/a", ch)}
    c.check("two_hops_is_a_chain", "redirect_chain" in rules, str(rules))
    c.check("CONTROL_one_permanent_hop_is_clean",
            classify_chain("https://x.test/b", follow_chain("https://x.test/b", fetch=fake)) == [])
    c.check("a_loop_is_named", [f["rule"] for f in classify_chain(
        "https://x.test/loop1", follow_chain("https://x.test/loop1", fetch=fake))] == ["redirect_loop"])
    c.check("a_302_is_temporary", "temporary_redirect" in {f["rule"] for f in classify_chain(
        "https://x.test/tmp", follow_chain("https://x.test/tmp", fetch=fake))})

    hs = check_hosts("https://x.test", fetch=fake)
    hr = {f["rule"] for f in hs["findings"]}
    c.check("two_hosts_serving_200_is_a_duplicate", "host_duplicate" in hr, str(hr))
    c.check("a_302_host_hop_is_flagged", "host_temporary_redirect" in hr, str(hr))

    rr = {u: {f["rule"] for f in audit_url(u, fetch=fake)} for u in
          ("https://x.test/a", "https://x.test/gone", "https://x.test/noidx",
           "https://x.test/hdrnoidx", "https://x.test/canon", "https://x.test/c",
           "https://x.test/comment")}
    c.check("sitemap_redirect", rr["https://x.test/a"] == {"sitemap_url_redirects"})
    c.check("sitemap_error", rr["https://x.test/gone"] == {"sitemap_url_error"})
    c.check("sitemap_meta_noindex", rr["https://x.test/noidx"] == {"sitemap_url_noindex"})
    c.check("sitemap_header_noindex", rr["https://x.test/hdrnoidx"] == {"sitemap_url_noindex"})
    c.check("sitemap_canonical_elsewhere", rr["https://x.test/canon"] == {"sitemap_url_canonical_elsewhere"})
    c.check("CONTROL_a_clean_page_is_clean", rr["https://x.test/c"] == set())
    c.check("a_noindex_inside_a_COMMENT_is_not_noindex", rr["https://x.test/comment"] == set())

    cr = check_canonicals(["https://x.test/canon-dead", "https://x.test/canon"], 1, fetch=fake)
    c.check("canonical_to_404_is_critical",
            [f["rule"] for f in cr["findings"]] == ["canonical_broken"], str(cr["findings"]))

    sm = parse_sitemap("<urlset><url><loc>https://x.test/p?a=1&amp;b=2</loc>"
                       "<lastmod>2026-01-01</lastmod></url></urlset>")
    c.check("sitemap_loc_is_xml_unescaped", sm["entries"][0]["loc"] == "https://x.test/p?a=1&b=2")
    c.check("sitemap_index_detected",
            parse_sitemap("<sitemapindex><sitemap><loc>a</loc></sitemap></sitemapindex>")["kind"] == "index")
    c.check("an_html_page_is_not_a_sitemap", parse_sitemap("<html><body>x</body></html>")["kind"] is None)

    soft, _ = is_soft404(200, "<h1>Page not found</h1>")
    c.check("soft404_by_wording", soft is True)
    c.check("CONTROL_a_real_404_is_not_soft", is_soft404(404, "")[0] is False)

    hf = {f["rule"] for f in header_findings("https://x.test/", {"x-content-type-options": "nosniff"})}
    c.check("missing_hsts_found", "missing_strict_transport_security" in hf)
    c.check("CONTROL_present_header_not_reported", "missing_x_content_type_options" not in hf)
    mc = mixed_content("https://x.test/", page_facts(
        '<script src="http://cdn.test/a.js"></script><!-- <img src="http://c.test/x.png"> -->'
        '<a href="http://other.test/">link</a>')["resources"])
    c.check("mixed_active_script_is_high_and_comment_and_anchor_ignored",
            len(mc) == 1 and mc[0]["severity"] == "high" and len(mc[0]["evidence"]["resources"]) == 1,
            str(mc))

    d = diff_runs({"findings": [{"rule": "a", "url": "1"}, {"rule": "b", "url": "1"}]},
                  {"findings": [{"rule": "b", "url": "1"}, {"rule": "c", "url": "1"}]})
    c.check("diff_new_fixed_persisting",
            [f["rule"] for f in d["new"]] == ["c"] and [f["rule"] for f in d["fixed"]] == ["a"]
            and [f["rule"] for f in d["persisting"]] == ["b"])
    v = variant_urls("https://x.test/p/")
    c.check("variants_flip_slash_and_add_tracking",
            v["trailing_slash"] == "https://x.test/p" and "utm_source=" in v["tracking_param"])
    return c.verdict()


# --------------------------------------------------------------------- main


def _save(out: dict, origin: str) -> str:
    host = urllib.parse.urlsplit(origin).hostname or "site"
    d = Path(".seo") / "sitecheck"
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = d / f"{host}-{stamp}.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return str(p)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("hosts", "soft404"):
        sub.add_parser(name).add_argument("origin")
    p = sub.add_parser("headers")
    p.add_argument("url")
    p = sub.add_parser("redirects")
    p.add_argument("--urls", required=True, help="file of URLs, one per line")
    p = sub.add_parser("sitemap")
    p.add_argument("source", help="origin (sitemaps read from robots.txt) or a sitemap URL")
    p.add_argument("--sample", type=int, default=200, help="0 = every URL")
    p = sub.add_parser("canonicals")
    p.add_argument("source", help="origin or sitemap URL")
    p.add_argument("--sample", type=int, default=100)
    p = sub.add_parser("variants")
    p.add_argument("source")
    p.add_argument("--sample", type=int, default=20)
    p = sub.add_parser("links")
    p.add_argument("--graph", required=True)
    p.add_argument("--origin", required=True)
    p.add_argument("--sitemap")
    p.add_argument("--sample", type=int, default=300)
    p = sub.add_parser("all")
    p.add_argument("origin")
    p.add_argument("--sample", type=int, default=150)
    p = sub.add_parser("diff")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    sub.add_parser("control")
    for sp in sub.choices.values():
        if not any(x.dest == "workers" for x in sp._actions):
            sp.add_argument("--workers", type=int, default=6)
            sp.add_argument("--save", action="store_true",
                            help="write the run to .seo/sitecheck/ for a later diff")
    a = ap.parse_args()

    def sources(src):
        if re.search(r"\.xml(\.gz)?$|sitemap", src, re.I):
            return [src]
        return sitemaps_for(src)

    def sm_urls(src, n):
        locs = []
        for s in sources(src):
            locs += [e["loc"] for e in read_sitemaps(s)["urls"]]
        return sample(locs, n)

    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "hosts":
        out = check_hosts(a.origin)
    elif a.cmd == "soft404":
        out = check_soft404(a.origin)
    elif a.cmd == "headers":
        out = check_headers(a.url)
    elif a.cmd == "redirects":
        urls = [x.strip() for x in Path(a.urls).read_text(encoding="utf-8").split("\n")
                if x.strip() and not x.startswith("#")]
        rows = _pool(lambda u: classify_chain(u, follow_chain(u)), urls, a.workers)
        out = {"ok": True, "check": "sitecheck-redirects", "checked": len(urls),
               "findings": _sorted([f for r in rows for f in r])}
    elif a.cmd == "sitemap":
        parts = [check_sitemap(s, a.sample, a.workers) for s in sources(a.source)]
        good = [p_ for p_ in parts if p_.get("ok")]
        out = (parts[0] if len(parts) == 1 else
               {"ok": bool(good), "check": "sitecheck-sitemap", "parts": parts,
                "findings": collect(good)})
    elif a.cmd == "canonicals":
        out = check_canonicals(sm_urls(a.source, a.sample), a.workers)
    elif a.cmd == "variants":
        out = check_variants(sm_urls(a.source, a.sample), a.workers)
    elif a.cmd == "links":
        out = check_links(a.graph, a.origin, a.sitemap, a.sample, a.workers)
    elif a.cmd == "diff":
        b = json.loads(Path(a.before).read_text(encoding="utf-8"))
        af = json.loads(Path(a.after).read_text(encoding="utf-8"))
        out = {"ok": True, "check": "sitecheck-diff", **diff_runs(b, af)}
    else:
        o = a.origin.rstrip("/")
        hosts = check_hosts(o)
        canon = hosts.get("canonical_origin") or o + "/"
        canon_o = canon.rstrip("/")
        parts = {"hosts": hosts, "soft404": check_soft404(canon_o),
                 "headers": check_headers(canon)}
        sms = sitemaps_for(canon_o)
        parts["sitemap"] = [check_sitemap(s, a.sample, a.workers) for s in sms]
        urls = sm_urls(canon_o, min(a.sample, 60))
        parts["canonicals"] = check_canonicals(urls, a.workers)
        parts["variants"] = check_variants(sample(urls, 10), a.workers)
        flat = [parts["hosts"], parts["soft404"], parts["headers"], *parts["sitemap"],
                parts["canonicals"], parts["variants"]]
        refused = [p_.get("check") for p_ in flat if p_.get("control_failed")]
        out = {"ok": True, "check": "sitecheck-all", "origin": o, "canonical_origin": canon,
               "refused": refused, "findings": collect([p_ for p_ in flat if p_.get("ok")]),
               "parts": {k: ({kk: vv for kk, vv in v.items() if kk != "findings"}
                             if isinstance(v, dict) else
                             [{kk: vv for kk, vv in x.items() if kk != "findings"} for x in v])
                         for k, v in parts.items()}}
        out["counts"] = {s: sum(1 for f in out["findings"] if f["severity"] == s)
                         for s in SEV_ORDER}
    if getattr(a, "save", False) and a.cmd not in ("control", "diff"):
        out["saved_to"] = _save(out, getattr(a, "origin", None) or getattr(a, "source", None)
                                or getattr(a, "url", "site"))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
