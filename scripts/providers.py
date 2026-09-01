#!/usr/bin/env python3
"""The provider registry - shared HTTP, disk cache, and a LIVE probe per source.

Every data source in this skill is registered here with a probe that actually
calls it. That gives three things nothing else did:

  1. `providers.py status` answers "what can I use right now?" from measurement,
     not from a table in a markdown file that went stale three months ago.
  2. Adding a source is a small, uniform change instead of a new bespoke script.
  3. Every probe carries its CONTROL. A source that returns an empty answer and
     a source that is broken look identical from one call, and this skill's
     whole doctrine is that those two must never share a code path. So a probe
     that can be controlled says how, and reports `control_ok` separately from
     `ok`. A probe whose control FAILS is reported as UNUSABLE even if the main
     call returned 200 - because at that point you cannot trust what it says.

Stdlib only. Import from the other scripts:

    from providers import http, http_json, cache_get, cache_put, PROVIDERS
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOOL_UA = "seo-manager/1.0 (+https://github.com/; SEO research)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CACHE_DIR = Path(os.environ.get("SEO_CACHE_DIR", Path.home() / ".cache" / "seo-manager"))


# --------------------------------------------------------------------- http


class HttpResult(dict):
    """A response, or an honest description of why there isn't one."""

    @property
    def ok(self) -> bool:
        return self.get("status") == 200

    def json(self):
        body = self.get("body") or b""
        # Google's XSSI guard, used by validator.schema.org among others.
        if body[:5] == b")]}'\n":
            body = body[5:]
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:
            return None

    def text(self) -> str:
        return (self.get("body") or b"").decode("utf-8", "replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into a returned response instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(url, *, data=None, headers=None, timeout=25, method=None, ua=TOOL_UA,
         retries=0, backoff=3.0, retry_on=(429, 500, 502, 503, 504),
         follow=True) -> HttpResult:
    """One HTTP call, with an optional bounded retry.

    `retries` exists because several free sources here are rate-limited at the
    shared-IP level and answer 429 on the first call and 200 on the second -
    measured on GDELT and crt.sh. A single-shot probe of those reports a
    permanent failure that is really a transient one.

    `follow=False` reports the FIRST response instead of the final one, and
    carries `location`. Every caller that audits what a URL *is* rather than
    what it eventually serves needs this: with redirects followed, a canonical
    or an hreflang alternate pointing at a 301 is indistinguishable from one
    pointing straight at a 200, and that difference is the whole finding.
    """
    h = {"User-Agent": ua, "Accept": "*/*", "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    opener = urllib.request.build_opener(_NoRedirect) if not follow else None
    last = None
    for attempt in range(retries + 1):
        t0 = time.time()
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with (opener.open(req, timeout=timeout) if opener
                  else urllib.request.urlopen(req, timeout=timeout)) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                return HttpResult(status=r.status, body=raw, ms=int((time.time() - t0) * 1000),
                                  ctype=r.headers.get("Content-Type", ""), url=r.geturl(),
                                  headers={k.lower(): v for k, v in r.headers.items()},
                                  attempts=attempt + 1)
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
                # Error bodies are gzipped too - we asked for it. Skipping the
                # decompress here silently blanks every error MESSAGE, which is
                # exactly the text that tells you what to fix (it turned PSI's
                # "enable the API on project N" into a bare 403).
                if e.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            except Exception:
                pass
            last = HttpResult(status=e.code, body=body, ms=int((time.time() - t0) * 1000),
                              error=f"HTTP {e.code}", attempts=attempt + 1,
                              url=url, location=(e.headers or {}).get("Location"))
            if e.code not in retry_on or attempt >= retries:
                return last
        except Exception as e:
            last = HttpResult(status=None, body=b"", ms=int((time.time() - t0) * 1000),
                              error=f"{type(e).__name__}: {e}", attempts=attempt + 1)
            if attempt >= retries:
                return last
        time.sleep(backoff * (attempt + 1))
    return last


def http_json(url, **kw):
    return http(url, **kw).json()


# -------------------------------------------------------------------- cache


def _cache_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / namespace / f"{digest}.json"


def cache_get(namespace: str, key: str, ttl_seconds: int):
    p = _cache_path(namespace, key)
    try:
        if time.time() - p.stat().st_mtime > ttl_seconds:
            return None
        return json.loads(p.read_text())
    except Exception:
        return None


def cache_put(namespace: str, key: str, value) -> None:
    p = _cache_path(namespace, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))
    except Exception:
        pass


