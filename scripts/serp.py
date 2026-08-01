#!/usr/bin/env python3
"""SERP provider layer for the seo-manager skill.

One question, several ways to answer it: "what is actually on page 1 for this
query?" The provider ladder runs cheapest-and-keyless first:

    ddg         DuckDuckGo HTML endpoint. Keyless, free, no account.
                DEFAULT. Verified working, but it goes through periods of
                blanket HTTP 202 refusal that NO proxy, endpoint or request
                shape works around - see provider_ddg. Fail over to browser.
    serpd       The local SERP daemon (scripts/serpd.py): the same headed-Chrome
                route to real Google, but held open behind an HTTP server, so a
                check is one call with no DOM in your context. ~1.4s/query, and
                its /batch endpoint does a whole 25-check research run in ~37s.
                Start it with `serpd.py --start`. Preferred when it is up.
    browser     Real Google through the browser MCP. Keyless, highest fidelity,
                and the ONLY route to real Google results - Google serves a
                JS-only shell to every HTTP client regardless of IP or UA
                (measured: 200 / ~90KB / zero <h3>, direct AND proxied, with a
                Googlebot UA too). This script cannot call MCP tools, so
                --provider browser prints a hardened recipe to run.
    searxng     Any SearXNG instance with the JSON API enabled (self-hosted is
                the reliable form - public instances antibot).
    brave       Brave Search API. Free tier: 1 query/s, 2000/month, BYO key.
    serpapi     SerpApi. Free tier 250 searches/month, BYO key. Real Google,
                top-100 in one credit, includes the AI Overview inline.
    dataforseo  DataForSEO live SERP. Paid, BYO login/password. Real Google.

Bing is deliberately NOT a provider. See RELEVANCE below.

Two guards sit in front of every provider's output, and they exist because the
dangerous SERP failures do not look like failures:

  SHAPE      An HTTP 200 with no parseable results is not an empty page 1. A
             parser that reports "no competitors" there feeds the research
             quality bar an authority count of zero and waves through a keyword
             the site cannot win.
  RELEVANCE  Worse: some engines return well-formed results FOR A DIFFERENT
             QUERY. Measured 2026-07-30 - Bing, asked for "self hosted rank
             tracker" through residential exits, returned ten clean
             <li class="b_algo"> blocks about Laposte.net webmail, and on a
             second session about Rufus/Windows XP. HTTP 200, no captcha, ten
             rows that parse perfectly. Status code and result count both look
             healthy. Only checking that the results are ABOUT the query
             catches it.

Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# The full browser header set, not just a User-Agent. A sibling project's
# backend/src/gametracker-ba.js:20-39 records the measurement: a bare UA gets
# the request reset or 403'd, the full set returns 200. It is also what made
# DuckDuckGo answer consistently here.
BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Domains whose presence on page 1 is a WEAKNESS signal: a forum thread, a
# Q&A post, a raw repo or a video is not a guide-shaped competitor, and a
# young site can out-answer it. (The quality bar's "strong SERP weakness".)
WEAK_DOMAINS = {
    "reddit.com", "news.ycombinator.com", "stackoverflow.com", "stackexchange.com",
    "quora.com", "youtube.com", "youtu.be", "gist.github.com", "pastebin.com",
    "medium.com", "dev.to", "hashnode.dev", "linkedin.com", "facebook.com",
    "x.com", "twitter.com", "discourse.org", "serverfault.com", "superuser.com",
    "answers.microsoft.com", "community.spiceworks.com", "tiktok.com",
}
DOCS_DOMAINS = {"github.com", "gitlab.com", "readthedocs.io", "npmjs.com", "pypi.org", "crates.io"}

THIN_TITLE = re.compile(
    r"^\s*(?:top\s+)?\d{1,2}\s+(?:best|top|free|great|awesome|essential)\b"
    r"|\b(?:best|top)\s+\d{1,2}\b",
    re.I,
)
YEAR_IN_TITLE = re.compile(r"\b(20[0-2]\d)\b")

results_meta: dict = {}


# ------------------------------------------------------------------- proxy


# Exit countries verified against one commercial residential pool.
# "us" is deliberately absent:
# asking for it silently returns a random non-US exit, which is worse than
# refusing because the caller believes it got a US SERP.
EU_COUNTRIES = ["gr", "se", "nl", "it", "de", "es", "pl", "ro", "pt", "be", "at", "cz", "dk", "ie", "ch"]


def _read_proxy_file() -> dict:
    path = os.path.expanduser("~/.seo-proxy")
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


class Proxy:
    """A residential proxy with password-encoded sticky sessions.

    What this is actually for, after measurement: a non-datacenter exit for the
    browser, and geo-pinned SERPs (checking how a page ranks from Germany).
    It is NOT a way around DuckDuckGo's 202 - see provider_ddg.

    Selectors ride on the PASSWORD, '_'-joined (reverse-engineered in
    against one commercial provider):

        <pass>[_country-<cc>]_session-<6char>_lifetime-<min>

    Same session token => same exit IP. No token => a NEW exit IP per request,
    which sounds better and is not: a rotating exit breaks any engine that does
    a redirect + cookie hop, because each hop lands on a different country.
    So this class is sticky by default and rotates only on demand.
    """

    def __init__(self, url: str, country: str | None = None, lifetime: int = 10):
        self.base = url
        self.country = country
        self.lifetime = lifetime
        self.session = self._token()

    @staticmethod
    def _token() -> str:
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

    def rotate(self):
        """New exit IP on the next request.

        Worth one attempt against a block that MIGHT be exit-scoped, but do not
        build a retry strategy on it: DuckDuckGo's 202 was measured surviving
        six fresh exits across six countries in the same minute.
        """
        self.session = self._token()

    def url(self) -> str:
        sel = f"_country-{self.country}" if self.country else ""
        sel += f"_session-{self.session}_lifetime-{self.lifetime}"
        # Insert the selector at the end of the password, i.e. just before the
        # final '@' that separates credentials from the host.
        at = self.base.rfind("@")
        return self.base[:at] + sel + self.base[at:] if at > 0 else self.base

    def label(self) -> str:
        """Loggable identity. NEVER returns credentials."""
        try:
            host = urllib.parse.urlparse(self.base).hostname or "proxy"
        except ValueError:
            host = "proxy"
        cc = self.country or "auto"
        return f"{host} (session {self.session}, {cc})"


def resolve_proxy(country: str | None, enabled: bool) -> Proxy | None:
    """env -> ~/.seo-proxy -> none. Never auto-discovers another repo's .env."""
    if not enabled:
        return None
    fromfile = _read_proxy_file()
    url = (
        os.environ.get("SEO_PROXY_URL")
        or fromfile.get("SEO_PROXY_URL")
        or os.environ.get("SEO_PROXY_SOCKS")
        or fromfile.get("SEO_PROXY_SOCKS")
        or ""
    ).strip()
    if not url:
        return None
    return Proxy(url, country)


