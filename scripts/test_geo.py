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
        G.ENGINES["fake"] = stub(references=[{"domain": "rival.example", "url": "https://rival.example"}],
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


def test_mention_markup_and_scripts() -> None:
    """2026-10-25, fifth pass: OUR matcher on Canonry's answer-prose and
    brand-matching fixtures, open-seo's aiVisibilityMatching cases and
    ansvisor's citation rows. 9 of 20 prose cases and 12 of 27 brand cases
    disagreed before this."""
    M = G._mentions
    print("\ncitation markup is not prose (Canonry answer-prose.test.ts):")
    check("a link chip whose LABEL is our host is a citation, not a mention",
          M("Book at [harborview.com](https://harborview.com/tours).", "harborview.com", None) == [],
          M("Book at [harborview.com](https://harborview.com/tours).", "harborview.com", None))
    check("a parenthesised chip with utm is not a mention",
          M("Great tours ([harborview.com](https://harborview.com/t?utm_source=chatgpt.com)).",
            "harborview.com", None) == [])
    check("an OpenAI file-citation marker is not a mention",
          M("Tours run daily \u30104:0\u2020harborview.com\u3011.", "harborview.com", None) == [])
    check("OpenAI private-use cite spans are not a mention",
          M("Daily tours \ue200cite\ue202harborview.com\ue201 exist.", "harborview.com", None) == [])
    check("image alt text is not a mention",
          M("Photo: ![harborview.com logo](https://harborview.com/l.png) here.", "harborview.com", None) == [])
    check("a footnote definition line is not a mention",
          M("Tours exist.[^1]\n\n[^1]: harborview.com tours page", "harborview.com", None) == [])
    check("CONTROL: a link whose label is prose keeps its prose",
          len(M("Try [Harborview Living](https://harborview.com) today.", "harborview.com",
                "Harborview Living")) == 1)
    check("www.<our domain> written in prose IS a mention",
          len(M("Visit www.harborview.com for tours.", "harborview.com", None)) == 1)
    check("a URL ends at an em dash, so the name after it survives",
          len(M("See https://example.com/tours\u2014Harborview Living is best.", "harborview.com",
                "Harborview Living")) == 1)
    check("a bare URL inside CJK text is blanked",
          M("\u8be6\u60c5\u8bf7\u8bbf\u95eehttps://harborview.com/pets\uff0c\u5f88\u53d7\u6b22\u8fce\u3002",
            "harborview.com", None) == [])
    check("an email address is not a mention", M("Write to support@acme.com today.", "acme.com", "Acme") == [])

    print("\nnames in every script (Canonry brand-matching.ts, open-seo b77bb5b1):")
    check("a two-character Korean brand is matched", len(M("\uc0bc\uc131 \uac24\ub7ed\uc2dc\uac00 \uc88b\ub2e4.",
                                                         "zz.test", "\uc0bc\uc131")) == 1)
    check("a brand inside an accented word is not a mention",
          M("Rapha\u00ebl painted it.", "rapha.test", "Rapha") == [] and M("\u00c9Nova rises.", "n.test", "Nova") == [])
    check("a Cyrillic brand inside a longer word is not a mention",
          M("\u041c\u0438\u0440\u043e\u0432\u043e\u0439 \u0440\u044b\u043d\u043e\u043a.", "m.test", "\u041c\u0438\u0440") == [])
    check("CONTROL: the Cyrillic brand as a word IS a mention",
          len(M("\u041c\u0438\u0440 \u0440\u0430\u0441\u0442\u0451\u0442.", "m.test", "\u041c\u0438\u0440")) == 1)
    check("a Greek brand inside a longer word is not a mention",
          M("\u039d\u03bf\u03b2\u03b1\u03ba wins.", "n.test", "\u039d\u03bf\u03b2\u03b1") == [])
    check("an underscore joins words", M("Use acme_tools here.", "a.test", "acme") == [])
    check("a hyphen/space/none variant of a two-word alias matches",
          all(len(M(f"{v} is great.", "v.test", "Vexlo IQ")) == 1 for v in ("Vexlo-IQ", "VexloIQ", "Vexlo  IQ")))
    check("NFD-encoded text matches an NFC alias", len(M("Cafe\u0301 Luna opens.", "c.test", "Caf\u00e9 Luna")) == 1)
    check("accents fold for Latin", len(M("Soleme is cheap.", "s.test", "Sol\u00eame")) == 1)
    check("full-width letters fold", len(M("\uff44\uff45\uff4d\uff4f works.", "d.test", "demo")) == 1)
    check("a zero-width space inside a name is ignored", len(M("No\u200bva wins.", "n.test", "Nova")) == 1)
    check("CONTROL: a Latin brand next to Japanese is still matched (no \\p{L} edge)",
          len(M("Notion\u306f\u4fbf\u5229\u3067\u3059\u3002", "notion.so", "Notion")) == 1)

    print("\nclick IDs are stripped, so one page is one source (Canonry url-normalize.ts):")
    for k in ("ttclid", "li_fat_id", "igshid", "yclid", "dclid", "gbraid", "wbraid", "mc_cid", "mc_eid",
              "_ga", "_gl"):
        check(f"{k} is stripped", G._clean_url(f"https://a.test/p?{k}=1&id=7") == "https://a.test/p?id=7",
              G._clean_url(f"https://a.test/p?{k}=1&id=7"))
    check("CONTROL: a real parameter survives", G._clean_url("https://a.test/p?page=2") == "https://a.test/p?page=2")

    print("\ncitations (ansvisor citation-rows, PSL private section):")
    check("a citation URL with no scheme keeps its domain",
          G._ref("example.com/page")["domain"] == "example.com", G._ref("example.com/page"))
    check("a hosting platform is a public suffix: two github.io sites are two sites",
          G._registrable("nova.github.io") == "nova.github.io"
          and G._registrable("other.github.io") != G._registrable("nova.github.io"))
    check("vercel.app and myshopify.com too",
          G._registrable("a.vercel.app") == "a.vercel.app" and G._registrable("x.y.myshopify.com") == "y.myshopify.com")
    check("CONTROL: an ordinary subdomain still folds", G._registrable("blog.example.com") == "example.com")
    check("CONTROL: substack.com is NOT on the PSL, so it folds as the list says",
          G._registrable("a.substack.com") == "substack.com")

    print("\na question that names us is flagged, and kept out of the unbranded rate:")
    saved = dict(G.ENGINES)
    try:
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(text="You asked about Nova vs Acme for small teams. Nova is fine.",
                                 references=[])
        r = G.ask("Nova vs Acme for small teams?", "nova.app", brand="Nova", engines=["fake"])
        check("ask() flags query_names_us", r.get("query_names_us") is True, {k: r.get(k) for k in ("query_names_us",)})
        r2 = G.ask("best tools for small teams?", "nova.app", brand="Nova", engines=["fake"])
        check("CONTROL: an unbranded question is not flagged", r2.get("query_names_us") is False)
        sw = G.sweep("nova.app", ["Nova vs Acme for small teams?", "best tools for small teams?"],
                     brand="Nova", engines=["fake"])
        seg = sw.get("by_query_type") or {}
        check("the sweep splits branded and unbranded",
              seg.get("branded", {}).get("answers") == 1 and seg.get("unbranded", {}).get("answers") == 1
              and seg["unbranded"]["answers_mentioning_us"] == 1, seg)
    finally:
        G.ENGINES.clear()
        G.ENGINES.update(saved)


def test_engine_requests() -> None:
    """2026-10-25: what each engine is ASKED, checked against the vendor docs
    read that day. OpenAI shut the search-preview models down 2026-07-23
    (developers.openai.com/api/docs/guides/tools-web-search); Perplexity ended
    Sonar chat completions 2026-09-27 (docs.perplexity.ai/docs/agent-api/
    migrate-from-sonar); SearchApi's location parameter differs per engine and
    was never sent, so a `--gl de` sweep was US-located under a `de` cache key."""
    import json as _j
    import urllib.parse as _up
    from providers import HttpResult
    sent = []

    def fake_http(url, **kw):
        sent.append({"url": url, "data": kw.get("data")})
        if "searchapi.io" in url:
            body = {"search_metadata": {"status": "Success"}, "markdown": "Answer.",
                    "text_blocks": [{"type": "paragraph", "answer": "Answer.", "reference_indexes": [0]}],
                    "reference_links": [{"index": 0, "link": "https://a.test/x"}]}
        elif "api.openai.com" in url:
            body = {"output": [
                {"type": "web_search_call", "action": {"sources": [{"url": "https://read.test/r"}]}},
                {"type": "message", "content": [{"type": "output_text", "text": "Nova is fine.",
                 "annotations": [{"type": "url_citation", "url": "https://nova.app/x", "title": "N"}]}]}]}
        else:
            body = {"output": [
                {"type": "search_results", "results": [{"url": "https://cited.test/c", "title": "C"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "An answer [1]."}]}]}
        return HttpResult(status=200, body=_j.dumps(body).encode())

    saved_http, saved_secret = G.http, G.read_secret
    G.http, G.read_secret = fake_http, (lambda *a, **k: "k")
    try:
        print("\nSearchApi: each engine gets its own location parameter:")
        want = {"chatgpt": ("country", "de"), "gemini": ("gl", "de"),
                "bing_copilot": ("market_code", "de-DE")}
        for eng, (k, v) in want.items():
            sent.clear()
            got = G._searchapi_engine(eng)("q", gl="de", hl="de")
            qs = dict(_up.parse_qsl(_up.urlsplit(sent[0]["url"]).query))
            check(f"{eng} sends {k}={v}", qs.get(k) == v, qs)
            check(f"{eng} records the location it sent", (got.get("method") or {}).get("location_sent") == v,
                  got.get("method"))
        check("SearchApi text_blocks become supports",
              got.get("supports") == [{"text": "Answer.", "domains": ["a.test"]}], got.get("supports"))
        sent.clear()
        got = G._searchapi_engine("perplexity")("q", gl="de", hl="de")
        check("perplexity (no location parameter) records that the request was NOT located",
              (got.get("method") or {}).get("location_sent") is None
              and (got.get("method") or {}).get("location_requested") == "de", got.get("method"))

        print("\nOpenAI: Responses API web_search, forced, located:")
        sent.clear()
        got = G.ENGINES["openai"]("q", gl="gb", hl="en")
        body = _j.loads(sent[0]["data"])
        check("calls /v1/responses, not the shut-down chat search models",
              sent[0]["url"].endswith("/v1/responses") and "search-preview" not in body.get("model", ""), sent[0])
        check("the web_search tool is FORCED (auto lets it answer from memory)",
              body.get("tools", [{}])[0].get("type") == "web_search" and body.get("tool_choice") == "required", body)
        check("user_location carries the requested country",
              body["tools"][0].get("user_location", {}).get("country") == "GB", body)
        check("url_citation annotations are the citations",
              [r["url"] for r in got["references"]] == ["https://nova.app/x"] and got["text"] == "Nova is fine.", got)
        check("web_search_call sources are RETRIEVED, not cited",
              [r["url"] for r in got.get("retrieved") or []] == ["https://read.test/r"], got.get("retrieved"))

        print("\nPerplexity: the Agent API, sources from output[] search_results:")
        sent.clear()
        got = G.ENGINES["perplexity"]("q", gl="us", hl="en")
        body = _j.loads(sent[0]["data"])
        check("calls /v1/agent with an input", sent[0]["url"].endswith("/v1/agent") and body.get("input") == "q",
              sent[0])
        check("search_results are read as references",
              [r["url"] for r in got["references"]] == ["https://cited.test/c"] and got["text"] == "An answer [1].", got)
    finally:
        G.http, G.read_secret = saved_http, saved_secret

    print("\ndiff refuses to pair answers taken by different methods:")
    mk = lambda m: {"methods": {"e": m}, "per_question": [{"query": f"q{i}", "counts": {"e": [i % 2, 1]}}
                                                        for i in range(8)]}
    d = G.diff_sweeps(mk({"location_sent": "us"}), mk({"location_sent": "de"}))
    check("an engine whose method changed is not compared", "e" not in d["engines"]
          and d.get("method_changed", {}).get("e"), d)
    d = G.diff_sweeps(mk({"location_sent": "us"}), mk({"location_sent": "us"}))
    check("CONTROL: the same method is compared", "e" in d["engines"], d)


def test_supports_and_owned() -> None:
    """2026-10-25: which sentence each source supports (SerpApi ai-overview and
    SearchApi text_blocks carry reference_indexes), several owned domains, and
    the engines that never return fan-out queries (elmo fanout-support.ts;
    SearchApi's Gemini/Copilot docs show no such field)."""
    print("\nsentences backed by our page, from reference_indexes:")
    blocks = [{"snippet": "Nova syncs offline.", "reference_indexes": [0]},
              {"snippet": "Acme is cheaper.", "reference_indexes": [1]},
              {"list": [{"snippet": "Both have apps.", "reference_indexes": [0, 1]}]}]
    refs = [{"index": 0, "link": "https://nova.app/docs"}, {"index": 1, "link": "https://acme.test/p"}]
    sup = G._supports(blocks, refs)
    check("each block names the domains behind it",
          sup == [{"text": "Nova syncs offline.", "domains": ["nova.app"]},
                  {"text": "Acme is cheaper.", "domains": ["acme.test"]},
                  {"text": "Both have apps.", "domains": ["nova.app", "acme.test"]}], sup)
    saved = dict(G.ENGINES)
    try:
        G.ENGINES.clear()
        G.ENGINES["fake"] = stub(text="Nova syncs offline. Acme is cheaper.",
                                 references=[{"domain": "nova.app", "url": "https://nova.app/docs"}],
                                 supports=sup)
        r = G.ask("offline notes app", "nova.app", engines=["fake"], use_cache=False)["results"][0]
        check("ask() lists the sentences our page backs",
              r.get("sentences_backed_by_us") == ["Nova syncs offline.", "Both have apps."], r.get("sentences_backed_by_us"))

        print("\nseveral owned domains are one owner:")
        G.ENGINES["fake"] = stub(text="See the docs.", references=[{"domain": "nova.dev", "url": "https://nova.dev/x"}])
        r = G.ask("q", "nova.app,nova.dev", engines=["fake"], use_cache=False)
        check("a citation of the second owned domain is cited", r["results"][0]["cited"] is True, r["results"][0])
        r = G.ask("q", "nova.app", engines=["fake"], use_cache=False)
        check("CONTROL: with one owned domain it is not", r["results"][0]["cited"] is False)

        print("\nfan-out: never offered is not the same as none returned:")
        G.ENGINES.clear()
        G.ENGINES["searchapi_gemini"] = stub(text="x", references=[])
        r = G.ask("q", "nova.app", engines=["searchapi_gemini"], use_cache=False)["results"][0]
        check("an engine that never returns fan-out says so", r.get("fan_out") == "not_offered", r.get("fan_out"))
        G.ENGINES.clear()
        G.ENGINES["searchapi_chatgpt"] = stub(text="x", references=[])
        r = G.ask("q", "nova.app", engines=["searchapi_chatgpt"], use_cache=False)["results"][0]
        check("a supporting engine that returned none is flagged missing, a drift signal",
              r.get("fan_out") == "missing", r.get("fan_out"))
        G.ENGINES["searchapi_chatgpt"] = stub(text="x", references=[], fan_out_queries=["a b"])
        r = G.ask("q", "nova.app", engines=["searchapi_chatgpt"], use_cache=False)["results"][0]
        check("CONTROL: returned fan-out is returned", r.get("fan_out") == "returned"
              and r.get("fan_out_queries") == ["a b"], r)
    finally:
        G.ENGINES.clear()
        G.ENGINES.update(saved)


def main() -> int:
    test_measurement()
    test_mention_markup_and_scripts()
    test_engine_requests()
    test_supports_and_owned()
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
        G.ENGINES["fake"] = stub(references=[{"domain": "rival.example", "url": "https://rival.example"}])
        miss = G.ask("q", "example.com", use_cache=False)
        check("an answer without us is not_cited", miss["not_cited_by"] == ["fake"])
        check("competitors are named", miss["results"][0]["competitors_cited"] == ["rival.example"])
        G.ENGINES["fake"] = stub(references=[
            {"domain": "rival.example", "url": "https://rival.example"},
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
            {"domain": "rival.example", "url": "https://rival.example"},
            {"domain": "rival.example", "url": "https://rival.example/x"}])
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
                   if x["domain"] == "rival.example") == 2, "2 answers, not 4 links")
        G.ENGINE_FURNITURE.pop("fake", None)
    finally:
        G.ENGINES.clear()
        G.ENGINES.update(saved)
    check("the engine registry is restored", set(G.ENGINES) == set(saved))

    print("\nsentences naming us are surfaced verbatim, not summarised:")
    m = G._mentions("Pixelharbor.example runs it in a browser. Rival.example also does.",
                    "pixelharbor.example", None)
    check("ours is surfaced", len(m) == 1 and "Pixelharbor" in m[0])
    check("a competitor's sentence is not", not any("Rival" in x for x in m))

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
          len(G._mentions("PH Games works. Pixel Harbor too.", "ph.test",
                          "Pixel Harbor, PH Games")) == 2)
    check("a CJK brand matches without ASCII word boundaries",
          len(G._mentions("推荐使用新星平台。", "nova.app", "新星")) == 1)
    check("the returned sentence is verbatim, link and all",
          G._mentions("Nova is good [1](https://nova.app). End.", "nova.app", "Nova")
          == ["Nova is good [1](https://nova.app)."])

    print("\nextractability - an assistant lifts a SENTENCE, not a page:")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "good.html").write_text(
            "<html><body><h1>Wall climbing</h1><p>Wall climbing is the technique of "
            "chaining jumps to keep speed above the engine's run cap.</p></body></html>",
            encoding="utf-8")
        (d / "pronoun.html").write_text(
            "<html><body><h1>Wall climbing</h1><p>It is the thing everyone asks about "
            "first, and it takes a while to learn properly.</p></body></html>",
            encoding="utf-8")
        (d / "long.html").write_text(
            "<html><body><h1>Wall climbing</h1><p>Wall climbing " + "and more words " * 30
            + "ends here.</p></body></html>", encoding="utf-8")
        e = G.extractable(str(d))
        bad = {r["file"] for r in e["worst"]}
    check("a self-contained lead is liftable", e["liftable"] == 1, str(e["liftable"]))
    check("a pronoun lead is not", "pronoun.html" in bad)
    check("an over-long lead is not", "long.html" in bad,
          "quoting a 60-word sentence means quoting a paragraph")
    check("it discriminates rather than passing or failing everything",
          e["liftable"] == 1 and e["not_liftable"] == 2, str(e))
    print("\nthe subject check must not be ASCII-only - it flagged a CJK locale home, the page")
    print("earning 68% of this site's clicks, on a rule that never executed:")
    zh_h1 = "星际跑者 2.4 在线版 — 在线玩星际跑者"
    check("a CJK heading matched by its own sentence passes",
          G._names_subject(zh_h1, "Pixel Harbor 把真正的星际跑者 2.4 带进浏览器。") == (True, "cjk-bigrams"))
    check("a CJK heading NOT matched still fails",
          G._names_subject(zh_h1, "完全无关的一句话关于别的东西。")[0] is False,
          "the bigram path has to discriminate, not just say yes")
    check("Cyrillic goes down the word path",
          G._names_subject("Играть в Star-Runner онлайн",
                           "Star-Runner запускается в браузере.") == (True, "words"))
    check("Cyrillic can still fail",
          G._names_subject("Играть в Star-Runner онлайн", "Совершенно другая тема.")[0] is False)
    check("Latin is unaffected",
          G._names_subject("Wall climbing", "Wall climbing is a technique.") == (True, "words"))
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
          len([x for x in G._SENT.split("Pixelharbor.example runs it.") if x.strip()]) == 1,
          "relaxing the ASCII rule to \\s* splits on every domain and abbreviation")
    n, unit, ok_ = G._sentence_length("Pixel Harborは本物のStar-Runner 2.4をブラウザに届けます")
    check("a Japanese sentence is measured in characters", unit == "chars",
          f"got {n} {unit} - by whitespace this reads as 3 'words'")
    check("and a real Japanese sentence is within bounds", ok_ is True, f"{n} chars")
    n2, unit2, _ = G._sentence_length("Wall climbing is the technique of chaining jumps.")
    check("English is still measured in words", unit2 == "words")
    nj, uj, okj = G._sentence_length(
        "Pixel Harborは本物のStar-Runner 2.4をWebAssemblyでブラウザに届けます")
    check("a mostly-Latin Japanese sentence is STILL measured in characters",
          uj == "chars" and okj is True,
          f"got {nj} {uj} - it is only 28% kana/kanji, so a ratio threshold "
          f"measures it as 3 'words'")
    _n3, _u3, ok3 = G._sentence_length("Too short.")
    check("a too-short lead still fails", ok3 is False)

    check("a single-sentence paragraph ending in a period IS structured",
          bool(G._TERM_END.search("Wall climbing and more words ends here.")),
          "the split rule finds no boundary here, and using IT to detect structure "
          "waives the length check on most paragraphs on a real site")
    check("a paragraph with no sentence punctuation is NOT structured",
          not G._TERM_END.search("Pixel Harbor ส่ง Star-Runner 2.4 เข้าเบราว์เซอร์"),
          "the dot in `Star-Runner 2.4` makes a bare terminator search say yes")
    check("a trailing quote or bracket does not hide the terminator",
          bool(G._TERM_END.search('He called it "the technique."')))

    print("\nthe subject rule tests whether the SENTENCE stands alone, not whether")
    print("it agrees with the h1's vocabulary:")
    check("a stylish headline does not sink a well-written lead",
          G._names_subject("A Classic That Never Needed Replacing",
                           "Star-Runner started life in 1999 as a mod for Moon-Base.")[0] is True,
          "matching only h1 tokens flagged this real page")
    check("an all-caps abbreviation counts as naming the subject",
          G._names_subject("Play Star-Runner 2.4 With Friends",
                           "Organising a game of SR 2.4 meant everyone owning it.")[0] is True)
    check("a lead that names nothing still fails",
          G._names_subject("Play Star-Runner 2.4 Free",
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
