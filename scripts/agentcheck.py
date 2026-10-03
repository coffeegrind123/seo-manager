#!/usr/bin/env python3
"""Can an AI agent READ, UNDERSTAND and ACT ON this site - and is it allowed to.

`crawllog.py` measures which AI crawlers actually came. This measures whether
they are permitted to and what they get when they arrive. The pair answers a
question neither half can: an assistant that never cites you might be blocked,
might be allowed but served a page it cannot parse, or might simply not rate
you - and those need completely different fixes.

  policy     robots.txt resolved PER AI CRAWLER, in the ai_search / ai_user /
             ai_training taxonomy that decides whether a bot can ever cite you
  page       what an agent gets from one URL: agent-UX semantics, token budget,
             whether the content survives without JavaScript, WebMCP tools
  llms       /llms.txt and /llms-full.txt - presence and well-formedness, with
             no citation claim attached to either (see the note below); a CDN
             403/406 is `blocked` (unknown), never `absent`
  reach      what each AI-crawler UA is SERVED, against a browser control and a
             forged-Googlebot control (an edge that refuses forged Googlebot is
             verifying by IP, and then a forged AI UA's refusal means nothing)
  discovery  Agentmap / ai-catalog.json (ARD), the /.well-known documents agents
             look for (api-catalog, OAuth metadata, A2A card, UCP) - optional
             surfaces, where only a 200 that is NOT the document is a finding
  all        all five together for one origin

THE POLICY CHECK IS THE ONE THAT PAYS. Blocking `ai_search` while allowing
`ai_training` is the worst reachable configuration and it is easy to arrive at
by accident, because the "block AI scrapers" advice everywhere treats the two
as one thing. It means models are trained on the site and no assistant can ever
cite it. This tool names that combination explicitly instead of counting bots.

ON /llms.txt - the honest framing, because the myth is load-bearing elsewhere:
Google's own AI-optimization documentation states Google Search IGNORES it, and
a server-log study measured 0.1% of AI-bot requests touching it. It is checked
here for well-formedness and reported as OPTIONALITY, never scored as a
citation or ranking lever. It IS genuinely consumed by AI coding agents reading
library docs, which is a real but different use. Evidence and sources:
`references/agent-readiness.md`.

WHAT THIS CANNOT SEE, and says so rather than guessing: anything that needs a
rendered page. Tap-target size, computed `cursor`, transparent overlays and the
real accessibility tree are all layout facts, and this is a static fetch. Those
are reported `unmeasurable_statically` with the tool that does answer them
(Lighthouse's `agentic-browsing` category), never silently scored as passing.

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
from providers import http, BROWSER_UA  # noqa: E402
from crawllog import BOTS  # noqa: E402  - one taxonomy, declared once

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

CATEGORY_MEANING = {
    "ai_search": "feeds an assistant that CITES sources - blocking this is what "
                 "makes you uncitable",
    "ai_user":   "a live fetch because a real person asked - blocking this breaks "
                 "the answer for someone already trying to reach you",
    "ai_training": "trains a model. Never cites, never sends traffic. Blocking it "
                   "costs no visibility.",
}


sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls  # noqa: E402


def _finding(sev, rule, detail, fix=None, **extra):
    f = {"severity": sev, "rule": rule, "detail": detail}
    if fix:
        f["fix"] = fix
    f.update(extra)
    return f


# -------------------------------------------------------------- robots.txt


_ROBOTS_EOL = re.compile(r"\r\n|\n|\r")


def robots_lines(text: str) -> list[str]:
    """Split robots.txt the way crawlers do: CR, LF or CRLF, nothing else.

    `str.splitlines()` also breaks on U+2028/2029, NEL, VT, FF and the C0
    separators, so the tail of a comment becomes a live rule no crawler reads.
    A leading BOM is dropped, as Google's parser does - left in place it glues
    onto the first field name and the whole first group vanishes."""
    return _ROBOTS_EOL.split((text or "").lstrip("﻿"))


def parse_robots(text: str) -> list[dict]:
    """robots.txt -> groups of {agents, rules}. Consecutive User-agent lines
    share one rule block, which is the part naive parsers get wrong."""
    groups, cur, expecting_agent = [], None, False
    for raw in robots_lines(text):
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if cur is None or not expecting_agent:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
                expecting_agent = True
            cur["agents"].append(value.lower())
        elif field in ("allow", "disallow"):
            if cur is None:
                continue          # directive before any user-agent: ignored
            expecting_agent = False
            cur["rules"].append((field, value))
        elif field == "content-signal":
            # Content Signals Policy (contentsignals.org, Cloudflare, 2025):
            # `Content-Signal: search=yes, ai-input=no, ai-train=no` inside a
            # group STATES what the group's agents may do with the content. It
            # is a declaration, not an access rule - a crawler may ignore it -
            # so it is reported as stated policy and never scored.
            if cur is None:
                continue
            expecting_agent = False
            cur.setdefault("content_signal", {}).update(parse_content_signal(value))
    return groups


CONTENT_SIGNAL_KEYS = ("search", "ai-input", "ai-train")


def parse_content_signal(value: str) -> dict:
    out = {}
    for part in value.split(","):
        k, _, v = part.strip().partition("=")
        k, v = k.strip().lower(), v.strip().lower()
        if k in CONTENT_SIGNAL_KEYS and v in ("yes", "no"):
            out[k] = (v == "yes")
    return out


def parse_link_header(value: str) -> list[dict]:
    """RFC 8288 `Link:` -> [{url, rel, type, ...}]. Commas inside quoted
    parameters are respected; a malformed segment is skipped, not fatal."""
    out = []
    if not value:
        return out
    # split on commas that are outside <...> and outside quotes
    segs, buf, depth, quoted = [], [], 0, False
    for ch in value:
        if ch == '"':
            quoted = not quoted
        elif ch == "<" and not quoted:
            depth += 1
        elif ch == ">" and not quoted:
            depth = max(0, depth - 1)
        if ch == "," and not quoted and depth == 0:
            segs.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        segs.append("".join(buf))
    for seg in segs:
        m = re.match(r'\s*<([^>]*)>\s*(.*)$', seg)
        if not m:
            continue
        row = {"url": m.group(1)}
        for pm in re.finditer(r';\s*([A-Za-z0-9*_-]+)\s*=\s*("([^"]*)"|([^;,\s]+))', m.group(2)):
            row[pm.group(1).lower()] = pm.group(3) if pm.group(3) is not None else pm.group(4)
        out.append(row)
    return out


def _match_len(pattern: str, path: str) -> int:
    """Longest-match semantics with * and $, as Google implements them."""
    if pattern == "":
        return -1
    rx = re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return len(pattern) if re.match(rx, path) else -1


def allowed(groups: list[dict], ua: str, path: str = "/") -> dict:
    """Google's rule: most-specific UA group wins; within it, longest match wins;
    a tie goes to Allow.

    EVERY group naming that same most-specific token is COMBINED first (RFC
    9309 2.2.1). Cloudflare's managed robots.txt prepends its own `User-agent:
    *` group with `Allow: /` ahead of the origin's `*` group, and reading only
    the first one reported the origin's `Disallow: /lp` as open."""
    ua = ua.lower()

    def spec(g):
        best = -1
        for a in g["agents"]:
            if not a:
                continue          # `User-agent:` with no value names nobody
            if a == "*":
                best = max(best, 0)
            elif ua.startswith(a) or a in ua:
                best = max(best, len(a))
        return best

    scored = [(spec(g), g) for g in groups]
    best_len = max((n for n, _ in scored), default=-1)
    if best_len < 0:
        return {"allowed": True, "matched_group": None, "reason": "no matching group"}
    chosen = [g for n, g in scored if n == best_len]
    best = {"agents": sorted({a for g in chosen for a in g["agents"]}),
            "rules": [r for g in chosen for r in g["rules"]]}

    win, win_len, win_dir = True, -1, None
    for field, value in best["rules"]:
        n = _match_len(value, path)
        if n > win_len or (n == win_len and field == "allow"):
            if n >= 0:
                win_len, win_dir = n, field
                win = (field == "allow")
    if win_dir is None:
        return {"allowed": True, "matched_group": best["agents"],
                "reason": "group has no matching rule"}
    return {"allowed": win, "matched_group": best["agents"],
            "reason": f"{win_dir}: matched {win_len} chars"}


CONTROL_ROBOTS = """\
# a comment line
Disallow: /orphan-directive-before-any-agent/
User-agent: *
Disallow: /g/
Allow: /g/public/

User-agent: GPTBot
User-agent: ClaudeBot
Disallow: /

User-agent: Bingbot
Allow: /

"""


def run_control() -> dict:
    """Prove the robots reader still discriminates.

    The whole `policy` verdict rests on `parse_robots` + `allowed`. If either
    over- or under-matches, the output is a confident statement about who may
    read the site that is simply wrong - and it looks identical either way.

    ⚠ This deliberately does NOT use `urllib.robotparser`. Behind a CDN,
    `RobotFileParser.read()` turns a 403 of the default Python UA into
    `disallow_all`, so it reports every path blocked for every agent - a value
    indistinguishable from a real site-wide Disallow. Measured 2026-09-01 on a
    site whose supposedly-blocked page had 4,106 Googlebot hits."""
    c = Controls("agentcheck-control")
    g = parse_robots(CONTROL_ROBOTS)

    c.check("comments_are_stripped", not any("#" in a for grp in g for a in grp["agents"]))
    c.check("consecutive_user_agents_share_one_block",
            any(set(grp["agents"]) == {"gptbot", "claudebot"} for grp in g),
            str([grp["agents"] for grp in g]))
    c.check("a_directive_before_any_agent_is_ignored",
            not any(v.startswith("/orphan") for grp in g for _f, v in grp["rules"]))

    c.check("star_group_applies_to_an_unlisted_agent",
            allowed(g, "SomeUnknownBot", "/g/x")["allowed"] is False)
    c.check("longest_match_wins_over_the_shorter_disallow",
            allowed(g, "SomeUnknownBot", "/g/public/x")["allowed"] is True)
    c.check("a_named_group_beats_the_star_group",
            allowed(g, "GPTBot", "/anything")["allowed"] is False)
    c.check("a_named_allow_group_is_not_dragged_down_by_star",
            allowed(g, "Bingbot", "/g/x")["allowed"] is True)
    # THE 2026-09-01 FINDING: a named group carrying a bare `Allow: /` escapes
    # every exclusion the `*` group set. It must be visible, not silently right.
    c.check("named_group_escapes_default_exclusion_is_detectable",
            allowed(g, "Bingbot", "/g/x")["allowed"] is not
            allowed(g, "SomeUnknownBot", "/g/x")["allowed"])

    c.check("empty_pattern_never_matches", _match_len("", "/anything") == -1)
    c.check("wildcard_matches_across_a_segment", _match_len("/a/*/c", "/a/b/c") >= 0)
    c.check("dollar_anchors_the_end", _match_len("/a$", "/a/b") == -1)
    c.check("dollar_still_matches_the_exact_path", _match_len("/a$", "/a") >= 0)

    # The taxonomy is imported from crawllog, so this instrument and the log
    # reader agree on who a bot IS. A silently-empty import would make `policy`
    # report zero AI crawlers - which reads as "none are blocked".
    cats = {b[2] for b in BOTS}
    ai = [b for b in BOTS if str(b[2]).startswith("ai_")]
    c.check("shared_bot_taxonomy_is_populated", len(ai) >= 8, f"got {len(ai)} of {len(BOTS)}")
    c.check("all_three_ai_categories_are_present",
            cats >= set(CATEGORY_MEANING), str(sorted(cats)))
    c.check("search_bots_are_not_miscounted_as_ai",
            any(b[2] == "search" for b in BOTS) and "search" not in CATEGORY_MEANING)

    empty = parse_robots("")
    c.check("an_empty_robots_allows_rather_than_denies",
            allowed(empty, "GPTBot", "/")["allowed"] is True,
            "an empty or unreachable robots.txt must never read as a site-wide block")

    # Content Signals: parsed per group, stated not enforced, garbage ignored.
    cs = parse_robots("User-agent: *\nContent-Signal: search=yes, ai-input=no, AI-Train=No\n"
                      "Allow: /\n\nUser-agent: GPTBot\nDisallow: /\n")
    c.check("content_signal_is_captured_on_its_group",
            cs[0].get("content_signal") == {"search": True, "ai-input": False, "ai-train": False},
            str(cs[0].get("content_signal")))
    c.check("content_signal_does_not_leak_to_the_next_group", "content_signal" not in cs[1])
    c.check("content_signal_does_not_change_access",
            allowed(cs, "GPTBot", "/")["allowed"] is False
            and allowed(cs, "SomeUnknownBot", "/")["allowed"] is True)
    c.check("an_unknown_signal_key_or_value_is_ignored",
            parse_content_signal("bogus=yes, search=maybe, ai-train=no") == {"ai-train": False})
    c.check("a_signal_before_any_group_is_ignored",
            not any(g_.get("content_signal") for g_ in parse_robots("Content-Signal: search=no\n")))

    # RFC 8288 Link: quoted commas survive, params are lower-cased, junk is skipped.
    lk = parse_link_header('<https://x.example/api>; rel="service-desc"; type="application/json", '
                           '<https://x.example/p.md>; rel="alternate"; type="text/markdown", '
                           '<https://x.example/q>; title="a, b"; rel=next, garbage')
    c.check("link_header_splits_on_unquoted_commas_only",
            [x["url"] for x in lk] == ["https://x.example/api", "https://x.example/p.md",
                                       "https://x.example/q"], str(lk))
    c.check("link_header_keeps_a_quoted_comma_inside_a_param", lk[2].get("title") == "a, b")
    c.check("link_header_reads_rel_and_type", lk[1]["rel"] == "alternate"
            and lk[1]["type"] == "text/markdown")
    c.check("an_empty_link_header_is_an_empty_list", parse_link_header("") == [])
    # 2026-10-03 readers, each fired both ways.
    cf = ("User-agent: *\nAllow: /\n# BEGIN Cloudflare Managed content\nUser-agent: GPTBot\n"
          "Disallow: /\n# END Cloudflare Managed Content\nUser-agent: *\nDisallow: /lp\n")
    gcf = parse_robots(cf)
    c.check("combined_star_groups_keep_the_origin_disallow",
            allowed(gcf, "Googlebot", "/lp")["allowed"] is False
            and allowed(gcf, "Googlebot", "/ok")["allowed"] is True)
    c.check("managed_block_found_and_not_invented",
            cloudflare_managed(cf)["disallows"] == ["gptbot"]
            and cloudflare_managed("User-agent: *\nAllow: /\n")["present"] is False)
    c.check("challenge_fingerprint_fires_and_stays_quiet",
            challenge_vendor(403, {"cf-mitigated": "challenge"}, "") == "cloudflare"
            and challenge_vendor(200, {}, "<p>hello</p>") is None)
    ok_ = {"status": 200, "text_len": 1000, "challenge": None}
    no_ = {"status": 403, "text_len": 10, "challenge": None}
    c.check("reach_refuses_when_forged_googlebot_is_refused",
            reach_verdict(ok_, no_, {"X": no_})["state"] == "cannot_ask")
    c.check("reach_reads_a_refusal_when_the_control_passes",
            reach_verdict(ok_, ok_, {"X": no_})["bots"] == {"X": "refused"})
    c.check("llms_cdn_403_is_blocked_not_absent",
            classify_llms_read(403, "", "") == "blocked" and classify_llms_read(404, "", "") == "absent")
    c.check("ucp_flat_shape_rejected_spec_shape_accepted",
            validate_ucp({"merchant": {}})["errors"] == ["missing-ucp-root"]
            and validate_ucp({"ucp": {"version": "2026-08-25", "services": {},
                                      "capabilities": {}}})["errors"] == [])
    c.check("ard_extension_media_type_is_not_an_error_but_a_bad_id_is",
            validate_ai_catalog({"specVersion": "1.0", "entries": [{
                "identifier": "urn:air:a.b:c", "displayName": "x", "type": "text/plain",
                "url": "u", "representativeQueries": ["a", "b"]}]}) == {"errors": [], "warnings": []}
            and validate_ai_catalog({"specVersion": "1.0", "entries": [{
                "identifier": "nope", "displayName": "x", "type": "text/plain",
                "url": "u"}]})["errors"] != [])
    return c.verdict(groups_parsed=len(g))


# Fetchers Google documents as user-triggered, which "generally ignore
# robots.txt rules" (developers.google.com/crawling/docs/crawlers-fetchers/
# google-user-triggered-fetchers, read 2026-10-03). A Disallow does not stop
# them, so reporting one as "blocked" claims a block that does not happen.
ROBOTS_NOT_BINDING = {"google-agent", "google-gemininotebook", "google-notebooklm"}


def policy_rows(groups: list[dict], path: str = "/") -> list[dict]:
    rows = []
    for _key, name, cat, _dns in BOTS:
        if not cat.startswith("ai_"):
            continue
        explicit = any(name.lower() in a or a in name.lower()
                       for g in groups for a in g["agents"] if a and a != "*")
        if name.lower() in ROBOTS_NOT_BINDING:
            rows.append({"bot": name, "category": cat, "allowed": None, "robots_applies": False,
                         "explicit_rule": explicit,
                         "reason": "user-triggered Google fetcher - Google says these generally "
                                   "ignore robots.txt, so a rule here neither blocks nor admits it"})
            continue
        verdict = allowed(groups, name, path)
        rows.append({"bot": name, "category": cat, "allowed": verdict["allowed"],
                     "robots_applies": True, "explicit_rule": explicit,
                     "reason": verdict["reason"]})
    return rows


def check_policy(origin: str, path: str = "/") -> dict:
    url = origin.rstrip("/") + "/robots.txt"
    r = http(url, timeout=20, ua=BROWSER_UA, retries=1)
    st = r.get("status")
    if st == 404:
        return {"ok": True, "check": "agent-policy", "robots_url": url, "status": 404,
                "verdict": "no_robots",
                "detail": "no robots.txt - everything is crawlable by default. That is "
                          "a valid posture, not a defect.",
                "findings": []}
    if st != 200:
        return {"ok": False, "check": "agent-policy", "robots_url": url, "status": st,
                "error": f"robots.txt returned HTTP {st}",
                "detail": "This is a FAILED READ, not an open policy. Google treats a "
                          "persistent 5xx on robots.txt as 'disallow everything', so an "
                          "unreadable robots.txt is the opposite of permissive."}

    body = r.text()
    ctype = (r.get("ctype") or "").lower()
    groups = parse_robots(body)
    findings = []

    if "html" in ctype or body.lstrip()[:1] == "<":
        findings.append(_finding(
            "critical", "robots_is_html",
            f"robots.txt is served as {ctype or 'HTML'} - a soft-404 page, not a rules file",
            "Serve it as text/plain. Crawlers parse this as garbage and fall back to "
            "crawling everything, or nothing."))

    rows = policy_rows(groups, path)
    by_cat = {}
    for row in rows:
        if row["robots_applies"]:
            by_cat.setdefault(row["category"], []).append(row)

    summary = {c: {"allowed": sum(1 for r_ in v if r_["allowed"]), "total": len(v)}
               for c, v in by_cat.items()}

    search_open = summary.get("ai_search", {}).get("allowed", 0)
    search_total = summary.get("ai_search", {}).get("total", 0)
    train_open = summary.get("ai_training", {}).get("allowed", 0)
    user_open = summary.get("ai_user", {}).get("allowed", 0)
    user_total = summary.get("ai_user", {}).get("total", 0)

    if search_total and search_open == 0:
        findings.append(_finding(
            "critical", "ai_search_fully_blocked",
            f"all {search_total} citing crawlers (ai_search) are disallowed"
            + (f" while {train_open} training crawlers are allowed" if train_open else ""),
            "These are the crawlers that build the index assistants CITE from. Blocking "
            "them makes the site permanently uncitable in ChatGPT Search, Perplexity, "
            "Claude and DuckAssist, and no amount of content work changes that."))
    elif search_total and search_open < search_total:
        blocked = [r_["bot"] for r_ in by_cat.get("ai_search", []) if not r_["allowed"]]
        findings.append(_finding(
            "high", "ai_search_partially_blocked",
            f"{len(blocked)} of {search_total} citing crawlers are disallowed: "
            + ", ".join(blocked),
            "Each blocked ai_search bot is one assistant that can never cite the site."))

    # ROBOTS.TXT GROUPS DO NOT INHERIT, and this is the trap that costs bandwidth
    # rather than visibility. A crawler obeys ONLY the most specific group naming
    # it, so a named `User-agent: GPTBot` group whose whole body is `Allow: /`
    # silently grants every path the `*` group closes. Nothing in the file looks
    # wrong: the exclusions are right there, a few lines above, in a group that
    # does not apply.
    #
    # MEASURED 2026-09-01 on combatskirmish.net: GPTBot, Amazonbot and
    # OAI-SearchBot were all permitted on /g/ (tokenised game binaries), /api/ and
    # /dl/, every one of which the default group disallows. Over the six days
    # 2026-08-26..09-01 Amazonbot made 5,035 requests, 839/day.
    star_disallows = [r[1] for g in groups if "*" in g["agents"]
                      for r in g["rules"] if r[0] == "disallow" and r[1]]
    escapes = []
    for r_ in rows:
        if not r_["explicit_rule"] or not r_["robots_applies"]:
            continue
        got = [d for d in star_disallows
               if allowed(groups, r_["bot"], d)["allowed"]]
        if got:
            escapes.append({"bot": r_["bot"], "category": r_["category"],
                            "reaches": sorted(got)[:8], "count": len(got)})
    if escapes:
        worst = max(e["count"] for e in escapes)
        findings.append(_finding(
            "high", "named_group_escapes_default_exclusions",
            f"{len(escapes)} named agent group(s) reach paths the `*` group disallows "
            f"(up to {worst} each): "
            + ", ".join(f"{e['bot']} -> {', '.join(e['reaches'][:3])}" for e in escapes[:4]),
            "robots.txt groups do NOT inherit - an agent obeys only the most specific "
            "group that names it, so a named group whose body is just `Allow: /` "
            "overrides every Disallow in the default group. Repeat the exclusions "
            "inside each named group. This is usually a bandwidth and crawl-budget "
            "bug rather than a visibility one, and it is invisible on a `/` check "
            "because the paths involved are never the homepage."))

    if search_total and search_open == 0 and train_open > 0:
        findings.append(_finding(
            "critical", "farmed_not_read",
            f"{train_open} training crawlers allowed, {search_total} citing crawlers "
            f"blocked - the worst of both",
            "The site feeds model training but can never be cited or sent traffic. If "
            "the intent was to block AI, block ai_training too; if it was to be "
            "reachable, unblock ai_search."))

    if user_total and user_open < user_total:
        blocked = [r_["bot"] for r_ in by_cat.get("ai_user", []) if not r_["allowed"]]
        findings.append(_finding(
            "high", "ai_user_blocked",
            f"live user-triggered fetchers blocked: {', '.join(blocked)}",
            "An ai_user fetch means a real person asked an assistant about you and it "
            "came to the page. Blocking it breaks the answer for someone already "
            "trying to reach you."))

    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
    if not sitemaps:
        findings.append(_finding(
            "medium", "no_sitemap_directive",
            "robots.txt declares no Sitemap:",
            "Add `Sitemap: <absolute url>`; it is the cheapest discovery hint there is."))

    if len(body.encode()) > 500 * 1024:
        findings.append(_finding(
            "high", "robots_too_large",
            f"{len(body.encode()) // 1024} KiB - Google stops parsing at 500 KiB"))

    for bad in ("noindex", "nofollow"):
        if re.search(rf"(?im)^\s*{bad}\s*:", body):
            findings.append(_finding(
                "medium", "unsupported_directive",
                f"`{bad}:` in robots.txt is ignored by Google",
                f"Use a meta robots tag or an X-Robots-Tag header for {bad}."))

    # Stated content-usage policy (Content Signals). Reported, never scored: it
    # is a declaration crawlers may ignore, so it is evidence of INTENT and of
    # nothing else. A `search=no` next to an `Allow: /` is the one thing worth
    # naming - the site says "do not surface me" to the crawlers it lets in.
    content_signals = [{"agents": g["agents"], "signals": g["content_signal"]}
                       for g in groups if g.get("content_signal")]
    for cs in content_signals:
        if cs["signals"].get("search") is False and any(
                allowed(groups, a if a != "*" else "SomeUnknownBot", path)["allowed"]
                for a in cs["agents"]):
            findings.append(_finding(
                "info", "content_signal_search_no_but_allowed",
                f"group {cs['agents']} declares search=no while the path is Allowed - "
                f"a stated wish not to be surfaced, which a crawler may honour or not",
                "If exclusion is the intent, Disallow is the enforceable instruction."))
            break

    managed = cloudflare_managed(body)
    if managed["present"]:
        search_keys = {n.lower() for _k, n, c, _d in BOTS if c == "ai_search"}
        closed_search = [a for a in managed["disallows"] if a in search_keys]
        findings.append(_finding(
            "high" if closed_search else "info", "cloudflare_managed_robots",
            "robots.txt carries Cloudflare's MANAGED block, injected at the edge - it is "
            "not in the origin's file. It closes: " + (", ".join(managed["disallows"]) or "nothing")
            + (f". That includes citing crawler(s) {closed_search}." if closed_search else "."),
            "Change it in the Cloudflare dashboard (Security Settings > robots.txt), not in "
            "the repo. Editing the origin file cannot remove a rule the edge prepends."))

    gaps = content_signal_gaps(groups)
    if gaps:
        findings.append(_finding(
            "low", "content_signal_not_in_named_groups",
            f"the `*` group declares a Content-Signal, but {len(gaps)} named group(s) do not: "
            + ", ".join(gaps[:8]),
            "A crawler reads only the most specific group naming it, so the `*` signal says "
            "nothing to these agents. Repeat the line in each named group if it is meant for them."))

    # The same URL can serve a DIFFERENT robots.txt to a crawler UA (a CDN
    # rule, a bot-management layer, a server that varies on User-Agent). What a
    # browser reads is then not what the crawler obeys. Forged UAs, so a
    # difference is reported as evidence of variation, never as what the real
    # crawler definitely receives.
    import hashlib
    probe_uas = {"Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
    for _k, name, cat, _d in BOTS:
        if cat == "ai_search" and len(probe_uas) < 6:
            probe_uas[name] = f"Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; {name}/1.0)"
    base_hash = hashlib.sha256(body.encode()).hexdigest()
    varies = []
    for name, ua in probe_uas.items():
        rr = http(url, timeout=20, ua=ua, retries=0)
        txt = rr.text() if rr.get("status") == 200 else ""
        if rr.get("status") != 200 or hashlib.sha256(txt.encode()).hexdigest() != base_hash:
            varies.append({"ua": name, "status": rr.get("status"),
                           "same_body": False, "bytes": len(txt.encode())})
    if varies:
        findings.append(_finding(
            "medium", "robots_varies_by_user_agent",
            f"robots.txt differs for {len(varies)} crawler UA(s) vs a browser: "
            + ", ".join(f"{v['ua']} (HTTP {v['status']}, {v['bytes']}B)" for v in varies[:6]),
            "The rules evaluated above are what a BROWSER reads. A crawler may be served "
            "something else, or refused. Sent from here these UAs are forgeries, so an edge "
            "that verifies bots by IP will treat them differently from the real crawlers - "
            "confirm with `agentcheck.py reach` and the access log before acting."))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"]))
    return {
        "ok": True, "check": "agent-policy", "robots_url": url, "status": 200,
        "cloudflare_managed": managed, "robots_by_user_agent": varies,
        "path_tested": path,
        "verdict": ("fail" if any(f["severity"] in ("critical", "high") for f in findings)
                    else "warn" if findings else "pass"),
        "summary": summary, "category_meaning": CATEGORY_MEANING,
        "bots": rows, "sitemaps": sitemaps, "findings": findings,
        "content_signals": {
            "declared": content_signals,
            "status": "Content Signals Policy (contentsignals.org) - a stated policy on "
                      "search / ai-input / ai-train that crawlers MAY ignore; absence is "
                      "not a finding and presence is not enforcement",
        },
        "note": "Resolved with Google's precedence rules: the most specific User-agent "
                "group wins, then the longest matching path rule, ties to Allow. "
                "Consecutive User-agent lines share one rule block.",
    }


# ------------------------------------------------- edge-layer and discovery


_CF_BEGIN = re.compile(r"(?im)^\s*#\s*BEGIN Cloudflare Managed content\s*$")
_CF_END = re.compile(r"(?im)^\s*#\s*END Cloudflare Managed content\s*$")


def cloudflare_managed(body: str) -> dict:
    """Cloudflare's managed robots.txt is PREPENDED at the edge between
    `# BEGIN Cloudflare Managed content` / `# END ...` markers (Cloudflare's
    docs, read 2026-10-03). It exists only in the live response, never in the
    origin's file, so an owner reading their repo sees rules nobody serves and
    misses the ones that are. The agents it closes are named for that reason."""
    b, e = _CF_BEGIN.search(body or ""), _CF_END.search(body or "")
    if not b:
        return {"present": False, "disallows": []}
    inner = body[b.end(): e.start() if e and e.start() > b.end() else len(body)]
    closed = sorted({a for g in parse_robots(inner) for a in g["agents"]
                     if a != "*" and ("disallow", "/") in g["rules"]})
    return {"present": True, "closed_by_end_marker": bool(e), "disallows": closed}


def content_signal_gaps(groups: list[dict]) -> list[str]:
    """Named groups that do not carry the `*` group's Content-Signal.

    A crawler obeys ONLY the most specific group naming it, so a signal written
    once under `User-agent: *` says nothing to GPTBot the moment GPTBot has a
    group of its own - which is exactly the layout Cloudflare's managed file
    produces."""
    if not any(g.get("content_signal") for g in groups if "*" in g["agents"]):
        return []
    named = {}
    for g in groups:
        for a in g["agents"]:
            if a and a != "*":
                named[a] = named.get(a, False) or bool(g.get("content_signal"))
    return sorted(a for a, has in named.items() if not has)


# Bot-challenge interstitials, as published by each vendor. EVERY pattern in a
# row must match the first 64 KB (unescaped), and the page must carry under 120
# visible words: a real article that merely says "Just a moment" is not a
# challenge, and calling it one hides a page that was served. Table adapted
# from jianruntech/geo-score's CHALLENGES (MIT), checked against each vendor's
# documented markers.
CHALLENGES = [
    ("cloudflare", [r"cf[-_]chl|/cdn-cgi/challenge-platform/|<title>\s*Just a moment"]),
    ("aws_waf", [r"awswaf|reportChallengeError"]),
    ("akamai", [r"Access Denied",
                r"Reference\s*#|errors\.edgesuite\.net|\d{1,3}\.[0-9a-f]{6,8}\.\d{10}\.[0-9a-f]{6,10}"]),
    ("fastly", [r"/_fs-ch-|<title>\s*Client Challenge"]),
    ("perimeterx", [r"px-captcha|_pxJsClientSrc"]),
    ("datadome", [r"captcha-delivery\.com"]),
    ("imperva", [r"_Incapsula_Resource", r"incident_id|Incapsula incident"]),
]


def _visible_words(doc: str) -> int:
    return len(re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", doc or ""))).split())


def challenge_vendor(status, headers: dict | None, body: str) -> str | None:
    h = {k.lower(): str(v).lower() for k, v in (headers or {}).items()}
    if h.get("cf-mitigated") == "challenge":
        return "cloudflare"
    head = __import__("html").unescape((body or "")[:65536])
    if _visible_words(head) >= 120:
        return None
    for vendor, pats in CHALLENGES:
        if all(re.search(p_, head, re.I) for p_ in pats):
            return vendor
    return None


def _visible_len(doc: str) -> int:
    return len(re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", doc or ""))).strip())


def reach_verdict(browser: dict, googlebot: dict, bots: dict[str, dict]) -> dict:
    """What each crawler UA is served - only when the probe can tell.

    A UA string sent from this container is a FORGERY of that crawler, and an
    edge that verifies bots by IP (Cloudflare verified bots, Akamai, reddit's
    edge - measured 2026-10-03: 403 to spoofed GPTBot AND to spoofed Googlebot)
    refuses forgeries whatever its AI policy is. So spoofed Googlebot is the
    control: if the edge lets a forged Googlebot through it is not verifying,
    and a refusal of a forged AI UA is a rule about that UA. If it refuses
    forged Googlebot too, the AI rows say nothing, and this answers cannot_ask
    rather than reporting a block it cannot see."""
    def fetched(r):
        return r.get("status") is not None

    if not fetched(browser) or browser.get("status") != 200 or browser.get("challenge"):
        return {"state": "cannot_ask", "bots": {},
                "reason": f"the browser control was not served the page "
                          f"(HTTP {browser.get('status')}, challenge={browser.get('challenge')}) "
                          f"- there is no baseline to compare a crawler against"}
    if not fetched(googlebot) or googlebot.get("status") != 200 or googlebot.get("challenge"):
        return {"state": "cannot_ask", "bots": {},
                "reason": f"a forged Googlebot UA was refused too (HTTP {googlebot.get('status')}, "
                          f"challenge={googlebot.get('challenge')}): the edge verifies crawlers by "
                          f"IP, so a forged AI UA's refusal says nothing about how the REAL "
                          f"crawler is treated. Read the access log (crawllog.py) instead."}
    base = browser.get("text_len") or 0
    out = {}
    for name, r in bots.items():
        if not fetched(r):
            out[name] = "silent"
        elif r.get("challenge"):
            out[name] = "challenged"
        elif not (200 <= (r.get("status") or 0) < 300):
            out[name] = "refused"
        elif abs((r.get("text_len") or 0) - base) <= max(400, 0.25 * base):
            out[name] = "served"
        else:
            out[name] = "differs"
    return {"state": "measured", "bots": out, "baseline_text_len": base}


def classify_llms_read(status, ctype: str, body: str) -> str:
    """absent / blocked / html_served / present / failed - five states, because
    'a CDN refused a script UA' and 'there is no file' are opposite findings
    that a bare `status != 200` reports identically."""
    if status in (404, 410):
        return "absent"
    if status in (401, 403, 406, 429):
        return "blocked"
    if status != 200:
        return "failed"
    head = (body or "").lstrip("﻿ \t\r\n")[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html") or (
            "html" in (ctype or "").lower() and "<" in head[:1]):
        return "html_served"
    return "present"


# Agentic Resource Discovery. Ported from the spec's own conformance suite
# (ards-project/ard-spec, conformance/bin/conformance-test `validate_manifest`
# and `classify_media_type`), which Lighthouse's `ard-schema` audit ports
# directly. Two tiers, because the suite has two: an ERROR makes the catalog
# invalid, a WARNING does not. A third-party port that turned "unregistered
# media type" into an error rejected Cloudflare's own live catalog.
AIR_ID = re.compile(r"^urn:air:([a-zA-Z0-9.-]+)(?::([a-zA-Z0-9._:-]+))?:([a-zA-Z0-9._-]+)$")
_RN = r"[0-9A-Za-z][0-9A-Za-z!#$&^_.+\-]{0,126}"
_TOK = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_QS = r'"(?:[\t !#-\[\]-~]|\\[\t !-~])*"'
_MEDIA = re.compile(rf"(?P<t>{_RN})/(?P<s>{_RN})(?P<p>(?:[ \t]*;[ \t]*{_TOK}[ \t]*=[ \t]*(?:{_TOK}|{_QS}))*)")
_MPARAM = re.compile(rf"[ \t]*;[ \t]*(?P<n>{_TOK})[ \t]*=[ \t]*(?P<v>{_TOK}|{_QS})")


def _parse_media(mt: str):
    m = _MEDIA.fullmatch(mt or "")
    if not m:
        return None
    params = tuple(sorted((x.group("n").lower(), x.group("v")) for x in _MPARAM.finditer(m.group("p"))))
    if len({n for n, _ in params}) != len(params):
        return None
    return f"{m.group('t').lower()}/{m.group('s').lower()}", params


AIR_TYPES = (
    "application/ai-catalog+json", "application/agent-card+json",
    "application/a2a-agent-card+json", "application/mcp-server-card+json",
    "application/agent-skills+zip", "application/agent-skills+gzip",
    'text/markdown; profile="urn:air:agent-skills"',
    "application/ai-registry", "application/ai-registry+json",
)
_AIR_KEYS = {_parse_media(t) for t in AIR_TYPES}
_AIR_PARAMS = {base: dict(p) for base, p in _AIR_KEYS}
AIR_RENAMED = {"application/mcp-server+json": "application/mcp-server-card+json"}


def classify_media_type(mt) -> str | None:
    """None when fine; a warning message otherwise. Never an error - the suite
    permits extension types without registration."""
    if not isinstance(mt, str):
        return f"media type must be a string, got {type(mt).__name__}"
    parsed = _parse_media(mt)
    if parsed is None:
        return f"media type {mt!r} is not a valid IANA media type"
    if parsed in _AIR_KEYS:
        return None
    base, params = parsed
    if base in AIR_RENAMED:
        return f"media type {mt!r} was renamed by ADR-0008 - use {AIR_RENAMED[base]!r}"
    if base in _AIR_PARAMS:
        want = _AIR_PARAMS[base]
        if dict(params) != want:
            return f"media type {mt!r} is a standard discovery type with different parameters (want {want})"
    return None


def validate_ai_catalog(doc) -> dict:
    errs, warns = [], []
    if not isinstance(doc, dict):
        return {"errors": ["catalog is not a JSON object"], "warnings": []}
    if doc.get("specVersion") != "1.0":
        errs.append(f"specVersion must be \"1.0\" (got {doc.get('specVersion')!r})")
    if "collections" in doc:
        warns.append("top-level `collections` was removed in ADR-0003; ignored, model "
                     "hierarchies inside entries")
    entries = doc.get("entries")
    if not isinstance(entries, list):
        errs.append("entries[] is required and must be an array")
        return {"errors": errs, "warnings": warns}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errs.append(f"entries[{i}] is not an object")
            continue
        ident = e.get("identifier")
        if not ident:
            errs.append(f"entries[{i}].identifier is missing")
        elif not AIR_ID.match(str(ident)):
            errs.append(f"entries[{i}].identifier {ident!r} is not urn:air:<publisher>:<namespace>:<name>")
        if not e.get("displayName"):
            errs.append(f"entries[{i}].displayName is missing")
        if not e.get("type"):
            errs.append(f"entries[{i}].type (media type) is missing")
        else:
            w = classify_media_type(e["type"])
            if w:
                warns.append(f"entries[{i}]: {w}")
        if ("url" in e) == ("data" in e):
            errs.append(f"entries[{i}] must carry exactly one of url / data")
        q = e.get("representativeQueries")
        if q is None:
            warns.append(f"entries[{i}] has no representativeQueries - valid, but not findable by search")
        elif not isinstance(q, list) or not all(isinstance(x, str) for x in q):
            errs.append(f"entries[{i}].representativeQueries must be an array of strings")
        elif not 2 <= len(q) <= 5:
            warns.append(f"entries[{i}].representativeQueries has {len(q)}; 2-5 are recommended")
        tm = e.get("trustManifest")
        if tm is not None and not isinstance(tm, dict):
            errs.append(f"entries[{i}].trustManifest must be an object")
        elif isinstance(tm, dict) and not tm.get("identity"):
            errs.append(f"entries[{i}].trustManifest is missing identity")
    return {"errors": errs, "warnings": warns}


UCP_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_ucp(doc) -> dict:
    """Universal Commerce Protocol profile, in the shape the spec HAS
    (ucp.dev/latest/specification/overview): a root `ucp` object with a dated
    `version`, and `services` / `capabilities` keyed by reverse-domain name,
    each a LIST of version variants. A flat {version, merchant, capabilities[]}
    is the shape the spec never had - a checker expecting it reported real
    Shopify profiles as broken (claude-seo, fixed 2026-09-23)."""
    errs = []
    root = doc.get("ucp") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return {"errors": ["missing-ucp-root"], "capabilities": 0, "services": 0}
    if not UCP_DATE.match(str(root.get("version") or "")):
        errs.append(f"ucp.version must be a YYYY-MM-DD date (got {root.get('version')!r})")
    caps = root.get("capabilities") if isinstance(root.get("capabilities"), dict) else {}
    svcs = root.get("services") if isinstance(root.get("services"), dict) else {}
    for name, variants in svcs.items():
        for v in variants if isinstance(variants, list) else [variants]:
            t = (v or {}).get("transport") if isinstance(v, dict) else None
            if t not in ("rest", "mcp", "a2a", "embedded"):
                errs.append(f"service {name}: transport {t!r} is not rest|mcp|a2a|embedded")
            elif t != "embedded" and not v.get("endpoint"):
                errs.append(f"service {name}: a {t} transport needs an endpoint")
    for name, variants in caps.items():
        for v in variants if isinstance(variants, list) else [variants]:
            missing = [k for k in ("version", "spec", "schema")
                       if not (isinstance(v, dict) and v.get(k))]
            if missing:
                errs.append(f"capability {name}: missing {', '.join(missing)}")
    return {"errors": errs, "version": root.get("version"),
            "capabilities": len(caps), "services": len(svcs),
            "capability_names": sorted(caps)}


def webmcp_scan(doc: str) -> dict:
    """`document.modelContext` is the current WebMCP entry point;
    `navigator.modelContext` alone is the legacy one. Only script bodies are
    read - the word in prose is not an API call."""
    scripts = " ".join(re.findall(r"<script\b[^>]*>(.*?)</script\s*>", doc or "", re.S | re.I))
    doc_api = bool(re.search(r"\bdocument\.modelContext\b", scripts))
    nav_api = bool(re.search(r"\bnavigator\.modelContext\b", scripts))
    return {"entry_point": "document" if doc_api else "navigator_legacy" if nav_api else None,
            "register_tool_calls": len(re.findall(r"\bregisterTool\s*\(", scripts)),
            "provide_context_calls": len(re.findall(r"\bprovideContext\s*\(", scripts))}


GOOGLEBOT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
                "+http://www.google.com/bot.html) Chrome/131.0.0.0 Safari/537.36")


def _fetch_for_reach(url: str, ua: str) -> dict:
    r = http(url, timeout=25, ua=ua, retries=1, retry_on=(502, 503, 504))
    st = r.get("status")
    body = r.text() if st is not None else ""
    return {"status": st, "text_len": _visible_len(body),
            "challenge": challenge_vendor(st, r.get("headers") or {}, body) if st else None}


def check_reach(url: str, categories=("ai_search", "ai_user")) -> dict:
    """Fetch one URL as a browser (control), a forged Googlebot (control), and
    each AI crawler UA in the given classes; classify what each was served."""
    browser = _fetch_for_reach(url, BROWSER_UA)
    googlebot = _fetch_for_reach(url, GOOGLEBOT_UA)
    bots = {}
    for _k, name, cat, _d in BOTS:
        if cat in categories and name not in bots:
            ua = f"Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; {name}/1.0; +bot)"
            bots[name] = _fetch_for_reach(url, ua)
    v = reach_verdict(browser, googlebot, bots)
    out = {"ok": True, "check": "agent-reach", "url": url, **v,
           "controls": {"browser": browser, "forged_googlebot": googlebot}}
    if v["state"] == "cannot_ask":
        out["control_failed"] = True
        return out
    by = {}
    for name, state in v["bots"].items():
        by.setdefault(state, []).append(name)
    out["by_state"] = by
    findings = []
    cat_of = {n: c for _k, n, c, _d in BOTS}
    shut = [n for n in by.get("refused", []) + by.get("challenged", []) if cat_of.get(n) == "ai_search"]
    if shut:
        findings.append(_finding(
            "high", "edge_refuses_citing_crawler_ua",
            f"the edge refuses or challenges citing-crawler UA(s) {shut} while it serves a "
            f"browser AND a forged Googlebot - a UA-based rule aimed at these crawlers",
            "robots.txt can say Allow and this still makes the site uncitable. Look for an "
            "'AI bots' block in the CDN/WAF (Cloudflare AI Crawl Control, a bot-fight rule)."))
    if by.get("differs"):
        findings.append(_finding(
            "medium", "crawler_served_different_content",
            f"{by['differs']} were served markedly different visible text from the browser",
            "Either cloaking, or a degraded bot variant. Diff the two responses before acting."))
    out["findings"] = findings
    out["verdict"] = ("fail" if any(f["severity"] in ("critical", "high") for f in findings)
                      else "warn" if findings else "pass")
    out["note"] = ("Every crawler UA here is a forgery sent from this machine; the forged-Googlebot "
                   "control establishes that the edge does not verify by IP, which is what lets "
                   "a refusal be read as a rule. The real crawlers' experience is in the access "
                   "log (crawllog.py) - this is the cheap pre-check, not the proof.")
    return out


WELL_KNOWN = [
    ("/.well-known/api-catalog", "RFC 9727 API catalog", "linkset+json"),
    ("/.well-known/oauth-protected-resource", "RFC 9728 protected-resource metadata", "json"),
    ("/.well-known/oauth-authorization-server", "RFC 8414 authorization-server metadata", "json"),
    ("/.well-known/agent-card.json", "A2A agent card", "json"),
    ("/.well-known/ucp", "Universal Commerce Protocol profile", "json"),
    ("/.well-known/ai-catalog.json", "Agentic Resource Discovery catalog", "json"),
]


def _json_or_none(r):
    try:
        return json.loads(r.text())
    except Exception:
        return None


def check_discovery(origin: str) -> dict:
    """Machine discovery surfaces: Agentmap / ai-catalog (ARD), the
    /.well-known documents agents look for, and a UCP profile.

    Every one is OPTIONAL - absence is never a finding. What IS a finding is a
    surface that answers 200 with the wrong thing: a catch-all host returns its
    HTML shell for every /.well-known path, and Lighthouse counts any 200 at
    /.well-known/ai-catalog.json as a catalog and fails it. So a random path is
    probed first; if it also answers 200, every 200 below is suspect."""
    o = origin.rstrip("/")
    import secrets
    probe = http(f"{o}/{secrets.token_hex(6)}-not-found-probe", timeout=20, ua=BROWSER_UA,
                 follow=False)
    catch_all = probe.get("status") == 200
    out = {"ok": True, "check": "agent-discovery", "origin": o,
           "unknown_path_status": probe.get("status"), "catch_all_host": catch_all,
           "documents": {}, "findings": []}
    if probe.get("status") is None:
        out.update(ok=False, control_failed=True,
                   reason=f"the origin did not answer at all ({probe.get('error')})")
        return out

    rob = http(f"{o}/robots.txt", timeout=20, ua=BROWSER_UA)
    agentmaps = (re.findall(r"(?im)^\s*agentmap:\s*(\S+)", rob.text())
                 if rob.get("status") == 200 else [])
    home = http(o + "/", timeout=25, ua=BROWSER_UA)
    link_rel = re.findall(r'<link\b[^>]*\brel=["\']?ai-catalog["\']?[^>]*>', home.text() or "", re.I)
    link_href = [m for tag in link_rel for m in re.findall(r'href=["\']([^"\']+)', tag)]
    hdr = [l["target"] for l in parse_link_header((home.get("headers") or {}).get("link", ""))
           if "ai-catalog" in (l.get("rel") or "")]
    out["catalog_signals"] = {"robots_agentmap": agentmaps, "link_rel": link_href, "link_header": hdr}

    for path, label, want in WELL_KNOWN:
        r = http(o + path, timeout=20, ua=BROWSER_UA, follow=False)
        st, ctype = r.get("status"), (r.get("ctype") or "").lower()
        rec = {"label": label, "status": st, "content_type": ctype.split(";")[0] or None}
        if st == 200:
            doc = _json_or_none(r)
            rec["json"] = doc is not None
            if doc is None:
                rec["state"] = "html_or_garbage"
                out["findings"].append(_finding(
                    "medium" if path.endswith("ai-catalog.json") else "low",
                    "well_known_soft_200",
                    f"{path} answers 200 with a non-JSON body ({ctype or 'no type'})"
                    + (" - the host is a catch-all" if catch_all else ""),
                    "Return 404 for well-known paths you do not serve. Lighthouse's "
                    "ard-schema audit FAILS a 200 at ai-catalog.json instead of marking it N/A."))
            else:
                rec["state"] = "present"
                if path.endswith("ai-catalog.json"):
                    rec.update(validate_ai_catalog(doc))
                elif path.endswith("/ucp"):
                    rec.update(validate_ucp(doc))
                if rec.get("errors"):
                    out["findings"].append(_finding(
                        "medium", "well_known_invalid",
                        f"{path} is JSON but invalid: " + "; ".join(rec["errors"][:4])))
                if want == "linkset+json" and "linkset" not in ctype:
                    out["findings"].append(_finding(
                        "low", "api_catalog_content_type",
                        f"{path} is served as {ctype or 'no type'}; RFC 9727 specifies "
                        f"application/linkset+json"))
        else:
            rec["state"] = "absent" if st in (404, 410) else "unknown"
        out["documents"][path] = rec

    for href in agentmaps + link_href + hdr:
        u = urllib.parse.urljoin(o + "/", href)
        r = http(u, timeout=20, ua=BROWSER_UA)
        doc = _json_or_none(r) if r.get("status") == 200 else None
        vr = (validate_ai_catalog(doc) if doc is not None
              else {"errors": [f"HTTP {r.get('status')} or not JSON"], "warnings": []})
        errs = vr["errors"]
        out["documents"][u] = {"label": "ai-catalog via signal", "status": r.get("status"), **vr}
        if errs:
            out["findings"].append(_finding(
                "medium", "signalled_catalog_invalid",
                f"an ai-catalog the site POINTS to is unusable ({u}): " + "; ".join(errs[:3])))
    out["verdict"] = ("fail" if any(f["severity"] in ("critical", "high") for f in out["findings"])
                      else "warn" if out["findings"] else "pass")
    out["framing"] = ("All of these are optional machine-discovery surfaces. None is a Google "
                      "ranking or citation signal; absence is never a finding. A 200 that is "
                      "not the document IS one, because it fails the audits that read it.")
    return out


# ----------------------------------------------------------------- llms.txt


def check_llms(origin: str) -> dict:
    out = {"ok": True, "check": "agent-llms", "origin": origin, "files": {}, "findings": []}
    for name in ("llms.txt", "llms-full.txt"):
        url = origin.rstrip("/") + "/" + name
        r = http(url, timeout=25, ua=BROWSER_UA, retries=1)
        st = r.get("status")
        rec = {"url": url, "status": st, "bytes": len(r.get("body") or b""),
               "content_type": (r.get("ctype") or "").split(";")[0] or None}
        rec["read"] = classify_llms_read(st, r.get("ctype") or "", r.text() if st == 200 else "")
        if rec["read"] == "blocked":
            out["findings"].append(_finding(
                "info", "llms_txt_blocked_at_edge",
                f"{name} answered HTTP {st} to a desktop-browser request - a CDN/WAF "
                f"refusal, so whether the file EXISTS is unknown, not 'absent'"))
        if st == 200:
            body = r.text()
            is_html = "html" in (rec["content_type"] or "") or body.lstrip()[:1] == "<"
            rec.update({
                "html_served": is_html,
                "h1": bool(re.match(r"\s*#\s+\S", body)),
                "blockquote_summary": bool(re.search(r"(?m)^\s*>\s+\S", body)),
                "links": len(re.findall(r"\[[^\]]*\]\(([^)]+)\)", body)),
                "sections": len(re.findall(r"(?m)^##\s+\S", body)),
            })
            if is_html:
                out["findings"].append(_finding(
                    "high", "llms_txt_is_html", f"{name} is served as HTML, not markdown",
                    "It must be text/plain or text/markdown. HTML here usually means the "
                    "SPA catch-all answered and the file does not exist."))
            else:
                if not rec["h1"]:
                    out["findings"].append(_finding(
                        "medium", "llms_txt_no_h1",
                        f"{name} does not start with an H1 title (the format requires one)"))
                if name == "llms.txt" and not rec["blockquote_summary"]:
                    out["findings"].append(_finding(
                        "low", "llms_txt_no_summary",
                        "llms.txt has no `> summary` blockquote after the title"))
                if not rec["links"]:
                    out["findings"].append(_finding(
                        "medium", "llms_txt_no_links",
                        f"{name} lists no markdown links - an index with nothing in it"))
        out["files"][name] = rec

    present = [n for n, v in out["files"].items() if v.get("read") == "present"]
    out["present"] = present
    unknown = [n for n, v in out["files"].items() if v.get("read") in ("blocked", "failed")]
    out["unknown"] = unknown
    if not present and not unknown:
        out["findings"].append(_finding(
            "low", "no_llms_txt",
            "neither /llms.txt nor /llms-full.txt exists",
            "Optional. Ship one for optionality with AI coding agents if the site has "
            "docs; do NOT ship one expecting Google or citation benefit."))
    out["verdict"] = ("fail" if any(f["severity"] in ("critical", "high")
                                    for f in out["findings"])
                      else "warn" if out["findings"] else "pass")
    out["framing"] = (
        "Presence is reported as OPTIONALITY, never as a ranking or citation signal. "
        "Google's AI-optimization docs state Search ignores llms.txt; a 2025 server-log "
        "study measured 0.1% of AI-bot requests touching it. Its real consumer today is "
        "AI coding agents reading library documentation.")
    return out


# -------------------------------------------------------------- page reading


TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.I | re.S)


def check_page(url: str) -> dict:
    r = http(url, timeout=30, ua=BROWSER_UA, retries=1)
    if r.get("status") != 200:
        return {"ok": False, "check": "agent-page", "url": url, "status": r.get("status"),
                "error": r.get("error") or f"HTTP {r.get('status')}",
                "detail": "failed read - not an agent-readiness verdict"}
    doc = r.text()
    # MARKUP = the document with comments, <script> and <style> removed. Every
    # STRUCTURAL check below must run on this, never on the raw `doc`.
    #
    # Measured on a real site 2026-08-01: a page whose only "<img>" was the literal
    # string inside an HTML comment explaining why the decorative art deliberately
    # uses NO <img> was reported as "1 of 1 <img> have no alt attribute". There was
    # no image element on the page at all.
    #
    # The false POSITIVE is the harmless half. The same bug runs the other way and
    # that is the dangerous one: `no_main_landmark` is a `re.search(r"<main\b")`, so
    # a comment or a JS template string merely MENTIONING <main> makes the check
    # pass on a page that has no <main> at all - a guard that silently stops
    # guarding. Same for <input> inside a script template (phantom unlabelled
    # fields) and <a> in a comment.
    #
    # token_budget deliberately keeps using the RAW doc: html_bytes is the transfer
    # cost an agent actually pays, comments and scripts included.
    markup = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    markup = re.sub(r"<script\b.*?</script\s*>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<style\b.*?</style\s*>", " ", markup, flags=re.S | re.I)
    findings = []

    text = re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", doc))).strip()
    html_bytes, text_bytes = len(doc.encode()), len(text.encode())
    ratio = (text_bytes / html_bytes) if html_bytes else 0
    # ~4 chars/token is the standard English approximation. Reported as an
    # ESTIMATE, and labelled one - no tokenizer is available here.
    est_tokens = round(len(text) / 4)

    if ratio < 0.05 and html_bytes > 20000:
        findings.append(_finding(
            "medium", "low_text_ratio",
            f"only {ratio:.1%} of {html_bytes // 1024} KiB is readable text",
            "An agent pays for the whole document and keeps a sliver. Heavy inline "
            "script/style is the usual cause."))
    if est_tokens > 30000:
        findings.append(_finding(
            "medium", "oversized_for_context",
            f"~{est_tokens:,} estimated tokens of text",
            "Long enough that an agent may truncate before reaching the answer. Split "
            "the page or front-load the conclusion."))

    # Does the content survive without JS? Agents overwhelmingly do not run it.
    body_only = re.sub(r"(?is).*?<body[^>]*>", "", doc)
    static_text = re.sub(r"\s+", " ", TAG_RE.sub(" ", SCRIPT_RE.sub(" ", body_only))).strip()
    if len(static_text) < 200 and len(doc) > 5000:
        findings.append(_finding(
            "critical", "requires_javascript",
            f"only {len(static_text)} characters of text in the raw HTML",
            "The content is assembled client-side. Most AI crawlers - including every "
            "ai_search bot - do not execute JavaScript, so they receive an empty page. "
            "Server-render or pre-render."))

    # --- agent-UX semantics, all statically decidable
    fake_buttons = re.findall(
        r"<(div|span)\b(?![^>]*\brole=)(?![^>]*\btabindex=)[^>]*\bon(?:click|mousedown)\s*=",
        markup, re.I)
    if fake_buttons:
        findings.append(_finding(
            "high", "div_onclick_without_role",
            f"{len(fake_buttons)} <div>/<span> with a click handler and no role/tabindex",
            "Use <button>/<a href>, or add role=\"button\" + tabindex=\"0\" + Enter/Space "
            "handlers. These are invisible in the accessibility tree, which is the "
            "cleanest signal an agent has."))

    anchors_nohref = re.findall(r"<a\b(?![^>]*\bhref=)[^>]*>", markup, re.I)
    if len(anchors_nohref) > 2:
        findings.append(_finding(
            "medium", "anchor_without_href",
            f"{len(anchors_nohref)} <a> elements with no href",
            "An anchor without href is not a link to any agent or crawler."))

    inputs = re.findall(r"<(input|select|textarea)\b[^>]*>", markup, re.I)
    labelled_ids = set(re.findall(r'<label\b[^>]*\bfor\s*=\s*"([^"]+)"', markup, re.I))
    unlabelled = 0
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", markup, re.I):
        if re.search(r'\btype\s*=\s*"(hidden|submit|button|image)"', tag, re.I):
            continue
        has_aria = re.search(r"\baria-label(?:ledby)?\s*=", tag, re.I)
        mid = re.search(r'\bid\s*=\s*"([^"]+)"', tag, re.I)
        if not has_aria and not (mid and mid.group(1) in labelled_ids):
            unlabelled += 1
    if unlabelled:
        findings.append(_finding(
            "high", "unlabelled_input",
            f"{unlabelled} form field(s) with no <label for>, aria-label or aria-labelledby",
            "An agent reading the accessibility tree gets the field's purpose from its "
            "label. Without one the field is a void it cannot fill correctly."))

    landmarks = {t: bool(re.search(rf"<{t}\b", markup, re.I)) for t in ("main", "nav", "header", "footer")}
    if not landmarks["main"]:
        findings.append(_finding(
            "medium", "no_main_landmark",
            "no <main> element",
            "<main> is how an agent finds the primary content instead of guessing "
            "between nav, sidebar and footer."))

    imgs = re.findall(r"<img\b[^>]*>", markup, re.I)
    noalt = [t for t in imgs if not re.search(r"\balt\s*=", t, re.I)]
    if noalt:
        findings.append(_finding(
            "low", "img_without_alt",
            f"{len(noalt)} of {len(imgs)} <img> have no alt attribute",
            "Use alt=\"\" for decorative images so the omission is explicit."))

    # --- WebMCP: an opportunity, never a failure
    forms = re.findall(r"<form\b[^>]*>", doc, re.I)
    webmcp_forms = [f for f in forms if re.search(r"\btool(name|description)\s*=", f, re.I)]
    wm = webmcp_scan(doc)
    webmcp_js = bool(wm["entry_point"] or wm["provide_context_calls"])

    # --- markdown availability (agents prefer a clean source)
    md_link = re.search(
        r'<link\b[^>]*rel="alternate"[^>]*type="text/(?:markdown|plain)"[^>]*>', doc, re.I)
    md_url = url.rstrip("/") + ".md"
    md = http(md_url, timeout=15, ua=BROWSER_UA)
    md_available = (md.get("status") == 200
                    and "html" not in (md.get("ctype") or "").lower())
    # CONTENT NEGOTIATION: the same canonical URL asked for `text/markdown`.
    # Cloudflare's "Markdown for Agents" answers this at the edge (measured on
    # www.cloudflare.com and developers.cloudflare.com, 2026-09), and dualmark
    # / aeo.js do it in the framework. Reported as a capability; a site that
    # serves HTML here is ordinary, not deficient.
    neg = http(url, timeout=20, ua=BROWSER_UA,
               headers={"Accept": "text/markdown, text/html;q=0.5"})
    neg_ctype = (neg.get("ctype") or "").lower()
    neg_headers = neg.get("headers") or {}
    negotiated_md = (neg.get("status") == 200 and neg_ctype.startswith("text/markdown"))
    vary = (neg_headers.get("vary") or (r.get("headers") or {}).get("vary") or "")
    # RFC 8288 Link headers on the ORIGINAL response - service discovery for
    # agents (API catalogue, MCP server card, a markdown twin) without parsing
    # the HTML. Zero extra requests.
    links = parse_link_header((r.get("headers") or {}).get("link", ""))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"]))
    return {
        "ok": True, "check": "agent-page", "url": url,
        "verdict": ("fail" if any(f["severity"] in ("critical", "high") for f in findings)
                    else "warn" if findings else "pass"),
        "token_budget": {"html_bytes": html_bytes, "text_bytes": text_bytes,
                         "text_ratio": round(ratio, 4), "estimated_tokens": est_tokens,
                         "note": "tokens are a ~4-chars-per-token ESTIMATE, not a "
                                 "tokenizer count"},
        "structure": {"landmarks": landmarks, "forms": len(forms), "inputs": len(inputs),
                      "images": len(imgs), "images_without_alt": len(noalt)},
        "markdown": {"alternate_link": bool(md_link), "dot_md_url": md_url,
                     "dot_md_available": md_available,
                     "content_negotiation": {
                         "requested": "Accept: text/markdown, text/html;q=0.5",
                         "status": neg.get("status"),
                         "content_type": neg_ctype or None,
                         "served_markdown": negotiated_md,
                         "vary_includes_accept": "accept" in vary.lower(),
                         "note": ("served_markdown=true is a capability worth noting; "
                                  "false is the ordinary web, not a defect. A markdown "
                                  "response WITHOUT `Vary: Accept` can be cached and "
                                  "served to browsers - that IS a defect, and only "
                                  "applies when served_markdown is true"),
                     }},
        "link_headers": {"count": len(links), "links": links[:20],
                         "status": "RFC 8288 service discovery - informational; absence "
                                   "is the norm on a content site"},
        "webmcp": {"forms_with_tools": len(webmcp_forms), "js_api_referenced": webmcp_js,
                   "entry_point": wm["entry_point"],
                   "register_tool_calls": wm["register_tool_calls"],
                   "entry_point_note": ("feature-detect `document.modelContext ?? "
                                        "navigator.modelContext`; navigator-only is the "
                                        "legacy entry point") if wm["entry_point"] ==
                   "navigator_legacy" else None,
                   "status": "proposed standard, Chrome origin trial - absence is an "
                             "opportunity, never a defect"},
        "findings": findings,
        "unmeasurable_statically": [
            {"what": "interactive target size (<24x24px)", "why": "needs layout"},
            {"what": "transparent overlays covering interactive nodes", "why": "needs layout"},
            {"what": "computed cursor:pointer on non-interactive elements", "why": "needs CSS cascade"},
            {"what": "the real accessibility tree", "why": "needs a browser"},
        ],
        "next": ("For the layout-dependent half, run Lighthouse's agentic-browsing "
                 "category: npx lighthouse@latest <url> --only-categories=agentic-browsing "
                 "(Chrome 150+; it reports a fractional pass-ratio, NOT a 0-100 score, and "
                 "the PageSpeed REST API does not return it)."),
    }


# ---------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("policy", help="robots.txt resolved per AI crawler")
    po.add_argument("origin")
    po.add_argument("--path", default="/", help="path to test the rules against")

    pg = sub.add_parser("page", help="what an agent gets from one URL")
    pg.add_argument("url")

    lm = sub.add_parser("llms", help="/llms.txt and /llms-full.txt")
    lm.add_argument("origin")

    sub.add_parser("control", help="prove the robots reader discriminates")

    rc = sub.add_parser("reach", help="what each AI-crawler UA is served, with a "
                                      "forged-Googlebot control")
    rc.add_argument("url")
    rc.add_argument("--all-classes", action="store_true",
                    help="also probe ai_training UAs (default: ai_search + ai_user)")

    dc = sub.add_parser("discovery", help="Agentmap / ai-catalog, /.well-known docs, UCP")
    dc.add_argument("origin")

    al = sub.add_parser("all", help="policy + llms + one page + reach + discovery")
    al.add_argument("origin")
    al.add_argument("--page", help="page to sample (default: the origin itself)")

    a = p.parse_args()
    if a.cmd == "control":
        out = run_control()
    elif a.cmd == "policy":
        out = check_policy(a.origin, a.path)
    elif a.cmd == "page":
        out = check_page(a.url)
    elif a.cmd == "llms":
        out = check_llms(a.origin)
    elif a.cmd == "reach":
        out = check_reach(a.url, ("ai_search", "ai_user", "ai_training") if a.all_classes
                          else ("ai_search", "ai_user"))
    elif a.cmd == "discovery":
        out = check_discovery(a.origin)
    else:
        pol, llm = check_policy(a.origin), check_llms(a.origin)
        pg_ = check_page(a.page or a.origin)
        rc_, dc_ = check_reach(a.page or a.origin), check_discovery(a.origin)
        parts = (pol, llm, pg_, rc_, dc_)
        worst = [d.get("verdict") for d in parts]
        out = {"ok": all(d.get("ok") for d in (pol, llm, pg_, dc_)),
               "check": "agent-all", "origin": a.origin,
               "verdict": ("fail" if "fail" in worst else
                           "warn" if "warn" in worst else "pass"),
               "policy": pol, "llms": llm, "page": pg_, "reach": rc_, "discovery": dc_}

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
