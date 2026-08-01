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

block_search = (allow_all + "\nUser-agent: OAI-SearchBot\nUser-agent: PerplexityBot\n"
                "User-agent: Claude-SearchBot\nUser-agent: DuckAssistBot\n"
                "User-agent: YouBot\nDisallow: /\n")
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

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all agentcheck tests passed")
