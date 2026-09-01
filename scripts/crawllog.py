#!/usr/bin/env python3
"""Server-log crawl analysis - what the bots ACTUALLY did, from your own logs.

This is the only first-party measurement in the whole skill. Everything else
here asks a third party what it thinks (a SERP, an autocomplete list, a Search
Console aggregate). An access log is the site's own record of what happened,
it is free, it is complete, and nobody else has it.

It answers four questions nothing else in this skill can:

  1. CRAWL BUDGET - where does Googlebot actually spend its visits? On a site
     with thousands of generated pages the answer is routinely "on assets and
     images", and you cannot fix what you cannot see. Search Console's Crawl
     Stats report shows totals; it will not tell you that 60% of your budget
     went to one image directory.

  2. WHAT BOTS WERE SERVED - status codes, per bot. A deploy that 404s the
     whole site for three minutes is invisible in every other tool, and
     catastrophic if Googlebot walked through the window. So is a 403 that
     only bots see.

  3. AI INGESTION, MEASURED - the geo-scan workflow measures whether
     assistants CITE the site. This measures whether they READ it, which is
     the prerequisite, and it is a hard number rather than a sample. The
     distinction between the three OpenAI agents is the whole point:

       GPTBot         trains models. Never cites you. Blocking it costs
                      nothing in traffic and gains nothing either.
       OAI-SearchBot  builds the index ChatGPT search cites FROM. Blocking it
                      removes you from ChatGPT's answers.
       ChatGPT-User   a live fetch because a real person asked a question and
                      the assistant went to your page for the answer. This is
                      the strongest AEO signal that exists - it is a citation
                      happening, recorded on your own server.

     Conflating them (as every "AI bot blocker" listicle does) is how sites
     accidentally opt out of AI search while still feeding the training run.

  4. WHO IS TAKING WITHOUT GIVING - SEO crawlers (Ahrefs, Semrush, MJ12) can
     out-request Googlebot several times over. They send no traffic and index
     nothing; they resell your content as competitor intelligence. Knowing the
     ratio is the input to a robots.txt decision.

USER AGENTS ARE CLAIMS, NOT FACTS. Anything can send `Googlebot` in a header,
and plenty does. `verify` performs Google's own documented check - reverse DNS
to a hostname on the operator's domain, then a FORWARD lookup back to the same
IP - because reverse DNS alone is forgeable by whoever controls the PTR record.
Counting spoofed hits as crawl budget is how you conclude Google loves a page
it has never fetched.

Formats: Caddy JSON (auto-detected), Apache/nginx `combined` and `common`, and
any custom regex. Reads .gz archives directly.

The logs usually live on the server, not here. `--remote` ships THIS FILE over
ssh and runs the aggregation there, so a gigabyte of decompressed log never
crosses the wire - only the JSON verdict comes back.

Usage:
    crawllog.py scan --glob '/var/log/caddy/access*.log*'
    crawllog.py scan --remote root@host --ssh-key ~/.ssh/id --glob '/var/log/caddy/access*.log*'
    crawllog.py scan -f access.log --days 7 --bot googlebot --silo-depth 2
    crawllog.py verify --ip 66.249.66.1 --ip 1.2.3.4
    crawllog.py gap --scan scan.json --sitemap https://example.com/sitemap.xml

Stdlib only.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob as globmod
import gzip
import io
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

UA = "seo-manager-crawllog/1.0"

# --------------------------------------------------------------------------
# The bot registry.
#
# `category` is the field that carries the meaning:
#   search       indexes the page and can send you organic traffic
#   ai_search    feeds an assistant that CITES sources - being read here is
#                the precondition for being cited
#   ai_training  trains a model. No citation, no traffic, ever.
#   ai_user      a live fetch triggered by a real person's question. The
#                strongest answer-engine signal available.
#   social       link unfurling / preview cards
#   seo_tool     third-party crawlers that resell your content as competitor
#                data. They index nothing and send nothing.
#
# `verify` lists the rDNS suffixes the operator publishes. An empty list means
# the operator does NOT support rDNS verification (OpenAI and Anthropic publish
# IP ranges instead) - `verify` reports that honestly rather than calling an
# unverifiable hit fake.
# --------------------------------------------------------------------------
BOTS = [
    # --- Google ---------------------------------------------------------
    ("google-inspectiontool", "Google-InspectionTool", "search", [".googlebot.com", ".google.com"]),
    ("googlebot-image", "Googlebot-Image", "search", [".googlebot.com", ".google.com"]),
    ("googlebot-video", "Googlebot-Video", "search", [".googlebot.com", ".google.com"]),
    ("googlebot-news", "Googlebot-News", "search", [".googlebot.com", ".google.com"]),
    ("storebot-google", "Storebot-Google", "search", [".googlebot.com", ".google.com"]),
    ("googleother", "GoogleOther", "search", [".googlebot.com", ".google.com"]),
    ("google-extended", "Google-Extended", "ai_training", [".googlebot.com", ".google.com"]),
    ("adsbot-google", "AdsBot-Google", "seo_tool", [".googlebot.com", ".google.com"]),
    ("apis-google", "APIs-Google", "seo_tool", [".googlebot.com", ".google.com"]),
    ("feedfetcher-google", "Feedfetcher-Google", "seo_tool", [".google.com"]),
    ("googlebot", "Googlebot", "search", [".googlebot.com", ".google.com"]),
    # --- Microsoft ------------------------------------------------------
    ("bingbot", "bingbot", "search", [".search.msn.com"]),
    ("adidxbot", "adidxbot", "seo_tool", [".search.msn.com"]),
    ("msnbot", "msnbot", "search", [".search.msn.com"]),
    ("bingpreview", "BingPreview", "search", [".search.msn.com"]),
    # --- OpenAI (three agents, three completely different meanings) ------
    ("chatgpt-user", "ChatGPT-User", "ai_user", []),
    ("oai-searchbot", "OAI-SearchBot", "ai_search", []),
    ("gptbot", "GPTBot", "ai_training", []),
    # --- Anthropic ------------------------------------------------------
    ("claude-user", "Claude-User", "ai_user", []),
    ("claude-searchbot", "Claude-SearchBot", "ai_search", []),
    ("claudebot", "ClaudeBot", "ai_training", []),
    ("anthropic-ai", "anthropic-ai", "ai_training", []),
    # --- Perplexity -----------------------------------------------------
    ("perplexity-user", "Perplexity-User", "ai_user", []),
    ("perplexitybot", "PerplexityBot", "ai_search", []),
    # --- Apple / Amazon / Meta / others ----------------------------------
    ("applebot-extended", "Applebot-Extended", "ai_training", [".applebot.apple.com"]),
    ("applebot", "Applebot", "search", [".applebot.apple.com"]),
    ("amazonbot", "Amazonbot", "ai_training", [".crawl.amazon.com"]),
    ("meta-externalagent", "meta-externalagent", "ai_training", []),
    ("meta-externalfetcher", "meta-externalfetcher", "ai_user", []),
    ("facebookexternalhit", "facebookexternalhit", "social", []),
    ("twitterbot", "Twitterbot", "social", []),
    ("linkedinbot", "LinkedInBot", "social", []),
    ("slackbot", "Slackbot", "social", []),
    ("discordbot", "Discordbot", "social", []),
    ("telegrambot", "TelegramBot", "social", []),
    ("whatsapp", "WhatsApp", "social", []),
    # --- Other search ----------------------------------------------------
    ("yandexbot", "YandexBot", "search", [".yandex.ru", ".yandex.net", ".yandex.com"]),
    ("duckassistbot", "DuckAssistBot", "ai_search", []),
    ("duckduckbot", "DuckDuckBot", "search", []),
    ("baiduspider", "Baiduspider", "search", [".baidu.com", ".baidu.jp"]),
    ("seznambot", "SeznamBot", "search", [".seznam.cz"]),
    ("petalbot", "PetalBot", "search", [".petalsearch.com", ".aspiegel.com"]),
    ("mojeekbot", "MojeekBot", "search", []),
    ("qwantbot", "Qwantbot", "search", []),
    # --- Other AI --------------------------------------------------------
    ("ccbot", "CCBot", "ai_training", []),
    ("bytespider", "Bytespider", "ai_training", []),
    ("youbot", "YouBot", "ai_search", []),
    ("cohere-ai", "cohere-ai", "ai_training", []),
    ("diffbot", "Diffbot", "ai_training", []),
    ("imagesiftbot", "ImagesiftBot", "ai_training", []),
    ("timpibot", "Timpibot", "ai_training", []),
    ("omgilibot", "Omgilibot", "ai_training", []),
    ("mistralai-user", "MistralAI-User", "ai_user", []),
    # --- SEO tooling (takes, never gives) --------------------------------
    ("ahrefsbot", "AhrefsBot", "seo_tool", [".ahrefs.com", ".ahrefs.net"]),
    ("semrushbot", "SemrushBot", "seo_tool", [".semrush.com"]),
    ("dataforseobot", "DataForSeoBot", "seo_tool", [".dataforseo.com"]),
    ("mj12bot", "MJ12bot", "seo_tool", []),
    ("dotbot", "DotBot", "seo_tool", [".opensiteexplorer.org", ".moz.com"]),
    ("rogerbot", "rogerbot", "seo_tool", [".moz.com"]),
    ("blexbot", "BLEXBot", "seo_tool", [".webmeup.com"]),
    ("barkrowler", "Barkrowler", "seo_tool", [".babbar.tech"]),
    ("serpstatbot", "serpstatbot", "seo_tool", []),
    ("screaming frog", "ScreamingFrog", "seo_tool", []),
    ("sitebulb", "Sitebulb", "seo_tool", []),
    ("zoominfobot", "ZoominfoBot", "seo_tool", []),
    ("censysinspect", "CensysInspect", "seo_tool", []),
    ("internetmeasurement", "InternetMeasurement", "seo_tool", []),
]

CATEGORY_ORDER = ["search", "ai_search", "ai_user", "ai_training", "social", "seo_tool"]

# Paths that are never an indexable page. Crawl spent here is not necessarily
# waste (an image crawl can be entirely legitimate) but it is not page-crawl
# budget, and separating the two is the whole point of the report.
ASSET_RE = re.compile(
    r"\.(?:js|mjs|css|map|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|eot|mp4|webm|"
    r"mp3|ogg|wav|wasm|zip|gz|br|bsp|wad|spr|mdl|pak|data|bin)(?:$|\?)",
    re.I,
)


# Which COMPANY operates each crawler. Used only by detect_ua_spoofing() below.
# Substring-matched against the bot key, longest first, so "googlebot-image" and
# "google-extended" both land on Google.
OPERATORS = [
    ("oai-searchbot", "OpenAI"), ("chatgpt-user", "OpenAI"), ("gptbot", "OpenAI"),
    ("claude-searchbot", "Anthropic"), ("claude-user", "Anthropic"),
    ("claudebot", "Anthropic"), ("anthropic-ai", "Anthropic"),
    ("perplexity-user", "Perplexity"), ("perplexitybot", "Perplexity"),
    ("applebot-extended", "Apple"), ("applebot", "Apple"),
    ("google-extended", "Google"), ("googlebot", "Google"), ("googleother", "Google"),
    ("adsbot-google", "Google"), ("apis-google", "Google"), ("feedfetcher-google", "Google"),
    ("storebot-google", "Google"), ("google-inspectiontool", "Google"),
    ("bingbot", "Microsoft"), ("bingpreview", "Microsoft"), ("msnbot", "Microsoft"),
    ("meta-externalagent", "Meta"), ("facebookexternalhit", "Meta"),
    ("amazonbot", "Amazon"), ("bytespider", "ByteDance"), ("ccbot", "CommonCrawl"),
    ("cohere-ai", "Cohere"), ("mistralai-user", "MistralAI"), ("diffbot", "Diffbot"),
    ("yandexbot", "Yandex"), ("baiduspider", "Baidu"),
    ("duckduckbot", "DuckDuckGo"), ("duckassistbot", "DuckDuckGo"),
    ("ahrefsbot", "Ahrefs"), ("semrushbot", "Semrush"), ("mj12bot", "Majestic"),
    ("dotbot", "Moz"), ("dataforseobot", "DataForSEO"), ("youbot", "You.com"),
]


# ⚠ `controls` IS IMPORTED LAZILY, INSIDE run_control() - NOT HERE.
# This module SHIPS ITS OWN SOURCE to a remote host over stdin for `--remote`
# runs, and that remote python has no sibling files. A module-level
# `from controls import ...` dies there with ModuleNotFoundError, and the
# failure surfaces as "remote scan failed" - which reads as an SSH, glob or log
# problem and sends you to the wrong system. Broke exactly that way 2026-09-01.


def operator_of(bot_key: str) -> str | None:
    for frag, owner in OPERATORS:
        if frag in bot_key:
            return owner
    return None


def detect_ua_spoofing(bots: list[dict], min_operators: int = 2) -> dict:
    """Find IPs presenting as crawlers from MORE THAN ONE company.

    A single address cannot legitimately be Anthropic's crawler and OpenAI's and
    Perplexity's. When it claims to be all three, the user-agent is forged, and
    every per-bot number that address contributed to is fiction.

    Why this is worth detecting automatically: it inflates exactly the metric
    people most want to read right now. Measured on a live site 2026-08-01, three
    addresses presented as 10-13 different operators each and accounted for 100%
    of the traffic attributed to Claude-SearchBot, anthropic-ai, cohere-ai,
    Google-Extended, Perplexity-User, MistralAI-User, Diffbot and
    Applebot-Extended - so the raw log said "assistants are reading us" when the
    real answer was "nothing is". They were vulnerability scanners: ~100% 404
    rates against /.env, /.ssh/*, /secrets.json.

    Deliberately NOT a DNS check - it needs no network, and it catches operators
    like OpenAI and Anthropic that publish IP RANGES rather than rDNS, where
    `verify` can return no verdict at all. Use both: this says the UA is forged,
    `verify` says whether a claimed IP is really the operator's.

    The CONTROL is built in: legitimate multi-bot operators (Googlebot +
    GoogleOther + Googlebot-Image from one Google address) collapse to ONE
    operator and are never flagged. If this returns nothing on a log that
    definitely has Googlebot in it, the detector is working, not idle.
    """
    per_ip: dict[str, dict] = defaultdict(lambda: {"operators": {}, "hits": 0, "uas": set()})
    for b in bots:
        owner = operator_of(b["key"])
        if not owner:
            continue                       # unknown/generic UA proves nothing
        for ip, hits in (b.get("_all_ips") or {}).items():
            e = per_ip[ip]
            e["operators"].setdefault(owner, 0)
            e["operators"][owner] += hits
            e["hits"] += hits
            e["uas"].add(b["bot"])

    flagged = []
    for ip, e in per_ip.items():
        if len(e["operators"]) >= min_operators:
            flagged.append({
                "ip": ip,
                "operators_claimed": sorted(e["operators"]),
                "operator_count": len(e["operators"]),
                "hits": e["hits"],
                "user_agents": sorted(e["uas"]),
            })
    flagged.sort(key=lambda x: (-x["operator_count"], -x["hits"]))
    return {
        "flagged_ips": flagged[:25],
        "flagged_ip_count": len(flagged),
        "spoofed_hits": sum(f["hits"] for f in flagged),
        "reading": (
            "Each listed IP presented as crawlers belonging to two or more DIFFERENT "
            "companies, which no genuine crawler does. Treat every hit from these "
            "addresses as forged and subtract it before reading any per-bot or "
            "per-category total - especially the ai_search / ai_user ones, which are "
            "small enough that a scanner can dominate them."
            if flagged else
            "No IP claimed more than one operator's crawler. Note this is a CEILING on "
            "honesty, not a clean bill of health: a scanner that forges only ONE "
            "identity is invisible here - pair it with `verify`."
        ),
    }


def classify_ua(ua: str):
    """Return (bot_key, bot_label, category) for a user-agent string.

    Ordered longest-prefix-first in BOTS so `Googlebot-Image` is not swallowed
    by `googlebot`, and `OAI-SearchBot` is not swallowed by a bare `bot`.
    """
    low = ua.lower()
    for key, label, cat, _verify in BOTS:
        if key in low:
            return key, label, cat
    if "bot" in low or "crawler" in low or "spider" in low or "http" in low:
        return "other-bot", "other-bot", "unknown"
    return None, None, None


def run_control() -> dict:
    """Prove the log reader, the UA taxonomy and the spoof detector discriminate.

    A log parser that returns None for every line reports ZERO crawl activity,
    which reads as "the bots have stopped coming" - the single most alarming and
    most wrong thing this instrument can say. Measured 2026-09-01: a grep for
    `GET /path` returned 0 for every path on this exact log format, because the
    logs are JSON."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from controls import Controls          # noqa: PLC0415 - see the
    # note at the top: a module-level import breaks the --remote path.
    c = Controls("crawllog-control")

    line = json.dumps({
        "ts": 1756684800.0, "status": 200, "size": 5120, "duration": 0.03,
        "request": {"method": "GET", "uri": "/maps/de_dust2", "host": "x.test",
                    "client_ip": "127.0.0.1:44322", "remote_ip": "127.0.0.1",
                    "headers": {"User-Agent": ["Mozilla/5.0 (compatible; Googlebot/2.1)"],
                                "Cf-Connecting-Ip": ["203.0.113.9"],
                                "X-Forwarded-For": ["198.51.100.7, 203.0.113.9"],
                                "Referer": ["https://example.org/a"]}}})
    r = parse_caddy(line)
    c.check("json_log_line_parses", r is not None,
            "a parser that returns None for every line reports zero crawl activity")
    if r:
        c.check("uri_is_read", r["uri"] == "/maps/de_dust2", str(r.get("uri")))
        c.check("real_client_ip_beats_the_proxy_hop", r["ip"] == "203.0.113.9",
                f"got {r['ip']} - 127.0.0.1 means every per-IP figure is one bucket")
        c.check("status_is_an_int", r["status"] == 200)
        c.check("referer_is_read", r["referer"] == "https://example.org/a")
        c.check("timestamp_is_parsed", r["ts"] is not None)
    c.check("a_non_json_line_is_skipped_not_crashed",
            parse_caddy("203.0.113.9 - - [01/Sep/2026:00:00:00 +0000] \"GET / HTTP/1.1\" 200 12") is None)

    # THE UA REGISTRY ORDERING. Both of these have shipped as bugs elsewhere.
    c.check("googlebot_image_is_not_swallowed_by_googlebot",
            classify_ua("Googlebot-Image/1.0")[0] == "googlebot-image",
            str(classify_ua("Googlebot-Image/1.0")))
    c.check("chatgpt_user_is_not_matched_by_gptbot",
            classify_ua("Mozilla/5.0 ChatGPT-User/1.0")[0] not in (None, "gptbot"),
            str(classify_ua("Mozilla/5.0 ChatGPT-User/1.0")))
    c.check("a_plain_browser_is_not_a_bot", classify_ua(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120 Safari/537.36")[0] is None,
        str(classify_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537.36")))
    c.check("an_unknown_crawler_is_still_recognised_as_one",
            classify_ua("SomeNewCrawler/1.0 (+http://x)")[0] == "other-bot")

    # SPOOF DETECTION, with its own control: one operator's several crawlers
    # from one address are LEGITIMATE and must never be flagged.
    legit = [{"key": "googlebot", "bot": "Googlebot", "_all_ips": {"66.249.66.1": 50}},
             {"key": "googlebot-image", "bot": "Googlebot-Image", "_all_ips": {"66.249.66.1": 20}},
             {"key": "google-extended", "bot": "Google-Extended", "_all_ips": {"66.249.66.1": 5}}]
    forged = legit + [{"key": "gptbot", "bot": "GPTBot", "_all_ips": {"66.249.66.1": 9}},
                      {"key": "claudebot", "bot": "ClaudeBot", "_all_ips": {"66.249.66.1": 7}}]
    c.check("one_operators_many_crawlers_are_not_flagged",
            detect_ua_spoofing(legit)["flagged_ip_count"] == 0,
            str(detect_ua_spoofing(legit)["flagged_ips"]))
    c.check("an_ip_claiming_three_companies_is_flagged",
            detect_ua_spoofing(forged)["flagged_ip_count"] == 1,
            str(detect_ua_spoofing(forged)["flagged_ips"]))
    c.check("spoofed_hits_are_counted_for_subtraction",
            detect_ua_spoofing(forged)["spoofed_hits"] == 91,
            str(detect_ua_spoofing(forged)["spoofed_hits"]))
    c.check("operator_lookup_resolves", operator_of("googlebot") == operator_of("googlebot-image")
            and operator_of("googlebot") is not None)
    return c.verdict(bots_in_registry=len(BOTS))


def bot_verify_suffixes(key: str):
    for k, _label, _cat, suf in BOTS:
        if k == key:
            return suf
    return []


# --------------------------------------------------------------------------
# Log parsing
# --------------------------------------------------------------------------

COMBINED_RE = re.compile(
    r'^(?P<host>\S+)\s+\S+\s+(?P<user>\S+)\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<uri>\S*)\s*(?P<proto>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+(?P<size>\S+)'
    r'(?:\s+"(?P<referer>[^"]*)"\s+"(?P<ua>[^"]*)")?'
)

# A Caddy placeholder that was never expanded. The front proxy shipped
# `X-Forwarded-For: {http.request.client_ip}` literally for months (it is
# recorded in this repo's CLAUDE.md), so the header is present, looks like a
# header, and is not an IP. Treating it as one silently buckets every request
# under a single fake client.
PLACEHOLDER_RE = re.compile(r"^\{.*\}$")


def _open(path: str):
    if path == "-":
        return sys.stdin
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _first_header(headers, *names):
    if not isinstance(headers, dict):
        return None
    lowered = {k.lower(): v for k, v in headers.items()}
    for n in names:
        v = lowered.get(n.lower())
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, str) and v and not PLACEHOLDER_RE.match(v):
            return v
    return None


def parse_caddy(line: str):
    try:
        d = json.loads(line)
    except Exception:
        return None
    req = d.get("request")
    if not isinstance(req, dict):
        return None
    hdrs = req.get("headers") or {}
    ua = _first_header(hdrs, "User-Agent") or ""
    # Real client IP, best source first. Cf-Connecting-Ip is authoritative when
    # Cloudflare fronts the origin; client_ip/remote_ip are the CDN edge or
    # 127.0.0.1 behind a local proxy hop.
    ip = (
        _first_header(hdrs, "Cf-Connecting-Ip", "True-Client-IP")
        or _xff_first(_first_header(hdrs, "X-Forwarded-For"))
        or _clean_ip(req.get("client_ip"))
        or _clean_ip(req.get("remote_ip"))
        or ""
    )
    ts = d.get("ts")
    when = None
    if isinstance(ts, (int, float)):
        when = datetime.fromtimestamp(ts, timezone.utc)
    elif isinstance(ts, str):
        when = _parse_iso(ts)
    return {
        "ts": when,
        "ip": ip,
        "method": req.get("method") or "",
        "uri": req.get("uri") or "",
        "host": req.get("host") or "",
        "status": int(d.get("status") or 0),
        "size": int(d.get("size") or 0),
        "ua": ua,
        "referer": _first_header(hdrs, "Referer", "Referrer") or "",
        "duration": d.get("duration"),
    }


def _xff_first(v):
    if not v:
        return None
    return _clean_ip(v.split(",")[0].strip())


def _clean_ip(v):
    if not isinstance(v, str) or not v or PLACEHOLDER_RE.match(v):
        return None
    return v.strip()


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


CLF_TIME = "%d/%b/%Y:%H:%M:%S %z"


def parse_clf(line: str):
    m = COMBINED_RE.match(line)
    if not m:
        return None
    g = m.groupdict()
    when = None
    try:
        when = datetime.strptime(g["time"], CLF_TIME)
    except Exception:
        pass
    uri = g["uri"] or ""
    return {
        "ts": when,
        "ip": _clean_ip(g["host"]) or "",
        "method": g["method"] or "",
        "uri": uri,
        "host": "",
        "status": int(g["status"] or 0),
        "size": int(g["size"]) if (g["size"] or "-").isdigit() else 0,
        "ua": g.get("ua") or "",
        "referer": (g.get("referer") or "").strip('"') if g.get("referer") not in (None, "-") else "",
        "duration": None,
    }


def make_parser(fmt: str, custom: str | None, fields: list[str] | None):
    if fmt == "caddy" or fmt == "json":
        return parse_caddy
    if fmt in ("combined", "common"):
        return parse_clf
    if fmt == "regex":
        rx = re.compile(custom)
        names = fields or []

        def _p(line):
            m = rx.match(line)
            if not m:
                return None
            g = m.groupdict() or dict(zip(names, m.groups()))
            when = None
            for k in ("time", "ts", "datetime"):
                if g.get(k):
                    when = _parse_iso(g[k])
                    if when is None:
                        try:
                            when = datetime.strptime(g[k], CLF_TIME)
                        except Exception:
                            when = None
                    break
            return {
                "ts": when,
                "ip": _clean_ip(g.get("ip") or g.get("host")) or "",
                "method": g.get("method") or "",
                "uri": g.get("uri") or g.get("path") or "",
                "host": g.get("vhost") or "",
                "status": int(g.get("status") or 0),
                "size": int(g["size"]) if (g.get("size") or "-").isdigit() else 0,
                "ua": g.get("ua") or g.get("agent") or "",
                "referer": g.get("referer") or g.get("referrer") or "",
                "duration": None,
            }

        return _p
    # auto
    def _auto(line):
        s = line.lstrip()
        if s.startswith("{"):
            return parse_caddy(line)
        return parse_clf(line)

    return _auto


def silo_of(uri: str, depth: int) -> str:
    path = uri.split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if ASSET_RE.search(path):
        # Bucket assets by their FIRST segment regardless of depth - the useful
        # question is "how much went to /mi/", not which image.
        return "/" + parts[0] + "/ (assets)"
    take = parts[:depth]
    out = "/" + "/".join(take)
    if len(parts) > depth:
        out += "/*"
    return out


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def expand_inputs(a) -> list[str]:
    """Resolve --file/--glob to real paths.

    The stdin fallback is deliberately CONDITIONAL on stdin actually being a
    pipe. It used to be unconditional (`if not files: files = ["-"]`), and that
    made a run with no --glob indistinguishable from a run that scanned real
    logs and found nothing: on a --remote scan stdin is empty, so the scan read
    zero lines and reported `human_requests: 0` / `referring_domains: 0` as if
    that were a measurement. A reader sees "this site has no backlinks".

    Measured 2026-08-01 on a live site: with --glob it read 1,060,154 human
    requests and 27 referring domains; without it, 0 and 0, same exit code.

    Returning [] here lets the callers refuse a verdict, which is the same rule
    `verify` and `footprint` already follow - "cannot ask" and "the answer is
    no" must never share a code path.
    """
    files: list[str] = []
    for f in a.file or []:
        files.append(f)
    for pat in a.glob or []:
        files.extend(sorted(globmod.glob(pat)))
    if files:
        return files
    # Stdin is a fallback for `cat access.log | crawllog.py scan`, so it applies
    # ONLY when no input was requested at all. A --glob that matched nothing is an
    # ERROR, not an invitation to read stdin: honouring it there is how an explicit
    # request for /var/log/caddy/*.log silently became "read the empty pipe and
    # report zero", which is the exact false negative this guard exists to stop.
    asked_for_files = bool(a.file or a.glob)
    if not asked_for_files and not sys.stdin.isatty():
        return ["-"]
    return []


def no_input_error(a) -> dict:
    """The refusal payload shared by every log-reading subcommand."""
    return {
        "ok": False,
        "error": "no input read - nothing to measure, so no verdict is available",
        "detail": (
            "No --file matched and no --glob matched any path"
            if (a.file or a.glob) else
            "Neither --file nor --glob was given, and stdin is not a pipe"
        ),
        "file": list(a.file or []),
        "glob": list(a.glob or []),
        "remote": getattr(a, "remote", None),
        "fix": "pass --glob '/var/log/caddy/access*.log*' (QUOTE it, so the shell does not "
               "expand it locally before it reaches the remote host), or --file <path>. "
               "Confirm the path exists on the host you are scanning.",
        "why_not_zero": "Reporting 0 here would be indistinguishable from a real scan that "
                        "found no bots and no referrers, which is a conclusion about the "
                        "SITE rather than about the input.",
    }


def cmd_scan(a):
    if a.remote:
        return run_remote(a)

    parser = make_parser(a.format, a.regex, a.fields)
    depth = a.silo_depth
    cutoff = None
    if a.days:
        cutoff = datetime.now(timezone.utc).timestamp() - a.days * 86400

    bots: dict[str, dict] = {}
    unparsed = 0
    total_lines = 0
    total_bot_hits = 0
    human_hits = 0
    files = expand_inputs(a)
    if not files:
        print(json.dumps(no_input_error(a), indent=2))
        sys.exit(2)
    seen_files = []

    only = set(x.lower() for x in (a.bot or []))

    for path in files:
        try:
            fh = _open(path)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"cannot open {path}: {exc}"}))
            sys.exit(2)
        seen_files.append(path)
        with fh if path != "-" else _NullCtx(fh):
            for line in fh:
                if not line.strip():
                    continue
                total_lines += 1
                rec = parser(line)
                if rec is None:
                    unparsed += 1
                    continue
                if cutoff and rec["ts"] and rec["ts"].timestamp() < cutoff:
                    continue
                key, label, cat = classify_ua(rec["ua"])
                if key is None:
                    human_hits += 1
                    continue
                if only and key not in only and (label or "").lower() not in only:
                    continue
                total_bot_hits += 1
                b = bots.get(key)
                if b is None:
                    b = bots[key] = {
                        "bot": label,
                        "category": cat,
                        "hits": 0,
                        "bytes": 0,
                        "urls": set(),
                        "ips": Counter(),
                        "status": Counter(),
                        "silos": Counter(),
                        "days": Counter(),
                        "assets": 0,
                        "errors": Counter(),   # uri -> count, 4xx/5xx only
                        "first": None,
                        "last": None,
                        "uas": Counter(),
                    }
                b["hits"] += 1
                b["bytes"] += rec["size"]
                path_only = rec["uri"].split("?", 1)[0]
                if len(b["urls"]) < a.max_urls:
                    b["urls"].add(path_only)
                if rec["ip"]:
                    b["ips"][rec["ip"]] += 1
                b["status"][str(rec["status"])] += 1
                b["silos"][silo_of(rec["uri"], depth)] += 1
                b["uas"][rec["ua"][:120]] += 1
                if ASSET_RE.search(path_only):
                    b["assets"] += 1
                if rec["status"] >= 400:
                    b["errors"][f'{rec["status"]} {path_only}'] += 1
                if rec["ts"]:
                    d = rec["ts"].strftime("%Y-%m-%d")
                    b["days"][d] += 1
                    t = rec["ts"].timestamp()
                    if b["first"] is None or t < b["first"]:
                        b["first"] = t
                    if b["last"] is None or t > b["last"]:
                        b["last"] = t

    out_bots = []
    for key, b in sorted(bots.items(), key=lambda kv: -kv[1]["hits"]):
        days = len(b["days"]) or 1
        errs = sum(v for k, v in b["status"].items() if k.isdigit() and int(k) >= 400)
        out_bots.append({
            "key": key,
            "bot": b["bot"],
            "category": b["category"],
            "hits": b["hits"],
            "hits_per_day": round(b["hits"] / days, 1),
            "days_seen": days,
            "unique_urls": len(b["urls"]),
            "unique_urls_capped": len(b["urls"]) >= a.max_urls,
            "mb": round(b["bytes"] / 1048576, 1),
            "asset_share": round(b["assets"] / b["hits"], 3) if b["hits"] else 0,
            "error_rate": round(errs / b["hits"], 4) if b["hits"] else 0,
            "status": dict(b["status"].most_common()),
            "top_silos": dict(b["silos"].most_common(a.top)),
            "top_errors": dict(b["errors"].most_common(5)),
            "distinct_ips": len(b["ips"]),
            "top_ips": dict(b["ips"].most_common(5)),
            # Full counter for detect_ua_spoofing(), which must see EVERY address:
            # a scanner rotating many IPs would hide under a top-5 truncation.
            # Stripped from the payload before printing.
            "_all_ips": dict(b["ips"]),
            "user_agents": dict(b["uas"].most_common(3)),
            "first_seen": _iso(b["first"]),
            "last_seen": _iso(b["last"]),
            "daily": dict(sorted(b["days"].items())),
        })

    by_cat = defaultdict(lambda: {"hits": 0, "bots": []})
    for b in out_bots:
        c = by_cat[b["category"]]
        c["hits"] += b["hits"]
        c["bots"].append(b["bot"])

    cats = {}
    for c in CATEGORY_ORDER + ["unknown"]:
        if c in by_cat:
            cats[c] = {
                "hits": by_cat[c]["hits"],
                "share_of_bot_traffic": round(by_cat[c]["hits"] / total_bot_hits, 3) if total_bot_hits else 0,
                "bots": by_cat[c]["bots"],
            }

    spoofing = detect_ua_spoofing(out_bots)
    for b in out_bots:
        b.pop("_all_ips", None)     # internal only - never part of the payload

    print(json.dumps({
        "ok": True,
        "files": seen_files,
        "lines_read": total_lines,
        "unparsed_lines": unparsed,
        "ua_spoofing": spoofing,
        "unparsed_share": round(unparsed / total_lines, 4) if total_lines else 0,
        "window_days": a.days,
        "silo_depth": depth,
        "human_hits": human_hits,
        "bot_hits": total_bot_hits,
        "bot_share": round(total_bot_hits / (total_bot_hits + human_hits), 3) if (total_bot_hits + human_hits) else 0,
        "by_category": cats,
        "bots": out_bots,
        "reading": {
            "ai_training": "reads you, never cites you, sends no traffic",
            "ai_search": "feeds an assistant that DOES cite - being read here is the precondition for being cited",
            "ai_user": "a live fetch because a person asked. The strongest answer-engine signal there is.",
            "seo_tool": "extracts your content as competitor data. Indexes nothing, sends nothing.",
            "unverified": "every count here is UA-claimed. Run `crawllog.py verify` before trusting a search-bot number.",
        },
    }, indent=2, ensure_ascii=False))


class _NullCtx:
    def __init__(self, fh):
        self.fh = fh

    def __enter__(self):
        return self.fh

    def __exit__(self, *a):
        return False


def _iso(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# remote execution - ship this file over ssh, aggregate there
# --------------------------------------------------------------------------

def ssh_cmd(a, args: list[str]) -> list[str]:
    """Build the ssh argv.

    ssh does NOT pass argv through untouched: it joins everything after the
    host into one string and hands it to the REMOTE shell, which re-parses it.
    So an unquoted `--glob '/var/log/*.gz'` is glob-expanded on the far side
    and arrives as twenty positional arguments argparse has never heard of.
    Quote every element exactly once, here, and nowhere else.
    """
    import shlex
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if a.ssh_key:
        cmd += ["-i", os.path.expanduser(a.ssh_key)]
    remote = "python3 - " + " ".join(shlex.quote(x) for x in args)
    return cmd + [a.remote, remote]


def run_remote(a):
    src = Path(__file__).read_text()
    args = ["scan"]
    for g in a.glob or []:
        args += ["--glob", g]
    for f in a.file or []:
        args += ["--file", f]
    if a.days:
        args += ["--days", str(a.days)]
    for b in a.bot or []:
        args += ["--bot", b]
    args += ["--silo-depth", str(a.silo_depth), "--top", str(a.top),
             "--max-urls", str(a.max_urls), "--format", a.format]
    if a.regex:
        args += ["--regex", a.regex]

    cmd = ssh_cmd(a, args)
    try:
        p = subprocess.run(cmd, input=src, capture_output=True, text=True, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "error": f"remote scan timed out after {a.timeout}s",
                          "hint": "narrow with --days, or --glob a single file"}))
        sys.exit(2)
    if p.returncode != 0:
        print(json.dumps({"ok": False, "error": "remote scan failed",
                          "returncode": p.returncode, "stderr": p.stderr[-2000:]}))
        sys.exit(2)
    sys.stdout.write(p.stdout)


# --------------------------------------------------------------------------
# verify - Google's own documented check
# --------------------------------------------------------------------------

DOH_ENDPOINTS = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
]


def _doh(name: str, rrtype: str):
    """One DNS question over HTTPS. Returns a list of answer strings, or None.

    None means "could not ask" (transport failure). An empty list means "asked,
    and the record does not exist". Collapsing those two is what turns a broken
    resolver into a false spoofing accusation - see `resolver_control`.
    """
    q = urllib.parse.urlencode({"name": name, "type": rrtype})
    last = None
    for base in DOH_ENDPOINTS:
        try:
            req = urllib.request.Request(
                f"{base}?{q}",
                headers={"accept": "application/dns-json", "User-Agent": UA},
            )
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            if d.get("Status") not in (0, 3):      # 0 NOERROR, 3 NXDOMAIN
                last = f"DNS status {d.get('Status')}"
                continue
            return [a.get("data", "").rstrip(".") for a in (d.get("Answer") or [])
                    if a.get("type") in (1, 12, 28)]
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            continue
    return None if last else []


def _ptr_name(ip: str) -> str:
    if ":" in ip:
        try:
            packed = socket.inet_pton(socket.AF_INET6, ip)
        except OSError:
            return ip
        nibbles = "".join(f"{b:02x}" for b in packed)
        return ".".join(reversed(nibbles)) + ".ip6.arpa"
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def resolver_control():
    """Prove the resolver can answer a PTR we KNOW exists, before trusting any 'no'.

    This container's Docker DNS silently refuses every reverse lookup - and the
    first run of this tool duly reported nine Googlebot IPs as spoofed, because
    `socket.gethostbyaddr` raised the same exception for "does not exist" as for
    "cannot ask". A negative result is only as good as its control, so the
    control is mandatory and its failure downgrades every verdict to `unknown`
    rather than `spoofed`.
    """
    ans = _doh(_ptr_name("8.8.8.8"), "PTR")
    ok = bool(ans) and any("dns.google" in a for a in ans)
    return {"ok": ok, "probe": "8.8.8.8 -> dns.google", "answer": ans}


def verify_ip(ip: str, expect_suffixes: list[str]):
    """Reverse DNS, then FORWARD-confirm back to the same IP.

    Reverse DNS alone proves nothing: the PTR record for an IP is controlled by
    whoever holds the address, so anyone can point theirs at
    `crawl-66-249-1-1.googlebot.com`. The forward lookup is what closes it -
    only Google can make `*.googlebot.com` resolve back to their own address.

    Resolution goes over DoH, not the system resolver: containers routinely ship
    a resolver that answers A records fine and drops PTR entirely.
    """
    out = {"ip": ip, "ptr": None, "forward": [], "verified": None, "reason": None}
    ptr = _doh(_ptr_name(ip), "PTR")
    if ptr is None:
        out["reason"] = "resolver could not be reached for the PTR query - verdict UNKNOWN, not spoofed"
        return out
    if not ptr:
        out["verified"] = False
        out["reason"] = "no PTR record exists for this IP"
        return out
    out["ptr"] = ptr[0]
    if expect_suffixes and not any(out["ptr"].lower().endswith(s) for s in expect_suffixes):
        out["verified"] = False
        out["reason"] = f"PTR {out['ptr']} is not on {', '.join(expect_suffixes)} - SPOOFED"
        return out
    fwd = _doh(out["ptr"], "AAAA" if ":" in ip else "A")
    if fwd is None:
        out["reason"] = "resolver could not be reached for the forward query - verdict UNKNOWN"
        return out
    out["forward"] = fwd
    if ip in fwd:
        out["verified"] = True
        out["reason"] = "PTR on the operator's domain and forward-confirms to the same IP"
    elif not fwd:
        # One-way rDNS. Measured on AhrefsBot: the PTR is `*.ahrefs.net` but that
        # hostname has no A record at all, so the loop cannot be closed. That is
        # UNPROVEN, not fake - and calling it SPOOFED would have been a false
        # accusation against a correctly-behaving crawler.
        out["verified"] = None
        out["reason"] = (f"PTR {out['ptr']} is on the operator's domain but has no forward record, "
                         f"so the loop cannot be closed - UNPROVEN, not spoofed")
    else:
        out["verified"] = False
        out["reason"] = f"forward lookup of {out['ptr']} returned {fwd}, not {ip} - SPOOFED"
    return out


def cmd_verify(a):
    ips: list[tuple[str, str, int]] = []   # (ip, bot_key, hits)
    if a.scan:
        data = json.loads(Path(a.scan).read_text()) if a.scan != "-" else json.load(sys.stdin)
        for b in data.get("bots", []):
            if a.bot and b["key"] not in a.bot and b["bot"].lower() not in [x.lower() for x in a.bot]:
                continue
            for ip, hits in list(b.get("top_ips", {}).items())[: a.per_bot]:
                ips.append((ip, b["key"], hits))
    for ip in a.ip or []:
        ips.append((ip, a.assume or "", 0))

    if not ips:
        print(json.dumps({"ok": False, "error": "nothing to verify",
                          "hint": "pass --ip, or --scan scan.json to verify the top IPs of each bot"}))
        sys.exit(2)

    def _work(item):
        ip, key, hits = item
        suf = bot_verify_suffixes(key) if key else []
        r = verify_ip(ip, suf)
        r["bot"] = key or None
        r["hits"] = hits
        r["verifiable_by_dns"] = bool(suf)
        if not suf:
            # This operator publishes IP ranges instead of rDNS. There is no
            # verdict to give, so do not manufacture one.
            r["verified"] = None
            r["reason"] = ("operator publishes IP ranges, not rDNS - no DNS verdict is possible; "
                           "absence of a PTR is NOT evidence of spoofing here")
        return r

    control = resolver_control()

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(_work, ips))

    if not control["ok"]:
        # The resolver is the instrument. A broken instrument reports UNKNOWN.
        for r in results:
            r["verified"] = None
            r["reason"] = "resolver control FAILED - no verdict is trustworthy in this environment"

    checkable = [r for r in results if r["verifiable_by_dns"]]
    verified = [r for r in checkable if r["verified"] is True]
    spoofed = [r for r in checkable if r["verified"] is False]
    unknown = [r for r in results if r["verified"] is None]
    print(json.dumps({
        "ok": control["ok"],
        "resolver_control": control,
        "checked": len(results),
        "dns_verifiable": len(checkable),
        "verified": len(verified),
        "spoofed": len(spoofed),
        "unknown": len(unknown),
        "spoofed_hits": sum(r["hits"] for r in spoofed),
        "results": results,
        "reading": "Only `dns_verifiable` bots can be proven; the rest publish IP ranges instead "
                   "and a missing PTR proves nothing about them. A spoofed hit inflates the crawl "
                   "numbers of whichever bot it impersonated - subtract `spoofed_hits` before "
                   "concluding anything about crawl budget. If `resolver_control.ok` is false, "
                   "EVERY verdict is `unknown` and none of them is a finding.",
    }, indent=2, ensure_ascii=False))
    if not control["ok"]:
        sys.exit(3)


# --------------------------------------------------------------------------
# gap - what the sitemap promises vs what was actually crawled
# --------------------------------------------------------------------------

def fetch_sitemap_urls(src: str, limit: int) -> list[str]:
    urls: list[str] = []
    todo = [src]
    seen = set()
    while todo and len(urls) < limit:
        cur = todo.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        try:
            if cur.startswith("http"):
                req = urllib.request.Request(cur, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                if cur.endswith(".gz") or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8", errors="replace")
            else:
                body = _open(cur).read()
        except Exception:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
        if "<sitemapindex" in body[:2000]:
            todo.extend(locs)
        else:
            urls.extend(locs)
    return urls[:limit]


def cmd_gap(a):
    data = json.loads(Path(a.scan).read_text()) if a.scan != "-" else json.load(sys.stdin)
    target = None
    for b in data.get("bots", []):
        if b["key"] == a.bot or b["bot"].lower() == a.bot.lower():
            target = b
            break
    if target is None:
        print(json.dumps({"ok": False, "error": f"bot {a.bot} not in the scan",
                          "available": [b["key"] for b in data.get("bots", [])]}))
        sys.exit(2)
    if target.get("unique_urls_capped"):
        print(json.dumps({"ok": False, "error": "the scan hit --max-urls, so its URL set is truncated",
                          "hint": "re-run scan with a higher --max-urls before a gap analysis"}))
        sys.exit(2)

    print(json.dumps({
        "ok": False,
        "error": "gap needs the crawled URL SET, which `scan` deliberately does not emit "
                 "(it would be megabytes of JSON).",
        "hint": "run `crawllog.py urls --bot <bot> --glob ... > crawled.txt` first, then "
                "`crawllog.py gap --crawled crawled.txt --sitemap <url>`",
    }, indent=2))
    sys.exit(2)


def cmd_urls(a):
    """Emit the distinct URL set one bot touched. Feeds `gap`."""
    if a.remote:
        src = Path(__file__).read_text()
        args = ["urls", "--bot", a.bot]
        for g in a.glob or []:
            args += ["--glob", g]
        for f in a.file or []:
            args += ["--file", f]
        if a.days:
            args += ["--days", str(a.days)]
        if a.status:
            args += ["--status", a.status]
        args += ["--format", a.format]
        p = subprocess.run(ssh_cmd(a, args), input=src, capture_output=True, text=True, timeout=a.timeout)
        if p.returncode != 0:
            print(json.dumps({"ok": False, "error": "remote urls failed", "stderr": p.stderr[-2000:]}))
            sys.exit(2)
        sys.stdout.write(p.stdout)
        return

    parser = make_parser(a.format, a.regex, a.fields)
    cutoff = datetime.now(timezone.utc).timestamp() - a.days * 86400 if a.days else None
    want = a.bot.lower()
    status_filter = None
    if a.status:
        status_filter = set()
        for part in a.status.split(","):
            part = part.strip()
            if part.endswith("xx"):
                base = int(part[0]) * 100
                status_filter.update(range(base, base + 100))
            elif part:
                status_filter.add(int(part))
    counts: Counter = Counter()
    _inputs = expand_inputs(a)
    if not _inputs:
        print(json.dumps(no_input_error(a), indent=2))
        sys.exit(2)
    for path in _inputs:
        fh = _open(path)
        for line in fh:
            if not line.strip():
                continue
            rec = parser(line)
            if rec is None:
                continue
            if cutoff and rec["ts"] and rec["ts"].timestamp() < cutoff:
                continue
            key, label, _cat = classify_ua(rec["ua"])
            if key is None:
                continue
            if key != want and (label or "").lower() != want:
                continue
            if status_filter and rec["status"] not in status_filter:
                continue
            counts[rec["uri"].split("?", 1)[0]] += 1
    for uri, n in counts.most_common():
        print(f"{n}\t{uri}")


def cmd_gap2(a):
    crawled = {}
    with open(a.crawled, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if "\t" in line:
                n, u = line.split("\t", 1)
                crawled[u] = int(n)
            else:
                crawled[line] = 1
    sm = fetch_sitemap_urls(a.sitemap, a.limit)
    if not sm:
        print(json.dumps({"ok": False, "error": f"no <loc> entries found at {a.sitemap}"}))
        sys.exit(2)

    def pathof(u):
        try:
            from urllib.parse import urlsplit
            return urlsplit(u).path or "/"
        except Exception:
            return u

    sm_paths = {pathof(u): u for u in sm}
    never = [u for p, u in sm_paths.items() if p not in crawled]
    crawled_pages = {p: n for p, n in crawled.items() if not ASSET_RE.search(p)}
    not_in_sitemap = sorted(
        ((n, p) for p, n in crawled_pages.items() if p not in sm_paths),
        reverse=True,
    )[: a.top]
    hit = [(crawled[p], u) for p, u in sm_paths.items() if p in crawled]
    hit.sort(reverse=True)

    print(json.dumps({
        "ok": True,
        "sitemap": a.sitemap,
        "sitemap_urls": len(sm_paths),
        "crawled_paths": len(crawled),
        "crawled_pages": len(crawled_pages),
        "in_sitemap_and_crawled": len(hit),
        "coverage": round(len(hit) / len(sm_paths), 4) if sm_paths else 0,
        "never_crawled_count": len(never),
        "never_crawled_sample": never[: a.top],
        "crawled_but_not_in_sitemap": [{"hits": n, "path": p} for n, p in not_in_sitemap],
        "most_crawled_in_sitemap": [{"hits": n, "url": u} for n, u in hit[: a.top]],
        "reading": "never_crawled = the sitemap promises a page the bot has not fetched in this window; "
                   "at scale that is a crawl-budget problem, not an indexing one. "
                   "crawled_but_not_in_sitemap = budget spent on URLs you never advertised - "
                   "check they are meant to be indexable at all.",
    }, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------

def add_input_args(s):
    s.add_argument("-f", "--file", action="append", help="log file (.gz ok, - for stdin). Repeatable.")
    s.add_argument("--glob", action="append", help="glob of log files. Repeatable. QUOTE IT.")
    s.add_argument("--format", default="auto",
                   choices=["auto", "caddy", "json", "combined", "common", "regex"])
    s.add_argument("--regex", help="custom line regex with named groups (--format regex)")
    s.add_argument("--fields", nargs="*", help="field names for an unnamed --regex")
    s.add_argument("--days", type=int, help="only lines from the last N days")
    s.add_argument("--remote", help="user@host - ships this file over ssh and aggregates THERE")
    s.add_argument("--ssh-key", help="identity file for --remote")
    s.add_argument("--timeout", type=int, default=900, help="remote timeout, seconds")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("control", help="prove the log reader and UA taxonomy discriminate").set_defaults(
        fn=lambda a: print(json.dumps(run_control(), indent=2, ensure_ascii=False)))

    s = sub.add_parser("scan", help="full bot report: budget by silo, statuses, AI ingestion")
    add_input_args(s)
    s.add_argument("--bot", action="append", help="restrict to these bots. Repeatable.")
    s.add_argument("--silo-depth", type=int, default=1, help="path segments per silo bucket")
    s.add_argument("--top", type=int, default=12, help="rows per top-N list")
    s.add_argument("--max-urls", type=int, default=200000, help="cap on the distinct-URL set held per bot")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("verify", help="reverse+forward DNS - is that really Googlebot?")
    s.add_argument("--ip", action="append", help="IP to check. Repeatable.")
    s.add_argument("--scan", help="a scan.json - verifies the top IPs of every bot in it")
    s.add_argument("--bot", action="append", help="restrict --scan to these bots")
    s.add_argument("--assume", help="bot key to check bare --ip values against")
    s.add_argument("--per-bot", type=int, default=3, help="IPs per bot from --scan")
    s.add_argument("--workers", type=int, default=8)
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("urls", help="distinct URL set one bot touched (feeds `gap`)")
    add_input_args(s)
    s.add_argument("--bot", required=True)
    s.add_argument("--status", help="only these statuses, e.g. '200' or '4xx,5xx'")
    s.set_defaults(fn=cmd_urls)

    s = sub.add_parser("gap", help="sitemap vs what was actually crawled")
    s.add_argument("--crawled", required=True, help="output of `crawllog.py urls`")
    s.add_argument("--sitemap", required=True, help="sitemap URL or file (index ok)")
    s.add_argument("--limit", type=int, default=200000)
    s.add_argument("--top", type=int, default=25)
    s.set_defaults(fn=cmd_gap2)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
