#!/usr/bin/env python3
"""geo.py - do answer engines CITE us, and is what they say right?

`crawllog.py` measures who CRAWLED (OAI-SearchBot: 27 verified hits) and
`backlinks.py referrers` measures who REFERRED (chatgpt.com: 18 clicks). Neither
asks the question that decides AI visibility: **when an assistant answers a
question this site exists to answer, is this site one of the sources it names?**

THE UNIT IS AN ANSWER, NOT A RANKING. A page can sit at position 3 and be absent
from the overview above it, which is what happened here: measured 2026-09-01,
Google's AI Overview for "how to play counter strike 1.6 in browser" cited seven
sources - facebook, play-cs.com, dos.zone, vogons, vpn4games, github, reddit -
and combatskirmish.net was not among them, while the DR 35 competitor was.

FAIL-CLOSED, STRUCTURALLY. `cannot_ask` and `not_cited` are different states and
never share a code path. An engine with no key reports `no_key`; an engine that
errored reports `failing`; only an engine that actually ANSWERED can report
`not_cited`. A sweep that could not reach any engine returns a refusal, not a
report saying nobody cites you.

    geo.py engines                          # what can be asked right now
    geo.py ask --query "..." --domain example.com
    geo.py sweep --domain example.com --from-state --max 12
    geo.py extractable --root ./public      # is each page's answer liftable?
    geo.py control

Stdlib only. Google's AI Overview is reachable today through the SERP keys this
skill already uses; the LLM engines are wired and report `no_key` until a key
exists, which is the point - the moment one appears they work.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse, uniform_verdict  # noqa: E402
from providers import cache_get, cache_put, http, read_secret  # noqa: E402

HERE = Path(__file__).resolve().parent
CACHE_TTL = 6 * 3600

# The engine's OWN domains, which appear in its citation list as plumbing rather
# than as sources. Google's AI Overview references carry google.com links
# (support pages, redirects, "more results"), and counted naively they dominate
# every share-of-voice table: measured 2026-09-01, google.com appeared 21 times
# across 5 answers while the leading real source appeared 5. Same class as the
# UGC platform chrome that polluted `brief.py`'s subtopics - excluded, but
# reported separately so the exclusion is visible rather than silent.
ENGINE_FURNITURE = {
    "google_ai_overview": {"google.com", "gstatic.com", "googleusercontent.com"},
}


def _host(u: str) -> str:
    try:
        h = urllib.parse.urlsplit(u).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:                                             # noqa: BLE001
        return ""


def _registrable(h: str) -> str:
    parts = [p for p in (h or "").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


# ------------------------------------------------------------------- engines
def engine_google_ai_overview(query: str, gl="us", hl="en") -> dict:
    """Google's AI Overview, through SerpApi.

    ⚠ TWO-STAGE. The first response carries only a `page_token` + `serpapi_link`;
    `references` and `text_blocks` do not exist until that link is followed.
    Reading citations off the first response yields [] for every overview on
    earth, which answers "are we cited" with a permanent silent no."""
    key = read_secret("SERPAPI_KEY", "~/.serpapi_key")
    if not key:
        return {"state": "no_key", "detail": "needs SERPAPI_KEY or ~/.serpapi_key"}
    qs = urllib.parse.urlencode({"engine": "google", "q": query, "gl": gl, "hl": hl,
                                 "api_key": key})
    r = http(f"https://serpapi.com/search.json?{qs}", timeout=60)
    if not r.ok:
        return {"state": "failing", "detail": f"HTTP {r.get('status')} {r.get('error') or ''}"}
    d = r.json() or {}
    if d.get("error"):
        return {"state": "failing", "detail": str(d["error"])[:200]}
    ai = d.get("ai_overview")
    if not ai:
        # A SERP with no overview is a MEASURED absence of the surface, which is
        # different from being absent FROM one. Both are reported, separately.
        return {"state": "answered", "has_answer": False, "text": "", "references": [],
                "detail": "no AI Overview on this SERP"}
    refs, text = ai.get("references") or [], ai.get("text_blocks") or []
    if not refs and ai.get("serpapi_link"):
        r2 = http(f"{ai['serpapi_link']}&api_key={key}", timeout=60)
        if not r2.ok:
            return {"state": "failing",
                    "detail": f"overview follow-up HTTP {r2.get('status')} - citation "
                              f"state UNKNOWN, not zero"}
        ai2 = (r2.json() or {}).get("ai_overview") or {}
        refs, text = ai2.get("references") or [], ai2.get("text_blocks") or []

    def flatten(blocks, out):
        for b in blocks or []:
            if b.get("snippet"):
                out.append(b["snippet"])
            flatten(b.get("list"), out)
        return out

    return {"state": "answered", "has_answer": True,
            "text": " ".join(flatten(text, []))[:6000],
            "references": [{"domain": _host(x.get("link", "")), "url": x.get("link"),
                            "source": x.get("source") or x.get("title")} for x in refs]}


def _llm_engine(name: str, env: str, dotfile: str, url: str, build, parse):
    def run(query: str, **_) -> dict:
        key = read_secret(env, dotfile)
        if not key:
            return {"state": "no_key", "detail": f"needs {env} or {dotfile}"}
        body, headers = build(key, query)
        r = http(url, data=body, headers=headers, timeout=90, method="POST")
        if not r.ok:
            return {"state": "failing",
                    "detail": f"HTTP {r.get('status')}: {(r.text() or '')[:200]}"}
        try:
            text, refs = parse(r.json() or {})
        except Exception as e:                                    # noqa: BLE001
            return {"state": "failing", "detail": f"unparseable answer: {e}"}
        return {"state": "answered", "has_answer": bool(text), "text": text[:6000],
                "references": refs}
    run.__name__ = f"engine_{name}"
    return run


def _perplexity_build(key, q):
    return (json.dumps({"model": "sonar", "messages": [{"role": "user", "content": q}]}).encode(),
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})


def _perplexity_parse(d):
    text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    cites = d.get("citations") or d.get("search_results") or []
    refs = [{"domain": _host(c if isinstance(c, str) else c.get("url", "")),
             "url": c if isinstance(c, str) else c.get("url"),
             "source": None if isinstance(c, str) else c.get("title")} for c in cites]
    return text, refs


def _openai_build(key, q):
    return (json.dumps({"model": "gpt-4o-mini-search-preview",
                        "messages": [{"role": "user", "content": q}]}).encode(),
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})


def _openai_parse(d):
    msg = ((d.get("choices") or [{}])[0].get("message") or {})
    text = msg.get("content") or ""
    refs = []
    for a in (msg.get("annotations") or []):
        u = ((a.get("url_citation") or {}).get("url")) or a.get("url")
        if u:
            refs.append({"domain": _host(u), "url": u,
                         "source": (a.get("url_citation") or {}).get("title")})
    return text, refs


ENGINES = {
    "google_ai_overview": engine_google_ai_overview,
    "perplexity": _llm_engine("perplexity", "PERPLEXITY_API_KEY", "~/.perplexity_key",
                              "https://api.perplexity.ai/chat/completions",
                              _perplexity_build, _perplexity_parse),
    "openai": _llm_engine("openai", "OPENAI_API_KEY", "~/.openai_key",
                          "https://api.openai.com/v1/chat/completions",
                          _openai_build, _openai_parse),
}


def engines_status() -> dict:
    rows = []
    for name, fn in ENGINES.items():
        try:
            probe = fn("what is counter-strike")
        except Exception as e:                                    # noqa: BLE001
            probe = {"state": "failing", "detail": f"{type(e).__name__}: {e}"}
        rows.append({"engine": name, "state": probe["state"],
                     "detail": probe.get("detail"),
                     "answered": probe.get("has_answer"),
                     "citations": len(probe.get("references") or [])})
    usable = [r for r in rows if r["state"] == "answered"]
    return {"ok": bool(usable), "check": "geo-engines", "engines": rows,
            "usable": [r["engine"] for r in usable],
            "note": ("`no_key` is CANNOT ASK, not a finding. A sweep with no usable "
                     "engine refuses rather than reporting that nobody cites you."),
            **({} if usable else
               {"reason": "no answer engine is reachable - nothing can be measured"})}


# ------------------------------------------------------------------- the ask
# ⚠ SENTENCE BOUNDARIES ARE NOT ASCII. `(?<=[.!?])\s+` never fires on Japanese
# (which ends sentences with 。 and uses no spaces), on Hindi/Bengali (danda ।)
# or on Urdu (۔). Measured 2026-09-01 across 21 locale homes: it returned the
# WHOLE paragraph as "one sentence" for hi and ur - 55 and 57 words - and a
# 3-"word" fragment for ja, producing three confident findings about the site
# that were entirely artefacts of this regex.
# ⚠ TWO ALTERNATIVES, not one relaxed rule. An ASCII terminator must be
# FOLLOWED BY WHITESPACE or the dot in `Combatskirmish.net` ends a sentence -
# which is precisely what a first attempt at this did, silently breaking the
# "which sentences name us" extraction. CJK and Indic terminators carry no
# trailing space, so they match zero-width.
_SENT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？।॥۔])\s*")

# Scripts that do not delimit words with spaces. A whitespace word count is
# meaningless for these - the same sentence reads as 3 "words" in Japanese and
# 20 in English - so they are measured in CHARACTERS against their own bounds.
_NOSPACE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0e7f]")
# Does the paragraph HAVE sentence structure? Three attempts got this wrong, so
# the reasoning is worth keeping:
#   1. "did the split fire" - no. The split needs whitespace after an ASCII
#      terminator, so a paragraph that is exactly ONE sentence ending at the end
#      of the string has no split point, and the length check gets waived on
#      most paragraphs on the site.
#   2. "is a terminator present anywhere" - no. The dot in `Counter-Strike 1.6`
#      is a terminator by that test, so a Thai paragraph with no sentence
#      punctuation at all still counted as structured.
#   3. What actually answers it: either the split produced more than one piece,
#      or the paragraph ENDS on a terminator. Thai satisfies neither, English
#      and Japanese satisfy one or both.
_TERM_END = re.compile(r"[.!?。！？।॥۔][\"'\u2019\u201d)\]]*\s*$")


def _mentions(text: str, domain: str, brand: str | None) -> list[str]:
    """Sentences that name us - returned VERBATIM for a human to judge.

    Deliberately NOT an automated fact-check. Deciding whether an assistant's
    claim about a site is true needs the site's own ground truth and a judgement;
    what this can do honestly is surface the exact sentences so the judgement is
    made on the real words rather than on a summary of them."""
    needles = {domain.lower(), _registrable(domain.lower())}
    if brand:
        needles.add(brand.lower())
    return [s.strip() for s in _SENT.split(text or "")
            if any(n and n in s.lower() for n in needles)][:8]


def ask(query: str, domain: str, *, brand: str | None = None,
        engines: list[str] | None = None, gl="us", hl="en",
        use_cache: bool = True) -> dict:
    domain = _host(domain) or domain.lower()
    want = engines or list(ENGINES)
    rows = []
    for name in want:
        fn = ENGINES.get(name)
        if not fn:
            rows.append({"engine": name, "state": "failing", "detail": "unknown engine"})
            continue
        ck = f"{name}:{gl}:{hl}:{query}"
        got = cache_get("geo", ck, CACHE_TTL) if use_cache else None
        if got is None:
            try:
                got = fn(query, gl=gl, hl=hl)
            except Exception as e:                                # noqa: BLE001
                got = {"state": "failing", "detail": f"{type(e).__name__}: {e}"}
            if use_cache and got.get("state") in ("answered", "no_key"):
                cache_put("geo", ck, got)
        if got["state"] != "answered":
            rows.append({"engine": name, "state": got["state"],
                         "detail": got.get("detail"), "cited": None,
                         "why": "cannot ask - this is NOT evidence of not being cited"})
            continue
        refs = got.get("references") or []
        raw = [_registrable(r["domain"]) for r in refs if r.get("domain")]
        furniture = ENGINE_FURNITURE.get(name, set())
        doms = [d for d in raw if d not in furniture]
        ours = _registrable(domain)
        # ⚠ NO ANSWER SURFACE IS NOT "NOT CITED". A SERP with no AI Overview is
        # not an answer we were left out of - there was nothing to be in. Folding
        # the two produces a citation rate computed against answers that never
        # existed, which understates every result and cannot be told from a real
        # miss once it is a number.
        if not got.get("has_answer"):
            rows.append({"engine": name, "state": "answered", "has_answer": False,
                         "cited": None, "citations": 0,
                         "why": "no answer surface on this query - nothing to be cited BY"})
            continue
        cited = ours in doms
        rows.append({
            "engine": name, "state": "answered",
            "has_answer": True,
            "cited": cited,
            "citation_position": (doms.index(ours) + 1) if cited else None,
            "citations": len(doms),
            "cited_domains": doms,
            "engine_furniture_excluded": sorted(set(raw) & furniture),
            "competitors_cited": [d for d in doms if d != ours],
            "sentences_naming_us": _mentions(got.get("text", ""), domain, brand),
            "answer_excerpt": (got.get("text") or "")[:400],
        })
    answered = [r for r in rows if r["state"] == "answered" and r.get("has_answer")]
    if not answered:
        surfaceless = [r for r in rows if r["state"] == "answered" and not r.get("has_answer")]
        if surfaceless and len(surfaceless) == len([r for r in rows if r["state"] == "answered"]):
            return {"ok": True, "check": "geo-ask", "query": query, "domain": domain,
                    "engines_asked": len(rows), "engines_answered": 0,
                    "cited_by": [], "not_cited_by": [],
                    "no_answer_surface": [r["engine"] for r in surfaceless],
                    "could_not_ask": [r["engine"] for r in rows if r["state"] != "answered"],
                    "results": rows,
                    "note": ("every engine reached this query and NONE produced an answer "
                             "surface. That is a fact about the query, not about us.")}
        return refuse("geo-ask",
                      "no engine answered, so nothing is known about citation. This is "
                      "'cannot ask', which must never be reported as 'not cited'.",
                      query=query, engines=rows)
    return {
        "ok": True, "check": "geo-ask", "query": query, "domain": domain,
        "engines_asked": len(rows), "engines_answered": len(answered),
        "cited_by": [r["engine"] for r in answered if r["cited"]],
        "not_cited_by": [r["engine"] for r in answered if r["cited"] is False],
        "no_answer_surface": [r["engine"] for r in rows
                              if r["state"] == "answered" and not r.get("has_answer")],
        "could_not_ask": [r["engine"] for r in rows if r["state"] != "answered"],
        "results": rows,
    }


def _bank_from_state(root: str | None, limit: int) -> tuple[list[str], str | None]:
    """The question bank comes from TRACKED KEYWORDS, not from imagination.

    A hand-written bank measures whatever its author already believed the site
    was about; the tracked list is what the site is actually trying to rank for,
    and it is already curated."""
    cmd = [sys.executable, str(HERE / "seostate.py")]
    if root:
        cmd += ["--root", root]
    cmd += ["keywords"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        d = json.loads(p.stdout or "{}")
    except Exception as e:                                        # noqa: BLE001
        return [], f"cannot read tracked keywords: {e}"
    rows = d.get("keywords") if isinstance(d, dict) else d
    if not isinstance(rows, list) or not rows:
        return [], "no tracked keywords in .seo/ - pass --bank instead"
    out = []
    for r in rows:
        kw = r.get("keyword") if isinstance(r, dict) else r
        if isinstance(kw, str) and kw.strip():
            out.append(kw.strip())
    return out[:limit], None


def sweep(domain: str, questions: list[str], *, brand=None, engines=None,
          gl="us", hl="en", use_cache: bool = True) -> dict:
    if not questions:
        return refuse("geo-sweep", "no questions - pass --bank or --from-state")
    st = engines_status()
    if not st["ok"]:
        return refuse("geo-sweep",
                      "no answer engine is reachable, so a sweep would report 'not cited' "
                      "for every question without having asked anything",
                      engines=st["engines"])
    asked, refused = [], []
    for q in questions:
        r = ask(q, domain, brand=brand, engines=engines, gl=gl, hl=hl,
                use_cache=use_cache)
        (refused if r.get("control_failed") else asked).append(r)

    if not asked:
        return refuse("geo-sweep", "every question failed to reach an engine",
                      examples=[r.get("reason") for r in refused][:3])

    ours = _registrable(_host(domain) or domain)
    share: dict[str, int] = {}
    surfaced = cited = 0
    for r in asked:
        for e in r["results"]:
            if e["state"] != "answered" or not e.get("has_answer"):
                continue
            surfaced += 1
            if e["cited"]:
                cited += 1
            # ⚠ ONE VOTE PER ANSWER. Counting every citation lets a single answer
            # that links a domain four times outweigh four answers that each link
            # it once, and `share` then exceeds 1.0 - which it did, at 4.2.
            for d in set(e["cited_domains"]):
                share[d] = share.get(d, 0) + 1
    top = sorted(share.items(), key=lambda kv: -kv[1])
    return {
        "ok": True, "check": "geo-sweep", "domain": ours,
        "questions_asked": len(asked), "questions_refused": len(refused),
        "answers_seen": surfaced,
        "answers_citing_us": cited,
        "citation_rate": (round(cited / surfaced, 3) if surfaced else None),
        "share_of_voice": [{"domain": d, "answers_citing_it": n,
                            "share": round(n / surfaced, 3) if surfaced else None}
                           for d, n in top[:20]],
        "our_rank_in_share_of_voice": (
            next((i + 1 for i, (d, _n) in enumerate(top) if d == ours), None)),
        "uniform_verdict_tell": uniform_verdict(
            [str(e["cited"]) for r in asked for e in r["results"]
             if e["state"] == "answered" and e.get("has_answer")],
            subject="answers"),
        "per_question": [{"query": r["query"], "cited_by": r["cited_by"],
                          "not_cited_by": r["not_cited_by"],
                          "no_answer_surface": r.get("no_answer_surface") or [],
                          "could_not_ask": r["could_not_ask"]} for r in asked],
        "reading": (
            "`answers_seen` counts engine answers that actually EXISTED - a SERP with no "
            "AI Overview is not an answer we were left out of, and it is excluded rather "
            "than counted as a miss. `citation_rate` is over those. `questions_refused` "
            "are questions no engine answered at all: unknown, never zero. "
            "`share` is one vote per ANSWER, not per citation link, so it cannot exceed "
            "1.0. The engine's own domains are excluded and listed in "
            "`engine_furniture_excluded` per result."),
    }


# ------------------------------------------------------- extractable answers
_TAG = re.compile(r"<[^>]+>")
# ⚠ SCRIPT-AWARE ON PURPOSE. The first version tokenised headings with
# `[a-z0-9]{4,}`, which extracts NOTHING from a pure-CJK heading - so the
# "does the lead sentence name its own subject" test could not run, and
# returned False. Measured 2026-09-01: that flagged `/zh/`, the page earning
# 68% of this site's clicks, as not liftable, on a check that never executed.
# "Cannot evaluate" reported as "fails" is the exact error this skill exists to
# prevent, reproduced from scratch in new code.
#
# \w with re.UNICODE covers Cyrillic, Arabic, Devanagari, Greek and Latin.
# CJK and Thai have no word boundaries at all, so a token rule cannot work
# there: those fall back to CHARACTER BIGRAMS, which is what a reader matching
# a Chinese title against a Chinese sentence is actually doing.
_WORD = re.compile(r"\w{3,}", re.UNICODE)
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0e7f]")
_BLOCK = re.compile(r"(?is)<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>")
_P = re.compile(r"(?is)<p[^>]*>(.*?)</p>")
_H1 = re.compile(r"(?is)<h1[^>]*>(.*?)</h1>")


def _sentence_length(sentence: str) -> tuple[int, str, bool]:
    """(size, unit, within_bounds). A liftable sentence is one a reader could be
    quoted; how that is measured depends on the script."""
    stripped = re.sub(r"\s+", "", sentence)
    # ANY CJK/Thai presence switches to characters. A ratio threshold looks
    # principled and is not: the real Japanese lead here is 28% kana/kanji
    # because the product and engine names stay in Latin, so a 0.3 cutoff
    # measured it as 3 "words" and flagged the page.
    if _NOSPACE.search(sentence):
        n = len(stripped)
        return n, "chars", 12 <= n <= 120
    n = len(sentence.split())
    return n, "words", 6 <= n <= 45


# A capitalised multi-word or hyphenated name - "Counter-Strike", "Combat
# Skirmish", "Half-Life". Used as a FALLBACK subject signal.
# Two shapes, because products are named both ways. `Counter-Strike`,
# `Combat Skirmish`, `Half-Life` - and the all-caps abbreviation with an
# optional version, `CS 1.6`, which the first pattern misses entirely and which
# is how half this site's own prose refers to the game.
# ⚠ The all-caps branch needs TWO capitals, so an ordinary sentence-initial word
# ("There", "Organising") cannot satisfy it.
_PROPER = re.compile(
    r"\b[A-Z][a-z]{2,}(?:[-\u2011][A-Z][a-z]+|\s+[A-Z][a-z]{2,})+"
    r"|\b[A-Z]{2,}(?:\s*\d+(?:\.\d+)*)?\b"
    # Model names: M3, AK47, AWP, USP. One capital plus digits is a name, and
    # `The M3 is a corner weapon` was flagged for lacking a subject because
    # neither the 3-character word rule nor the two-capital rule reaches it.
    r"|\b[A-Z][0-9]+\b")


def _names_subject(h1: str, sentence: str, *, extra: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Does the lead sentence stand on its own - does it name what it is about?

    ⚠ THE H1 IS EVIDENCE, NOT THE DEFINITION. A first version matched only
    against h1 tokens and flagged `/landers/retro-shooter` - h1 "A Retro Shooter
    That Never Needed Replacing", lead "Counter-Strike started life in 1999 as a
    mod for Half-Life" - which names its subject about as clearly as a sentence
    can. The h1 was a stylish headline; the rule was measuring vocabulary
    agreement between two elements rather than whether the SENTENCE is
    self-contained, which is the thing an assistant cares about.

    So: h1/title/slug tokens first, then a proper-noun fallback. What still
    fails is a lead that names nothing at all - "There is no purchase, no
    subscription and no account" - which is exactly what should fail.

    Returns (verdict, basis); `not-evaluable` is NOT `False`."""
    if not sentence:
        return False, "not-evaluable"
    if not h1 and not extra:
        return (bool(_PROPER.search(sentence)), "proper-noun") if _PROPER.search(sentence) \
            else (False, "not-evaluable")
    low_h, low_s = h1.lower(), sentence.lower()
    # ⚠ CJK IS CHECKED FIRST, and this ordering is the fix. `\w` with re.UNICODE
    # DOES match CJK - it tokenises a Chinese heading into whole runs between
    # punctuation ("在线玩反恐精英"), which are far too long to appear verbatim in
    # a sentence. So the word branch does not fail to find tokens, it finds
    # useless ones, and a bigram fallback placed after it never runs.
    if _CJK.search(h1):
        bigrams = {h1[i:i + 2] for i in range(len(h1) - 1)
                   if _CJK.match(h1[i]) and _CJK.match(h1[i + 1])}
        if bigrams:
            return any(b in sentence for b in bigrams), "cjk-bigrams"
    sources = " ".join([h1, *extra]).lower()
    words = [w for w in _WORD.findall(sources)
             if not w.isdigit() and not _CJK.search(w)]
    if words and any(w in low_s for w in words):
        return True, "words"
    # FALLBACK: the sentence names a proper entity of its own. This is what
    # rescues a well-written lead under a headline-style h1.
    if _PROPER.search(sentence):
        return True, "proper-noun"
    return (False, "words") if words else (False, "not-evaluable")