def read_secret(env_name: str, dotfile: str | None = None) -> str:
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    if dotfile:
        try:
            return Path(dotfile).expanduser().read_text().strip()
        except Exception:
            pass
    return ""


# ------------------------------------------------------------------- probes
#
# Each returns (ok, detail, control_ok).  control_ok is None when the probe has
# no meaningful control - which is itself worth reporting, because a source
# without a control can only ever tell you it answered, never that it is right.


def _probe_google_autocomplete():
    r = http("https://suggestqueries.google.com/complete/search?" +
             urllib.parse.urlencode({"client": "firefox", "hl": "en", "q": "chess openings"}), ua=BROWSER_UA)
    j = r.json() or []
    n = len(j[1]) if isinstance(j, list) and len(j) > 1 else 0
    c = http("https://suggestqueries.google.com/complete/search?" +
             urllib.parse.urlencode({"client": "firefox", "hl": "en", "q": "zqxjkv wprtl nonsense"}), ua=BROWSER_UA)
    cj = c.json() or []
    cn = len(cj[1]) if isinstance(cj, list) and len(cj) > 1 else 0
    return n > 0, f"{r.get('status')} n={n}", cn == 0


def _probe_bing_autosuggest():
    r = http("https://api.bing.com/osjson.aspx?query=" + urllib.parse.quote("chess openings"))
    j = r.json() or []
    n = len(j[1]) if isinstance(j, list) and len(j) > 1 else 0
    c = http("https://api.bing.com/osjson.aspx?query=" + urllib.parse.quote("zqxjkv wprtl nonsense"))
    cj = c.json() or []
    cn = len(cj[1]) if isinstance(cj, list) and len(cj) > 1 else 0
    return n > 0, f"{r.get('status')} n={n}", cn == 0


def _probe_ddg_autocomplete():
    r = http("https://duckduckgo.com/ac/?type=list&q=" + urllib.parse.quote("chess openings"), ua=BROWSER_UA)
    j = r.json()
    n = len(j[1]) if isinstance(j, list) and len(j) == 2 and isinstance(j[1], list) else (len(j) if isinstance(j, list) else 0)
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_youtube_suggest():
    r = http("https://suggestqueries.google.com/complete/search?" +
             urllib.parse.urlencode({"client": "firefox", "ds": "yt", "q": "chess openings"}), ua=BROWSER_UA)
    j = r.json() or []
    n = len(j[1]) if isinstance(j, list) and len(j) > 1 else 0
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_yandex_suggest():
    r = http("https://suggest.yandex.com/suggest-ff.cgi?part=" + urllib.parse.quote("chess openings"))
    j = r.json() or []
    n = len(j[1]) if isinstance(j, list) and len(j) > 1 else 0
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_amazon_suggest():
    r = http("https://completion.amazon.com/api/2017/suggestions?mid=ATVPDKIKX0DER&alias=aps&limit=10&prefix=" +
             urllib.parse.quote("running shoes"))
    j = r.json() or {}
    n = len(j.get("suggestions", [])) if isinstance(j, dict) else 0
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_tranco():
    r = http("https://tranco-list.eu/api/ranks/domain/github.com")
    ranks = (r.json() or {}).get("ranks") or []
    # Control: a domain that is certainly NOT in the top 1M must answer 200
    # with an EMPTY rank list. If it errors instead, "absent" and "broken"
    # would be indistinguishable and the source is unusable for the gate.
    c = http("https://tranco-list.eu/api/ranks/domain/zqxjkvwprtl-nonexistent-xyz.com")
    cranks = (c.json() or {}).get("ranks") or []
    return bool(ranks), f"{r.get('status')} ranks={len(ranks)}", c.ok and not cranks