# ------------------------------------------------------------------- http


_HAVE_CURL: bool | None = None


def have_curl() -> bool:
    global _HAVE_CURL
    if _HAVE_CURL is None:
        _HAVE_CURL = shutil.which("curl") is not None
    return _HAVE_CURL


def _fetch_curl(url, data, hdrs, timeout, proxy) -> tuple[int, str]:
    cmd = ["curl", "-sS", "-L", "--compressed", "-m", str(timeout), "--write-out", "\n%{http_code}"]
    for k, v in hdrs.items():
        cmd += ["-H", f"{k}: {v}"]
    if proxy:
        cmd += ["--proxy", proxy.url()]
    if data is not None:
        cmd += ["--data-binary", data if isinstance(data, str) else data.decode()]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        return 0, "curl timed out"
    if proc.returncode != 0:
        # Never surface stderr verbatim: curl echoes the proxy URL (with
        # credentials) in several of its error messages.
        return 0, f"curl exit {proc.returncode}"
    body, _, code = proc.stdout.rpartition("\n")
    try:
        return int(code.strip()), body
    except ValueError:
        return 0, "curl produced no status code"


def fetch(url: str, *, data=None, headers=None, timeout=30, proxy: Proxy | None = None) -> tuple[int, str]:
    """HTTP with curl as the transport, urllib only as a fallback.

    curl is not a stylistic choice. urllib's ProxyHandler mishandles the HTTPS
    CONNECT tunnel through this residential proxy: measured 2026-07-30, every
    request died with IncompleteRead(~7900 of ~29000 bytes) or
    RemoteDisconnected, across Connection:close, identity encoding, and gzip.
    The identical request through curl and the same proxy returned the full
    body. curl also matches the convention of the sibling search-console and
    adsense skills - no dependencies, just curl.
    """
    hdrs = {**BROWSER_HEADERS, **(headers or {})}
    if proxy and proxy.url().startswith("socks") and not have_curl():
        raise RuntimeError(
            "SEO_PROXY_SOCKS is set but curl is unavailable and urllib cannot speak SOCKS5. "
            "Install curl, or use the HTTP CONNECT endpoint in SEO_PROXY_URL "
            "(many providers serve both; :8080 is typically HTTP CONNECT, and SOCKS5 has been measured "
            ":1080 dropping streamed responses anyway)."
        )
    if have_curl():
        return _fetch_curl(url, data, hdrs, timeout, proxy)

    opener_args = []
    if proxy:
        p = proxy.url()
        opener_args.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    opener = urllib.request.build_opener(*opener_args)
    req = urllib.request.Request(url, data=data.encode() if isinstance(data, str) else data, headers=hdrs)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, type(exc).__name__


def registrable(host: str) -> str:
    host = (host or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    two = {"co.uk", "com.au", "co.jp", "co.nz", "com.br", "co.in", "org.uk", "ac.uk", "gov.uk"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


# --------------------------------------------------------------- relevance


RELEVANCE_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "how",
    "what", "why", "is", "are", "was", "were", "best", "vs", "versus", "my",
    "your", "you", "can", "does", "do", "from", "that", "this", "it",
}
# Tuned on real fixtures (2026-07-30): the poisoned Bing reads scored
# coverage 0.25 / hit_rate 0.00; a good DuckDuckGo read for the same query
# scored 1.00 / 0.90. These sit in the middle of a very wide gap.
MIN_COVERAGE = 0.6
MIN_HIT_RATE = 0.3


def relevance_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in RELEVANCE_STOPWORDS and len(t) > 2]


