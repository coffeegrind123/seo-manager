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


def test_measurement() -> None:
    """2026-10-03: what a citation IS (cleaned, resolved, classified), how sure
    a rate is, and when an engine's silence is the parser's fault."""
    saved = dict(G.ENGINES)
    try:
        print("\nregistrable domain - a public suffix is not a site:")
        check("bbc.co.uk stays bbc.co.uk", G._registrable("news.bbc.co.uk") == "bbc.co.uk",
              G._registrable("news.bbc.co.uk"))
        check("abc.net.au stays abc.net.au", G._registrable("www.abc.net.au") == "abc.net.au")
        check("ox.ac.uk stays ox.ac.uk", G._registrable("www.ox.ac.uk") == "ox.ac.uk")
        check("CONTROL: sub.example.com is example.com", G._registrable("a.b.example.com") == "example.com")
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(references=[{"domain": "other.co.uk", "url": "https://other.co.uk/"}])
        check("another .co.uk site citing is NOT us",
              G.ask("q", "mysite.co.uk", use_cache=False)["cited_by"] == [])
        print("\nan EMPTY overview block is no answer surface, not an uncited answer:")
        import json as _j
        from providers import HttpResult
        real_http, real_secret = G.http, G.read_secret
        try:
            G.read_secret = lambda *a, **k: "k"
            G.http = lambda url, **kw: HttpResult(status=200, body=_j.dumps(
                {"ai_overview": {"error": "An AI Overview is not available for this search"}}).encode())
            r = G.engine_google_ai_overview("q")
            check("an error-only ai_overview block has_answer=False",
                  r["state"] == "answered" and r["has_answer"] is False, r)
            G.http = lambda url, **kw: HttpResult(status=200, body=_j.dumps(
                {"ai_overview": {"text_blocks": [{"snippet": "an answer"}],
                                 "references": [{"link": "https://a.test/x"}]}}).encode())
            r = G.engine_google_ai_overview("q")
            check("CONTROL: a populated block is an answer with its reference",
                  r["has_answer"] is True and r["references"][0]["domain"] == "a.test", r)
        finally:
            G.http, G.read_secret = real_http, real_secret
        print("\nURL hygiene - one page, one key:")
        check("utm_* and the text fragment are stripped",
              G._clean_url("https://Example.com/a?utm_source=openai&x=1#:~:text=foo")
              == "https://example.com/a?x=1", G._clean_url("https://Example.com/a?utm_source=openai&x=1#:~:text=foo"))
        check("a Google /url?q= wrapper is unwrapped without a request",
              G._unwrap("https://www.google.com/url?q=https://site.test/p&sa=U") == "https://site.test/p")
        check("CONTROL: an ordinary URL is unchanged by unwrapping",
              G._unwrap("https://site.test/p") == "https://site.test/p")

        print("\ncitation source kinds (gap domains are not all one thing):")
        k = G.classify_source
        check("reddit is a forum", k("reddit.com", "https://www.reddit.com/r/x/comments/1") == "forum")
        check("a forum. host is a forum", k("forum.example.com", "https://forum.example.com/t/1") == "forum")
        check("youtube is video", k("youtube.com", "https://www.youtube.com/watch?v=1") == "video")
        check("github is developer", k("github.com", "https://github.com/a/b") == "developer")
        check("a docs. host is developer", k("docs.example.com", "https://docs.example.com/x") == "developer")
        check("wikipedia is reference", k("wikipedia.org", "https://en.wikipedia.org/wiki/X") == "reference")
        check("a .gov is institutional", k("nih.gov", "https://www.nih.gov/x") == "institutional")
        check("a .ac.uk is institutional", k("ox.ac.uk", "https://www.ox.ac.uk/x") == "institutional")
        check("amazon is ecommerce", k("amazon.com", "https://www.amazon.com/dp/B0") == "ecommerce")
        check("prnewswire is pr", k("prnewswire.com", "https://www.prnewswire.com/news/x") == "pr")
        check("g2 is reviews", k("g2.com", "https://www.g2.com/products/x/reviews") == "reviews")
        check("a 'best X' listicle path is a listicle",
              k("someblog.test", "https://someblog.test/best-vpn-for-gaming") == "listicle")
        check("a vs page is comparison",
              k("someblog.test", "https://someblog.test/x-vs-y") == "comparison")
        check("CONTROL: an unremarkable page is other",
              k("someblog.test", "https://someblog.test/about") == "other")

        print("\nlist rank - where in a list we are named:")
        pats = G._name_patterns("nova.app", "Nova")
        txt = "Options:\n1. Acme does it.\n2. Nova is good.\n3. Zed.\n\nAlso Nova in prose."
        check("rank is the list position of the first item naming us", G._list_rank(txt, pats) == 2)
        check("a prose-only mention has no rank (None, never 0)",
              G._list_rank("Nova is fine in prose.", pats) is None)
        check("bullets are ranked too", G._list_rank("- A\n- B\n- Nova\n", pats) == 3)

        print("\nretrieved vs cited - a page the engine READ is not a page it CITED:")
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(references=[{"domain": "play-cs.com", "url": "https://play-cs.com"}],
                                 retrieved=[{"domain": "example.com", "url": "https://example.com/a"}])
        r = G.ask("q", "example.com", use_cache=False)
        row = r["results"][0]
        check("retrieved but not cited is its own rung",
              row["cited"] is False and row["retrieved_not_cited"] is True, row)
        check("and it does not count as a citation", r["cited_by"] == [])

        print("\nrates carry intervals, and small samples are labelled:")
        seq = iter([True, True, False])

        def flaky(q, **_):
            hit = next(seq)
            refs = [{"domain": "example.com", "url": "https://example.com"}] if hit else \
                   [{"domain": "other.test", "url": "https://other.test"}]
            return {"state": "answered", "has_answer": True, "text": "x", "references": refs}
        G.ENGINES.clear()
        G.ENGINES["fake"] = flaky
        r = G.ask("q", "example.com", use_cache=False, runs=3)
        rr = r["results"][0]["runs"]
        check("2/3 carries a Wilson interval", rr["ci95"] and rr["ci95"][0] < 0.67 < rr["ci95"][1], rr)
        check("n=3 is labelled a small sample", rr["small_sample"] is True, rr)
        check("2/3 is not a STABLE band", rr["stable"] is False, rr)

        print("\nparser-drift control: an engine that answers and never cites anything")
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(text="an answer with no sources at all", references=[])
        sw = G.sweep("example.com", [f"q{i}" for i in range(6)], use_cache=False)
        check("6 answers with ZERO citations marks the engine's parser suspect",
              "fake" in sw["parser_suspect_engines"], sw.get("parser_suspect_engines"))
        check("and its not-cited answers leave the rate (unknown, not zero)",
              sw["answers_seen"] == 0 and sw["citation_rate"] is None, sw)
        G.ENGINES["fake"] = stub(references=[{"domain": "a.test", "url": "https://a.test"}])
        sw = G.sweep("example.com", [f"q{i}" for i in range(6)], use_cache=False)
        check("CONTROL: an engine that cites someone else is not suspect",
              sw["parser_suspect_engines"] == [] and sw["answers_seen"] == 6, sw)

        print("\ndiff - paired questions, exact test, refusal when too few move:")
        def mk(flags):
            return {"check": "geo-sweep", "per_question": [
                {"query": f"q{i}", "counts": {"fake": [1 if f else 0, 1]}} for i, f in enumerate(flags)]}
        d = G.diff_sweeps(mk([0] * 10), mk([1] * 8 + [0] * 2))
        e = d["engines"]["fake"]
        check("8 of 10 questions flipping to cited is a significant gain",
              e["verdict"] == "gained" and e["p_holm"] < 0.05, e)
        d = G.diff_sweeps(mk([0] * 10), mk([1] * 3 + [0] * 7))
        e = d["engines"]["fake"]
        check("3 flips cannot be tested (fewer than 6 discordant)", e["verdict"] == "too_few_changes", e)
        d = G.diff_sweeps(mk([0, 1]), {"check": "geo-sweep", "per_question": [{"query": "zz", "counts": {}}]})
        check("no shared questions refuses", d["engines"] == {} and d["paired_questions"] == 0, d)

        print("\ngap - Search Console joined to the answer surface:")
        gsc = {"rows": [
            {"keys": ["play cs"], "clicks": 10, "impressions": 400, "ctr": 0.025, "position": 2.1},
            {"keys": ["cs maps"], "clicks": 1, "impressions": 300, "ctr": 0.003, "position": 9.0},
            {"keys": ["cs guide"], "clicks": 50, "impressions": 500, "ctr": 0.1, "position": 1.5},
            {"keys": ["cs mods"], "clicks": 20, "impressions": 300, "ctr": 0.066, "position": 3.0},
            {"keys": ["tiny"], "clicks": 0, "impressions": 3, "ctr": 0, "position": 5.0}]}
        obs = {"play cs": {"state": "answered", "has_answer": True, "cited": False,
                           "competitors": ["a.test"]},
               "cs maps": {"state": "answered", "has_answer": True, "cited": False,
                           "competitors": ["b.test"]},
               "cs guide": {"state": "answered", "has_answer": True, "cited": True, "competitors": []},
               "cs mods": {"state": "answered", "has_answer": False}}
        g = G.gap_tiers(gsc, obs, min_impressions=25)
        t = {r["query"]: r["tier"] for r in g["rows"]}
        check("top-4 rank, competitor cited -> B (ranks_not_cited)", t.get("play cs") == "B", t)
        check("rank 5-20, competitor cited -> A", t.get("cs maps") == "A", t)
        check("cited -> C only if CTR is below its position baseline, else cited",
              t.get("cs guide") in ("C", "cited"), t)
        check("no overview -> D", t.get("cs mods") == "D", t)
        check("under min impressions is not judged", "tiny" not in t, t)
        check("an unanswerable query is X, never a tier",
              G.gap_tiers(gsc, {"play cs": {"state": "failing"}}, 25)["rows"][0]["tier"] == "X")
    finally:
        G.ENGINES.clear()
        G.ENGINES.update(saved)