def _probe_gdelt():
    r = http("https://api.gdeltproject.org/api/v2/doc/doc?query=" + urllib.parse.quote('"counter strike"') +
             "&mode=timelinevol&format=json&timespan=3m", retries=2, backoff=5)
    tl = (r.json() or {}).get("timeline") or []
    n = len(tl[0].get("data", [])) if tl else 0
    return n > 0, f"{r.get('status')} points={n} attempts={r.get('attempts')}", None


def _probe_google_news():
    r = http("https://news.google.com/rss/search?q=" + urllib.parse.quote("counter strike") +
             "&hl=en-US&gl=US&ceid=US:en", ua=BROWSER_UA)
    n = (r.get("body") or b"").count(b"<item>")
    return n > 0, f"{r.get('status')} items={n}", None


def _probe_wikidata():
    r = http("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&limit=5&search=" +
             urllib.parse.quote("Counter-Strike"))
    n = len((r.json() or {}).get("search", []))
    c = http("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&limit=5&search=zqxjkvwprtlnonsense")
    cn = len((c.json() or {}).get("search", []))
    return n > 0, f"{r.get('status')} n={n}", cn == 0


def _probe_openalex():
    r = http("https://api.openalex.org/works?per-page=3&search=" + urllib.parse.quote("search engine optimization"))
    n = len((r.json() or {}).get("results", []))
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_crossref():
    r = http("https://api.crossref.org/works?rows=3&query=" + urllib.parse.quote("web search ranking"))
    n = len(((r.json() or {}).get("message") or {}).get("items", []))
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_w3c_nu():
    r = http("https://validator.w3.org/nu/?out=json&doc=" + urllib.parse.quote("https://example.com/"), timeout=45)
    j = r.json()
    return isinstance(j, dict) and "messages" in j, f"{r.get('status')}", None


def _probe_schema_validator():
    r = http("https://validator.schema.org/validate?url=" + urllib.parse.quote("https://www.google.com/"),
             method="POST", data=b"", timeout=60)
    j = r.json() or {}
    return "tripleGroups" in j, f"{r.get('status')} groups={len(j.get('tripleGroups', []))}", None


def _probe_wayback():
    r = http("https://archive.org/wayback/available?url=example.com", timeout=30)
    j = r.json() or {}
    ok = isinstance(j.get("archived_snapshots"), dict)
    # Control: a domain never archived must answer 200 with an EMPTY snapshot
    # object, not an error - otherwise "never archived" reads as a failure.
    c = http("https://archive.org/wayback/available?url=zqxjkvwprtl-nonexistent-xyz.com", timeout=30)
    cj = c.json() or {}
    return ok, f"{r.get('status')}", c.ok and not (cj.get("archived_snapshots") or {})


def _probe_wayback_cdx():
    r = http("https://web.archive.org/cdx/search/cdx?url=example.com&output=json&limit=5&fl=timestamp,original",
             timeout=60, retries=1)
    j = r.json()
    n = (len(j) - 1) if isinstance(j, list) and j else 0
    return n > 0, f"{r.get('status')} rows={n} ({r.get('ms')}ms)", None


def _probe_crtsh():
    # ⚠ The probe domain IS the control here, and the first choice was wrong:
    # `%.example.com` answers 404, which looked like crt.sh being flaky and was
    # really a domain with no subdomain certificates in the CT logs. Probe a
    # domain that certainly has them, or you report a broken instrument every
    # time the query is simply empty.
    # 404 IS in retry_on here, and only because of the probe domain. `%.github.com`
    # is known to return ~1,100 rows, so a 404 on THAT query cannot mean "no
    # certificates" - it means crt.sh is unwell. Measured in one 10-second
    # window: 404, 404, 502.
    #
    # ⚠ Do NOT copy that inference to a real lookup. On an arbitrary domain
    # crt.sh's 404 is genuinely ambiguous between "no certs" and "server
    # unhappy", which is why this source is never used to assert an ABSENCE of
    # subdomains - only to enumerate the ones it does return.
    r = http("https://crt.sh/?q=%25.github.com&output=json", timeout=60, retries=3, backoff=4,
             retry_on=(404, 429, 500, 502, 503, 504))
    j = r.json()
    n = len(j) if isinstance(j, list) else 0
    return n > 0, f"{r.get('status')} rows={n} attempts={r.get('attempts')}", None