def verify_relevance(query: str, results: list[dict]) -> dict:
    """Are these results actually ABOUT the query?

    Two numbers, because one is not enough:

      coverage   how many DISTINCT query tokens appear anywhere in the top 10.
                 Catches the engine that latched onto one token and ignored the
                 rest - the real Bing failure matched only "self" out of
                 "self hosted rank tracker" and returned Self-brand furniture
                 and Wikipedia's article on selfhood. A naive "at least one
                 query token appears" rule PASSES that; this does not.
      hit_rate   what fraction of results carry at least half the query tokens.
                 Catches a result set where the tokens are scattered one per
                 page rather than concentrated in relevant pages.
    """
    qt = relevance_tokens(query)
    top = results[:10]
    if not qt or not top:
        # Nothing to judge against. Fail open with a note rather than blocking:
        # a one-word query after stopword removal is legitimate.
        return {"checked": False, "pass": True, "coverage": None, "hit_rate": None,
                "note": "not enough query tokens or results to judge relevance"}
    blobs = []
    for r in top:
        slug = (r.get("url") or "").replace("-", " ").replace("_", " ").replace("/", " ")
        blobs.append(" ".join([r.get("title") or "", slug, r.get("snippet") or ""]).lower())
    joined = " ".join(blobs)
    coverage = sum(1 for t in qt if t in joined) / len(qt)
    need = max(2, (len(qt) + 1) // 2) if len(qt) >= 2 else 1
    hit_rate = sum(1 for b in blobs if sum(1 for t in qt if t in b) >= need) / len(blobs)
    ok = coverage >= MIN_COVERAGE and hit_rate >= MIN_HIT_RATE

    # Two measured FALSE POSITIVES (2026-08-01). Both were on-topic SERPs that
    # this guard called "results for a different query", which under the quality
    # bar means the candidate cannot be queued at all - so an over-strict guard
    # silently deletes exactly the long-tail research a young site depends on.
    # The failure this guard exists for (Bing answering "self hosted rank
    # tracker" with Self-brand furniture) is characterised by LOW COVERAGE - it
    # matched 1 of 4 tokens. Neither exemption below touches that case.
    verdict_override = None

    # (1) One surviving token cannot discriminate. "cs 1.6 unblocked" reduces to
    # ["unblocked"], so coverage is a binary "does this exact word appear". The
    # real page 1 was entirely browser-play results - the RIGHT intent - and
    # scored 0.0 because no title said "unblocked". Google broadening a
    # qualifier is not the engine answering a different question, and it is
    # itself a useful finding: the qualifier has no distinct SERP to win.
    if not ok and len(qt) == 1 and coverage < MIN_COVERAGE:
        ok, verdict_override = True, "qualifier_ignored"

    # (2) Full coverage means every distinct query token IS present across the
    # top 10, which definitionally excludes the wrong-query failure. A low
    # hit_rate there measures synonym spread, not correctness: "cs 1.6 zombie
    # mode online" returned Zombie Plague servers, Zombie Escape and zombie-mod
    # server lists - unmistakably the right SERP - and missed by 0.25 vs 0.30
    # because titles say "mod"/"servers" rather than "mode"/"online".
    elif not ok and coverage >= 1.0:
        ok, verdict_override = True, "low_concentration"

    out = {
        "checked": True,
        "pass": ok,
        "coverage": round(coverage, 3),
        "hit_rate": round(hit_rate, 3),
        "query_tokens": qt,
        "tokens_required_per_result": need,
        "thresholds": {"coverage": MIN_COVERAGE, "hit_rate": MIN_HIT_RATE},
        "note": (
            "results are about the query"
            if ok and not verdict_override
            else "THESE RESULTS ARE NOT FOR THIS QUERY - the engine returned a well-formed SERP "
                 "for something else. Do not score it, do not derive an authority count from it, "
                 "and do not treat it as an empty page 1. Retry on another provider."
        ),
    }
    if verdict_override == "qualifier_ignored":
        out["qualifier_ignored"] = True
        out["note"] = ("PASSED WITH A CAVEAT - the query reduced to one distinctive token and the "
                       "engine did not honour it, so page 1 is the BROADER query's. Score it, but "
                       "read the authority count as the broad term's, and treat the qualifier as "
                       "having no SERP of its own to win.")
    elif verdict_override == "low_concentration":
        out["low_concentration"] = True
        out["note"] = ("PASSED WITH A CAVEAT - every query token appears across page 1 (coverage "
                       "1.0), so these ARE this query's results; the low hit_rate reflects synonym "
                       "variation in titles rather than a wrong SERP.")
    return out


def shape_ok(results: list[dict]) -> bool:
    return bool(results) and any((r.get("url") or "").startswith("http") for r in results)


# --------------------------------------------------------------- providers


def provider_ddg(query: str, count: int, *, proxy: Proxy | None = None, **_) -> list[dict]:
    """DuckDuckGo's no-JS HTML endpoint. Keyless. ~10 results/page.

    Throttling is real and it is NOT an HTTP 429: exhaustion returns HTTP 202
    with a ~14KB anomaly page. Everything else about it was measured on
    2026-07-30, and most of it contradicts the obvious guesses:

      - It is NOT per-IP, and a proxy does not rescue it. Six fresh residential
        exits across six countries (nl se it pl ie cz) all returned 202 in the
        same minute that a direct request did.
      - It is NOT the request shape. POST and GET, the full browser header set
        and a bare User-Agent, html.duckduckgo.com and lite.duckduckgo.com and
        duckduckgo.com/html - all 202 together.
      - It IS time-based. The same client got 10 clean results ~20 minutes
        earlier and again ~40 minutes later.

    So the honest handling is: retry with backoff, rotate the proxy session
    once in case this instance happens to be exit-scoped, and otherwise fail
    over to --provider browser, which was verified returning real Google
    results at the exact moment every HTTP provider was 202-ing.
    """
    results: list[dict] = []
    backoffs = [4, 10, 25]
    for page in range(0, max(1, (count + 9) // 10)):
        body = urllib.parse.urlencode({"q": query, "s": page * 10, "kl": "us-en"})
        status, text = 0, ""
        rotated = False
        for attempt in range(len(backoffs) + 1):
            status, text = fetch(
                "https://html.duckduckgo.com/html/", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Origin": "https://html.duckduckgo.com",
                         "Referer": "https://html.duckduckgo.com/"},
                proxy=proxy,
            )
            if status == 200 and "result__a" in text:
                break
            # Cheap and occasionally right - but measured NOT to clear the 202,
            # so it is one attempt, not a strategy.
            if proxy and not rotated and status in (200, 202, 403, 429):
                proxy.rotate()
                rotated = True
                continue
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
        if status != 200 or "result__a" not in text:
            if not results:
                hint = ""
                if status in (200, 202):
                    hint = (
                        " - the anomaly page. Measured: this is time-based, not per-IP and not "
                        "request-shape, so neither a proxy rotation nor a different endpoint "
                        "clears it"
                        + (" (a rotation was already tried this call)" if rotated else "")
                        + ". Use --provider browser, which returns real Google and has been "
                        "verified working while this endpoint was refusing, or wait it out."
                    )
                raise RuntimeError(f"duckduckgo returned HTTP {status}{hint}")
            break
        found = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|</html>)',
            text, re.S,
        )
        if not found:
            break
        for href, title_html, tail in found:
            url = html.unescape(href)
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                url = urllib.parse.unquote(m.group(1))
            if url.startswith("//"):
                url = "https:" + url
            sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
            snippet = re.sub(r"<[^>]+>", " ", sm.group(1) if sm else "")
            snippet = html.unescape(re.sub(r"\s+", " ", snippet)).strip()[:300]
            results.append({
                "title": html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip(),
                "url": url,
                "snippet": snippet,
            })
        if len(results) >= count:
            break
        time.sleep(1.5)
    return results[:count]


