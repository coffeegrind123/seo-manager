#!/usr/bin/env python3
"""Keyword expansion + demand signals for the seo-manager skill.

The free research primitive is Google Autocomplete (`suggestqueries`): keyless,
no account, no quota worth worrying about, and it is what every "free keyword
tool" is actually built on. It returns REAL queries people type - which is a
better signal than a stale volume database for anything less than a year old -
but it returns no volume numbers.

So this script does three separable jobs:

  expand    autocomplete sweeps (modifiers, questions, comparisons, alphabet
            soup, tool-intent) -> a deduped candidate list with a demand PROXY
  volume    enrich candidates with real volume/KD, when a paid source is
            configured (DataForSEO). Never invents numbers.
  gsc       turn Search Console's own query data into candidates - the single
            best free seam, because those queries are proven relevant to THIS
            domain and half-ranked already.

The demand proxy is honest about what it is: autocomplete surfaces a query
because enough people type it, and Google orders the list roughly by
popularity, so `first seen at prefix depth D, rank R` is a real ordinal
signal. It is NOT a monthly search volume and this script never labels it one.

Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# The modifier sets, grouped by what they hunt for. The research workflow's
# rung-1 seams map onto these directly.
MODIFIERS = {
    "question": ["how to", "how do i", "what is", "what are", "why", "when", "which", "can i", "does"],
    "commercial": ["best", "top", "cheapest", "free", "alternative to", "review of"],
    "comparison": ["vs", "or", "alternatives", "compared to", "versus"],
    "audience": ["for beginners", "for developers", "for small business", "for teams", "for agencies", "for freelancers"],
    "constraint": ["without", "with", "on windows", "on mac", "on linux", "open source", "self hosted", "no code"],
    "problem": ["not working", "error", "fix", "troubleshoot", "slow", "failed", "issue"],
    "tool_intent": ["generator", "calculator", "checker", "converter", "analyzer", "template",
                    "builder", "validator", "estimator", "comparison tool"],
    "commercial_tail": ["pricing", "cost", "worth it", "reddit", "example", "tutorial", "guide"],
}
ALPHABET = list("abcdefghijklmnopqrstuvwxyz")

# Queries the tool-shaped sweep is looking for (build-guide vs build-tool split).
TOOL_VERBS = re.compile(
    r"\b(generat|calculat|convert|check|test|validat|estimat|build|creat|compar|pick|analyz|audit|format|encode|decode)\w*\b",
    re.I,
)
COMMERCIAL_HINT = re.compile(r"\b(best|top|vs|versus|alternative|alternatives|pricing|cost|cheap|free|review|compare)\b", re.I)
QUESTION_HINT = re.compile(r"^\s*(how|what|why|when|which|who|where|can|does|do|is|are|should)\b", re.I)
TRANSACTIONAL_HINT = re.compile(r"\b(buy|download|install|sign ?up|trial|demo|coupon|discount)\b", re.I)


def fetch_json(url: str, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, text/javascript, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    return json.loads(raw)


# ------------------------------------------------------------ autocomplete


# Six keyless suggestion corpora, all verified answering from a datacenter IP on
# 2026-08-01. They are not redundant: each is a DIFFERENT population typing into
# a DIFFERENT box, so a phrase surfaced by several of them is corroborated by
# independent evidence rather than by one algorithm's ordering.
#
#   google   the base corpus - broadest, what everything else is compared to
#   bing     an independent web-search index and audience (keyless osjson)
#   ddg      a third web-search corpus, no personalisation at all
#   youtube  VIDEO intent - a phrase strong here wants a demo, not an essay
#   yandex   a fourth engine; dominant in RU/TR, useful sanity check elsewhere
#   amazon   PRODUCT intent - a phrase strong here is someone about to buy
#
# The last two lines are the point: intent is usually GUESSED from the wording
# of a phrase. Which engines surface it is evidence instead of a guess.
SUGGEST_ENGINES = {
    "google": lambda q, hl, gl: (
        "https://suggestqueries.google.com/complete/search?"
        + urllib.parse.urlencode({"client": "firefox", "hl": hl, "gl": gl, "q": q})),
    "youtube": lambda q, hl, gl: (
        "https://suggestqueries.google.com/complete/search?"
        + urllib.parse.urlencode({"client": "firefox", "ds": "yt", "hl": hl, "q": q})),
    "bing": lambda q, hl, gl: (
        "https://api.bing.com/osjson.aspx?" + urllib.parse.urlencode({"query": q})),
    "ddg": lambda q, hl, gl: (
        "https://duckduckgo.com/ac/?" + urllib.parse.urlencode({"type": "list", "q": q})),
    "yandex": lambda q, hl, gl: (
        "https://suggest.yandex.com/suggest-ff.cgi?" + urllib.parse.urlencode({"part": q})),
    "amazon": lambda q, hl, gl: (
        "https://completion.amazon.com/api/2017/suggestions?"
        + urllib.parse.urlencode({"mid": "ATVPDKIKX0DER", "alias": "aps", "limit": "10", "prefix": q})),
}
INTENT_ENGINES = {"youtube": "video", "amazon": "product"}
# Legacy `--source` values, kept working.
_SOURCE_ALIAS = {"chrome": "google", "google": "google", "youtube": "youtube"}


def _parse_suggestions(engine: str, data) -> list[str]:
    """Every engine answers a different shape. Normalising them here keeps the
    difference in one place instead of spread through the sweep."""
    if engine == "amazon":
        if isinstance(data, dict):
            return [s.get("value", "") for s in data.get("suggestions", []) if isinstance(s, dict)]
        return []
    if isinstance(data, list) and len(data) == 2 and isinstance(data[1], list):
        return [s for s in data[1] if isinstance(s, str)]      # opensearch pair
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
        return [s for s in data[1] if isinstance(s, str)]
    if isinstance(data, list):                                  # ddg type=list flat form
        return [s for s in data if isinstance(s, str)]
    return []


def autocomplete(query: str, hl="en", gl="us", source="chrome", engine=None) -> list[str]:
    """One suggestion call against one engine.

    `engine` is the current parameter; `source` is the older one and still maps
    onto it, so existing callers and saved commands keep working unchanged.
    """
    engine = engine or _SOURCE_ALIAS.get(source, "google")
    build = SUGGEST_ENGINES.get(engine)
    if not build:
        return []
    try:
        data = fetch_json(build(query, hl, gl))
    except Exception:
        return []
    out, seen = [], set()
    for s in _parse_suggestions(engine, data):
        s = s.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def classify_intent(kw: str) -> str:
    if TRANSACTIONAL_HINT.search(kw):
        return "transactional"
    if re.search(r"\b(vs|versus|alternative|alternatives|compared to)\b", kw, re.I):
        return "comparison"
    if COMMERCIAL_HINT.search(kw):
        return "commercial"
    if QUESTION_HINT.search(kw):
        return "informational"
    return "informational"


def _resolve_engines(a) -> list[str]:
    """Which corpora to sweep. `--engines` wins; `--source` is the fallback so
    every existing invocation keeps its exact old behaviour (one engine)."""
    req = getattr(a, "engines", None)
    if not req:
        return [_SOURCE_ALIAS.get(getattr(a, "source", "chrome"), "google")]
    out: list[str] = []
    for e in req:
        if e == "all":
            out.extend(SUGGEST_ENGINES)
        elif e == "web":
            out.extend(["google", "bing", "ddg"])
        else:
            out.append(e)
    seen, uniq = set(), []
    for e in out:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq


def cmd_expand(a):
    seeds = [s.strip().lower() for s in a.seed if s.strip()]
    groups = a.groups or ["question", "commercial", "comparison", "audience", "constraint", "problem", "commercial_tail"]
    if a.tools:
        groups = ["tool_intent"]

    # depth 0 = the bare seed, 1 = seed + one modifier, 2 = alphabet soup
    engines = _resolve_engines(a)

    hits: dict[str, dict] = {}

    def record(kw: str, depth: int, rank: int, via: str, engine: str):
        kw = re.sub(r"\s+", " ", kw.strip().lower())
        if not kw or len(kw) < 3:
            return
        cur = hits.get(kw)
        if cur is None:
            hits[kw] = {"keyword": kw, "min_depth": depth, "best_rank": rank, "times_seen": 1,
                        "via": [via], "engines": [engine]}
        else:
            cur["times_seen"] += 1
            cur["min_depth"] = min(cur["min_depth"], depth)
            cur["best_rank"] = min(cur["best_rank"], rank)
            if via not in cur["via"]:
                cur["via"].append(via)
            if engine not in cur["engines"]:
                cur["engines"].append(engine)

    queries: list[tuple[str, int, str]] = []
    for seed in seeds:
        queries.append((seed, 0, "seed"))
        for group in groups:
            for mod in MODIFIERS.get(group, []):
                # prefix form for question words, suffix form for the rest -
                # "how to <seed>" vs "<seed> alternatives"
                if group == "question" or mod in ("best", "top", "cheapest", "free"):
                    queries.append((f"{mod} {seed}", 1, group))
                else:
                    queries.append((f"{seed} {mod}", 1, group))
        if a.alphabet:
            for letter in ALPHABET:
                queries.append((f"{seed} {letter}", 2, "alphabet"))

    calls = 0
    calls_by_engine: dict[str, int] = defaultdict(int)
    engines_answering: set[str] = set()
    for q, depth, via in queries:
        if a.max_calls and calls >= a.max_calls:
            break
        for engine in engines:
            suggestions = autocomplete(q, hl=a.hl, gl=a.gl, engine=engine)
            calls += 1
            calls_by_engine[engine] += 1
            if suggestions:
                engines_answering.add(engine)
            for rank, s in enumerate(suggestions, 1):
                record(s, depth, rank, via, engine)
        if a.delay:
            time.sleep(a.delay)

    # An engine that answered NOTHING all sweep is a dead instrument, not a
    # verdict that nobody searches there - so agreement is scored against the
    # engines that actually answered, never against the ones we asked for.
    live_engines = sorted(engines_answering)
    rows = []
    for kw, row in hits.items():
        if a.must_contain and not all(t in kw for t in a.must_contain):
            continue
        words = len(kw.split())
        # Demand proxy: shallower prefix + higher autocomplete rank + seen via
        # more sweeps = more people type it. 0-100, ordinal only.
        proxy = max(0, 100 - row["min_depth"] * 22 - (row["best_rank"] - 1) * 4 + min(row["times_seen"] - 1, 6) * 3)
        agreeing = [e for e in row["engines"] if e in engines_answering]
        rows.append(
            {
                **row,
                "words": words,
                "intent": classify_intent(kw),
                "intent_evidence": sorted({INTENT_ENGINES[e] for e in agreeing if e in INTENT_ENGINES}),
                "tool_shaped": bool(TOOL_VERBS.search(kw)),
                "mid_tail": 2 <= words <= 5,
                "demand_proxy": proxy,
                "engine_agreement": len(agreeing),
                "engine_agreement_pct": (round(100 * len(agreeing) / len(live_engines))
                                         if live_engines else None),
            }
        )

    key = {"proxy": lambda r: -r["demand_proxy"], "words": lambda r: (r["words"], -r["demand_proxy"]),
           "alpha": lambda r: r["keyword"],
           "agreement": lambda r: (-r["engine_agreement"], -r["demand_proxy"])}[a.sort]
    rows.sort(key=key)
    if a.limit:
        rows = rows[: a.limit]

    by_intent = defaultdict(int)
    for r in rows:
        by_intent[r["intent"]] += 1
    print(json.dumps(
        {
            "ok": True,
            "seeds": seeds,
            "autocomplete_calls": calls,
            "candidates": len(rows),
            "intent_mix": dict(by_intent),
            "tool_shaped_count": sum(1 for r in rows if r["tool_shaped"]),
            "mid_tail_count": sum(1 for r in rows if r["mid_tail"]),
            "source": a.source,
            "engines_requested": engines,
            "engines_answering": live_engines,
            "engines_silent": sorted(set(engines) - engines_answering),
            "calls_by_engine": dict(calls_by_engine),
            "demand_proxy_note": "ORDINAL autocomplete signal (prefix depth + suggestion rank + "
                                 "how many sweeps surfaced it), 0-100. NOT a monthly search volume - "
                                 "never report it as one.",
            "engine_agreement_note": (
                "how many INDEPENDENT suggestion corpora surfaced this exact phrase, out of "
                "engines_answering. Different engines, different audiences, different "
                "algorithms - so 4/5 is corroboration in a way that rank 1 on one engine is "
                "not. Still ordinal, still not a volume. An engine in engines_silent was NOT "
                "counted against anything: a dead instrument is not evidence of no demand."),
            "intent_evidence_note": (
                "'video' means YouTube's corpus surfaced it, 'product' means Amazon's did. "
                "That is OBSERVED intent rather than intent guessed from the wording, and it "
                "beats the `intent` field when the two disagree."),
            "results": rows,
        },
        indent=2, ensure_ascii=False,
    ))


# ------------------------------------------------------------------ volume


def dataforseo_ideas(seeds: list[str], location=2840, language="en", limit=200) -> dict:
    login = os.environ.get("DATAFORSEO_LOGIN", "")
    password = os.environ.get("DATAFORSEO_PASSWORD", "")
    if not (login and password):
        raise RuntimeError("volume via DataForSEO needs DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD")
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    payload = json.dumps([{
        "keywords": seeds[:5],
        "location_code": int(location),
        "language_code": language,
        "include_serp_info": False,
        "include_seed_keyword": True,
        "limit": limit,
        "order_by": ["keyword_info.search_volume,desc"],
    }])
    req = urllib.request.Request(
        "https://api.dataforseo.com/v3/dataforseo_labs/google/keyword_ideas/live",
        data=payload.encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    try:
        items = data["tasks"][0]["result"][0]["items"] or []
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"DataForSEO returned no items: {json.dumps(data)[:300]}")
    return {
        "results": [
            {
                "keyword": it.get("keyword"),
                "volume": (it.get("keyword_info") or {}).get("search_volume"),
                "kd": (it.get("keyword_properties") or {}).get("keyword_difficulty"),
                "cpc": (it.get("keyword_info") or {}).get("cpc"),
                "competition": (it.get("keyword_info") or {}).get("competition_level"),
                "intent": classify_intent(it.get("keyword") or ""),
            }
            for it in items
        ]
    }


def cmd_volume(a):
    seeds = [s.strip() for s in a.seed if s.strip()]
    if not seeds:
        print(json.dumps({"ok": False, "error": "pass at least one --seed"}))
        sys.exit(1)
    if len(seeds) > 5:
        print(json.dumps({"ok": False, "error": "DataForSEO expands at most 5 seeds per call - "
                                                "that is the facet count by design. Batch them."}))
        sys.exit(1)
    try:
        data = dataforseo_ideas(seeds, a.location, a.language, a.limit)
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": str(exc),
            "fallback": "No paid volume source configured. That is a DATA GATE, not a failure: the "
                        "quality bar's volume floor and KD ceiling are simply inapplicable when the "
                        "data does not exist. Use `expand` for candidates, `serp.py` for the "
                        "authority gate, and NEVER invent numbers to fill the gap.",
        }, indent=2))
        sys.exit(2)
    rows = data["results"]
    if a.min_volume:
        rows = [r for r in rows if (r.get("volume") or 0) >= a.min_volume]
    if a.max_kd is not None:
        rows = [r for r in rows if r.get("kd") is None or r["kd"] <= a.max_kd]
    print(json.dumps({"ok": True, "seeds": seeds, "count": len(rows), "results": rows[: a.limit]}, indent=2))


# -------------------------------------------------------------------- gsc


def cmd_gsc(a):
    """Turn a Search Console search-analytics JSON export into candidates.

    Produce the input with the `search-console` skill (its search-analytics
    query), then pipe it here. Striking-distance queries - real impressions at
    position 11-50 - are the highest-yield free seam there is: proven relevant
    to this exact domain and already half-ranked.
    """
    text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    data = json.loads(text)
    # Both shapes are supported ON PURPOSE: the Search Console API returns
    # {"rows": [...]}, but every natural way to save it - jq '.rows', a paged
    # fetch that concatenates, the search-console skill's own examples - hands
    # you the bare LIST. The isinstance test has to come FIRST: the old form
    # `data.get("rows", data if isinstance(data, list) else [])` evaluated
    # .get() on the list before the default could ever be used, so a bare list
    # raised AttributeError instead of taking the branch written for it.
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("rows") or []
    else:
        print(json.dumps({"ok": False, "error":
              f"expected a JSON object with a 'rows' key or a bare list of rows, got {type(data).__name__}"}))
        return
    out = []
    for r in rows:
        keys = r.get("keys") or []
        kw = (keys[0] if keys else r.get("query") or "").strip().lower()
        if not kw:
            continue
        pos = r.get("position")
        imp = r.get("impressions") or 0
        clicks = r.get("clicks") or 0
        ctr = r.get("ctr")
        if imp < a.min_impressions:
            continue
        if pos is None:
            band = "unknown"
        elif pos <= 3:
            band = "top3"
        elif pos <= 10:
            band = "page1"
        elif pos <= 20:
            band = "striking-distance"
        elif pos <= 50:
            band = "page3-5"
        else:
            band = "deep"
        out.append({
            "keyword": kw,
            "impressions": imp,
            "clicks": clicks,
            "ctr": round(ctr, 4) if isinstance(ctr, (int, float)) else ctr,
            "position": round(pos, 1) if isinstance(pos, (int, float)) else pos,
            "band": band,
            "intent": classify_intent(kw),
            "tool_shaped": bool(TOOL_VERBS.search(kw)),
            # Impressions with no clicks at a decent position = a CTR problem
            # (title/description), not a ranking problem.
            "ctr_underperformer": bool(pos and pos <= 10 and imp >= 100 and (ctr or 0) < 0.02),
        })
    order = {"striking-distance": 0, "page3-5": 1, "page1": 2, "top3": 3, "deep": 4, "unknown": 5}
    out.sort(key=lambda r: (order.get(r["band"], 9), -r["impressions"]))
    if a.band:
        out = [r for r in out if r["band"] in a.band]
    print(json.dumps({
        "ok": True,
        "count": len(out),
        "striking_distance": sum(1 for r in out if r["band"] == "striking-distance"),
        "ctr_underperformers": sum(1 for r in out if r["ctr_underperformer"]),
        "results": out[: a.limit] if a.limit else out,
    }, indent=2, ensure_ascii=False))


# --------------------------------------------------------------- bing bands
_SCRIPT_RANGES = [
    ("cjk", "\u4e00-\u9fff\u3400-\u4dbf"), ("kana", "\u3040-\u30ff"),
    ("hangul", "\uac00-\ud7af"), ("cyrillic", "\u0400-\u04ff"),
    ("arabic", "\u0600-\u06ff"), ("thai", "\u0e00-\u0e7f"),
    ("devanagari", "\u0900-\u097f"), ("hebrew", "\u0590-\u05ff"),
]
_SCRIPT_RE = [(n, re.compile(f"[{r}]")) for n, r in _SCRIPT_RANGES]


def script_of(kw: str) -> str:
    for name, rx in _SCRIPT_RE:
        if rx.search(kw):
            return name
    return "latin"


def cmd_bing(a):
    """Band a `bing.py queries` export the same way cmd_gsc bands Search Console.

    This exists because a program can spend months optimising against the wrong
    engine without ever noticing. Measured on combatskirmish.net 2026-09-01:
    Bing delivered 57,596 impressions and 4,300 clicks in 29 days while Google
    Search Console reported 545 and 43 for the same window - about 100x - and
    every workflow here had been reading GSC. Whichever engine actually sends
    the traffic is the one whose half-ranked queries are worth mining.

    Two things this does that the GSC path does not need:

    1. AGGREGATES duplicate query rows. Bing returns one row per market, so the
       same string appears several times with different numbers; summing the
       impressions and taking an IMPRESSION-WEIGHTED position is the only honest
       way to collapse them. A plain mean over rows lets a 3-impression market
       move the headline position as much as a 3,000-impression one.
    2. Segments by SCRIPT. That is what surfaced the finding above: Chinese
       queries were 15% of queries and 73% of all clicks, converting at 30.3%
       against 2.5% for everything else. A single blended CTR hides a segment
       performing twelve times better than the average - and hides which half of
       the site is actually earning.
    """
    text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    data = json.loads(text)
    rows = data.get("queries") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print(json.dumps({"ok": False, "error":
              "expected `bing.py queries` output (a dict with 'queries') or a bare list"}))
        return
    agg = {}
    for r in rows:
        kw = (r.get("query") or "").strip()
        if not kw:
            continue
        imp = r.get("impressions") or 0
        e = agg.setdefault(kw, {"impressions": 0, "clicks": 0, "wpos": 0.0, "posimp": 0})
        e["impressions"] += imp
        e["clicks"] += r.get("clicks") or 0
        pos = r.get("avg_position")
        if isinstance(pos, (int, float)):
            e["wpos"] += pos * imp
            e["posimp"] += imp
    out = []
    for kw, e in agg.items():
        if e["impressions"] < a.min_impressions:
            continue
        pos = (e["wpos"] / e["posimp"]) if e["posimp"] else None
        ctr = (e["clicks"] / e["impressions"]) if e["impressions"] else 0.0
        if pos is None:
            band = "unknown"
        elif pos <= 3:
            band = "top3"
        elif pos <= 10:
            band = "page1"
        elif pos <= 20:
            band = "striking-distance"
        elif pos <= 50:
            band = "page3-5"
        else:
            band = "deep"
        out.append({
            "keyword": kw, "impressions": e["impressions"], "clicks": e["clicks"],
            "ctr": round(ctr, 4), "position": round(pos, 1) if pos is not None else None,
            "band": band, "script": script_of(kw), "intent": classify_intent(kw),
            "tool_shaped": bool(TOOL_VERBS.search(kw)),
            "ctr_underperformer": bool(pos and pos <= 10 and e["impressions"] >= 100 and ctr < 0.02),
        })
    order = {"striking-distance": 0, "page3-5": 1, "page1": 2, "top3": 3, "deep": 4, "unknown": 5}
    out.sort(key=lambda r: (order.get(r["band"], 9), -r["impressions"]))
    by_script = {}
    for r in out:
        b = by_script.setdefault(r["script"], {"queries": 0, "impressions": 0, "clicks": 0})
        b["queries"] += 1
        b["impressions"] += r["impressions"]
        b["clicks"] += r["clicks"]
    for b in by_script.values():
        b["ctr"] = round(b["clicks"] / b["impressions"], 4) if b["impressions"] else 0.0
    if a.band:
        out = [r for r in out if r["band"] in a.band]
    if getattr(a, "script", None):
        out = [r for r in out if r["script"] in a.script]
    print(json.dumps({
        "ok": True, "count": len(out),
        "striking_distance": sum(1 for r in out if r["band"] == "striking-distance"),
        "ctr_underperformers": sum(1 for r in out if r["ctr_underperformer"]),
        "by_script": dict(sorted(by_script.items(), key=lambda kv: -kv[1]["clicks"])),
        "reading": ("`by_script` first. A blended CTR can hide one language segment earning "
                    "most of the clicks at many times the average rate - which is exactly "
                    "what it was hiding here. `ctr_underperformer` marks a query ranking "
                    "top-10 with real impressions and under 2% CTR: that is a title and "
                    "snippet problem, not a ranking problem, and it is fixed differently."),
        "results": out[: a.limit] if a.limit else out,
    }, indent=2, ensure_ascii=False))


# -------------------------------------------------------------------- main


# ------------------------------------------------------- SERP-overlap clustering

def cmd_cluster(a):
    """Group keywords by how much their SERPs agree. One cluster = one page.

    The question this answers is the one that decides the whole content plan and
    that nothing else in this skill answers: are `widget crosshair generator`
    and `how to change a widget crosshair` one page or two? Guessing from the
    words is unreliable - phrasings that look identical routinely return
    disjoint SERPs, and phrasings that look unrelated routinely return the same
    ten URLs.

    Google has already answered it. If two queries return substantially the
    SAME page-1 URLs, Google considers them the same intent, and two pages
    targeting them will compete with each other rather than add up. If the SERPs
    disagree, they are different intents and one page cannot serve both.

    Rule: >= `--overlap` shared URLs in the top `--top-n` merges the pair.
    3-of-10 is the industry default and it holds up: at 2 you merge everything
    through a single ubiquitous result (a Wikipedia page, a vendor homepage), at
    4+ you split clusters that are obviously one topic.

    Clustering is TRANSITIVE here (single-link): A~B and B~C puts all three in
    one cluster even if A and C do not overlap directly. That matches how a hub
    page actually works - B is the bridge - but it means one promiscuous keyword
    can chain two real clusters together, so `bridges` names any keyword holding
    a cluster together by a single link. Check those by eye.

    A refused SERP read is NOT an empty overlap. Unreadable queries are excluded
    and listed in `unread`, never silently clustered alone - a keyword that
    failed to read looks exactly like a keyword with a unique SERP, and treating
    them the same invents a cluster.
    """
    kws: list[str] = []
    for k in a.keywords or []:
        kws.append(k.strip())
    if a.file:
        raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
        kws.extend(x.strip() for x in raw.splitlines() if x.strip())
    kws = list(dict.fromkeys([k for k in kws if k]))

    records = []
    if a.serps:
        try:
            d = json.loads(open(a.serps, encoding="utf-8").read())
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"cannot read {a.serps}: {exc}"}))
            sys.exit(2)
        records = d.get("results", d if isinstance(d, list) else [])
    else:
        if not kws:
            print(json.dumps({"ok": False, "error": "no keywords",
                              "hint": "--keywords/--file, or --serps with a saved batch"}))
            sys.exit(2)
        body = json.dumps({"queries": kws, "depth": a.depth, "view": "full"}).encode()
        req = urllib.request.Request(a.daemon.rstrip("/") + "/batch", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=max(120, 12 * len(kws))) as r:
                d = json.loads(r.read().decode())
        except Exception as exc:
            print(json.dumps({
                "ok": False,
                "error": f"serpd batch failed: {type(exc).__name__}: {exc}",
                "hint": "python3 seodoctor.py --hard, then serpd.py --start (NO trailing &)",
            }))
            sys.exit(2)
        records = d.get("results", [])
        if a.save_serps:
            open(a.save_serps, "w", encoding="utf-8").write(json.dumps(d))

    urls: dict[str, list[str]] = {}
    unread: list[dict] = []
    for r in records:
        q = r.get("query")
        if not q:
            continue
        if not r.get("ok"):
            unread.append({"keyword": q, "error": r.get("error") or "refused read"})
            continue
        rows = r.get("results") or []
        if not rows:
            unread.append({"keyword": q, "error": "no results in a read marked ok"})
            continue
        urls[q] = [(x.get("url") or "").split("#")[0].rstrip("/") for x in rows[: a.top_n]]

    keys = list(urls)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for i, x in enumerate(keys):
        sx = set(urls[x])
        for y in keys[i + 1:]:
            shared = sx & set(urls[y])
            if len(shared) >= a.overlap:
                edges.append({"a": x, "b": y, "shared": len(shared),
                              "urls": sorted(shared)[:5]})
                rx, ry = find(x), find(y)
                if rx != ry:
                    parent[rx] = ry

    groups: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)

    degree = defaultdict(int)
    for e in edges:
        degree[e["a"]] += 1
        degree[e["b"]] += 1

    out = []
    for members in sorted(groups.values(), key=len, reverse=True):
        # The head is the keyword sharing the most SERP with the rest - the one
        # a single page should actually target.
        head = max(members, key=lambda m: (degree[m], -len(m)))
        bridges = [m for m in members
                   if len(members) > 2 and degree[m] == 1]
        common = set(urls[head])
        for m in members:
            common &= set(urls[m])
        out.append({
            "size": len(members),
            "head": head,
            "members": members,
            "bridges": bridges,
            "shared_by_all": sorted(common)[:5],
            "verdict": ("ONE page - Google returns the same results for all of these"
                        if len(members) > 1 else "its own page - no SERP overlap with the rest"),
        })

    print(json.dumps({
        "ok": True,
        "keywords_in": len(kws) or len(records),
        "keywords_clustered": len(keys),
        "unread": unread,
        "unread_count": len(unread),
        "rule": f">={a.overlap} shared URLs in the top {a.top_n} merges two keywords",
        "clusters": len(out),
        "multi_keyword_clusters": sum(1 for c in out if c["size"] > 1),
        "detail": out,
        "reading": "Each cluster is ONE page targeting its `head`, with the other members as "
                   "sections and H2s - not one page each. Building a page per member inside a "
                   "cluster is self-cannibalisation you can predict before writing a word. "
                   "`bridges` are members attached by a single overlapping pair: if one looks "
                   "wrong, it is chaining two real clusters together and should be split out. "
                   "`unread` keywords have NO verdict - re-run them, do not assume they are singletons.",
    }, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("expand", help="autocomplete sweep -> candidate keywords")
    s.add_argument("--seed", action="append", required=True, help="repeatable")
    s.add_argument("--groups", nargs="*", choices=list(MODIFIERS), help="which modifier sets to sweep")
    s.add_argument("--tools", action="store_true", help="tool-intent sweep only (the build-tool queue)")
    s.add_argument("--alphabet", action="store_true", help="also run a-z suffix soup (26 extra calls/seed)")
    s.add_argument("--must-contain", nargs="*", help="keep only candidates containing all these tokens")
    s.add_argument("--hl", default="en")
    s.add_argument("--gl", default="us")
    s.add_argument("--source", default="chrome", choices=["chrome", "youtube"],
                   help="legacy single-engine selector; --engines supersedes it")
    s.add_argument("--engines", nargs="*", default=None,
                   choices=list(SUGGEST_ENGINES) + ["all", "web"],
                   help="suggestion corpora to sweep. 'web' = google+bing+ddg (the three "
                        "web-search engines), 'all' = those plus youtube/yandex/amazon. "
                        "More engines = more calls, but a real agreement signal.")
    s.add_argument("--sort", default="proxy", choices=["proxy", "words", "alpha", "agreement"])
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--max-calls", type=int, default=120)
    s.add_argument("--delay", type=float, default=0.15)
    s.set_defaults(fn=cmd_expand)

    s = sub.add_parser("volume", help="real volume + KD (needs DataForSEO creds)")
    s.add_argument("--seed", action="append", required=True, help="up to 5 MID-TAIL seeds, never head terms")
    s.add_argument("--location", type=int, default=2840)
    s.add_argument("--language", default="en")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--min-volume", type=int)
    s.add_argument("--max-kd", type=float)
    s.set_defaults(fn=cmd_volume)

    s = sub.add_parser("cluster", help="SERP-overlap clustering - one page or five?")
    s.add_argument("--keywords", action="append", help="keyword. Repeatable.")
    s.add_argument("--file", help="newline-delimited keywords, or - for stdin")
    s.add_argument("--serps", help="pre-fetched serpd /batch view=full JSON (skips fetching)")
    s.add_argument("--daemon", default="http://127.0.0.1:8791")
    s.add_argument("--depth", type=int, default=10)
    s.add_argument("--overlap", type=int, default=3,
                   help="shared top-N URLs required to merge two keywords")
    s.add_argument("--top-n", type=int, default=10, help="SERP depth compared for overlap")
    s.add_argument("--save-serps", help="write the fetched SERPs here for reuse")
    s.set_defaults(fn=cmd_cluster)

    s = sub.add_parser("gsc", help="Search Console rows -> candidates (striking distance first)")
    s.add_argument("file", help="search-analytics JSON, or - for stdin")
    s.add_argument("--min-impressions", type=int, default=10)
    s.add_argument("--band", nargs="*", choices=["top3", "page1", "striking-distance", "page3-5", "deep", "unknown"])

    b = sub.add_parser("bing", help="band a `bing.py queries` export (the engine that may actually send your traffic)")
    b.add_argument("file", help="bing.py queries JSON, or - for stdin")
    b.add_argument("--min-impressions", type=int, default=10)
    b.add_argument("--band", nargs="*", choices=["top3", "page1", "striking-distance", "page3-5", "deep", "unknown"])
    b.add_argument("--script", nargs="*", help="filter by script: cjk, latin, cyrillic, arabic, ...")
    b.add_argument("--limit", type=int, default=100)
    b.set_defaults(fn=cmd_bing)
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_gsc)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