def _probe_marginalia():
    # Retries because the failure shape here is a dropped CONNECTION (status
    # None), not an HTTP code - a small independent index on modest hardware.
    # Measured: one probe failed outright, three curls seconds later all
    # answered 200. A single-shot probe reports it dead about 1 run in 10.
    r = http("https://api.marginalia.nu/public/search/" + urllib.parse.quote("counter strike"),
             retries=2, backoff=3, timeout=40)
    n = len((r.json() or {}).get("results", [])) if isinstance(r.json(), dict) else 0
    return n > 0, f"{r.get('status')} n={n}", None


def _probe_cloudflare_radar():
    tok = read_secret("CLOUDFLARE_API_TOKEN", "~/.cloudflare_token")
    if not tok:
        return None, "no CLOUDFLARE_API_TOKEN / ~/.cloudflare_token", None
    r = http("https://api.cloudflare.com/client/v4/radar/ranking/domain/github.com?limit=1",
             headers={"Authorization": f"Bearer {tok}"})
    j = r.json() or {}
    return bool(j.get("success")), f"{r.get('status')}", None


def _probe_openpagerank():
    key = read_secret("OPENPAGERANK_API_KEY", "~/.openpagerank_key")
    if not key:
        return None, "no OPENPAGERANK_API_KEY / ~/.openpagerank_key", None
    if key.startswith("opr_live_"):
        r = http("https://openpagerank.keywordseverywhere.com/v1/domains/bulk",
                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                 data=json.dumps({"domains": ["github.com"]}).encode())
        res = (r.json() or {}).get("results") or []
        found = bool(res and res[0].get("found"))
        return found, f"{r.get('status')} dr={res[0].get('open_page_rank') if res else None}", None
    r = http("https://openpagerank.com/api/v1.0/getPageRank?domains%5B%5D=github.com",
             headers={"API-OPR": key})
    j = r.json() or {}
    return bool(j.get("status_code") == 200 or j.get("response")), f"{r.get('status')} (legacy key)", None


def _probe_bing_webmaster():
    key = read_secret("BING_WEBMASTER_API_KEY", "~/.bing_webmaster_key")
    if not key:
        return None, "no BING_WEBMASTER_API_KEY / ~/.bing_webmaster_key", None
    r = http(f"https://ssl.bing.com/webmaster/api.svc/json/GetUserSites?apikey={key}")
    j = r.json() or {}
    sites = (j.get("d") or []) if isinstance(j, dict) else []
    return r.ok, f"{r.get('status')} sites={len(sites)}", None


def _probe_serper():
    key = read_secret("SERPER_API_KEY", "~/.serper_key")
    if not key:
        return None, "no SERPER_API_KEY / ~/.serper_key", None
    return True, "key present (not spent on a probe - credits are one-off)", None


def _probe_serpapi():
    key = read_secret("SERPAPI_KEY", "~/.serpapi_key")
    if not key:
        return None, "no SERPAPI_KEY / ~/.serpapi_key", None
    r = http(f"https://serpapi.com/account?api_key={key}")
    j = r.json() or {}
    left = j.get("total_searches_left")
    return r.ok, f"{r.get('status')} searches_left={left}", None


def _probe_pagespeed():
    """PSI needs an API key OR an OAuth token from a project where it is ENABLED.

    Measured 2026-08-01: with the existing GSC service account and the `openid`
    scope, the API answers 403 naming the exact project that needs to enable it -
    which is a one-click, free, no-card owner action rather than a new key. With
    the cloud-platform scope you get a misleading "insufficient scopes" instead.
    """
    try:
        import googleauth  # noqa: F401
    except Exception:
        pass
    from pagecheck import psi_token  # local import: shares the JWT minting
    tok, err = psi_token()
    if not tok:
        return None, f"no usable Google credential: {err}", None
    r = http("https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
             "?url=https://example.com&strategy=mobile&category=performance",
             headers={"Authorization": f"Bearer {tok}"}, timeout=120)
    if r.ok:
        return True, "200 (API enabled on the project)", None
    msg = str(((r.json() or {}).get("error") or {}).get("message", ""))[:150]
    return False, f"{r.get('status')} {msg}", None


