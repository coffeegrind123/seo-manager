#!/usr/bin/env python3
"""Citable sources and entity coverage - the two things information gain needs.

The quality bar requires every guide to carry at least one fact, number or
artifact that exists on no page-1 result, and requires it to be REAL. That
requirement has always been the easiest one to fake, because the honest way to
meet it is work: go and find something nobody on page 1 bothered to look up.

This gives that work a free, keyless starting point.

  sources   OpenAlex + Crossref - peer-reviewed work on a topic, with citation
            counts, DOIs, years and open-access links. A real number from a
            real paper, attributable to a real source, is exactly the shape of
            fact the information-gain rule is asking for.
  entities  Wikidata - resolve a topic to actual entities, with descriptions
            and types. Answers "what IS this thing, formally" without guessing.
  related   Wikipedia `morelike` - the article neighbourhood of a topic. What a
            thorough page on this subject would be expected to mention.
  coverage  Compare a DRAFT against that neighbourhood: which strongly-related
            concepts does the draft never mention? A semantic completeness
            check that costs nothing and needs no model.

⚠ WHAT THIS IS NOT. It does not verify claims, and it cannot tell you whether a
paper's finding is sound, current, or applicable to your niche. It hands you
CANDIDATE sources to read. Citing one of these because this script returned it,
without opening it, is exactly the fabrication the quality bar forbids - it just
has a DOI attached, which makes it worse rather than better.

⚠ `coverage` reports ABSENCE OF A WORD, not absence of a concept. A draft that
covers a topic in different vocabulary scores as a gap. Read every gap before
acting on it; it is a prompt, never a verdict.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import collections
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http  # noqa: E402

MAILTO = "seo-manager@example.com"   # OpenAlex asks for a contact; it gets the polite pool


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402


def _fail(where, r, extra=None):
    return {"ok": False, "source": where, "status": r.get("status"),
            "error": f"{where}: HTTP {r.get('status')}",
            "detail": (r.text() if hasattr(r, "text") else "")[:200],
            "note": "REFUSED, not empty. Never report this as 'no sources exist'.",
            **(extra or {})}


# ------------------------------------------------------------------- sources


def openalex(query, limit=8, since_year=None):
    params = {"search": query, "per-page": str(limit), "mailto": MAILTO}
    if since_year:
        params["filter"] = f"from_publication_date:{since_year}-01-01"
    r = http("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=40, retries=1)
    if not r.ok:
        return _fail("openalex", r)
    results = (r.json() or {}).get("results") or []
    rows = []
    for w in results:
        loc = (w.get("primary_location") or {})
        rows.append({
            "title": w.get("title"),
            "year": w.get("publication_year"),
            "cited_by": w.get("cited_by_count"),
            "doi": w.get("doi"),
            "open_access_url": (w.get("open_access") or {}).get("oa_url"),
            "venue": (loc.get("source") or {}).get("display_name"),
            "type": w.get("type"),
        })
    rows.sort(key=lambda x: -(x.get("cited_by") or 0))
    return {"ok": True, "source": "openalex", "query": query, "count": len(rows), "results": rows,
            "empty_means": "OpenAlex answered with no matching work. A real answer - this "
                           "topic has no indexed literature - not a failed read."}


def crossref(query, limit=8):
    r = http("https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query": query, "rows": str(limit), "select": "title,DOI,issued,is-referenced-by-count,container-title,URL"}),
        timeout=40, retries=1)
    if not r.ok:
        return _fail("crossref", r)
    items = ((r.json() or {}).get("message") or {}).get("items") or []
    rows = [{
        "title": (i.get("title") or [None])[0],
        "year": ((i.get("issued") or {}).get("date-parts") or [[None]])[0][0],
        "cited_by": i.get("is-referenced-by-count"),
        "doi": i.get("DOI"),
        "url": i.get("URL"),
        "venue": (i.get("container-title") or [None])[0],
    } for i in items]
    rows.sort(key=lambda x: -(x.get("cited_by") or 0))
    return {"ok": True, "source": "crossref", "query": query, "count": len(rows), "results": rows}


def _coverage(draft_text: str, titles: list[str]) -> dict:
    """The pure half of `coverage`, extracted so it can be controlled offline.

    Kept identical in behaviour - `cmd_coverage` now calls it - because a
    control that exercises a REIMPLEMENTATION of the logic proves nothing about
    the logic that actually runs."""
    draft_words = set(WORD.findall(draft_text.lower()))
    covered, gaps = [], []
    for title in titles:
        toks = [w for w in WORD.findall((title or "").lower())
                if w not in STOP and len(w) > 3]
        if not toks:
            continue
        present = sum(1 for w in toks if w in draft_words)
        row = {"concept": title, "matched_tokens": present, "tokens": len(toks)}
        (covered if present else gaps).append(row)
    return {"covered": covered, "gaps": gaps}


def run_control() -> dict:
    """Prove the coverage matcher discriminates, offline.

    The failure that matters is a matcher that returns ZERO gaps - which reads
    as "the draft covers the whole topic" and is exactly what an empty
    neighbourhood or a broken tokeniser produces."""
    c = Controls("factcheck-control")
    draft = ("The bomb site is covered from the doors. Recoil resets between "
             "bursts, and the defuse kit halves the timer.")
    titles = ["Bomb defusal", "Recoil control", "Economy management", "Smoke grenade"]

    r = _coverage(draft, titles)
    names = {g["concept"] for g in r["gaps"]}
    c.check("a_concept_the_draft_names_is_covered",
            "Recoil control" in {x["concept"] for x in r["covered"]}, str(r["covered"]))
    c.check("a_concept_the_draft_never_names_is_a_gap",
            "Economy management" in names, str(names))
    c.check("the_matcher_is_not_returning_everything_as_a_gap",
            0 < len(r["gaps"]) < len(titles), f"{len(r['gaps'])} of {len(titles)}")
    c.check("an_empty_neighbourhood_yields_no_false_coverage",
            _coverage(draft, []) == {"covered": [], "gaps": []},
            "zero gaps from zero input must never read as full coverage")
    c.check("stopwords_alone_do_not_make_a_concept",
            _coverage(draft, ["the and of"])["gaps"] == [])
    c.check("an_empty_draft_makes_everything_a_gap",
            len(_coverage("", titles)["gaps"]) == len(titles))
    c.check("matching_is_case_insensitive",
            _coverage("RECOIL", ["Recoil control"])["gaps"] == [])

    # --- claims: extraction, citation proximity, and the three verification states
    draft = ("Intro. According to the Stanford Report, 47% of marketers ship late "
             "[source](https://good.example/s). Revenue reached $3.2 billion last year "
             "[report](https://good.example/r). Uptime was 99.5% "
             "[dead](https://dead.example/x). " + ("Filler sentence here. " * 12) +
             "\n\nA far-away claim: 12 percent of teams churn. " + ("More filler. " * 20) +
             "\n\n```\nrate = '88%'\n```\n`inline 77%` too.")
    claims = extract_claims(draft)
    texts = [x["text"] for x in claims]
    c.check("a_percentage_is_a_claim", "47%" in texts, str(texts))
    c.check("a_named_source_is_a_claim",
            any(x["kind"] == "authority" and x["named_source"] == "Stanford Report" for x in claims))
    c.check("a_number_inside_a_code_block_is_not_a_claim", "88%" not in texts)
    c.check("a_number_inside_inline_code_is_not_a_claim", "77%" not in texts)
    c.check("a_claim_near_a_link_is_cited",
            next(x for x in claims if x["text"] == "47%")["citation"] == "https://good.example/s")
    c.check("a_claim_far_from_any_link_is_uncited",
            next(x for x in claims if x["text"] == "12 percent")["citation"] is None)
    c.check("a_previous_sentences_link_is_not_borrowed",
            extract_claims("Per [x](https://a.example/) it rained. Sales rose 41% after. " * 3)
            [0]["citation"] is None,
            "a link that closed the previous sentence is not this claim's citation")
    c.check("a_footnote_right_after_the_full_stop_still_counts",
            extract_claims("Sales rose 41%.[^1] Then more prose followed here. " * 3)
            [0]["citation"] == "[^1]")
    c.check("a_same_sentence_link_before_the_claim_still_counts",
            extract_claims("[Gartner](https://a.example/) puts churn at 41% overall. " * 3)
            [0]["citation"] == "https://a.example/")
    c.check("a_bare_year_is_not_a_quantity_claim",
            "2025" not in [x["text"] for x in extract_claims("In 2025 we shipped. " * 10)])

    pages = {
        "https://good.example/s": {"status": 200, "body": ("<html><body>" + "x " * 100 +
                                   "The Stanford Report shows 47% of marketers ship late."
                                   "</body></html>").encode()},
        "https://good.example/r": {"status": 200, "body": ("<html><body>" + "y " * 100 +
                                   "Revenue reached $2.9 billion.</body></html>").encode()},
        "https://dead.example/x": {"status": 404, "body": b"", "error": "HTTP 404"},
    }

    class _R(dict):
        def text(self):
            return (self.get("body") or b"").decode()
    verify_claims(claims, fetch=lambda u: _R(pages.get(u) or {"status": None, "error": "no route"}))
    st = {x["text"]: x["verification"]["state"] for x in claims}
    c.check("a_number_present_in_the_cited_page_verifies", st.get("47%") == "verified", str(st))
    c.check("a_named_source_present_in_the_page_verifies",
            st.get("According to the Stanford Report") == "verified")
    c.check("a_number_absent_from_a_read_page_is_not_in_source",
            st.get("$3.2 billion") == "not_in_source")
    c.check("an_unreachable_source_is_unverified_never_false", st.get("99.5%") == "unverified")
    c.check("an_uncited_claim_stays_uncited", st.get("12 percent") == "uncited")
    c.check("the_three_verification_states_are_distinct",
            len({st.get("47%"), st.get("$3.2 billion"), st.get("99.5%")}) == 3)
    c.check("each_source_is_fetched_once",
            len(verify_claims(claims, fetch=lambda u: _R(pages[u]))) == 3)
    c.check("a_thousands_separator_does_not_hide_a_match",
            "3200" in _norm_number("3,200") and "47%" in _norm_number("47%"))
    return c.verdict(note="the matcher is proven offline; whether OpenAlex/Crossref/"
                          "Wikipedia ANSWER is a separate question - `providers.py status` "
                          "is the live probe for that")


def cmd_sources(a):
    oa, cr = openalex(a.query, a.limit, a.since_year), crossref(a.query, a.limit)
    merged, seen = [], set()
    for row in (oa.get("results") or []) + (cr.get("results") or []):
        doi = (row.get("doi") or "").lower().replace("https://doi.org/", "")
        key = doi or (row.get("title") or "").lower()[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
    merged.sort(key=lambda x: -(x.get("cited_by") or 0))
    return {
        "ok": oa.get("ok") or cr.get("ok"),
        "query": a.query,
        "providers": {"openalex": {"ok": oa.get("ok"), "count": oa.get("count", 0),
                                   "error": oa.get("error")},
                      "crossref": {"ok": cr.get("ok"), "count": cr.get("count", 0),
                                   "error": cr.get("error")}},
        "count": len(merged),
        "results": merged[: a.limit],
        "how_to_use": "CANDIDATES to read, not citations to paste. Open the source, check the "
                      "number is what you think it is and that it still holds, then cite it "
                      "with the DOI. A cited paper nobody opened is a fabricated fact with a "
                      "reference attached.",
    }


# ------------------------------------------------------------------ entities


def cmd_entities(a):
    r = http("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "wbsearchentities", "search": a.topic, "language": a.lang,
         "format": "json", "limit": str(a.limit)}), timeout=30, retries=1)
    if not r.ok:
        return _fail("wikidata", r)
    hits = (r.json() or {}).get("search") or []
    return {
        "ok": True, "source": "wikidata", "topic": a.topic, "count": len(hits),
        "entities": [{"id": h.get("id"), "label": h.get("label"),
                      "description": h.get("description"),
                      "url": "https:" + h["url"] if str(h.get("url", "")).startswith("//") else h.get("url")}
                     for h in hits],
        "empty_means": "Wikidata knows no entity by this name. A real answer - controlled "
                       "against a nonsense string, which also returns zero.",
    }


def _morelike(topic, limit, lang):
    r = http(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": f"morelike:{topic}",
         "srlimit": str(limit), "srnamespace": "0", "format": "json"}), timeout=30, retries=1)
    if not r.ok:
        return None, _fail("wikipedia-morelike", r)
    return ((r.json() or {}).get("query") or {}).get("search") or [], None


def cmd_related(a):
    hits, err = _morelike(a.topic, a.limit, a.lang)
    if err:
        return err
    return {
        "ok": True, "source": "wikipedia-morelike", "topic": a.topic, "count": len(hits),
        "related": [{"title": h.get("title"), "words": h.get("wordcount")} for h in hits],
        "empty_means": "no neighbourhood found - usually the topic string does not match an "
                       "article title. Resolve it first with trendfeeds.py wiki.",
        "how_to_use": "the article neighbourhood of a subject: what a thorough page would be "
                      "expected to touch. Use it to find the angle page 1 missed, not to pad "
                      "a draft with keywords.",
    }


WORD = re.compile(r"[a-z0-9][a-z0-9'\-]+")
STOP = {"the", "and", "for", "with", "that", "this", "from", "have", "has", "are", "was",
        "list", "of", "in", "on", "to", "a", "an", "history", "index"}


def cmd_coverage(a):
    try:
        draft = Path(a.draft).read_text(encoding="utf-8", errors="replace").lower()
    except OSError as e:
        return {"ok": False, "error": f"cannot read draft: {e}"}
    hits, err = _morelike(a.topic, a.limit, a.lang)
    if err:
        return err
    if not hits:
        return {"ok": False, "error": "no article neighbourhood for this topic",
                "hint": "resolve the exact title first: trendfeeds.py wiki --topic '<topic>'"}

    r = _coverage(draft, [h.get("title") or "" for h in hits])
    covered, gaps = r["covered"], r["gaps"]
    return {
        "ok": True, "topic": a.topic, "draft": a.draft,
        "neighbourhood": len(hits),
        "mentioned": len(covered), "not_mentioned": len(gaps),
        "coverage_pct": round(100 * len(covered) / (len(covered) + len(gaps))) if (covered or gaps) else None,
        "gaps": gaps[:25],
        "warning": "this matches WORDS, not meaning. A draft that covers a concept in other "
                   "vocabulary is scored as a gap, and a draft that name-drops a word without "
                   "explaining it is scored as covered. Read each gap and decide; never let a "
                   "coverage number drive an edit on its own, and never 'fix' a gap by "
                   "inserting the phrase - that is the template convergence the sameness gate "
                   "exists to catch.",
    }


# -------------------------------------------------------------------- claims
# The instrument behind the Non-negotiable "a source you cannot cite, you have
# not verified". `sources` hands out candidates; nothing checked that a number
# the draft went on to state is actually IN the page the draft cites for it.
# Mapped from `claude-seo/scripts/content_verify.py` (claim extraction with a
# citation-proximity check), and extended with the half that matters: `--fetch`
# opens each cited source and looks for the claimed number in its text.
#
# THREE STATES per cited claim, never two:
#   verified          the number (or the named source) appears in the fetched page
#   not_in_source     the page was READ and the number is not in it - the finding
#   unverified        the page could not be fetched or yielded no text - unknown
# A fetch failure is not a false claim. Collapsing it would make every dead link
# a fabrication and every paywall a lie.

_CLAIM_PATTERNS = [
    ("statistic", re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|percent\b|per cent\b)", re.I)),
    ("money", re.compile(r"[$\u20ac\u00a3]\s?\d+(?:[.,]\d+)*\s*(?:million|billion|trillion|[kmb])?\b",
                         re.I)),
    ("quantity", re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:million|billion|trillion|thousand)\b", re.I)),
    ("quantity", re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")),
    ("comparative", re.compile(r"\b(?:\d+(?:\.\d+)?x|twice|three times|ten times)\s+"
                               r"(?:as\s+\w+|more|less|faster|slower|higher|lower|larger|smaller)\b",
                               re.I)),
    ("authority", re.compile(r"\b(?i:according to)\s+(?:(?i:a|an|the)\s+)?"
                             r"([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,4})", re.U)),
    ("authority", re.compile(r"\b([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,3})\s+"
                             r"(?:reports?|found|estimates?|measured|says|said|shows?)\s+that\b")),
]
_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)|<?(https?://[^\s)>\]]+)>?|\[\^?\d+\]")
_CODE = [re.compile(r"(?s)```.*?```"), re.compile(r"(?s)~~~.*?~~~"), re.compile(r"`[^`\n]*`")]
CITATION_WINDOW = 200
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_TAGS = re.compile(r"(?s)<(script|style|noscript)[^>]*>.*?</\1>|<[^>]+>")


def _blank_code(text: str) -> str:
    out = text
    for rx in _CODE:
        out = rx.sub(lambda m: re.sub(r"\S", " ", m.group(0)), out)
    return out


def extract_claims(text: str) -> list[dict]:
    """Verifiable claims in prose, each with the nearest citation marker."""
    masked = _blank_code(text)
    lines = [0]
    for ln in masked.splitlines():
        lines.append(lines[-1] + len(ln) + 1)

    def line_of(off):
        lo, hi = 0, len(lines) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if lines[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    links = [(m.start(), m.end(), m.group(1) or m.group(2) or m.group(0))
             for m in _LINK.finditer(masked)]
    seen, claims = set(), []
    for kind, rx in _CLAIM_PATTERNS:
        for m in rx.finditer(masked):
            span = (m.start(), m.end())
            if any(a <= span[0] < b for a, b in seen):
                continue
            # a bare year is a date, not a claim
            frag = m.group(0)
            if kind == "quantity" and re.fullmatch(r"(?:19|20)\d\d", frag.strip()):
                continue
            seen.add(span)
            # A citation FOLLOWS its claim by convention, so the nearest link
            # after the claim wins; a link before it is the fallback. Measured
            # while writing the control: a centre-distance rule attached the
            # second claim in a paragraph to the FIRST claim's source.
            def _same_sentence_after(a):
                gap = masked[m.end():a]
                return (not re.search(r"[.!?\n]", gap)) or re.fullmatch(r"[.!?\s]{0,3}", gap)
            after = sorted((a - m.end(), url) for a, b, url in links
                           if m.end() <= a <= m.end() + CITATION_WINDOW
                           and _same_sentence_after(a))
            # ...and only within the SAME sentence: a link that closed the
            # previous sentence is that sentence's citation, and borrowing it
            # turns an uncited claim into a not_in_source against the wrong page.
            before = sorted((m.start() - b, url) for a, b, url in links
                            if m.start() - CITATION_WINDOW <= b <= m.start()
                            and not re.search(r"[.!?\n]", masked[b:m.start()]))
            near = after or before
            ctx = masked[max(0, m.start() - 80):m.end() + 80]
            claims.append({
                "kind": kind, "text": re.sub(r"\s+", " ", frag).strip(),
                "line": line_of(m.start()), "offset": m.start(),
                "context": re.sub(r"\s+", " ", ctx).strip(),
                "named_source": (m.group(1).strip() if kind == "authority" and m.groups() else None),
                "numbers": _NUM.findall(frag),
                "citation": (near[0][1] if near else None),
                "citation_is_url": bool(near and near[0][1].startswith("http")),
            })
    claims.sort(key=lambda c: c["offset"])
    return claims


def _norm_number(n: str) -> set[str]:
    """The spellings a page might use for one number. `47%` ~ `47 percent`,
    `3,200` ~ `3200`, `3.2` stays `3.2`. Deliberately small - the goal is to
    not miss an honest restatement, not to accept any digit on the page."""
    bare = n.replace(",", "")
    out = {n, bare}
    if "." in bare and bare.endswith("0"):
        out.add(bare.rstrip("0").rstrip("."))
    return out


def _page_text(r) -> str:
    body = r.text() if hasattr(r, "text") else ""
    return re.sub(r"\s+", " ", html_unescape(_TAGS.sub(" ", body)))


def html_unescape(s: str) -> str:
    import html
    return html.unescape(s)


def verify_claims(claims: list[dict], *, fetch=None, timeout: int = 25) -> dict:
    """Open each cited URL ONCE and look for the claim's numbers / named source."""
    fetch = fetch or (lambda u: http(u, timeout=timeout, retries=1,
                                     ua="Mozilla/5.0 (compatible; seo-manager/1.0)"))
    pages: dict[str, dict] = {}
    for c in claims:
        url = c.get("citation") if c.get("citation_is_url") else None
        if not url:
            c["verification"] = {"state": "uncited", "reason": "no citation within "
                                                               f"{CITATION_WINDOW} chars"}
            continue
        if url not in pages:
            r = fetch(url)
            ok = bool(r) and r.get("status") == 200
            text = _page_text(r) if ok else ""
            pages[url] = {"status": r.get("status") if r else None, "ok": ok,
                          "text": text, "chars": len(text),
                          "error": None if ok else (r.get("error") if r else "no response")}
        pg = pages[url]
        if not pg["ok"] or pg["chars"] < 200:
            c["verification"] = {"state": "unverified", "url": url,
                                 "reason": (f"source could not be read (HTTP {pg['status']}, "
                                            f"{pg['error'] or pg['chars']} chars) - UNKNOWN, "
                                            f"not false")}
            continue
        low = pg["text"].lower()
        if c["kind"] == "authority" and c.get("named_source"):
            hit = c["named_source"].lower() in low
            c["verification"] = {"state": "verified" if hit else "not_in_source", "url": url,
                                 "reason": (f"named source {c['named_source']!r} "
                                            f"{'appears' if hit else 'does not appear'} in the page")}
            continue
        nums = c.get("numbers") or []
        if not nums:
            c["verification"] = {"state": "unverified", "url": url,
                                 "reason": "claim carries no number to look for"}
            continue
        missing = [n for n in nums if not any(v in low for v in _norm_number(n))]
        c["verification"] = {"state": "verified" if not missing else "not_in_source",
                             "url": url,
                             "reason": ("every number appears in the source text" if not missing
                                        else f"{missing} not found in {pg['chars']} chars of source "
                                             f"text - the page was READ; this is the finding")}
    return pages


