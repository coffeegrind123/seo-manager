#!/usr/bin/env python3
"""Regression tests for AI answer sampling.

The whole instrument turns on one distinction, and three of these cases are bugs
it shipped in its first hour against real Google AI Overviews.

    python3 test_geo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import geo as G  # noqa: E402

FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:150]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def stub(**kw):
    base = {"state": "answered", "has_answer": True, "text": "", "references": []}
    base.update(kw)
    return lambda q, **_: base


def main() -> int:
    saved = dict(G.ENGINES)
    try:
        print("cannot_ask must never become not_cited - the whole point:")
        G.ENGINES.clear()
        G.ENGINES["nokey"] = lambda q, **_: {"state": "no_key", "detail": "no key"}
        r = G.ask("q", "example.com", use_cache=False)
        check("an unaskable engine refuses", r.get("control_failed") is True)
        check("the refusal says why", "never be reported as 'not cited'" in str(r.get("reason")))
        check("engines_status is not ok with nothing usable", G.engines_status()["ok"] is False)
        check("a sweep with no engine refuses",
              G.sweep("example.com", ["q"], use_cache=False).get("control_failed") is True,
              "otherwise it reports 'not cited' for every question without asking")

        print("\ncitation, both directions:")
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(references=[{"domain": "play-cs.com", "url": "https://play-cs.com"}])
        miss = G.ask("q", "example.com", use_cache=False)
        check("an answer without us is not_cited", miss["not_cited_by"] == ["fake"])
        check("competitors are named", miss["results"][0]["competitors_cited"] == ["play-cs.com"])
        G.ENGINES["fake"] = stub(references=[
            {"domain": "play-cs.com", "url": "https://play-cs.com"},
            {"domain": "www.example.com", "url": "https://www.example.com/a"}])
        hit = G.ask("q", "example.com", use_cache=False)
        check("an answer citing us is cited", hit["cited_by"] == ["fake"])
        check("a www citation matches the bare domain", hit["results"][0]["cited"] is True)
        check("the citation position is reported", hit["results"][0]["citation_position"] == 2)

        print("\nno answer surface is NOT a miss - measured on a real SERP with no overview:")
        G.ENGINES["fake"] = stub(has_answer=False)
        none = G.ask("q", "example.com", use_cache=False)
        check("it is not reported as not_cited",
              none["not_cited_by"] == [] and none["no_answer_surface"] == ["fake"])
        check("it is not a refusal either", none["ok"] is True,
              "there being no answer is a fact about the QUERY, not an inability to ask")
        sw = G.sweep("example.com", ["q1", "q2"], use_cache=False)
        check("it is excluded from the citation rate",
              sw["answers_seen"] == 0 and sw["citation_rate"] is None,
              "counting it as a miss computes a rate against answers that never existed")

        print("\nshare of voice: one vote per ANSWER, and the engine's own plumbing")
        print("excluded - google.com appeared 21 times across 5 real answers:")
        G.ENGINES["fake"] = stub(references=[
            {"domain": "google.com", "url": "https://google.com/a"},
            {"domain": "google.com", "url": "https://google.com/b"},
            {"domain": "play-cs.com", "url": "https://play-cs.com"},
            {"domain": "play-cs.com", "url": "https://play-cs.com/x"}])
        G.ENGINE_FURNITURE["fake"] = {"google.com"}
        one = G.ask("q", "example.com", use_cache=False)
        check("the engine's own domain is excluded",
              "google.com" not in one["results"][0]["cited_domains"])
        check("but the exclusion is visible, not silent",
              one["results"][0]["engine_furniture_excluded"] == ["google.com"])
        sv = G.sweep("example.com", ["q1", "q2"], use_cache=False)
        check("share never exceeds 1.0", all(x["share"] <= 1.0 for x in sv["share_of_voice"]),
              str(sv["share_of_voice"]))
        check("a domain cited twice in one answer gets one vote",
              next(x["answers_citing_it"] for x in sv["share_of_voice"]
                   if x["domain"] == "play-cs.com") == 2, "2 answers, not 4 links")
        G.ENGINE_FURNITURE.pop("fake", None)
    finally:
        G.ENGINES.clear()
        G.ENGINES.update(saved)
    check("the engine registry is restored", set(G.ENGINES) == set(saved))

    print("\nsentences naming us are surfaced verbatim, not summarised:")
    m = G._mentions("Combatskirmish.net runs it in a browser. Play-cs.com also does.",
                    "combatskirmish.net", None)
    check("ours is surfaced", len(m) == 1 and "Combatskirmish" in m[0])
    check("a competitor's sentence is not", not any("Play-cs" in x for x in m))

    print("\nextractability - an assistant lifts a SENTENCE, not a page:")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "good.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>Bunny hopping is the technique of "
            "chaining jumps to keep speed above the engine's run cap.</p></body></html>",
            encoding="utf-8")
        (d / "pronoun.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>It is the thing everyone asks about "
            "first, and it takes a while to learn properly.</p></body></html>",
            encoding="utf-8")
        (d / "long.html").write_text(
            "<html><body><h1>Bunny hopping</h1><p>Bunny hopping " + "and more words " * 30
            + "ends here.</p></body></html>", encoding="utf-8")
        e = G.extractable(str(d))
        bad = {r["file"] for r in e["worst"]}
    check("a self-contained lead is liftable", e["liftable"] == 1, str(e["liftable"]))
    check("a pronoun lead is not", "pronoun.html" in bad)
    check("an over-long lead is not", "long.html" in bad,
          "quoting a 60-word sentence means quoting a paragraph")
    check("it discriminates rather than passing or failing everything",
          e["liftable"] == 1 and e["not_liftable"] == 2, str(e))
    check("a missing directory refuses",
          G.extractable("/no/such/dir").get("control_failed") is True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all geo tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