# name, category, cost, needs_key, probe, note
PROVIDERS = [
    ("google-autocomplete", "expansion", "free", False, _probe_google_autocomplete,
     "the base expansion primitive - real queries, no volume"),
    ("bing-autosuggest", "expansion", "free", False, _probe_bing_autosuggest,
     "independent second engine; keyless osjson endpoint"),
    ("ddg-autocomplete", "expansion", "free", False, _probe_ddg_autocomplete,
     "third independent engine"),
    ("youtube-suggest", "expansion", "free", False, _probe_youtube_suggest,
     "video intent, from the YouTube corpus"),
    ("yandex-suggest", "expansion", "free", False, _probe_yandex_suggest,
     "fourth engine; strongest signal in RU/TR markets"),
    ("amazon-suggest", "expansion", "free", False, _probe_amazon_suggest,
     "commercial/product intent evidence"),
    ("tranco", "authority", "free", False, _probe_tranco,
     "domain popularity rank with ~40 days of history; controlled"),
    ("openpagerank", "authority", "free key", True, _probe_openpagerank,
     "DR-equivalent + referring domains from the Common Crawl link graph"),
    ("cloudflare-radar", "authority", "free key", True, _probe_cloudflare_radar,
     "independent popularity bucket from 1.1.1.1 resolver traffic"),
    ("gdelt", "trends", "free", False, _probe_gdelt,
     "per-topic news volume timeline; 429s under shared-IP load, retries"),
    ("google-news-rss", "trends", "free", False, _probe_google_news,
     "recency + which publishers are covering a topic"),
    ("wikidata", "entities", "free", False, _probe_wikidata,
     "entity resolution for semantic coverage; controlled"),
    ("openalex", "facts", "free", False, _probe_openalex,
     "peer-reviewed sources for the information-gain requirement"),
    ("crossref", "facts", "free", False, _probe_crossref,
     "DOI metadata + citation counts"),
    ("w3c-nu", "technical", "free", False, _probe_w3c_nu,
     "HTML validity for any URL"),
    ("schema-validator", "technical", "free", False, _probe_schema_validator,
     "Google's own structured-data extractor, keyless"),
    ("wayback-available", "history", "free", False, _probe_wayback,
     "is a URL archived, and when; controlled"),
    ("wayback-cdx", "history", "free", False, _probe_wayback_cdx,
     "full capture history - when a competitor page actually changed (slow)"),
    ("crt.sh", "history", "free", False, _probe_crtsh,
     "certificate transparency -> subdomain footprint; flaky, retries"),
    ("marginalia", "serp", "free", False, _probe_marginalia,
     "independent non-commercial index; research seam, NOT the authority gate"),
    ("serper", "serp", "2500 one-off credits", True, _probe_serper,
     "real Google, 1 credit/search"),
    ("serpapi", "serp", "250/month", True, _probe_serpapi,
     "real Google top-100 + AI Overview"),
    ("bing-webmaster", "volume", "free key", True, _probe_bing_webmaster,
     "the only free real impression counts + backlinks, verified property only"),
    ("pagespeed", "technical", "free", True, _probe_pagespeed,
     "Core Web Vitals lab data; needs the API enabled on a Google project"),
]


def probe_all(categories=None, timeout=180):
    rows = []
    wanted = [p for p in PROVIDERS if not categories or p[1] in categories]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_run_probe, p): p for p in wanted}
        for f in concurrent.futures.as_completed(futures, timeout=timeout):
            rows.append(f.result())
    order = {p[0]: i for i, p in enumerate(PROVIDERS)}
    rows.sort(key=lambda r: order.get(r["provider"], 999))
    return rows


