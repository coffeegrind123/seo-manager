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

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all agentcheck tests passed")