def provider_serpd(query: str, count: int, *, target=None, gl="us", hl="en", **_) -> list[dict]:
    """The local SERP daemon (scripts/serpd.py) - real Google, ~1.4s/query.

    Same headed-Chrome route as --provider browser, but the daemon holds the
    browser open and does the navigate/readiness/extract/score cycle itself, so
    this is one HTTP call with no DOM in the caller's context. For a whole
    research run use its /batch endpoint directly rather than looping here:
    25 queries measured at 37s and 1.4KB of verdicts.
    """
    port = os.environ.get("SERPD_PORT", "8791")
    qs = urllib.parse.urlencode({"q": query, "depth": count, "gl": gl, "hl": hl,
                                 **({"target": target} if target else {})})
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/serp?{qs}", timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read())
        except Exception:
            raise RuntimeError(f"serpd HTTP {exc.code}")
        raise RuntimeError(f"serpd: {data.get('error', 'unknown')}")
    except Exception:
        raise RuntimeError(
            f"serpd is not running on 127.0.0.1:{port}. Start it with: "
            "python3 scripts/serpd.py --start"
        )
    if not data.get("ok"):
        raise RuntimeError(f"serpd: {data.get('error', 'unknown')}")
    if data.get("ai_overview"):
        results_meta["ai_overview"] = data["ai_overview"]
    return data.get("results", [])[:count]


def provider_searxng(query: str, count: int, *, instance=None, proxy=None, **_) -> list[dict]:
    base = (instance or os.environ.get("SEARXNG_URL") or "").rstrip("/")
    if not base:
        raise RuntimeError("searxng needs --instance https://your-searxng or SEARXNG_URL")
    qs = urllib.parse.urlencode({"q": query, "format": "json", "engines": "google,bing,duckduckgo", "language": "en"})
    status, text = fetch(f"{base}/search?{qs}", proxy=proxy)
    if status != 200:
        raise RuntimeError(f"searxng returned HTTP {status}: {text[:160]}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("searxng did not return JSON - is the json format enabled in settings.yml?")
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": (r.get("content") or "")[:300]}
            for r in data.get("results", [])[:count]]


