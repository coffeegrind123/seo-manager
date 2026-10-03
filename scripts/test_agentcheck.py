#!/usr/bin/env python3
"""Regression tests for the agent-readiness checks.

The robots.txt resolver is the part that must be right: it decides whether the
tool says "assistants cannot cite you" or "you are fine", and Google's
precedence rules are unintuitive enough that a plausible-looking parser gets
them wrong silently. Consecutive `User-agent:` lines sharing one rule block,
longest-match wins, ties to Allow, and `Disallow:` with an empty value meaning
*allow everything* are each tested here.

Verified live while writing: nytimes.com trips `farmed_not_read` (blocks every
citing crawler, allows a training one) while reddit.com does NOT (it blocks
everything, which is a coherent policy). That distinction is the whole point of
the rule, so it is pinned below.

Run: python3 test_agentcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentcheck as ac  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


print("robots.txt parsing")

g = ac.parse_robots("""
User-agent: *
Disallow: /api/
Allow: /

User-agent: GPTBot
User-agent: CCBot
Disallow: /
""")
check("consecutive User-agent lines share ONE rule block",
      len(g) == 2 and sorted(g[1]["agents"]) == ["ccbot", "gptbot"], str(g))
check("comments and blank lines ignored", len(g[0]["rules"]) == 2, str(g[0]))

g2 = ac.parse_robots("Disallow: /orphan\nUser-agent: *\nAllow: /")
check("a directive before any User-agent is discarded",
      len(g2) == 1 and g2[0]["rules"] == [("allow", "/")], str(g2))

print("\nrule precedence (Google's, not first-match)")

G = ac.parse_robots("""
User-agent: *
Disallow: /
User-agent: GPTBot
Allow: /
""")
check("a specific UA group beats the wildcard", ac.allowed(G, "GPTBot", "/")["allowed"])
check("an unlisted UA falls to the wildcard", not ac.allowed(G, "SomeBot", "/")["allowed"])

L = ac.parse_robots("User-agent: *\nDisallow: /\nAllow: /public/")
check("longest match wins (allow deeper than disallow)",
      ac.allowed(L, "X", "/public/a")["allowed"])
check("the shorter disallow still applies elsewhere",
      not ac.allowed(L, "X", "/private/a")["allowed"])

T = ac.parse_robots("User-agent: *\nDisallow: /x\nAllow: /x")
check("an equal-length tie goes to Allow", ac.allowed(T, "X", "/x")["allowed"])

E = ac.parse_robots("User-agent: BadBot\nDisallow:")
check("`Disallow:` with an empty value allows everything",
      ac.allowed(E, "BadBot", "/anything")["allowed"], str(ac.allowed(E, "BadBot", "/anything")))

W = ac.parse_robots("User-agent: *\nDisallow: /*.pdf$")
check("wildcard + end-anchor matches", not ac.allowed(W, "X", "/a/b.pdf")["allowed"])
check("end-anchor does not over-match", ac.allowed(W, "X", "/a/b.pdf.html")["allowed"])

check("no robots rules at all = allowed", ac.allowed([], "X", "/")["allowed"])

print("\nthe policy findings must fire")


def policy_from(text, monkey_status=200):
    """Drive check_policy against a literal robots.txt body."""
    class R(dict):
        def text(self_):
            return text
    real = ac.http

    def fake(url, **kw):
        r = R(status=monkey_status, ctype="text/plain", body=text.encode())
        return r
    ac.http = fake
    try:
        return ac.check_policy("https://x.test")
    finally:
        ac.http = real


def rules_of(res):
    return {f["rule"] for f in res.get("findings", [])}


allow_all = "User-agent: *\nAllow: /\nSitemap: https://x.test/sitemap.xml\n"
r = policy_from(allow_all)
check("a fully open robots.txt passes", r["verdict"] == "pass", str(rules_of(r)))
check("ai_search counted as fully allowed",
      r["summary"]["ai_search"]["allowed"] == r["summary"]["ai_search"]["total"])

# Derived from the taxonomy rather than pinned to a snapshot of it. A hardcoded
# roster here fails every time an answer engine is ADDED - which is a change to
# the world, not a regression - and the failure names the wrong thing: it read
# "ai_search_fully_blocked does not fire" when the truth was "there is now a
# citing crawler this fixture never blocked". (GrokBot, added 2026-09-01.)
#
# The assertion below is about the RULE - block every citing crawler and the
# verdict is `fully`, not `partially` - so deriving the input cannot make it a
# mirror of the implementation. The roster itself is checked independently
# straight after, so a taxonomy that silently emptied still fails.
from crawllog import BOTS as _BOTS  # noqa: E402
_ai_search = [label for _k, label, cat, _v in _BOTS if cat == "ai_search"]
check("the ai_search roster is not empty", len(_ai_search) >= 4, str(_ai_search))
check("and still holds the engines this fixture was written around",
      {"OAI-SearchBot", "PerplexityBot", "Claude-SearchBot"} <= set(_ai_search),
      str(sorted(_ai_search)))
block_search = (allow_all + "".join(f"\nUser-agent: {b}" for b in _ai_search)
                + "\nDisallow: /\n")
r = policy_from(block_search)
check("ai_search_fully_blocked fires", "ai_search_fully_blocked" in rules_of(r), str(rules_of(r)))
check("farmed_not_read fires when trainers stay allowed",
      "farmed_not_read" in rules_of(r), str(rules_of(r)))

# The reddit case: block EVERYTHING. Incoherence is the finding, not blocking.
block_all = "User-agent: *\nDisallow: /\n"
r = policy_from(block_all)
check("blocking everything does NOT trip farmed_not_read (it is coherent)",
      "farmed_not_read" not in rules_of(r), str(rules_of(r)))
check("blocking everything still reports ai_search_fully_blocked",
      "ai_search_fully_blocked" in rules_of(r))

r = policy_from(allow_all + "\nUser-agent: ChatGPT-User\nDisallow: /\n")
check("ai_user_blocked fires", "ai_user_blocked" in rules_of(r), str(rules_of(r)))

r = policy_from("User-agent: *\nAllow: /\n")
check("no_sitemap_directive fires", "no_sitemap_directive" in rules_of(r))

r = policy_from(allow_all + "\nNoindex: /x\n")
check("unsupported_directive fires on Noindex:", "unsupported_directive" in rules_of(r))

r = policy_from("<!DOCTYPE html><html><body>Not found</body></html>")
check("robots_is_html fires on a soft-404", "robots_is_html" in rules_of(r), str(rules_of(r)))

r = policy_from(allow_all, monkey_status=503)
check("a 503 robots.txt is a FAILED READ, not an open policy",
      r.get("ok") is False and "5xx" in (r.get("detail") or "") + "5xx", str(r)[:120])

print("\npage checks")


def page_from(doc):
    class R(dict):
        def text(self_):
            return doc

    real = ac.http
    calls = {"n": 0}

    def fake(url, **kw):
        calls["n"] += 1
        if url.endswith(".md"):                       # the markdown probe
            return R(status=404, ctype="text/html", body=b"")
        return R(status=200, ctype="text/html", body=doc.encode())
    ac.http = fake
    try:
        return ac.check_page("https://x.test/p")
    finally:
        ac.http = real


BODY = "<html><head><title>T</title></head><body><main><h1>H</h1>" + ("word " * 300) + "</main></body></html>"
r = page_from(BODY)
check("a clean semantic page passes", r["verdict"] == "pass", str(rules_of(r)))

r = page_from("<html><head><title>T</title></head><body><div id=root></div>"
              + "<script>" + ("x=1;" * 3000) + "</script></body></html>")
check("requires_javascript fires on an empty shell",
      "requires_javascript" in rules_of(r), str(rules_of(r)))

r = page_from(BODY.replace("<main>", "<div onclick='go()'>").replace("</main>", "</div>"))
check("div_onclick_without_role fires", "div_onclick_without_role" in rules_of(r), str(rules_of(r)))
check("no_main_landmark fires when <main> is gone", "no_main_landmark" in rules_of(r))

r = page_from(BODY.replace("<h1>H</h1>",
                           '<h1>H</h1><form><input type="text" name="q"></form>'))
check("unlabelled_input fires", "unlabelled_input" in rules_of(r), str(rules_of(r)))

r = page_from(BODY.replace("<h1>H</h1>",
                           '<h1>H</h1><form><label for="q">Q</label><input id="q" type="text"></form>'))
check("a properly labelled input does NOT fire",
      "unlabelled_input" not in rules_of(r), str(rules_of(r)))

r = page_from(BODY.replace("<h1>H</h1>", '<h1>H</h1><input type="hidden" name="csrf">'))
check("hidden/submit inputs are not counted as unlabelled",
      "unlabelled_input" not in rules_of(r), str(rules_of(r)))

r = page_from(BODY.replace("<h1>H</h1>", '<h1>H</h1><img src=a.png><img src=b.png alt="b">'))
check("img_without_alt fires", "img_without_alt" in rules_of(r))

# Structural checks must read MARKUP, not raw HTML. Measured on a real site
# 2026-08-01: the only "<img>" on the page was the literal string inside a comment
# explaining why the decorative art deliberately uses no <img>, and it was reported
# as an image with no alt. Both directions are tested, because the false NEGATIVE
# is the dangerous one - a comment that merely mentions <main> must not satisfy the
# landmark check on a page that has none.
r = page_from(BODY.replace("<h1>H</h1>",
                           '<h1>H</h1><!-- decorative art, deliberately no <img> here -->'))
check("an <img> inside a COMMENT is not counted",
      "img_without_alt" not in rules_of(r), str(r["structure"]))
check("...and the image count itself stays 0", r["structure"]["images"] == 0, str(r["structure"]))

r = page_from(BODY.replace("<main>", "<div>").replace("</main>", "</div>")
                  .replace("<h1>H</h1>", '<h1>H</h1><!-- there is no <main> on this page -->'))
check("a COMMENT mentioning <main> does not satisfy the landmark check",
      "no_main_landmark" in rules_of(r), str(rules_of(r)))

r = page_from(BODY.replace("<h1>H</h1>",
                           '<h1>H</h1><script>var t = \'<input type="text" name="q">\';</script>'))
check("an <input> inside a <script> is not a phantom form field",
      "unlabelled_input" not in rules_of(r), str(r["structure"]))

r = page_from(BODY.replace("<h1>H</h1>", '<h1>H</h1><form toolname="search" '
                                         'tooldescription="Search the catalogue"></form>'))
check("WebMCP tool forms are detected", r["webmcp"]["forms_with_tools"] == 1, str(r["webmcp"]))
check("WebMCP absence is never a finding",
      not any("webmcp" in x for x in rules_of(page_from(BODY))))

check("layout-dependent checks are declared unmeasurable, not passed",
      len(page_from(BODY)["unmeasurable_statically"]) >= 4)

print("\nllms.txt framing")


def llms_from(status, body, ctype="text/plain"):
    class R(dict):
        def text(self_):
            return body
    real = ac.http
    ac.http = lambda url, **kw: R(status=status, ctype=ctype, body=body.encode())
    try:
        return ac.check_llms("https://x.test")
    finally:
        ac.http = real


r = llms_from(200, "# Title\n\n> Summary here\n\n## Docs\n- [A](https://x.test/a)\n")
check("a well-formed llms.txt passes", r["verdict"] == "pass",
      str({f["rule"] for f in r["findings"]}))
r = llms_from(200, "<html><body>404</body></html>", ctype="text/html")
check("llms_txt_is_html fires", "llms_txt_is_html" in {f["rule"] for f in r["findings"]})
r = llms_from(404, "")
check("a missing llms.txt is LOW, never critical",
      all(f["severity"] in ("low", "info") for f in r["findings"]), str(r["findings"]))
check("the framing never claims a ranking benefit",
      "never as a ranking or citation signal" in r["framing"])

# robots.txt groups DO NOT INHERIT. A named group whose body is just `Allow: /`
# grants every path the `*` group closes, and nothing in the file looks wrong -
# the exclusions are right there, a few lines up, in a group that does not apply.
# Measured 2026-09-01: 18 named agent groups reached up to 11 disallowed paths
# each, including /g/ (game binaries) and /api/, while `policy` on `/` said PASS.
def _escapes(text):
    groups = ac.parse_robots(text)
    star = [r[1] for g in groups if "*" in g["agents"]
            for r in g["rules"] if r[0] == "disallow" and r[1]]
    named = {a for g in groups for a in g["agents"] if a != "*"}
    return {b: len([d for d in star if ac.allowed(groups, b, d)["allowed"]])
            for b in named
            if any(ac.allowed(groups, b, d)["allowed"] for d in star)}

BROKEN = "User-agent: *\nDisallow: /api/\nDisallow: /g/\nAllow: /\n\nUser-agent: GPTBot\nAllow: /\n"
REPEATED = ("User-agent: *\nDisallow: /api/\nDisallow: /g/\nAllow: /\n\n"
            "User-agent: GPTBot\nDisallow: /api/\nDisallow: /g/\nAllow: /\n")
NO_NAMED = "User-agent: *\nDisallow: /api/\nAllow: /\n"

check("a named group with a bare Allow:/ is caught escaping", _escapes(BROKEN))
check("and the count is the number of paths it reaches",
      _escapes(BROKEN).get("gptbot") == 2)
check("repeating the exclusions inside the named group clears it",
      not _escapes(REPEATED))
check("CONTROL: a file with no named group cannot escape", not _escapes(NO_NAMED))
check("CONTROL: the probe finds nothing when `*` disallows nothing",
      not _escapes("User-agent: *\nAllow: /\n\nUser-agent: GPTBot\nAllow: /\n"))

print("\nline splitting (RFC 9309 / Google's parser: CR, LF, CRLF only)")

# `str.splitlines()` also breaks on U+2028, U+2029, U+0085, VT, FF and the
# C0 separators. Every one of those turns the tail of a COMMENT into a live
# rule that no real crawler reads - a phantom `Disallow: /` reported as policy.
for sep, label in ((" ", "U+2028"), (" ", "U+2029"), ("\x85", "NEL"),
                   ("\x0b", "VT"), ("\x0c", "FF"), ("\x1c", "FS")):
    t = f"User-agent: GPTBot\n# note{sep}Disallow: /\nAllow: /\n"
    gg = ac.parse_robots(t)
    check(f"a {label} inside a comment does not create a rule",
          gg and gg[0]["rules"] == [("allow", "/")], str(gg))
check("CONTROL: CRLF still splits",
      ac.parse_robots("User-agent: *\r\nDisallow: /x\r\n")[0]["rules"] == [("disallow", "/x")])
check("CONTROL: a bare CR still splits",
      ac.parse_robots("User-agent: *\rDisallow: /x\r")[0]["rules"] == [("disallow", "/x")])
gb = ac.parse_robots("User-agent:\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
check("an EMPTY User-agent value matches no crawler",
      ac.allowed(gb, "GPTBot", "/x")["allowed"] is True, str(ac.allowed(gb, "GPTBot", "/x")))
check("a leading BOM does not hide the first User-agent",
      ac.parse_robots("﻿User-agent: GPTBot\nDisallow: /\n")[0]["agents"] == ["gptbot"])


print("\nRFC 9309 2.2.1: every group matching a crawler is COMBINED")
CF = (Path(__file__).resolve().parent.parent / "assets" / "fixtures" / "agentcheck"
      / "cloudflare-managed-robots.txt").read_text(encoding="utf-8")
gcf = ac.parse_robots(CF)
check("Cloudflare-managed: the origin's Disallow survives the prepended `*` group",
      ac.allowed(gcf, "Googlebot", "/lp")["allowed"] is False, str(ac.allowed(gcf, "Googlebot", "/lp")))
check("CONTROL: and the managed Allow still opens everything else",
      ac.allowed(gcf, "Googlebot", "/pricing")["allowed"] is True)
check("Cloudflare-managed: GPTBot is closed by its own named group",
      ac.allowed(gcf, "GPTBot", "/")["allowed"] is False)
two = ac.parse_robots("User-agent: GPTBot\nDisallow: /a\n\nUser-agent: *\nDisallow: /\n\n"
                      "User-agent: GPTBot\nDisallow: /b\n")
check("two groups naming the same crawler combine",
      not ac.allowed(two, "GPTBot", "/a")["allowed"] and not ac.allowed(two, "GPTBot", "/b")["allowed"]
      and ac.allowed(two, "GPTBot", "/c")["allowed"])

print("\nCloudflare managed robots.txt, and Content-Signal coverage")
mg = ac.cloudflare_managed(CF)
check("the managed block is detected", mg["present"] is True, mg)
check("and the agents it closes are named", {"gptbot", "claudebot", "google-extended"} <= set(mg["disallows"]),
      mg)
check("CONTROL: an ordinary robots.txt is not managed",
      ac.cloudflare_managed("User-agent: *\nDisallow: /x\n")["present"] is False)
gaps = ac.content_signal_gaps(gcf)
check("named groups without the `*` group's Content-Signal are listed",
      "gptbot" in gaps and "amazonbot" in gaps, gaps)
check("CONTROL: no `*` signal means no gap to report",
      ac.content_signal_gaps(ac.parse_robots("User-agent: *\nAllow: /\nUser-agent: GPTBot\nDisallow: /\n")) == [])
check("CONTROL: a named group that repeats the signal is not a gap",
      ac.content_signal_gaps(ac.parse_robots(
          "User-agent: *\nContent-Signal: ai-train=no\nAllow: /\n\n"
          "User-agent: GPTBot\nContent-Signal: ai-train=no\nDisallow: /x\n")) == [])

print("\nbot-challenge fingerprints (every pattern in a row must match)")
ch = ac.challenge_vendor
check("Cloudflare interstitial", ch(403, {"cf-mitigated": "challenge"}, "<html>x</html>") == "cloudflare")
check("Cloudflare by body", ch(403, {}, "<title>Just a moment...</title> /cdn-cgi/challenge-platform/h/g/orchestrate") == "cloudflare")
check("DataDome", ch(403, {}, "<script src='https://ct.captcha-delivery.com/c.js'>") == "datadome")
check("Akamai needs BOTH the phrase and a reference",
      ch(403, {}, "Access Denied. Reference #18.6f2b1402.1727950000.abc1234") == "akamai"
      and ch(403, {}, "Access Denied to this page, please log in") is None)
check("PerimeterX", ch(403, {}, "<div id='px-captcha'></div>") == "perimeterx")
check("a long real page mentioning 'Just a moment' is not a challenge",
      ch(200, {}, "<title>Just a moment</title>" + " word" * 400) is None)
check("CONTROL: an ordinary 200 is no challenge", ch(200, {}, "<html><p>hello</p></html>") is None)

print("\nreachability verdict: a spoofed AI UA is only evidence when spoofed Googlebot passes")
B = {"status": 200, "text_len": 5000, "challenge": None}
OK = {"status": 200, "text_len": 4900, "challenge": None}
NO = {"status": 403, "text_len": 20, "challenge": None}
v = ac.reach_verdict(B, OK, {"GPTBot": NO, "OAI-SearchBot": OK})
check("browser 200 + googlebot 200 + GPTBot 403 = an edge rule against GPTBot",
      v["state"] == "measured" and v["bots"]["GPTBot"] == "refused" and v["bots"]["OAI-SearchBot"] == "served",
      v)
v = ac.reach_verdict(B, NO, {"GPTBot": NO})
check("spoofed Googlebot ALSO refused = the edge verifies bots by IP: cannot ask from here",
      v["state"] == "cannot_ask" and v["bots"] == {}, v)
v = ac.reach_verdict(NO, OK, {"GPTBot": NO})
check("the browser control refused = no baseline, cannot ask", v["state"] == "cannot_ask", v)
v = ac.reach_verdict(B, OK, {"GPTBot": {"status": 200, "text_len": 300, "challenge": None}})
check("a 200 carrying a fraction of the page is `differs`, not `served`", v["bots"]["GPTBot"] == "differs", v)
v = ac.reach_verdict(B, OK, {"GPTBot": {"status": None, "text_len": 0, "challenge": None}})
check("no answer at all is `silent`, never `refused`", v["bots"]["GPTBot"] == "silent", v)
v = ac.reach_verdict(B, OK, {"GPTBot": {"status": 200, "text_len": 900, "challenge": "cloudflare"}})
check("a 200 challenge page is `challenged`", v["bots"]["GPTBot"] == "challenged", v)

print("\nllms.txt read classification - a CDN 403 is not an absent file")
lc = ac.classify_llms_read
check("404 is absent", lc(404, "text/html", "") == "absent")
check("403 is blocked, not absent", lc(403, "text/html", "denied") == "blocked")
check("406 is blocked, not absent", lc(406, "", "") == "blocked")
check("200 HTML is a soft-404, not a file", lc(200, "text/html", "<!doctype html><html>") == "html_served")
check("200 text is present", lc(200, "text/plain", "# Site\n> x\n") == "present")
check("5xx is a failed read", lc(503, "", "") == "failed")

print("\nai-catalog.json (ARD) validation - the conformance suite's two tiers")
import json as _json
CFCAT = _json.loads((Path(__file__).resolve().parent.parent / "assets" / "fixtures" / "agentcheck"
                     / "cloudflare-ai-catalog.json").read_text(encoding="utf-8"))
v = ac.validate_ai_catalog(CFCAT)
check("Cloudflare's live catalog (extension media types) has NO errors", v["errors"] == [], v)
good = {"specVersion": "1.0", "entries": [{
    "identifier": "urn:air:example.com:docs", "displayName": "Docs",
    "type": "application/mcp-server-card+json", "url": "https://example.com/mcp.json",
    "representativeQueries": ["how do I", "what is"]}]}
v = ac.validate_ai_catalog(good)
check("a well-formed catalog has no errors and no warnings", v == {"errors": [], "warnings": []}, v)
bad = {"specVersion": "0.9", "collections": [], "entries": [{
    "identifier": "example", "type": "not a media type", "url": "x", "data": {},
    "representativeQueries": ["one"]}]}
v = ac.validate_ai_catalog(bad)
for frag in ("specVersion", "identifier", "displayName", "exactly one"):
    check(f"bad catalog ERRORS on `{frag}`", any(frag in e for e in v["errors"]), v)
for frag in ("collections", "media type", "representativeQueries"):
    check(f"bad catalog only WARNS on `{frag}`",
          any(frag in w for w in v["warnings"]) and not any(frag in e for e in v["errors"]), v)
check("a renamed type is a warning naming its replacement",
      any("mcp-server-card+json" in w for w in ac.validate_ai_catalog({"specVersion": "1.0", "entries": [{
          "identifier": "urn:air:a.b:c", "displayName": "x", "type": "application/mcp-server+json",
          "url": "u", "representativeQueries": ["a", "b"]}]})["warnings"]))
check("a non-object is one error, not a crash", ac.validate_ai_catalog([1])["errors"] != [])

print("\nUCP profile (/.well-known/ucp) validation - the shape the spec has")
ucp = {"ucp": {"version": "2026-08-25", "supported_versions": {"2026-08-25": "https://ucp.dev/x"},
               "services": {"dev.ucp.shopping": [{"version": "2026-08-25", "transport": "rest",
                                                  "endpoint": "https://shop.example/ucp"}]},
               "capabilities": {"dev.ucp.shopping.checkout": [
                   {"version": "2026-08-25", "spec": "https://ucp.dev/s", "schema": "https://ucp.dev/j"}]}}}
r = ac.validate_ucp(ucp)
check("a spec-shaped profile validates", r["errors"] == [] and r["capabilities"] == 1, r)
r = ac.validate_ucp({"version": "1", "merchant": {}, "capabilities": []})
check("the flat shape the spec never had is `missing-ucp-root`", "missing-ucp-root" in r["errors"], r)
r = ac.validate_ucp({"ucp": {"version": "Aug 2026", "services": {"s": [{"transport": "rest"}]},
                             "capabilities": {"c": [{"version": "x"}]}}})
check("bad version, rest without endpoint, capability without spec/schema are all named",
      any("version" in e for e in r["errors"]) and any("endpoint" in e for e in r["errors"])
      and any("spec" in e for e in r["errors"]), r)

print("\nWebMCP entry points")
w = ac.webmcp_scan("<script>(document.modelContext ?? navigator.modelContext).registerTool({});"
                   "x.registerTool({})</script>")
check("document.modelContext is the current entry point", w["entry_point"] == "document", w)
check("registerTool call sites are counted", w["register_tool_calls"] == 2, w)
check("navigator-only is reported as legacy",
      ac.webmcp_scan("<script>navigator.modelContext.provideContext({})</script>")["entry_point"] == "navigator_legacy")
check("CONTROL: a page with neither says none",
      ac.webmcp_scan("<p>modelContext is a word here</p>")["entry_point"] is None)


print("\nGoogle's user-triggered fetchers 'generally ignore robots.txt' - no false 'blocked'")
import agentcheck as _ac
gr = _ac.parse_robots("User-agent: *\nDisallow: /\n")
rows = _ac.policy_rows(gr, "/")
ga = next(r for r in rows if r["bot"] == "Google-Agent")
check("Google-Agent under Disallow is reported robots_applies=False, not blocked",
      ga["robots_applies"] is False and ga["allowed"] is None, ga)
cu = next(r for r in rows if r["bot"] == "Claude-User")
check("CONTROL: Claude-User (honours robots) is still blocked", cu["allowed"] is False, cu)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all agentcheck tests passed")