def extractable(root: str, limit: int = 400) -> dict:
    """Does each page state its answer in ONE self-contained, liftable sentence?

    The geolook thesis, applied: an assistant lifts a sentence, not a page. A
    definition split across three paragraphs, or one that opens on a pronoun
    with no antecedent, cannot be quoted - so the page can rank and still never
    be the sentence that gets used."""
    d = Path(root)
    if not d.is_dir():
        return refuse("geo-extractable", f"{root} is not a directory")
    files = [p for p in sorted(d.rglob("*"))
             if p.suffix.lower() in (".html", ".htm") and p.is_file()][:limit]
    if not files:
        return refuse("geo-extractable", f"no HTML under {root}")
    rows = []
    for p in files:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        body = _BLOCK.sub(" ", raw)
        h1 = _TAG.sub("", (_H1.search(body) or [None, ""])[1]).strip() if _H1.search(body) else ""
        paras = [re.sub(r"\s+", " ", _TAG.sub(" ", m)).strip() for m in _P.findall(body)]
        lead = next((x for x in paras if len(x) > 60), "")
        pieces = [t.strip() for t in _SENT.split(lead) if t.strip()] if lead else []
        first = pieces[0] if pieces else ""
        words, unit, len_ok = _sentence_length(first)
        # ⚠ If the paragraph carries NO sentence terminator, "the first sentence"
        # IS the whole paragraph and its length says nothing. Thai writes without
        # sentence-ending punctuation at all, so this fires there every time -
        # and reporting 270 chars as "too long to quote" is a statement about
        # Thai orthography, not about the page. Not evaluable, so it does not vote.
        has_terminator = len(pieces) > 1 or bool(lead and _TERM_END.search(lead))
        if not has_terminator:
            len_ok = True
        # A liftable sentence names its subject and stands alone. The two ways it
        # fails are opening on a pronoun with no antecedent, and being so long
        # that quoting it means quoting a paragraph.
        # ⚠ A PRONOUN AND AN EXISTENTIAL ARE NOT THE SAME FAULT.
        # "It does not mean a stripped-down clone" dangles: the quote carries
        # nothing to resolve `It` against. But "There are three ways to get
        # Counter-Strike 1.6 running on a Chromebook" is a perfectly quotable
        # sentence - the existential opener is a construction, not a defect,
        # and it fails only when the sentence ALSO names nothing. Treating the
        # two alike flagged a genuinely good lead on /guides/chromebook.
        slug_words = p.stem.replace("-", " ").replace("_", " ")
        names_subject, subject_basis = _names_subject(h1, first, extra=(slug_words,))
        bare_pronoun = bool(re.match(
            r"(?i)^(it|this|that|they|these|those|he|she)\b", first))
        existential = bool(re.match(r"(?i)^there\s+(is|are|was|were)\b", first))
        starts_pronoun = bare_pronoun or (existential and not names_subject)
        # A subject test that could not run must not vote. `evaluable` is False
        # only when there is no h1 at all, and then the page is judged on the
        # rules that DID run rather than failed on the one that did not.
        ok = (bool(first) and len_ok and not starts_pronoun
              and (names_subject or subject_basis == "not-evaluable"))
        rows.append({"file": str(p.relative_to(d)), "h1": h1[:80],
                     "lead_sentence": first[:200], "length": words, "unit": unit,
                     "liftable": ok, "subject_check": subject_basis,
                     "length_check": "words/chars" if has_terminator else "not-evaluable",
                     "why": None if ok else ", ".join(filter(None, [
                         "no lead paragraph" if not first else "",
                         f"{words} {unit}" if first and not len_ok and has_terminator else "",
                         ("opens on a pronoun with no antecedent" if bare_pronoun else
                          "opens on `there is/are` and names no subject" if starts_pronoun
                          else ""),
                         ("does not name its own subject"
                          if first and not names_subject
                          and subject_basis != "not-evaluable" else ""),
                     ]))})
    bad = [r for r in rows if not r["liftable"]]
    return {
        "ok": True, "check": "geo-extractable", "scanned": len(rows),
        "liftable": len(rows) - len(bad), "not_liftable": len(bad),
        "uniform_verdict_tell": uniform_verdict([str(r["liftable"]) for r in rows],
                                                subject="pages"),
        "worst": bad[:25],
        "reading": ("An assistant lifts a SENTENCE, not a page. A page can rank and still "
                    "never be the sentence that gets quoted. ⚠ This is a shape check, not "
                    "a quality one - a liftable sentence that is wrong still passes."),
    }