def provider_brave(query: str, count: int, *, proxy=None, **_) -> list[dict]:
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        raise RuntimeError("brave needs BRAVE_SEARCH_API_KEY (free tier: 2000 queries/month)")
    qs = urllib.parse.urlencode({"q": query, "count": min(count, 20), "country": "us", "search_lang": "en"})
    status, text = fetch(f"https://api.search.brave.com/res/v1/web/search?{qs}",
                         headers={"Accept": "application/json", "X-Subscription-Token": key}, proxy=proxy)
    if status != 200:
        raise RuntimeError(f"brave returned HTTP {status}: {text[:160]}")
    data = json.loads(text)
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": (r.get("description") or "")[:300]}
            for r in (data.get("web", {}).get("results") or [])[:count]]


def provider_serpapi(query: str, count: int, *, gl="us", hl="en", proxy=None, **_) -> list[dict]:
    key = os.environ.get("SERPAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("serpapi needs SERPAPI_KEY")
    qs = urllib.parse.urlencode({"engine": "google", "q": query, "num": min(count, 100),
                                 "gl": gl, "hl": hl, "api_key": key})
    status, text = fetch(f"https://serpapi.com/search.json?{qs}", timeout=45, proxy=proxy)
    if status != 200:
        raise RuntimeError(f"serpapi returned HTTP {status}: {text[:160]}")
    data = json.loads(text)
    if data.get("error"):
        raise RuntimeError(f"serpapi: {data['error']}")
    results = [{"title": r.get("title", ""), "url": r.get("link", ""),
                "snippet": (r.get("snippet") or "")[:300], "position": r.get("position")}
               for r in (data.get("organic_results") or [])[:count]]
    ai = data.get("ai_overview")
    if ai:
        results_meta["ai_overview"] = {
            "present": True,
            "references": [{"domain": host_of(r.get("link", "")), "url": r.get("link"),
                            "title": r.get("title") or r.get("source")}
                           for r in (ai.get("references") or [])],
        }
    return results


def provider_dataforseo(query: str, count: int, *, gl=2840, hl="en", proxy=None, **_) -> list[dict]:
    login = os.environ.get("DATAFORSEO_LOGIN", "")
    password = os.environ.get("DATAFORSEO_PASSWORD", "")
    if not (login and password):
        raise RuntimeError("dataforseo needs DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD")
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    loc = int(gl) if str(gl).isdigit() else 2840
    payload = json.dumps([{"keyword": query, "location_code": loc, "language_code": hl, "depth": min(count, 100)}])
    status, text = fetch("https://api.dataforseo.com/v3/serp/google/organic/live/advanced", data=payload,
                         headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                         timeout=60, proxy=proxy)
    if status != 200:
        raise RuntimeError(f"dataforseo returned HTTP {status}: {text[:160]}")
    data = json.loads(text)
    try:
        items = data["tasks"][0]["result"][0]["items"] or []
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"dataforseo returned no results: {text[:200]}")
    results = []
    for it in items:
        if it.get("type") == "ai_overview":
            results_meta["ai_overview"] = {"present": True, "references": []}
            continue
        if it.get("type") != "organic":
            continue
        results.append({"title": it.get("title", ""), "url": it.get("url", ""),
                        "snippet": (it.get("description") or "")[:300], "position": it.get("rank_absolute")})
    return results[:count]


PROVIDERS = {
    "serpd": provider_serpd,
    "ddg": provider_ddg,
    "searxng": provider_searxng,
    "brave": provider_brave,
    "serpapi": provider_serpapi,
    "dataforseo": provider_dataforseo,
}


# -------------------------------------------------------- browser handoff


# Readiness trichotomy, ported from veikkaus-browser/scripts/wait-cloudflare.js.
# The distinction that matters: "challenging" (a real challenge is up) is not
# the same as "short" (the page simply has not hydrated). Collapsing them is
# what produced 40 false "blocked" verdicts in one recorded batch.
BROWSER_READY = r"""(function(){
  var t=document.body?document.body.innerText:'';
  var markers=['Performing security verification','Just a moment','Checking your browser',
               'Verify you are human','Verifying you are human','cf-please-wait','needs to review the security'];
  for(var i=0;i<markers.length;i++){ if(t.indexOf(markers[i])>=0)
    return JSON.stringify({status:'challenging',marker:markers[i]}); }
  if(t.indexOf('Ray ID:')>=0 && t.length<600)
    return JSON.stringify({status:'challenging',marker:'cf:ray-id-interstitial'});
  // Hard fail: Turnstile already fingerprinted and REJECTED this browser. It
  // renders a feedback report, never a checkbox, so clicking can only time out.
  var fr=document.querySelector('iframe[src*="/auto/failure"],iframe[src*="cf-turnstile-feedback"]');
  if(fr) return JSON.stringify({status:'hard-fail',marker:'turnstile-rejected',
    hint:'fingerprint rejected - retrying is futile, change approach (new profile, proxy, or another provider)'});
  // Deliberately NOT hard-fail. Measured: a query that returned /sorry during a
  // 6-wide burst returned 8 clean results on its own 20s later. Conflating a
  // transient IP rate-limit with a terminal fingerprint rejection throws away
  // work that a short backoff would have got.
  if(/sorry\/index|unusual traffic from your computer/i.test(t))
    return JSON.stringify({status:'rate-limited',marker:'google-sorry',
      hint:'transient IP rate limit from bursting - back off and retry, do not treat as blocked'});
  // Wait for the DATA, not for page chrome that loads regardless.
  if(document.querySelectorAll('a h3').length>0)
    return JSON.stringify({status:'ready',len:t.length,results:document.querySelectorAll('a h3').length});
  if(t.length<1000) return JSON.stringify({status:'short',len:t.length,sample:t.substring(0,150)});
  return JSON.stringify({status:'short',len:t.length,sample:t.substring(0,150),
    note:'body is substantial but no result headings yet'});
})()"""