def main() -> int:
    test_measurement()
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

    print("\nmention matching - a name, not a substring:")
    check("a brand inside a longer word is not a mention",
          G._mentions("Casanova is a film. Nothing else here.", "nova.app", "Nova") == [],
          G._mentions("Casanova is a film. Nothing else here.", "nova.app", "Nova"))
    check("CONTROL: the brand as a word IS a mention",
          len(G._mentions("Try Nova for this. Or not.", "nova.app", "Nova")) == 1)
    check("a citation LINK is not a prose mention",
          G._mentions("Use a VPN [1](https://nova.app/guide). It works.", "nova.app", None) == [],
          G._mentions("Use a VPN [1](https://nova.app/guide). It works.", "nova.app", None))
    check("a bare URL is not a prose mention either",
          G._mentions("See https://www.nova.app/x for details.", "nova.app", None) == [])
    check("CONTROL: the domain written in prose IS a mention",
          len(G._mentions("Nova.app has a guide. Fine.", "nova.app", None)) == 1)
    check("another domain ENDING in ours is not us",
          G._mentions("Play at supernova.app today.", "nova.app", None) == []
          and G._mentions("Play at play-nova.app today.", "nova.app", None) == [])
    check("the brand as another site's label is not us",
          G._mentions("Nova.io is unrelated.", "nova.app", "Nova") == [])
    check("a blank or one-letter alias matches nothing",
          G._mentions("Anything at all here.", "x.test", " , a") == [])
    check("comma-separated aliases are each matched",
          len(G._mentions("CS Skirmish works. Combat Skirmish too.", "cs.test",
                          "Combat Skirmish, CS Skirmish")) == 2)
    check("a CJK brand matches without ASCII word boundaries",
          len(G._mentions("推荐使用新星平台。", "nova.app", "新星")) == 1)
    check("the returned sentence is verbatim, link and all",
          G._mentions("Nova is good [1](https://nova.app). End.", "nova.app", "Nova")
          == ["Nova is good [1](https://nova.app)."])

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
    print("\nthe subject check must not be ASCII-only - it flagged /zh/, the page")
    print("earning 68% of this site's clicks, on a rule that never executed:")
    zh_h1 = "CS1.6 网页版 — 在线玩反恐精英 1.6"
    check("a CJK heading matched by its own sentence passes",
          G._names_subject(zh_h1, "Combat Skirmish 把真正的反恐精英 1.6 带进浏览器。") == (True, "cjk-bigrams"))
    check("a CJK heading NOT matched still fails",
          G._names_subject(zh_h1, "完全无关的一句话关于别的东西。")[0] is False,
          "the bigram path has to discriminate, not just say yes")
    check("Cyrillic goes down the word path",
          G._names_subject("Играть в Counter-Strike онлайн",
                           "Counter-Strike запускается в браузере.") == (True, "words"))
    check("Cyrillic can still fail",
          G._names_subject("Играть в Counter-Strike онлайн", "Совершенно другая тема.")[0] is False)
    check("Latin is unaffected",
          G._names_subject("Bunny hopping", "Bunny hopping is a technique.") == (True, "words"))
    check("no h1 is not-evaluable rather than a failure",
          G._names_subject("", "A sentence.")[1] == "not-evaluable",
          "a check that could not run must not vote")

    print("\nsentence boundaries and word counts are not ASCII either:")
    check("a Japanese sentence splits on 。",
          len([x for x in G._SENT.split("これは一文です。これは二文目です。") if x.strip()]) == 2)
    check("a Hindi sentence splits on the danda",
          len([x for x in G._SENT.split("यह एक वाक्य है। यह दूसरा है।") if x.strip()]) == 2)
    check("an Urdu sentence splits on ۔",
          len([x for x in G._SENT.split("یہ ایک جملہ ہے۔ یہ دوسرا ہے۔") if x.strip()]) == 2)
    check("English is unaffected",
          len([x for x in G._SENT.split("One sentence. Two sentences.") if x.strip()]) == 2)
    check("a dot inside a DOMAIN does not end a sentence",
          len([x for x in G._SENT.split("Combatskirmish.net runs it.") if x.strip()]) == 1,
          "relaxing the ASCII rule to \\s* splits on every domain and abbreviation")
    n, unit, ok_ = G._sentence_length("Combat Skirmishは本物のCounter-Strike 1.6をブラウザに届けます")
    check("a Japanese sentence is measured in characters", unit == "chars",
          f"got {n} {unit} - by whitespace this reads as 3 'words'")
    check("and a real Japanese sentence is within bounds", ok_ is True, f"{n} chars")
    n2, unit2, _ = G._sentence_length("Bunny hopping is the technique of chaining jumps.")
    check("English is still measured in words", unit2 == "words")
    nj, uj, okj = G._sentence_length(
        "Combat Skirmishは本物のCounter-Strike 1.6をWebAssemblyでブラウザに届けます")
    check("a mostly-Latin Japanese sentence is STILL measured in characters",
          uj == "chars" and okj is True,
          f"got {nj} {uj} - it is only 28% kana/kanji, so a ratio threshold "
          f"measures it as 3 'words'")
    _n3, _u3, ok3 = G._sentence_length("Too short.")
    check("a too-short lead still fails", ok3 is False)

    check("a single-sentence paragraph ending in a period IS structured",
          bool(G._TERM_END.search("Bunny hopping and more words ends here.")),
          "the split rule finds no boundary here, and using IT to detect structure "
          "waives the length check on most paragraphs on a real site")
    check("a paragraph with no sentence punctuation is NOT structured",
          not G._TERM_END.search("Combat Skirmish ส่ง Counter-Strike 1.6 เข้าเบราว์เซอร์"),
          "the dot in `Counter-Strike 1.6` makes a bare terminator search say yes")
    check("a trailing quote or bracket does not hide the terminator",
          bool(G._TERM_END.search('He called it "the technique."')))

    print("\nthe subject rule tests whether the SENTENCE stands alone, not whether")
    print("it agrees with the h1's vocabulary:")
    check("a stylish headline does not sink a well-written lead",
          G._names_subject("A Retro Shooter That Never Needed Replacing",
                           "Counter-Strike started life in 1999 as a mod for Half-Life.")[0] is True,
          "matching only h1 tokens flagged this real page")
    check("an all-caps abbreviation counts as naming the subject",
          G._names_subject("Play Counter-Strike 1.6 With Friends",
                           "Organising a game of CS 1.6 meant everyone owning it.")[0] is True)
    check("a lead that names nothing still fails",
          G._names_subject("Play Counter-Strike 1.6 Free",
                           "There is no purchase, no subscription and no account.")[0] is False,
          "this is what the rule exists to catch")
    check("an ordinary capitalised opener is not a proper noun",
          not G._PROPER.search("Organising a game meant everyone owning it"),
          "otherwise every sentence passes and the rule measures nothing")
    check("an existential opener is caught like a pronoun",
          bool(__import__("re").match(
              r"(?i)^(it|this|that|they|these|those|he|she|there\s+(is|are|was|were))\b",
              "There is no purchase.")))

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
