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
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http  # noqa: E402

MAILTO = "seo-manager@example.com"   # OpenAlex asks for a contact; it gets the polite pool


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

    draft_words = set(WORD.findall(draft))
    covered, gaps = [], []
    for h in hits:
        title = h.get("title") or ""
        toks = [w for w in WORD.findall(title.lower()) if w not in STOP and len(w) > 3]
        if not toks:
            continue
        present = sum(1 for w in toks if w in draft_words)
        row = {"concept": title, "matched_tokens": present, "tokens": len(toks)}
        (covered if present else gaps).append(row)
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


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

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

    a = p.parse_args()
    out = a.fn(a)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