BROWSER_EXTRACT = r"""(function(){
  var seen={}, out=[];
  var hs=document.querySelectorAll('a h3');
  for(var i=0;i<hs.length;i++){
    var h=hs[i], a=h.closest('a'); if(!a||!a.href) continue;
    var u=a.href;
    if(u.indexOf('/url?')===0) u=new URLSearchParams(u.split('?')[1]).get('q')||u;
    if(seen[u]) continue;
    if(/google\.[a-z.]+\/(search|preferences|advanced)/.test(u)) continue;
    seen[u]=1;
    var box=a.closest('div.g')||a.closest('div[data-hveid]')||a.parentElement;
    var snip='';
    if(box){ var lines=(box.innerText||'').split('\n'); lines.shift(); snip=lines.join(' ').slice(0,240); }
    out.push({position:out.length+1,title:h.textContent.trim(),url:u,snippet:snip});
  }
  var body=document.body.innerText;
  var q0=(new URLSearchParams(location.search).get('q')||'').toLowerCase();
  var paa=[], pseen={};
  document.querySelectorAll('[data-q]').forEach(function(e){
    var q=(e.getAttribute('data-q')||'').trim();
    // The search box carries data-q too and hands the query straight back.
    if(!q||pseen[q]||q.toLowerCase()===q0) return;
    pseen[q]=1; paa.push(q); });
  return JSON.stringify({
    query:new URLSearchParams(location.search).get('q'),
    provider:'browser-google',
    ai_overview:{present:/AI Overview|AI-powered overview/i.test(body),references:[]},
    people_also_ask:paa.slice(0,10),
    results:out
  });
})()"""

# IANA timezone + locale per verified proxy exit country, so the browser's
# fingerprint agrees with where its traffic appears to come from. This is
# COHERENCE, not disguise: a UA claiming Chrome-on-Windows from a German exit
# with an America/New_York clock is more suspicious than no override at all.
COUNTRY_FINGERPRINT = {
    "de": ("de_DE", "Europe/Berlin"), "nl": ("nl_NL", "Europe/Amsterdam"),
    "se": ("sv_SE", "Europe/Stockholm"), "it": ("it_IT", "Europe/Rome"),
    "es": ("es_ES", "Europe/Madrid"), "pl": ("pl_PL", "Europe/Warsaw"),
    "ro": ("ro_RO", "Europe/Bucharest"), "pt": ("pt_PT", "Europe/Lisbon"),
    "be": ("nl_BE", "Europe/Brussels"), "at": ("de_AT", "Europe/Vienna"),
    "cz": ("cs_CZ", "Europe/Prague"), "dk": ("da_DK", "Europe/Copenhagen"),
    "ie": ("en_IE", "Europe/Dublin"), "ch": ("de_CH", "Europe/Zurich"),
    "gr": ("el_GR", "Europe/Athens"),
}


