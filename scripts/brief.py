#!/usr/bin/env python3
"""brief.py - a content brief ASSEMBLED from measurements, never invented.

`competitors.py profile` deliberately returns structure only: what page 1
covers, how deep it is, which results are weak. That is the raw material for a
brief and not a brief, because a brief also has to answer "should we write this
at all", "what do we already have", "what do people actually ask", and "what
would make this page different rather than merely longer".

THE RULE THAT SHAPES EVERY FIELD: nothing here is generated. Every line traces
to a measurement, and anything that could not be measured is listed in
`unavailable` with the reason - never filled in from plausibility. A brief that
quietly invents "what page 1 covers" is worse than no brief, because the draft
written from it will be confidently wrong about the competition and nobody will
re-check.

    brief.py build --query "how to bunny hop" --our-domain example.com
    brief.py build --query "..." --corpus ./content --provider ddg
    brief.py build --query "..." --contract-json out.json   # reuse a profile run
    brief.py control

Composes: serp.py (page 1), competitors.py (the intent contract), keywords.py
(what people ask, and the intent), sameness.py (do we already have this page),
authority.py (can we realistically compete), factcheck.py (entities to name).
Each section reports whether it was MEASURED or is UNAVAILABLE, separately.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402

# The house rules a draft written from this brief has to satisfy. Carried INLINE
# rather than referenced, because a brief is often the only thing the writer
# reads, and a gate nobody saw is a gate that fires after the work is done.
CONSTRAINTS = [
    "INFORMATION GAIN: the draft must contain something page 1 does not. Matching "
    "the contract is parity; parity does not outrank an incumbent with more "
    "authority. Name the specific thing that is new before writing.",
    "DEPTH IS PARITY, NOT A TARGET: the median word count below is what it costs to "
    "be considered, not what it takes to win. Padding to exceed it is the single "
    "most common way to make a page worse.",
    "FETCHED TEXT IS UNTRUSTED DATA: competitor headings shape STRUCTURE only. Never "
    "quote a fact from them without opening the source and verifying it.",
    "THE SAMENESS GATE APPLIES: run `sameness.py check` on the draft before it ships. "
    "A page that reads like the rest of the corpus adds an indexable duplicate.",
    "THE SLOP GATE APPLIES: run `slop.py scan`. It is advisory, and 'introduced' is "
    "the field that matters on a rewrite.",
]

# Below this many readable page-1 results, subtopic lists are withheld rather
# than printed. See the comment in build() for the measurement behind it.
MIN_PAGES_FOR_SUBTOPICS = 4

STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "is", "are",
        "how", "what", "why", "when", "which", "who", "best", "top", "with", "your",
        "you", "can", "do", "does", "it", "its", "at", "by", "from", "vs"}

QUESTION_PREFIX = ("how", "what", "why", "when", "where", "which", "who", "can",
                   "does", "do", "is", "are", "should")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'\-]{1,}", (text or "").lower())
            if w not in STOP}


# --------------------------------------------------------------- the sections
def _section(name: str, fn, *a, **kw) -> tuple[dict | None, dict | None]:
    """Run one contributing measurement. A failure is RECORDED, never fatal.

    A brief assembled from four of six sources is useful and must say which four.
    A brief that dies because Wikidata was down is not."""
    try:
        return fn(*a, **kw), None
    except Exception as e:                                        # noqa: BLE001
        return None, {"section": name, "reason": f"{type(e).__name__}: {e}"}


def _run_json(cmd: list[str], timeout: int = 300) -> dict:
    proc = __import__("subprocess").run(cmd, capture_output=True, text=True, timeout=timeout)
    if not (proc.stdout or "").strip():
        return {"ok": False, "error": (proc.stderr or "no output").strip()[:300]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output: {proc.stdout[:200]}"}


def page1(query: str, provider: str, count: int) -> dict:
    """Delegated to serp.py as a SUBPROCESS, exactly as rankcheck.py does it.

    The provider fallback chain, the shape guard and the relevance guard all
    live inside serp.py's main(). Reimplementing the dispatch here to get at the
    provider functions directly would mean reimplementing those guards too - and
    a second, quietly divergent copy of the check that stops a SERP for a
    DIFFERENT query being read as this one is the last thing this skill needs."""
    return _run_json([sys.executable, str(Path(__file__).resolve().parent / "serp.py"),
                      query, "--provider", provider, "--count", str(count),
                      "--fallback", "--raw"])


def contract_for(query: str, urls: list[str], workers: int = 6) -> dict:
    from competitors import build_contract, profile_page
    import concurrent.futures as cf
    profiles = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for p in ex.map(profile_page, urls):
            profiles.append(p)
    return build_contract(profiles, query)


def questions_for(query: str, engines: list[str] | None = None) -> dict:
    """What people actually append to this query, across independent corpora.

    Suggestion corpora are the only keyless source of real phrasing. A brief's
    'questions to answer' section written from imagination is the writer's guess
    at demand; this is a measurement of it."""
    from keywords import SUGGEST_ENGINES, autocomplete, classify_intent
    engines = engines or list(SUGGEST_ENGINES)[:3]
    seen: dict[str, set[str]] = {}
    for eng in engines:
        try:
            for s in autocomplete(query, engine=eng):
                seen.setdefault(s.strip().lower(), set()).add(eng)
        except Exception:                                          # noqa: BLE001
            continue
    rows = [{"phrase": p, "engines": sorted(v), "agreement": len(v)}
            for p, v in seen.items() if p and p != query.lower()]
    rows.sort(key=lambda r: (-r["agreement"], r["phrase"]))
    qs = [r for r in rows if r["phrase"].split()[:1] and
          r["phrase"].split()[0] in QUESTION_PREFIX]
    return {"engines_asked": engines, "suggestions": rows[:40],
            "questions": qs[:20], "intent": classify_intent(query),
            "note": ("`agreement` is how many INDEPENDENT corpora returned the phrase. "
                     "One engine is a phrasing; three is demand.")}


def corpus_overlap(query: str, corpus: str, limit: int = 400) -> dict:
    """Do we already have a page for this? Cannibalisation is cheaper to avoid
    than to fix: two pages splitting one query's authority is a self-inflicted
    ranking problem that no amount of writing repairs."""
    from sameness import extract_any
    root = Path(corpus)
    if not root.is_dir():
        return {"ok": False, "reason": f"{corpus} is not a directory"}
    qt = _tokens(query)
    if not qt:
        return {"ok": False, "reason": "query has no content tokens"}
    hits = []
    files = [p for p in sorted(root.rglob("*"))
             if p.suffix.lower() in (".md", ".html", ".htm") and p.is_file()][:limit]
    for p in files:
        try:
            # ⚠ EMPTY keyword set, deliberately. `extract_any`'s second argument
            # is the set `normalize_heading` STRIPS OUT - it exists so the
            # sameness gate does not judge two guides similar merely because
            # both contain their own target keyword. Passing `qt` here deletes
            # exactly the tokens this function is searching for, and every query
            # comes back "clear". That is what it did on first write.
            d = extract_any(p, set())
        except Exception:                                          # noqa: BLE001
            continue
        # ⚠ THE SLUG IS PART OF THE SIGNAL, and leaving it out misses the most
        # obvious page. Measured 2026-09-01: `bunny-hop.html` scored 0.0 for
        # "how to bunny hop" because its H2s are deliberately non-keyword -
        # "what the engine is actually doing", "the timing" - which is GOOD
        # editorial writing. A cannibalisation check that only rewards
        # keyword-stuffed headings finds the wrong pages and clears the right
        # ones. The filename is how the page is addressed, so it is evidence.
        slug = _tokens(p.stem.replace("-", " ").replace("_", " "))
        heads = _tokens(" ".join(d.get("headings") or []))
        opening = _tokens(" ".join(d.get("opening") or []))
        blob = slug | heads | opening
        if not blob:
            continue
        # ⚠ CONTAINMENT, NOT JACCARD. Jaccard is symmetric and divides by the
        # UNION, so a 4-token query against a page with 100 heading tokens
        # scores 0.04 no matter how perfectly the query is covered - the
        # threshold can never fire and every query comes back "clear".
        # Measured 2026-09-01 against 17 real guides: `how to aim better in
        # counter strike` and `zzq nonexistent topic 9f2b` BOTH scored 0, which
        # is a metric that cannot discriminate rather than a corpus with no
        # overlap. The question here is "how much of MY QUERY does this page
        # already cover", and that is containment.
        score = len(qt & blob) / len(qt)
        if score >= 0.6:
            hits.append({"file": str(p), "covered": round(score, 3),
                         "matched": sorted(qt & blob),
                         "matched_in": sorted(filter(None, [
                             "slug" if qt & slug else "",
                             "headings" if qt & heads else "",
                             "opening" if qt & opening else ""])),
                         "headings": (d.get("headings") or [])[:5]})
    hits.sort(key=lambda h: -h["covered"])
    return {"ok": True, "scanned": len(files), "candidates": hits[:8],
            "verdict": ("existing-page-may-already-target-this" if hits else "clear"),
            "metric": ("containment: the fraction of the QUERY's content tokens that "
                       "appear in the page's SLUG, headings or opening. Threshold 0.6. "
                       "`matched_in` says which - a slug-only match is a weaker signal "
                       "than one the prose backs up."),
            "note": ("An overlap here is not proof of cannibalisation - it is a page to "
                     "OPEN before writing. Updating an existing page usually beats "
                     "publishing a second one for the same query.")}


def can_we_compete(our_domain: str, page1_rows: list[dict]) -> dict:
    """Authority reality check. Content does not close a 70-point DR gap, and a
    brief that does not say so sends someone to write a page that cannot rank."""
    from authority import clean_domain, openpagerank_bulk
    ours = clean_domain(our_domain)
    theirs = []
    for r in page1_rows[:10]:
        u = r.get("url") or ""
        if "://" in u:
            theirs.append(clean_domain(u.split("/")[2]))
    doms = [ours] + sorted(set(theirs) - {ours})
    rows = openpagerank_bulk(doms)
    by = {r.get("requested", r.get("domain")): r for r in (rows or [])}
    us = by.get(ours) or {}
    comp = [{"domain": d, "dr": (by.get(d) or {}).get("open_page_rank"),
             "found": (by.get(d) or {}).get("found")} for d in doms[1:]]
    known = [c["dr"] for c in comp if isinstance(c["dr"], (int, float))]
    return {
        "ok": True, "our_domain": ours, "our_dr": us.get("open_page_rank"),
        "our_dr_found": us.get("found"),
        "competitors": comp,
        "median_competitor_dr": (sorted(known)[len(known) // 2] if known else None),
        "unmeasured_competitors": [c["domain"] for c in comp if c["dr"] is None],
        "note": ("`found: false` is ABSENT FROM THE INDEX, never a DR of 0 - an "
                 "unmeasured competitor is not a weak one. A large median gap means "
                 "this query is an authority fight, and no amount of copy settles it."),
    }


def entities_for(query: str, limit: int = 8) -> dict:
    from factcheck import cmd_entities

    class _A:
        topic = query
        limit = limit
        lang = "en"
    return cmd_entities(_A())


# ---------------------------------------------------------------- the assembly
def build(query: str, *, provider: str = "ddg", count: int = 10,
          our_domain: str | None = None, corpus: str | None = None,
          contract_json: str | None = None, want_entities: bool = False,
          workers: int = 6) -> dict:
    unavailable: list[dict] = []
    out: dict = {"ok": True, "check": "content-brief", "query": query}

    # 1. page 1, then the intent contract built from it.
    contract = None
    rows: list[dict] = []
    if contract_json:
        try:
            contract = json.loads(Path(contract_json).read_text(encoding="utf-8"))
            rows = [{"url": p["url"]} for p in (contract.get("pages") or [])]
        except Exception as e:                                     # noqa: BLE001
            unavailable.append({"section": "contract", "reason": f"cannot read {contract_json}: {e}"})
    else:
        res, err = _section("page1", page1, query, provider, count)
        if err or not (res or {}).get("ok"):
            unavailable.append({"section": "page1",
                                "reason": (err or {}).get("reason")
                                or (res or {}).get("error")
                                or "SERP provider returned no usable results"})
        else:
            rows = res.get("results") or []
            out["ai_overview"] = (res.get("ai_overview") or {}).get("present")
            c, cerr = _section("contract", contract_for, query,
                               [r["url"] for r in rows if r.get("url")], workers)
            if cerr or not (c or {}).get("ok"):
                unavailable.append({"section": "contract",
                                    "reason": (cerr or {}).get("reason") or "no page read"})
            else:
                contract = c

    # ⚠ THE REFUSAL THAT MATTERS. Without page 1 there is no depth target, no
    # contract and no weakness read - and a brief that states them anyway is
    # stating the writer's assumptions back to them with a tool's authority.
    if not contract:
        return refuse("content-brief",
                      "page 1 could not be read, so there is no measured contract, no "
                      "depth target and no competitive read. A brief without those is "
                      "a guess with a tool's name on it.",
                      query=query, unavailable=unavailable,
                      what_to_do=("check `providers.py status` and `serp.py --control`, or "
                                  "pass --contract-json from a completed "
                                  "`competitors.py profile` run"))

    out["competition"] = {
        "results_read": contract.get("read"),
        "unread": len(contract.get("unread") or []),
        "challenged": contract.get("challenged") or [],
        "depth": contract.get("depth"),
        "format": contract.get("format"),
        "weak_results": contract.get("weak_results") or [],
        "note": contract.get("contract_note"),
    }
    # ⚠ A CONTRACT IS ONLY AS GOOD AS THE NUMBER OF PAGES BEHIND IT, and the
    # failure is not an empty list - it is a confident one. Measured 2026-09-01
    # on "how to bunny hop in cs 1.6": only 2 of 10 results had readable article
    # structure, and the `gaps` list came back as `ratings, submission, score,
    # favorite, interactions` - GameBanana and mods.vg platform furniture, read
    # as subtopics because the UGC registry is a fixed domain list and mods.vg
    # is not on it. Printed as "differentiators" that is an instruction to write
    # a section about somebody's ratings widget.
    #
    # Widening the domain list forever is not the fix; refusing to draw
    # subtopics from a sample too small to support them is. `gaps` are by
    # construction covered by a MINORITY of page 1, so they are the first thing
    # to become noise as the sample shrinks.
    read = contract.get("read") or 0
    strong = read >= MIN_PAGES_FOR_SUBTOPICS
    out["must_cover"] = contract.get("contract") or []
    if strong:
        out["differentiators"] = contract.get("gaps") or []
    else:
        out["differentiators"] = []
        out["differentiators_withheld"] = {
            "reason": (f"only {read} page-1 result(s) had readable article structure "
                       f"(need {MIN_PAGES_FOR_SUBTOPICS}). A minority-coverage subtopic "
                       f"drawn from {read} page(s) is as likely to be that site's own "
                       f"furniture as a real gap - measured: `ratings`, `submission`, "
                       f"`score` from two mod-hosting pages."),
            "raw_gaps": [g.get("subtopic") for g in (contract.get("gaps") or [])][:15],
            "what_to_do": ("read the page-1 URLs yourself, or escalate the unread ones "
                           "through `competitors.py` browser_candidates and re-run"),
        }
    out["subtopic_confidence"] = {
        "pages_behind_the_contract": read,
        "threshold": MIN_PAGES_FOR_SUBTOPICS,
        "verdict": "usable" if strong else "too-small-to-draw-subtopics",
    }

    # 2. what people actually ask.
    q, qerr = _section("questions", questions_for, query)
    if qerr:
        unavailable.append(qerr)
    else:
        out["intent"] = q.pop("intent", None)
        out["questions"] = q

    # 3. do we already have this page?
    if corpus:
        c, cerr = _section("corpus", corpus_overlap, query, corpus)
        if cerr:
            unavailable.append(cerr)
        elif not c.get("ok"):
            unavailable.append({"section": "corpus", "reason": c.get("reason")})
        else:
            out["existing_coverage"] = c

    # 4. can we realistically compete?
    if our_domain:
        a, aerr = _section("authority", can_we_compete, our_domain, rows)
        if aerr:
            unavailable.append(aerr)
        else:
            out["authority"] = a

    # 5. entities worth naming (optional - a network call for a nice-to-have).
    if want_entities:
        e, eerr = _section("entities", entities_for, query)
        if eerr:
            unavailable.append(eerr)
        elif (e or {}).get("ok"):
            out["entities"] = e

    out["constraints"] = CONSTRAINTS
    out["unavailable"] = unavailable
    out["completeness"] = {
        "sections_measured": sum(1 for k in ("competition", "questions",
                                             "existing_coverage", "authority", "entities")
                                 if k in out),
        "sections_unavailable": len(unavailable),
        "note": ("Nothing in this brief is generated. Every section is a measurement or "
                 "is listed in `unavailable` with its reason. A missing section is a "
                 "question that was not answered, NOT a finding of 'nothing there'."),
    }
    return out


# -------------------------------------------------------------------- control
def run_control() -> dict:
    """Prove the assembler refuses rather than invents, offline.

    The failure this guards is specific: a brief is written from, not audited. A
    fabricated 'page 1 covers' section produces a draft that is confidently
    wrong about the competition, and nobody re-opens the SERP to check."""
    c = Controls("brief-control")

    # THE REFUSAL. No contract must mean no brief - not a brief with the
    # competitive sections quietly missing.
    r = build("a query", provider="definitely-not-a-provider", count=1)
    c.check("no_readable_page_1_refuses_outright", r.get("control_failed") is True,
            str(r)[:200])
    c.check("the_refusal_says_what_is_missing",
            "depth target" in str(r.get("reason", "")), str(r.get("reason"))[:120])
    c.check("the_refusal_offers_the_way_forward", bool(r.get("what_to_do")))
    c.check("the_refusal_carries_no_invented_sections",
            not any(k in r for k in ("must_cover", "competition", "differentiators")),
            str(sorted(r)))

    # A section that fails must be RECORDED, not fatal and not silently absent.
    got, err = _section("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    c.check("a_failed_section_is_recorded_not_raised", got is None and err is not None)
    c.check("the_recorded_failure_names_the_section", err["section"] == "boom")
    c.check("the_recorded_failure_keeps_the_reason", "RuntimeError" in err["reason"])

    c.check("tokeniser_drops_stopwords", "the" not in _tokens("the bomb site"))
    c.check("tokeniser_keeps_content_words", {"bomb", "site"} <= _tokens("the bomb site"))
    c.check("tokeniser_is_case_insensitive", _tokens("BOMB") == _tokens("bomb"))
    c.check("an_empty_query_yields_no_tokens", _tokens("") == set())

    # THE CANNIBALISATION METRIC, in both directions. A "clear" verdict from a
    # metric that cannot fire is the expensive failure here: it green-lights a
    # second page for a query an existing page already owns.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        d = Path(td)
        # This page's headings deliberately AVOID the query words - the real
        # `bunny-hop.html` is written exactly this way, and it scored 0.0 until
        # the slug was counted.
        (d / "bunny-hop.html").write_text(
            "<html><body><h2>What the engine is actually doing</h2>"
            "<p>Chaining jumps keeps speed above the run cap, and it is the "
            "movement technique every scrim player learns first.</p>"
            "</body></html>", encoding="utf-8")
        (d / "economy.html").write_text(
            "<html><body><h2>Round economy and buy timing</h2>"
            "<p>The loss bonus decides whether a full buy is affordable next "
            "round, so the economy is really a sequencing problem.</p>"
            "</body></html>", encoding="utf-8")
        hit = corpus_overlap("how to bunny hop", str(d))
        miss = corpus_overlap("zzq nonexistent topic 9f2b", str(d))
        c.check("an_existing_page_for_the_query_is_found",
                hit.get("verdict") == "existing-page-may-already-target-this",
                str(hit)[:200])
        c.check("a_page_whose_headings_avoid_the_keyword_is_still_found",
                any("bunny-hop" in x["file"] for x in hit.get("candidates", [])),
                "good editorial headings must not make a page invisible to this check")
        c.check("the_matching_signal_is_reported",
                "slug" in (hit.get("candidates") or [{}])[0].get("matched_in", []),
                str((hit.get("candidates") or [{}])[0].get("matched_in")))
        c.check("an_unrelated_query_is_clear", miss.get("verdict") == "clear", str(miss)[:160])
        c.check("the_metric_discriminates_rather_than_always_clearing",
                hit.get("verdict") != miss.get("verdict"),
                "both answering 'clear' is a metric that cannot fire, not a clean corpus")
        c.check("the_unrelated_sibling_page_is_not_dragged_in",
                not any("economy" in x["file"] for x in hit.get("candidates", [])))
        c.check("it_reports_what_it_scanned", hit.get("scanned") == 2, str(hit.get("scanned")))

    c.check("the_house_rules_travel_with_the_brief", len(CONSTRAINTS) >= 4)
    c.check("information_gain_is_the_first_constraint",
            CONSTRAINTS[0].startswith("INFORMATION GAIN"),
            "it is the one that decides whether the page should exist")
    c.check("depth_is_stated_as_parity_not_a_target",
            any("PARITY" in x for x in CONSTRAINTS))
    c.check("fetched_text_is_declared_untrusted",
            any("UNTRUSTED" in x for x in CONSTRAINTS))

    c.check("question_prefixes_are_real_question_words",
            {"how", "why", "what"} <= set(QUESTION_PREFIX))

    # THE SMALL-SAMPLE GUARD. The failure it prevents is a confident list, not
    # an empty one, so the control has to check both directions.
    thin = {"ok": True, "read": 2, "contract": [], "depth": {}, "format": {},
            "gaps": [{"subtopic": "ratings", "covered_by": 1},
                     {"subtopic": "submission", "covered_by": 1}],
            "pages": [], "unread": []}
    fat = dict(thin, read=8)
    tf = Path(__file__).resolve().parent / "_brief_ctl.json"
    try:
        tf.write_text(json.dumps(thin), encoding="utf-8")
        r_thin = build("q", contract_json=str(tf))
        tf.write_text(json.dumps(fat), encoding="utf-8")
        r_fat = build("q", contract_json=str(tf))
    finally:
        tf.unlink(missing_ok=True)
    c.check("a_thin_contract_withholds_subtopics",
            r_thin.get("differentiators") == [] and "differentiators_withheld" in r_thin,
            str(r_thin.get("differentiators"))[:120])
    c.check("the_withheld_list_is_still_visible_for_review",
            "ratings" in (r_thin.get("differentiators_withheld") or {}).get("raw_gaps", []),
            "withholding must not mean hiding - the reader has to be able to check")
    c.check("a_real_contract_still_reports_its_gaps",
            [g["subtopic"] for g in r_fat.get("differentiators", [])] == ["ratings", "submission"],
            str(r_fat.get("differentiators")))
    c.check("confidence_is_stated_either_way",
            r_thin["subtopic_confidence"]["verdict"] != r_fat["subtopic_confidence"]["verdict"])
    return c.verdict()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="assemble a brief for one query")
    b.add_argument("--query", required=True)
    b.add_argument("--provider", default="ddg")
    b.add_argument("--count", type=int, default=10)
    b.add_argument("--our-domain", help="adds the authority reality check")
    b.add_argument("--corpus", help="directory of published pages - cannibalisation check")
    b.add_argument("--contract-json", help="reuse a completed competitors.py profile run")
    b.add_argument("--entities", action="store_true", help="also resolve Wikidata entities")
    b.add_argument("--workers", type=int, default=6)
    sub.add_parser("control", help="prove the assembler refuses rather than invents")

    a = ap.parse_args()
    out = (run_control() if a.cmd == "control" else
           build(a.query, provider=a.provider, count=a.count, our_domain=a.our_domain,
                 corpus=a.corpus, contract_json=a.contract_json,
                 want_entities=a.entities, workers=a.workers))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
