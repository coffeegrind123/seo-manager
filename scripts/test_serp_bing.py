#!/usr/bin/env python3
"""Tests for the Bing provider and the DuckDuckGo exit/dedupe fixes in serp.py.

Offline: every page here is synthetic, built in the shape measured on real
Bing responses on 2026-10-12 (an `sb_form_q` search box, `<li class="b_algo">`
blocks with an h2 link wrapped as bing.com/ck/a?...&u=a1<base64>, and inline
JS that mentions "b_ad" and "Challenge" on a page with neither). The network
is replaced by a fake `fetch`, so the provider functions run end to end.

Expected values are written by hand, never computed by serp.py: the base64
literals were encoded separately, and every verdict below is what a human
reading the fixture would say.

The negative fixtures are the point, as in test_guards.py: a wrong-query page
and a collapsed page both carry a correct query echo and ten-row-shaped,
perfectly parseable results.

    python3 test_serp_bing.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serp  # noqa: E402

failures: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


# 'https://www.example.com/', 'https://example.org/guide?x=1' (padding
# stripped, as Bing serves it) and 'https://example.net/a'.
B64_COM = "aHR0cHM6Ly93d3cuZXhhbXBsZS5jb20v"
B64_ORG = "aHR0cHM6Ly9leGFtcGxlLm9yZy9ndWlkZT94PTE"
B64_NET = "aHR0cHM6Ly9leGFtcGxlLm5ldC9h"


def wrap(b64: str) -> str:
    return ("https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;ptn=3&amp;ver=2&amp;hsh=4"
            f"&amp;fclid=00&amp;u=a1{b64}&amp;ntb=1")


def page(echo, rows, *, title=None, ad=False, module=None) -> str:
    t = title if title is not None else (f"{echo} - Search" if echo else "Bing")
    box = (f'<input class="b_searchbox " id="sb_form_q" name="q" type="search" value="{echo}"\n'
           f'   maxlength="1000" />' if echo is not None else "")
    out = [f"<!DOCTYPE html><html><head><title>{t}</title></head><body>{box}",
           '<script>_G={adc:"b_ad"};v=["#b_results ."+_G.adc];'
           'r.className.indexOf("b_adTop")!==-1;var s="Challenge";</script><ol id="b_results">']
    if ad:
        out.append('<li class="b_ad b_adTop"><ul><li class="b_adLastChild"><h2>'
                   '<a href="https://www.bing.com/aclk?x=1">Ad: Hiking Boots Sale</a></h2></li></ul></li>')
    if module:
        out.append(f'<li class="{module}"><h4>Some results have been removed</h4></li>')
    for href, ttl, snip in rows:
        out.append('<li class="b_algo" data-id iid=SERP.5343><div class="b_tpcn"><a class="tilk" '
                   f'href="{href}"><cite>x</cite></a></div><h2><a target="_blank" href="{href}" '
                   f'h="ID=SERP,5126.2">{ttl}</a></h2><div class="b_caption">'
                   f'<p class="b_lineclamp2">{snip}</p></div></li>')
    out.append('<li class="b_pag"><nav><a href="/search?q=x&amp;first=11">Next</a></nav></li>'
               "</ol><div>Related searches</div></body></html>")
    return "".join(out)


Q = "best hiking boots"
GOOD = [
    (wrap(B64_COM), "The <strong>Best Hiking Boots</strong> of 2026", "Tested on 400 miles of trail."),
    (wrap(B64_ORG), "Hiking Boots Buying Guide", "How to fit hiking boots."),
    ("https://shop.example.com/c/hiking-boots?msockid=0af0914", "Hiking Boots | Shop", "Free returns on boots."),
    (wrap(B64_NET), "Best Waterproof Hiking Boots", "Our picks for wet trails."),
]
WRONG = [
    ("https://speed.example.com/", "Internet Speed Test", "Measure download and upload."),
    ("https://fast.example.net/", "Fast.example - Speed Test", "How fast is your connection."),
    ("https://isp.example.org/test", "Test Your Broadband", "Ping and jitter explained."),
    ("https://markets.example.com/", "Prediction Markets", "Trade on outcomes."),
]
CQ = "play trail games"
COLLAPSED = [
    ("https://store.example.com/", "Play Store - Apps on Store", "Download and play apps."),
    ("https://dict.example.org/play", "PLAY | Definition in the Dictionary", "To engage in activity."),
    ("https://portal.example.net/", "Play Free Online Games at Portal", "Thousands of games to play."),
    ("https://console.example.com/", "PlayConsole - Official Site", "Play now."),
]
# The same query answered properly - what a different exit got on 2026-10-31
# for the query every other exit collapsed.
CQ_GOOD = [
    ("https://trails.example.com/games", "Play Trail Games Online", "Trail games you can play free."),
    ("https://arcade.example.org/trail", "Trail Games - Play in Browser", "Play trail racing games."),
    ("https://mtb.example.net/play", "Play Mountain Trail Games", "Trail bike games to play."),
]
UNPARSED_PAGE = page(CQ, [])
CHALLENGE = ("<html><head><title>One last step</title></head><body><h1>One last step</h1>"
             "<p>Please solve the challenge below to continue</p><div id=\"b_captcha\"></div></body></html>")


# ---------------------------------------------------------------- fake net
class FakeNet:
    """Replaces serp.fetch. Routes by URL; records every call."""

    def __init__(self, serp_pages, exit_cc="us", ip_status=200):
        self.serp_pages = list(serp_pages)
        self.exit_cc = exit_cc
        self.ip_status = ip_status
        self.calls = []

    def __call__(self, url, *, data=None, headers=None, timeout=30, proxy=None):
        self.calls.append({"url": url, "data": data, "proxy_country": getattr(proxy, "country", None)})
        if url.startswith(serp.IP_ECHO_URL.split("?")[0]):
            if self.ip_status != 200 or self.exit_cc is None:
                return self.ip_status if self.ip_status != 200 else 200, "{\"status\":\"fail\"}"
            return 200, json.dumps({"status": "success", "countryCode": self.exit_cc.upper()})
        if not self.serp_pages:
            return 0, "no more pages"
        return self.serp_pages.pop(0)


@contextlib.contextmanager
def fake(net):
    orig_fetch, orig_sleep = serp.fetch, time.sleep
    serp.fetch, time.sleep = net, (lambda s: None)
    serp.results_meta.clear()
    try:
        yield net
    finally:
        serp.fetch, time.sleep = orig_fetch, orig_sleep


def proxy(cc=None):
    return serp.Proxy("http://user:pass@proxy.example.invalid:8080", country=cc)


def refusal(fn):
    try:
        fn()
    except serp.SerpRefusal as e:
        return e
    return None


def main() -> int:
    print("UNWRAP - Bing's /ck/a redirect to the real destination:")
    check("plain wrapper", serp.unwrap_bing(wrap(B64_COM)) == ("https://www.example.com/", True))
    check("padding-stripped wrapper", serp.unwrap_bing(wrap(B64_ORG)) == ("https://example.org/guide?x=1", True))
    check("undecodable wrapper is flagged, not trusted",
          serp.unwrap_bing("https://www.bing.com/ck/a?u=a1%%%")[1] is False)
    check("bing-internal relative target is not a result",
          serp.unwrap_bing("https://www.bing.com/ck/a?u=a1L21hcHM_cT1ib290cw")[1] is False)
    check("msockid stripped", serp.unwrap_bing("https://shop.example.com/c?a=1&msockid=9")[0]
          == "https://shop.example.com/c?a=1")

    print("\nPARSE - a valid page:")
    p = serp.parse_bing(page(Q, GOOD, ad=True, module="b_msg"))
    check("four organic rows, the ad not among them", p["organic_count"] == 4, p["organic_count"])
    check("ad detected", p["ads_detected"] is True, p["ad_markers"])
    check("module recorded", p["modules_detected"] == ["b_msg"], p["modules_detected"])
    check("urls unwrapped in order",
          [r["url"] for r in p["results"]] == ["https://www.example.com/", "https://example.org/guide?x=1",
                                               "https://shop.example.com/c/hiking-boots", "https://example.net/a"],
          [r["url"] for r in p["results"]])
    check("title tags stripped", p["results"][0]["title"] == "The Best Hiking Boots of 2026", p["results"][0]["title"])
    check("snippet read from b_caption", p["results"][1]["snippet"] == "How to fit hiking boots.")
    check("echo from the search box", p["echo"] == Q)
    check("valid page accepted", serp.bing_guard(Q, p)["refused"] is False, serp.bing_guard(Q, p))
    plain = serp.parse_bing(page(Q, GOOD))
    check("inline JS 'b_ad' is not an ad", plain["ads_detected"] is False, plain["ad_markers"])
    check("inline JS 'Challenge' is not a challenge", plain["challenge"] is None)
    check("no modules on a plain page", plain["modules_detected"] == [])

    print("\nGUARDS - the pages that must be refused, each under its OWN name:")
    w = serp.bing_guard(Q, serp.parse_bing(page(Q, WRONG)))
    check("wrong-query page -> wrong_query", w.get("kind") == "wrong_query", w)
    c = serp.bing_guard(CQ, serp.parse_bing(page(CQ, COLLAPSED)))
    check("collapsed page -> query_collapse", c.get("kind") == "query_collapse", c)
    check("collapse names the word it collapsed to", c.get("collapse", {}).get("collapsed_to") == "play")
    check("collapse and wrong-query are distinct kinds", c.get("kind") != w.get("kind"))
    ch = serp.bing_guard(Q, serp.parse_bing(CHALLENGE))
    check("challenge page -> challenge", ch.get("kind") == "challenge", ch)
    em = serp.bing_guard(Q, serp.parse_bing(page("internet speed test", GOOD)))
    check("echo of another query -> wrong_query + echo_mismatch",
          em.get("kind") == "wrong_query" and em.get("echo_mismatch") is True and em.get("answered") == "internet speed test", em)
    nm = serp.bing_guard(Q, serp.parse_bing(page(None, GOOD, title="Bing")))
    check("no echo at all -> echo_missing", nm.get("kind") == "echo_missing", nm)
    te = serp.bing_guard(Q, serp.parse_bing(page(None, GOOD, title=f"{Q} - Search")))
    check("title echo is accepted when the box is absent",
          te.get("refused") is False and te.get("echo_source") == "title", te)
    un = serp.bing_guard(Q, serp.parse_bing(page(Q, [])))
    check("200 with no rows -> unparsed, never an empty SERP", un.get("kind") == "unparsed", un)
    check("challenge is cannot_ask", serp.SerpRefusal("challenge", "x").as_dict()["class"] == "cannot_ask")
    check("wrong_query is wrong_answer", serp.SerpRefusal("wrong_query", "x").as_dict()["class"] == "wrong_answer")
    check("query_collapse is wrong_answer",
          serp.SerpRefusal("query_collapse", "x").as_dict()["class"] == "wrong_answer")

    print("\nCOLLAPSE detector - both directions:")
    hole = [{"title": "BEST Definition & Meaning", "url": "https://dict.example.org/best", "snippet": ""},
            {"title": "Best - Encyclopedia", "url": "https://enc.example.org/Best", "snippet": ""},
            {"title": "Best Electronics Store", "url": "https://bestshop.example.com/", "snippet": ""}]
    check("one-token query: relevance alone passes the collapse (the hole)",
          serp.verify_relevance("best boots", hole)["pass"] is True)
    check("...and detect_collapse catches it", serp.detect_collapse("best boots", hole)["collapse"] is True)
    brand = [{"title": "Examplesoft Media Player", "url": "https://examplesoft.example.com/", "snippet": "Free player."},
             {"title": "Examplesoft - Wikipedia", "url": "https://enc.example.org/Examplesoft", "snippet": ""},
             {"title": "Examplesoft for Windows", "url": "https://dl.example.net/examplesoft", "snippet": ""}]
    check("a brand SERP missing the trailing qualifier is NOT a collapse (non-lead word not tested)",
          serp.detect_collapse("download examplesoft", brand)["collapse"] is False)
    check("word-start match: 'art' does not hit 'start'",
          serp.detect_collapse("art supplies", [{"title": "Start here", "url": "", "snippet": ""}] * 3)
          ["lead_share"] == 0.0)
    check("one-word query is not judged", serp.detect_collapse("boots", hole)["checked"] is False)

    print("\nPROVIDER bing - end to end over a fake network:")
    with fake(FakeNet([(200, page(Q, GOOD, ad=True))], exit_cc="us")) as net:
        rows = serp.provider_bing(Q, 10, proxy=proxy(), gl="us")
        m = dict(serp.results_meta)
    check("returns the four organic rows", len(rows) == 4, len(rows))
    check("engine reported as bing", m.get("engine") == "bing")
    check("exit confirmed as us", m.get("exit_country") == "us" and m.get("exit_unconfirmed") is False, m)
    check("unpinned proxy was pinned to the market", net.calls[0]["proxy_country"] == "us", net.calls)
    check("cc and setlang sent", "cc=US" in net.calls[1]["url"] and "setlang=en" in net.calls[1]["url"],
          net.calls[1]["url"])
    check("no pagination parameter sent (measured ignored)", "first=" not in net.calls[1]["url"])
    check("depth never claims beyond what was read",
          m.get("depth", {}).get("read") == 4 and m["depth"]["pages_read"] == 1, m.get("depth"))
    check("organic count and ad presence recorded",
          m.get("bing", {}).get("organic_count") == 4 and m["bing"]["ads_detected"] is True, m.get("bing"))

    with fake(FakeNet([(200, page(Q, GOOD))], exit_cc="gb")):
        e = refusal(lambda: serp.provider_bing(Q, 10, proxy=proxy("de")))
    check("exit landed elsewhere -> exit_mismatch refusal, no SERP fetch",
          e is not None and e.kind == "exit_mismatch" and e.as_dict()["class"] == "cannot_ask",
          e and e.as_dict())

    with fake(FakeNet([(200, page(Q, GOOD))], exit_cc=None)):
        rows = serp.provider_bing(Q, 10, proxy=proxy("de"), gl="us")
        m = dict(serp.results_meta)
    check("unconfirmable exit is reported null + unconfirmed, not dropped",
          m.get("exit_country") is None and m.get("exit_unconfirmed") is True and m.get("exit_requested") == "de", m)
    check("--proxy-country wins over --gl for the market", m.get("bing", {}).get("market") == "de")

    with fake(FakeNet([(200, CHALLENGE)])):
        e = refusal(lambda: serp.provider_bing(Q, 10, proxy=proxy()))
    check("challenge -> SerpRefusal(challenge)", e is not None and e.kind == "challenge", e)
    check("refusal carries the exit it was asked from", e is not None and e.detail.get("exit_country") == "us")
    with fake(FakeNet([(200, page(CQ, COLLAPSED))])):
        e = refusal(lambda: serp.provider_bing(CQ, 10, proxy=proxy()))
    check("collapse -> SerpRefusal(query_collapse)", e is not None and e.kind == "query_collapse", e)
    with fake(FakeNet([(503, "unavailable")])):
        e = refusal(lambda: serp.provider_bing(Q, 10, proxy=proxy()))
    check("HTTP 503 -> transport", e is not None and e.kind == "transport", e)
    with fake(FakeNet([])):
        e = refusal(lambda: serp.provider_bing(Q, 10, proxy=proxy("zz")))
    check("unverified market refused before any request", e is not None and e.kind == "unverified_market", e)

    print("\nPROVIDER bing - per-exit refusals rotate the exit (measured 2026-10-31):")
    with fake(FakeNet([(200, page(CQ, COLLAPSED)), (200, page(CQ, CQ_GOOD))])) as net:
        px = proxy("us")
        first_session = px.session
        rows = serp.provider_bing(CQ, 10, proxy=px, gl="us")
        m = dict(serp.results_meta)
    check("collapse on exit 1, good page on exit 2 -> rows returned", len(rows) == 3, len(rows))
    tried = m.get("bing", {}).get("attempts", [])
    check("both attempts reported, the refused one with its kind",
          len(tried) == 2 and tried[0].get("kind") == "query_collapse" and tried[1].get("kind") == "ok", tried)
    check("the two attempts used different sessions (a new exit IP)",
          len({a.get("session") for a in tried}) == 2 and tried[0].get("session") == first_session, tried)
    check("every attempt re-confirmed its exit", sum(1 for c in net.calls if "ip-api" in c["url"]) == 2,
          net.calls)
    check("the successful read's exit is the one reported", m.get("exit_country") == "us", m)

    n = serp.BING_EXIT_ATTEMPTS
    with fake(FakeNet([(200, page(CQ, COLLAPSED))] * n)) as net:
        e = refusal(lambda: serp.provider_bing(CQ, 10, proxy=proxy("us")))
    check("every exit collapses -> still a query_collapse refusal, never rows",
          e is not None and e.kind == "query_collapse", e and e.as_dict())
    check("the refusal lists every attempt",
          e is not None and len(e.detail.get("attempts", [])) == n, e and e.detail.get("attempts"))
    check("attempts are bounded", sum(1 for c in net.calls if "ip-api" not in c["url"]) == n, len(net.calls))

    with fake(FakeNet([(200, CHALLENGE)] + [(200, page(CQ, COLLAPSED))] * (n - 1))):
        e = refusal(lambda: serp.provider_bing(CQ, 10, proxy=proxy("us")))
    check("an engine ANSWER outranks cannot-ask in the final kind",
          e is not None and e.kind == "query_collapse", e and e.as_dict())

    with fake(FakeNet([(200, UNPARSED_PAGE), (200, page(CQ, CQ_GOOD))])):
        e = refusal(lambda: serp.provider_bing(CQ, 10, proxy=proxy("us")))
    check("unparsed is a PARSER problem: not retried on another exit",
          e is not None and e.kind == "unparsed" and len(e.detail.get("attempts", [])) == 1, e and e.as_dict())

    with fake(FakeNet([(200, page(CQ, COLLAPSED)), (200, page(Q, GOOD))], exit_cc=None)) as net:
        e = refusal(lambda: serp.provider_bing(CQ, 10, proxy=None))
    check("no proxy -> one attempt only (the IP cannot change)",
          e is not None and e.kind == "query_collapse" and len(e.detail.get("attempts", [])) == 1,
          e and e.as_dict())

    print("\nPROVIDER ddg - dedupe and exit reporting:")

    def ddg_page(rows):
        body = "".join(f'<a rel="nofollow" class="result__a" href="{u}">{t}</a>'
                       f'<a class="result__snippet" href="{u}">{s}</a>' for u, t, s in rows)
        return f"<html><body>{body}</html>"

    p1 = [(f"https://site{i}.example.com/hiking-boots", f"Hiking boots {i}", "boots") for i in range(1, 11)]
    # Page 2 as measured: positions 11-12 repeat 1-2, then new results.
    p2 = p1[:2] + [(f"https://more{i}.example.org/boots", f"More boots {i}", "boots") for i in range(1, 9)]
    with fake(FakeNet([(200, ddg_page(p1)), (200, ddg_page(p2))], exit_cc=None)):
        rows = serp.provider_ddg(Q, 20, proxy=proxy("de"))
        m = dict(serp.results_meta)
    urls = [r["url"] for r in rows]
    check("repeated results dropped", len(urls) == len(set(urls)) == 18, len(urls))
    check("first position kept for a repeat", urls[0] == "https://site1.example.com/hiking-boots")
    check("drop count reported", m.get("depth", {}).get("duplicates_dropped") == 2, m.get("depth"))
    check("requested-but-unconfirmed exit said explicitly",
          m.get("exit_country") is None and m.get("exit_unconfirmed") is True and m.get("exit_requested") == "de", m)

    with fake(FakeNet([(200, ddg_page(p1)), (200, ddg_page(p1))], exit_cc="de")):
        rows = serp.provider_ddg(Q, 20, proxy=proxy("de"))
        m = dict(serp.results_meta)
    check("a page that is all repeats stops pagination", len(rows) == 10 and m["depth"]["pagination_repeated"] is True,
          m.get("depth"))
    check("confirmed exit reported", m.get("exit_country") == "de" and m.get("exit_unconfirmed") is False, m)

    print("\nMAIN - a refused provider's metadata does not leak into the one that answered:")

    def fake_bing(*a, **k):
        serp.results_meta.update({"engine": "bing", "exit_country": "us", "bing": {"x": 1}})
        raise serp.SerpRefusal("query_collapse", "collapsed")

    def fake_ddg(*a, **k):
        return [{"title": "Best Hiking Boots", "url": "https://a.example.com/hiking-boots", "snippet": "best hiking boots"},
                {"title": "Hiking Boots Guide", "url": "https://b.example.com/boots", "snippet": "hiking boots"}]

    def fake_fail(*a, **k):
        raise RuntimeError("not configured in test")

    saved = dict(serp.PROVIDERS)
    saved_argv = sys.argv
    try:
        for k in serp.PROVIDERS:
            serp.PROVIDERS[k] = fake_fail
        serp.PROVIDERS["bing"], serp.PROVIDERS["ddg"] = fake_bing, fake_ddg
        serp.results_meta.clear()
        sys.argv = ["serp.py", Q, "--provider", "bing", "--no-proxy", "--raw"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            serp.main()
        out = json.loads(buf.getvalue())
    finally:
        serp.PROVIDERS.clear()
        serp.PROVIDERS.update(saved)
        sys.argv = saved_argv
    check("fell back to ddg", out.get("provider") == "ddg" and out.get("fell_back_from") == "bing", out.get("provider"))
    check("engine is the answering one", out.get("engine") == "duckduckgo", out.get("engine"))
    check("no bing exit on the ddg payload", "exit_country" not in out and "bing" not in out, sorted(out))
    check("bing refusal kept, by name", (out.get("provider_refusals") or {}).get("bing", {}).get("kind")
          == "query_collapse", out.get("provider_refusals"))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        return 1
    print("all serp bing tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