# -------------------------------------------------------------------- control
def run_control() -> dict:
    c = Controls("geo-control")

    c.check("host_is_normalised", _host("https://WWW.Example.com/x") == "example.com")
    c.check("registrable_folds_a_subdomain",
            _registrable("a.b.example.com") == "example.com")
    c.check("registrable_does_not_over_fold",
            _registrable("notexample.com") == "notexample.com")

    text = ("Combatskirmish.net runs the game in a browser. Play-cs.com also does. "
            "Nothing here names anyone else.")
    m = _mentions(text, "combatskirmish.net", None)
    c.check("a_sentence_naming_us_is_surfaced", len(m) == 1 and "Combatskirmish" in m[0], str(m))
    c.check("a_sentence_naming_a_competitor_is_not_ours",
            not any("Play-cs" in x for x in m))
    c.check("mentions_are_case_insensitive",
            len(_mentions("visit COMBATSKIRMISH.NET now.", "combatskirmish.net", None)) == 1)
    c.check("no_mention_yields_nothing",
            _mentions("A sentence about nothing in particular.", "x.test", None) == [])

    # ⚠ THE CENTRAL DISTINCTION. `cannot_ask` must never become `not_cited`.
    saved = dict(ENGINES)
    try:
        ENGINES.clear()
        ENGINES["nokey"] = lambda q, **_: {"state": "no_key", "detail": "no key"}
        r = ask("q", "example.com", use_cache=False)
        c.check("an_unaskable_engine_refuses_rather_than_reporting_not_cited",
                r.get("control_failed") is True, str(r)[:160])
        c.check("the_refusal_says_so_in_words",
                "never be reported as 'not cited'" in str(r.get("reason", "")))
        c.check("engines_status_is_not_ok_with_no_usable_engine",
                engines_status()["ok"] is False)
        # ⚠ use_cache=False on EVERY control sweep. The cache is keyed on
        # (engine, gl, hl, query) and knows nothing about which fixture was
        # installed, so a previous control run's stub answers this one's
        # question - `answers_seen` came back 0 for a fixture that plainly
        # returns an answer.
        c.check("a_sweep_with_no_engine_refuses",
                sweep("example.com", ["q"], use_cache=False).get("control_failed") is True)

        ENGINES.clear()
        ENGINES["fake"] = lambda q, **_: {
            "state": "answered", "has_answer": True,
            "text": "Play-cs.com is the usual answer here.",
            "references": [{"domain": "play-cs.com", "url": "https://play-cs.com"},
                           {"domain": "dos.zone", "url": "https://dos.zone"}]}
        miss = ask("q", "example.com", use_cache=False)
        c.check("an_answer_without_us_is_not_cited",
                miss["results"][0]["cited"] is False and miss["not_cited_by"] == ["fake"])
        c.check("competitors_are_named", miss["results"][0]["competitors_cited"] ==
                ["play-cs.com", "dos.zone"])

        ENGINES["fake"] = lambda q, **_: {
            "state": "answered", "has_answer": True, "text": "Example.com does it.",
            "references": [{"domain": "play-cs.com", "url": "https://play-cs.com"},
                           {"domain": "www.example.com", "url": "https://www.example.com/a"}]}
        hit = ask("q", "example.com", use_cache=False)
        c.check("an_answer_citing_us_is_cited", hit["results"][0]["cited"] is True)
        c.check("the_citation_position_is_reported",
                hit["results"][0]["citation_position"] == 2)
        c.check("a_www_citation_still_matches_the_bare_domain",
                hit["cited_by"] == ["fake"])
        c.check("cited_and_not_cited_are_distinguishable",
                miss["results"][0]["cited"] != hit["results"][0]["cited"])

        ENGINES["fake"] = lambda q, **_: {
            "state": "answered", "has_answer": True, "text": "",
            "references": [{"domain": "google.com", "url": "https://google.com/a"},
                           {"domain": "google.com", "url": "https://google.com/b"},
                           {"domain": "play-cs.com", "url": "https://play-cs.com"},
                           {"domain": "play-cs.com", "url": "https://play-cs.com/x"}]}
        ENGINE_FURNITURE["fake"] = {"google.com"}
        f = ask("q", "example.com", use_cache=False)
        c.check("the_engines_own_domain_is_excluded",
                "google.com" not in f["results"][0]["cited_domains"],
                str(f["results"][0]["cited_domains"]))
        c.check("but_the_exclusion_is_reported_not_silent",
                f["results"][0]["engine_furniture_excluded"] == ["google.com"])
        sv = sweep("example.com", ["q1", "q2"], use_cache=False)
        c.check("share_of_voice_is_one_vote_per_answer",
                all(r["share"] <= 1.0 for r in sv["share_of_voice"]),
                str(sv["share_of_voice"]))
        c.check("a_domain_cited_twice_in_one_answer_gets_one_vote",
                next(r["answers_citing_it"] for r in sv["share_of_voice"]
                     if r["domain"] == "play-cs.com") == 2,
                "2 answers, not 4 citations")
        ENGINE_FURNITURE.pop("fake", None)

        # A SERP with no overview is not an answer we were left out of.
        ENGINES["fake"] = lambda q, **_: {"state": "answered", "has_answer": False,
                                          "text": "", "references": []}
        sw = sweep("example.com", ["q1", "q2"], use_cache=False)
        c.check("an_absent_answer_surface_is_not_counted_as_a_miss",
                sw["answers_seen"] == 0 and sw["citation_rate"] is None,
                str({k: sw[k] for k in ("answers_seen", "citation_rate")}))
        one = ask("q", "example.com", use_cache=False)
        c.check("no_answer_surface_is_not_reported_as_not_cited",
                one["not_cited_by"] == [] and one["no_answer_surface"] == ["fake"],
                str({k: one.get(k) for k in ("not_cited_by", "no_answer_surface")}))
        c.check("and_it_is_not_a_refusal_either", one["ok"] is True,
                "the query genuinely has no answer surface - that is a fact about "
                "the query, not an inability to ask")
    finally:
        ENGINES.clear()
        ENGINES.update(saved)
    c.check("the_engine_registry_is_restored", set(ENGINES) == set(saved))

    # Extractability, both directions.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        d = Path(td)
        (d / "good.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>Bunny hopping is the technique of "
            "chaining jumps to keep speed above the engine's run cap.</p></body></html>",
            encoding="utf-8")
        (d / "pronoun.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>It is the thing everyone asks about "
            "first, and it takes a while to learn properly.</p></body></html>",
            encoding="utf-8")
        e = extractable(str(d))
        by = {r["file"]: r for r in ([*e["worst"]] + [])}
        c.check("a_self_contained_lead_is_liftable", e["liftable"] == 1, str(e))
        c.check("a_pronoun_lead_is_not", "pronoun.html" in by, str(sorted(by)))
        c.check("the_reason_is_given",
                "pronoun" in (by.get("pronoun.html") or {}).get("why", ""),
                str((by.get("pronoun.html") or {}).get("why")))
        c.check("extractability_discriminates",
                e["liftable"] == 1 and e["not_liftable"] == 1,
                f"1 of each expected, got {e['liftable']} liftable / "
                f"{e['not_liftable']} not - a checker that passes or fails "
                f"everything is measuring the template, not the prose")
        (d / "long.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>Bunny hopping " + "and more words " * 30
            + "ends here.</p></body></html>", encoding="utf-8")
        e2 = extractable(str(d))
        c.check("an_over_long_lead_is_not_liftable",
                any(r["file"] == "long.html" for r in e2["worst"]),
                "quoting a 60-word sentence means quoting a paragraph")
        c.check("an_empty_directory_refuses",
                extractable(str(d / "nope")).get("control_failed") is True)
    return c.verdict(engines=sorted(ENGINES))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("engines", help="which answer engines can be asked right now")

    a1 = sub.add_parser("ask", help="one question, every reachable engine")
    a1.add_argument("--query", required=True)
    a1.add_argument("--domain", required=True)
    a1.add_argument("--brand")
    a1.add_argument("--engine", action="append")
    a1.add_argument("--gl", default="us")
    a1.add_argument("--hl", default="en")
    a1.add_argument("--no-cache", action="store_true")

    a2 = sub.add_parser("sweep", help="a question bank, and the share of voice it reveals")
    a2.add_argument("--domain", required=True)
    a2.add_argument("--brand")
    a2.add_argument("--bank", help="file with one question per line")
    a2.add_argument("--from-state", action="store_true",
                    help="use the TRACKED keywords in .seo/ as the bank")
    a2.add_argument("--root")
    a2.add_argument("--max", type=int, default=10, help="cap the questions (each costs calls)")
    a2.add_argument("--engine", action="append")
    a2.add_argument("--gl", default="us")
    a2.add_argument("--hl", default="en")
    a2.add_argument("--no-cache", action="store_true")

    a3 = sub.add_parser("extractable", help="is each page's answer one liftable sentence?")
    a3.add_argument("--root", required=True)
    a3.add_argument("--limit", type=int, default=400)

    sub.add_parser("control", help="prove cannot_ask never becomes not_cited")

    a = ap.parse_args()
    if a.action == "engines":
        out = engines_status()
    elif a.action == "control":
        out = run_control()
    elif a.action == "extractable":
        out = extractable(a.root, a.limit)
    elif a.action == "ask":
        out = ask(a.query, a.domain, brand=a.brand, engines=a.engine, gl=a.gl, hl=a.hl,
                  use_cache=not a.no_cache)
    else:
        qs, err = ([], None)
        if a.bank:
            try:
                qs = [x.strip() for x in Path(a.bank).read_text(encoding="utf-8").splitlines()
                      if x.strip() and not x.startswith("#")]
            except OSError as e:
                err = f"cannot read {a.bank}: {e}"
        elif a.from_state:
            qs, err = _bank_from_state(a.root, a.max)
        out = (refuse("geo-sweep", err) if err else
               sweep(a.domain, qs[:a.max], brand=a.brand, engines=a.engine,
                     gl=a.gl, hl=a.hl, use_cache=not a.no_cache))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