def _run_probe(p):
    name, category, cost, needs_key, fn, note = p
    t0 = time.time()
    try:
        ok, detail, control_ok = fn()
    except Exception as e:
        ok, detail, control_ok = False, f"{type(e).__name__}: {e}", None
    # A failed control makes the source UNUSABLE, not merely quiet: at that
    # point an empty answer and a broken one are indistinguishable.
    state = ("unconfigured" if ok is None else
             "control_failed" if control_ok is False else
             "usable" if ok else "failing")
    return {"provider": name, "category": category, "cost": cost, "needs_key": needs_key,
            "state": state, "detail": detail, "control_ok": control_ok,
            "ms": int((time.time() - t0) * 1000), "note": note}



def run_control() -> dict:
    """Prove the REGISTRY discriminates - the three states, and the shape.

    `providers.py status` is already a live control sweep. This is the layer
    beneath it: proof that the registry can still REPRESENT the distinction it
    exists for. A state machine that collapsed `control_failed` into `failing`,
    or `unconfigured` into either, would report a plausible table forever while
    "cannot ask" and "the answer is no" quietly became the same thing."""
    from controls import Controls
    c = Controls("providers-control")

    def state_of(ok, control_ok):
        return _run_probe(("t", "test", "free", False,
                           lambda: (ok, "d", control_ok), "n"))["state"]

    c.check("a_working_source_is_usable", state_of(True, True) == "usable")
    c.check("a_source_that_answered_but_failed_its_control_is_not_usable",
            state_of(True, False) == "control_failed",
            "answering is not the same as answering correctly")
    c.check("an_unkeyed_source_is_unconfigured_not_failing",
            state_of(None, None) == "unconfigured",
            "'cannot ask' must never share a state with 'the answer is no'")
    c.check("a_broken_source_is_failing", state_of(False, True) == "failing")
    c.check("the_four_states_are_all_distinct",
            len({state_of(True, True), state_of(True, False),
                 state_of(None, None), state_of(False, True)}) == 4)

    boom = _run_probe(("t", "test", "free", False,
                       lambda: (_ for _ in ()).throw(RuntimeError("nope")), "n"))
    c.check("an_exception_becomes_a_state_not_a_crash", boom["state"] == "failing")
    c.check("the_exception_text_survives_into_detail", "RuntimeError" in boom["detail"])

    c.check("a_source_without_a_control_is_visible_as_such",
            _run_probe(("t", "test", "free", False, lambda: (True, "d", None), "n"))
            ["control_ok"] is None,
            "a source with no control can only say it answered, never that it is right")

    c.check("the_registry_is_populated", len(PROVIDERS) >= 15)
    c.check("every_entry_has_the_full_shape", all(len(p) == 6 for p in PROVIDERS))
    c.check("provider_names_are_unique",
            len({p[0] for p in PROVIDERS}) == len(PROVIDERS))
    c.check("every_probe_is_callable", all(callable(p[4]) for p in PROVIDERS))
    return c.verdict(providers=len(PROVIDERS),
                     note="this proves the registry; `providers.py status` is the LIVE sweep")

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", nargs="?", default="status",
                   choices=["status", "list", "control"])
    p.add_argument("--category", action="append",
                   help="expansion|authority|trends|entities|facts|technical|history|serp|volume")
    p.add_argument("--json", action="store_true", help="JSON only, no table")
    a = p.parse_args()

    if a.cmd == "control":
        out = run_control()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if out.get("ok") else 1)

    if a.cmd == "list":
        print(json.dumps([{"provider": n, "category": c, "cost": cost, "needs_key": k, "note": note}
                          for n, c, cost, k, _, note in PROVIDERS], indent=2))
        return

    rows = probe_all(a.category)
    summary = {}
    for r in rows:
        summary[r["state"]] = summary.get(r["state"], 0) + 1
    out = {"ok": True, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "summary": summary, "providers": rows,
           "note": "state=control_failed means the source answered but its control did not - "
                   "treat it as UNUSABLE, because absence and breakage cannot be told apart."}
    if a.json:
        print(json.dumps(out, indent=2))
        return
    for r in rows:
        mark = {"usable": "OK  ", "failing": "FAIL", "unconfigured": "--  ",
                "control_failed": "CTRL"}[r["state"]]
        print(f"{mark} {r['provider']:<22} {r['category']:<10} {r['detail']}")
    print("\n" + json.dumps({"summary": summary}, indent=None))


if __name__ == "__main__":
    main()