def cmd_claims(a):
    try:
        text = Path(a.draft).read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "check": "factcheck-claims", "error": f"cannot read draft: {e}"}
    if len(_blank_code(text).split()) < 50:
        return {"ok": False, "check": "factcheck-claims", "control_failed": True,
                "reason": "draft has under 50 words of prose - nothing to check, and a clean "
                          "report here would read as 'every claim verified'"}
    claims = extract_claims(text)
    pages = verify_claims(claims) if a.fetch else {}
    if not a.fetch:
        for c in claims:
            c["verification"] = ({"state": "uncited", "reason": "no citation within "
                                                                f"{CITATION_WINDOW} chars"}
                                 if not c.get("citation") else
                                 {"state": "cited_unchecked",
                                  "reason": "pass --fetch to open the source and look for the number"})
    states = collections.Counter(c["verification"]["state"] for c in claims)
    return {
        "ok": True, "check": "factcheck-claims", "draft": a.draft,
        "claims": len(claims),
        "by_state": dict(states),
        "uncited": [c for c in claims if c["verification"]["state"] == "uncited"],
        "not_in_source": [c for c in claims if c["verification"]["state"] == "not_in_source"],
        "unverified": [c for c in claims if c["verification"]["state"] == "unverified"],
        "verified": [{"text": c["text"], "line": c["line"], "url": c["verification"].get("url")}
                     for c in claims if c["verification"]["state"] == "verified"],
        "cited_unchecked": [{"text": c["text"], "line": c["line"], "citation": c["citation"]}
                            for c in claims if c["verification"]["state"] == "cited_unchecked"],
        "sources_fetched": {u: {k: v for k, v in p.items() if k != "text"} for u, p in pages.items()},
        "reading": ("`uncited` is the work list the information-gain rule already implies. "
                    "`not_in_source` is the one that matters: the cited page was READ and the "
                    "number is not there - either the citation is wrong or the number is. "
                    "`unverified` is UNKNOWN (dead link, paywall, JS-only page), never a "
                    "finding against the claim. A number can be restated ('47 percent', "
                    "'0.47') in ways this does not recognise - open the page before calling "
                    "a not_in_source a fabrication. No score, on purpose."),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("control", help="prove the coverage matcher discriminates (offline)").set_defaults(
        fn=lambda a: run_control())

    s = sub.add_parser("sources", help="OpenAlex + Crossref - citable work on a topic")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--since-year", type=int, help="only work published from this year on")
    s.set_defaults(fn=cmd_sources)

    e = sub.add_parser("entities", help="Wikidata entity resolution for a topic")
    e.add_argument("--topic", required=True)
    e.add_argument("--limit", type=int, default=8)
    e.add_argument("--lang", default="en")
    e.set_defaults(fn=cmd_entities)

    r = sub.add_parser("related", help="Wikipedia article neighbourhood of a topic")
    r.add_argument("--topic", required=True)
    r.add_argument("--limit", type=int, default=15)
    r.add_argument("--lang", default="en")
    r.set_defaults(fn=cmd_related)

    c = sub.add_parser("coverage", help="which related concepts a draft never mentions")
    c.add_argument("--draft", required=True)
    c.add_argument("--topic", required=True)
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--lang", default="en")
    c.set_defaults(fn=cmd_coverage)

    k = sub.add_parser("claims", help="numeric/authority claims in a draft: uncited ones, "
                                      "and with --fetch whether the cited page carries the number")
    k.add_argument("--draft", required=True)
    k.add_argument("--fetch", action="store_true",
                   help="open every cited URL once and look for the claimed number in its text")
    k.set_defaults(fn=cmd_claims)

    a = p.parse_args()
    out = a.fn(a)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