def browser_handoff(query: str, count: int, gl: str, hl: str, proxy: Proxy | None) -> dict:
    url = "https://www.google.com/search?" + urllib.parse.urlencode(
        {"q": query, "num": min(count, 30), "hl": hl, "gl": gl, "pws": "0"}
    )
    start = {
        "headless": False,
        "low_memory": False,
        "user_data_dir": "/tmp/seo-serp-profile",
        "window_size": "1440x900",
    }
    steps = [
        "mcp__browser__get_browser_status - reuse a running browser rather than restarting it",
        f"mcp__browser__start_browser({json.dumps(start)[1:-1]}) if not already running",
    ]
    if proxy:
        start["proxy"] = "<SEO_PROXY_URL with the session selector - read it from the env, never paste it here>"
        cc = proxy.country
        fp = COUNTRY_FINGERPRINT.get(cc or "")
        if fp:
            steps.append(f"mcp__browser__set_locale(locale={fp[0]!r}) then "
                         f"mcp__browser__set_timezone(timezone_id={fp[1]!r}) - match the proxy's exit country")
    steps += [
        f"mcp__browser__navigate(url={url!r})",
        "mcp__browser__execute_js(script=<readiness_script>) -> parse the JSON status",
        "  status 'ready'      -> run <extract_script>",
        "  status 'short'      -> mcp__browser__wait(8), re-run readiness (up to 3 times)",
        "  status 'challenging'-> mcp__browser__wait(8), re-run readiness (up to 3 times), then "
        "ONE mcp__browser__reload_page, then 3 more polls before declaring blocked",
        "  status 'hard-fail'  -> STOP polling. The fingerprint was rejected; clicking cannot fix it. "
        "Rotate the proxy session, use a fresh user_data_dir, or switch provider.",
        "mcp__browser__execute_js(script=<extract_script>)",
        "pipe the returned JSON back: serp.py --score-json - (runs the relevance + weakness scoring)",
    ]
    return {
        "ok": True,
        "provider": "browser",
        "action_required": "drive the browser MCP yourself - this script cannot call MCP tools",
        "why_browser": (
            "Google serves a JS-only shell to every HTTP client - measured 2026-07-30 at 200/~90KB "
            "with zero <h3> and zero result links, direct AND through a residential proxy, and with "
            "a Googlebot UA. The browser is the only route to real Google results."
        ),
        "start_browser_args": start,
        "critical": [
            "headless MUST be false: in --headless=new a Turnstile challenge is unsolvable, while a "
            "headed browser often loads the same page with no challenge at all.",
            "low_memory MUST be false: its flags (software WebGL, --disable-gpu) are themselves a "
            "bot signal, and bypass_cloudflare has been measured timing out under it and solving "
            "without it.",
            "keep the persistent user_data_dir so cookies and any challenge clearance survive runs. "
            "Verified by ps: this launches real /bin/google-chrome with NO --headless flag, on the "
            "Xvfb display, with uBlock already side-loaded by the MCP - the same shape the "
            "veikkaus-browser skill uses.",
            "never blank the User-Agent - an empty override is MORE fingerprintable than the real one. "
            "Only align locale/timezone, and only to match the proxy's exit country.",
        ],
        "url": url,
        "readiness_script": BROWSER_READY,
        "extract_script": BROWSER_EXTRACT,
        "steps": steps,
        "proxy": proxy.label() if proxy else None,
    }


# ------------------------------------------------------------------ scoring


def score(results: list[dict], target_domain: str | None = None) -> dict:
    """Annotate page-1 results with the signals the quality bar asks for.

    HINTS, not verdicts. The authority count is the agent's call after reading
    the titles - a heuristic cannot tell "recognised brand in the niche" from
    "random blog with a good domain". What it CAN do reliably is spot the
    weakness signals, which is the half that gets miscounted by eye.
    """
    weak, authorityish, target_hit = [], [], None
    for i, r in enumerate(results, 1):
        r.setdefault("position", i)
        dom = registrable(host_of(r.get("url", "")))
        r["domain"] = dom
        tags = []
        if dom in WEAK_DOMAINS:
            tags.append("forum/social/video")
        if dom in DOCS_DOMAINS:
            tags.append("repo/package-host")
        title = r.get("title", "")
        if THIN_TITLE.search(title):
            tags.append("listicle")
        year = YEAR_IN_TITLE.search(title)
        if year:
            r["title_year"] = int(year.group(1))
        r["signals"] = tags
        if tags and "repo/package-host" not in tags:
            weak.append({"position": r["position"], "domain": dom, "why": ",".join(tags)})
        elif not tags:
            authorityish.append({"position": r["position"], "domain": dom})
        if target_domain and dom == registrable(target_domain) and target_hit is None:
            target_hit = r["position"]

    top10 = results[:10]
    weak10 = [w for w in weak if w["position"] <= 10]
    return {
        "results_seen": len(results),
        "weakness_signals": weak10,
        "weakness_count": len(weak10),
        "strong_serp_weakness": len(weak10) >= 2,
        "authority_candidates": [a for a in authorityish if a["position"] <= 10],
        "authority_candidate_count": len([a for a in authorityish if a["position"] <= 10]),
        "distinct_domains_top10": len({r.get("domain") for r in top10}),
        "target_position": target_hit,
        "verdict_note": (
            "authority_candidate_count is a CEILING on the real authority count, not the count "
            "itself: it counts every page-1 domain that is not obviously a forum, video, repo or "
            "listicle. Read the titles and decide which are genuinely established authority for "
            "THIS query (recognised brands, the vendor's own domain, official docs). 4+ real ones "
            "= DROP the candidate."
        ),
    }


# --------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", help="the search query")
    p.add_argument("--provider", default=os.environ.get("SEO_SERP_PROVIDER", "ddg"),
                   choices=["ddg", "serpd", "searxng", "brave", "serpapi", "dataforseo", "browser"])
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--instance", help="searxng base URL")
    p.add_argument("--gl", default="us", help="country (serpapi/brave) or location_code (dataforseo)")
    p.add_argument("--hl", default="en")
    p.add_argument("--target-domain", help="report this domain's position if present")
    p.add_argument("--fallback", action="store_true", help="try other configured providers on failure")
    p.add_argument("--proxy-country", metavar="CC",
                   help=f"pin the residential exit country. Verified pool: {' '.join(EU_COUNTRIES)}")
    p.add_argument("--no-proxy", action="store_true", help="ignore SEO_PROXY_URL for this call")
    p.add_argument("--score-json", metavar="PATH_OR_-",
                   help="skip fetching: score a results JSON produced elsewhere (e.g. the browser path)")
    p.add_argument("--query-for-scoring", help="the query --score-json results were fetched for, "
                                               "when the payload does not carry it")
    p.add_argument("--raw", action="store_true", help="omit the scoring block")
    a = p.parse_args()

    if a.proxy_country and a.proxy_country.lower() not in EU_COUNTRIES:
        cc = a.proxy_country.lower()
        extra = (" 'us' in particular is NOT honoured - the pool silently returns a random non-US "
                 "exit, so a US-pinned request would quietly lie about its geo." if cc == "us" else "")
        print(json.dumps({"ok": False, "error": f"unverified proxy country {cc!r}.{extra}",
                          "verified_pool": EU_COUNTRIES}, indent=2))
        sys.exit(2)

    # -- score an externally-produced payload (the browser path) -------------
    if a.score_json:
        text = sys.stdin.read() if a.score_json == "-" else open(a.score_json, encoding="utf-8").read()
        data = json.loads(text)
        if isinstance(data, str):  # execute_js hands back a JSON string
            data = json.loads(data)
        results = data.get("results", data if isinstance(data, list) else [])
        payload = {**(data if isinstance(data, dict) else {}), "results": results}
        query = a.query_for_scoring or a.query or payload.get("query") or ""
        rel = verify_relevance(query, results)
        payload["relevance"] = rel
        if not rel["pass"]:
            payload["ok"] = False
            payload["error"] = "wrong-query results - refusing to score"
            payload["observed_titles"] = [r.get("title") for r in results[:10]]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            sys.exit(3)
        payload["ok"] = True
        payload["scoring"] = score(results, a.target_domain)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not a.query:
        p.error("query is required unless --score-json is used")

    proxy = resolve_proxy(a.proxy_country, enabled=not a.no_proxy)

    if a.provider == "browser":
        print(json.dumps(browser_handoff(a.query, a.count, a.gl, a.hl, proxy), indent=2))
        return

    def keyed_alternatives() -> list[str]:
        avail = []
        try:
            port = os.environ.get("SERPD_PORT", "8791")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if json.loads(r.read()).get("chrome_alive"):
                    avail.append("serpd")   # real Google, so it outranks the keyed ones
        except Exception:
            pass
        if os.environ.get("SERPAPI_KEY"):
            avail.append("serpapi")
        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            avail.append("brave")
        if os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD"):
            avail.append("dataforseo")
        if os.environ.get("SEARXNG_URL") or a.instance:
            avail.append("searxng")
        return avail

    chain = [a.provider]
    if a.fallback:
        chain += [c for c in (keyed_alternatives() + ["ddg"]) if c not in chain]

    errors, results, used = {}, None, a.provider
    for cand in chain:
        try:
            got = PROVIDERS[cand](a.query, a.count, instance=a.instance, gl=a.gl, hl=a.hl, proxy=proxy)
        except Exception as exc:
            errors[cand] = str(exc)
            continue
        if not shape_ok(got):
            # HTTP 200 with nothing parseable is NOT an empty page 1.
            errors[cand] = ("returned no parseable results - treat as a failed read, never as an "
                            "empty SERP")
            continue
        rel = verify_relevance(a.query, got)
        if not rel["pass"]:
            errors[cand] = (f"returned results for a DIFFERENT query (coverage {rel['coverage']}, "
                            f"hit_rate {rel['hit_rate']}): "
                            + ", ".join(f'"{r.get("title","")[:40]}"' for r in got[:3]))
            continue
        results, used = got, cand
        break

    if results is None:
        payload = browser_handoff(a.query, a.count, a.gl, a.hl, proxy)
        payload.update({
            "ok": False,
            "provider": a.provider,
            # `error` mirrors the first real reason as a one-liner. Without it this
            # payload carried ok:false with error UNSET while the actual cause sat
            # in `errors` - and a caller reading the obvious top-level field prints
            # "FAILED: None", which reads like an opaque crash and gets the SCRIPT
            # blamed for a provider throttle it diagnosed perfectly. Measured
            # 2026-08-01: that misread cost a research run's worth of confidence.
            # The full per-provider detail stays in `errors`; this is a signpost.
            "error": ("; ".join(f"{k}: {v}" for k, v in errors.items())[:400]
                      or "all configured SERP providers failed - see the browser handoff"),
            "errors": errors,
            "proxy": proxy.label() if proxy else None,
            "why_this_block": (
                "Every configured SERP provider failed or returned results that were not for this "
                "query, so this is the browser handoff rather than a dead end. The browser path is "
                "keyless, takes a different network route, and returns real Google."
            ),
            "never": (
                "Do NOT proceed as if page 1 were empty. No usable SERP read means no authority "
                "count, and no authority count means the candidate does not pass. This also does "
                "not consume one of the run's 25 SERP checks."
            ),
        })
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(2)

    payload = {
        "ok": True,
        "query": a.query,
        "provider": used,
        "fell_back_from": a.provider if used != a.provider else None,
        "provider_errors": errors or None,
        "proxy": proxy.label() if proxy else None,
        "results": results,
        "relevance": verify_relevance(a.query, results),
        **results_meta,
    }
    if not a.raw:
        payload["scoring"] = score(results, a.target_domain)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
