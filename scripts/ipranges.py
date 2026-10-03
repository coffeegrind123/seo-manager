#!/usr/bin/env python3
"""ipranges.py - verify a crawler address against the ranges its OPERATOR publishes.

`crawllog.py verify` proves Googlebot and bingbot by reverse-then-forward DNS.
That path is closed to every AI crawler in the `BOTS` table, and deliberately
so: their rDNS lists are EMPTY because a guessed suffix reports every genuine
hit as spoofed (prior-art.md #9). So the rows the GEO reading is built on -
OAI-SearchBot, ChatGPT-User, Claude-SearchBot, PerplexityBot - could never be
verified, and "every Anthropic row is forged" (#10) was INFERRED from one
address claiming several operators rather than checked against anything.

The operators publish their ranges. Probed 2026-09-20, all five serve the SAME
shape - `{"creationTime": ..., "prefixes": [{"ipv4Prefix"|"ipv6Prefix": ...}]}`:

    OpenAI      openai.com/gptbot.json, searchbot.json, chatgpt-user.json
    Anthropic   claude.com/crawling/bots.json      (linked only from a support article)
    Perplexity  perplexity.ai/perplexitybot.json, perplexity-user.json
    Google      developers.google.com/static/crawling/ipranges/{common-crawlers,
                special-crawlers, user-triggered-fetchers,
                user-triggered-fetchers-google}.json
    Bing        bing.com/toolbox/bingbot.json

⚠ Google's list MOVED. The documented `/search/apis/ipranges/` path now 301s to
`/crawling/ipranges/`. A client that followed a remembered URL and read the HTML
redirect page as "no prefixes" would report every Googlebot address as spoofed.
The fetcher follows the redirect and REFUSES a body that does not parse as the
known shape, so a moved or reshaped file is `unavailable`, never an empty list.

THREE STATES, NEVER TWO. For an address claiming bot X:
    verified       inside a prefix the operator publishes
    spoofed        the operator publishes a list, and the address is in none of it
    unverifiable   the operator publishes no list, or its list could not be read
"spoofed" needs a list that was actually read. A fetch failure cannot become a
forgery finding - that is the `providers.py` rule, applied to a CIDR table.

    ipranges.py check --ip 1.2.3.4 --bot gptbot
    ipranges.py status                     # which operators' lists can be read now
    ipranges.py control                    # offline: fixtures in both directions

Stdlib only (`ipaddress`, `urllib`).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402
from providers import cache_get, cache_put, http  # noqa: E402

CACHE_TTL = 24 * 3600
GOOGLE_BASE = "https://developers.google.com/static/crawling/ipranges/"

# operator -> published files. The URL is the one that answers TODAY; the
# fetcher follows redirects so a future move is survivable, and a body that is
# not the known shape is refused rather than read as empty.
OPERATOR_FILES: dict[str, dict[str, str]] = {
    "OpenAI": {
        "gptbot": "https://openai.com/gptbot.json",
        "searchbot": "https://openai.com/searchbot.json",
        "chatgpt-user": "https://openai.com/chatgpt-user.json",
    },
    "Anthropic": {"bots": "https://claude.com/crawling/bots.json"},
    "Perplexity": {
        "perplexitybot": "https://www.perplexity.ai/perplexitybot.json",
        "perplexity-user": "https://www.perplexity.ai/perplexity-user.json",
    },
    "Google": {
        "common-crawlers": GOOGLE_BASE + "common-crawlers.json",
        "special-crawlers": GOOGLE_BASE + "special-crawlers.json",
        "user-triggered-fetchers": GOOGLE_BASE + "user-triggered-fetchers.json",
        "user-triggered-fetchers-google": GOOGLE_BASE + "user-triggered-fetchers-google.json",
        # Named on the user-triggered-fetchers page 2026-10-03 (creationTime
        # 2026-10-02, 20 prefixes). Missing it made every agent address in it
        # read as SPOOFED once a Google agent row could reach this table.
        "user-triggered-agents": GOOGLE_BASE + "user-triggered-agents.json",
    },
    "Microsoft": {"bingbot": "https://www.bing.com/toolbox/bingbot.json"},
}

# Which published file a given bot SHOULD appear in. Used to report
# `file_agrees_with_bot`; an address in the operator's other file is still that
# operator's address (verified), but a GPTBot claim from the searchbot range is
# worth seeing.
BOT_FILE: dict[str, tuple[str, str]] = {
    "gptbot": ("OpenAI", "gptbot"),
    "oai-searchbot": ("OpenAI", "searchbot"),
    "chatgpt-user": ("OpenAI", "chatgpt-user"),
    "claudebot": ("Anthropic", "bots"),
    "claude-user": ("Anthropic", "bots"),
    "claude-searchbot": ("Anthropic", "bots"),
    "anthropic-ai": ("Anthropic", "bots"),
    "perplexitybot": ("Perplexity", "perplexitybot"),
    "perplexity-user": ("Perplexity", "perplexity-user"),
    "googlebot": ("Google", "common-crawlers"),
    "googlebot-image": ("Google", "common-crawlers"),
    "googlebot-video": ("Google", "common-crawlers"),
    "googlebot-news": ("Google", "common-crawlers"),
    "storebot-google": ("Google", "common-crawlers"),
    "google-inspectiontool": ("Google", "common-crawlers"),
    "googleother": ("Google", "common-crawlers"),
    "google-extended": ("Google", "common-crawlers"),
    "google-cloudvertexbot": ("Google", "special-crawlers"),
    "adsbot-google": ("Google", "special-crawlers"),
    "apis-google": ("Google", "special-crawlers"),
    "feedfetcher-google": ("Google", "user-triggered-fetchers"),
    "google-read-aloud": ("Google", "user-triggered-fetchers"),
    # Google does not say which of its three user-triggered files each agent
    # uses; `check` matches against all of an operator's files, so the second
    # element only names where a hit is EXPECTED, never where it must be.
    # rDNS is not a witness here: user-owned fetchers resolve to
    # *.gae.googleusercontent.com, which every App Engine tenant can obtain.
    "google-agent": ("Google", "user-triggered-agents"),
    "gemininotebook": ("Google", "user-triggered-fetchers-google"),
    "notebooklm": ("Google", "user-triggered-fetchers-google"),
    # GoogleAgent-Mariner / -URLContext / Gemini-Deep-Research are NOT on
    # Google's fetchers page (checked 2026-10-03; they come from the community
    # list), so they stay UNVERIFIABLE - "spoofed" needs a documented mapping.
    "bingbot": ("Microsoft", "bingbot"),
    "bingpreview": ("Microsoft", "bingbot"),
    "msnbot": ("Microsoft", "bingbot"),
    "adidxbot": ("Microsoft", "bingbot"),
}


def parse_ranges(payload) -> list[str] | None:
    """The published shape, or None. Anything else is UNREADABLE, not empty."""
    if not isinstance(payload, dict):
        return None
    prefixes = payload.get("prefixes")
    if not isinstance(prefixes, list):
        return None
    out = []
    for p in prefixes:
        if not isinstance(p, dict):
            continue
        v = p.get("ipv4Prefix") or p.get("ipv6Prefix")
        if not v:
            continue
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError:
            continue
        out.append(v)
    # A well-formed document with zero prefixes is also not a list to judge by.
    return out or None


def fetch_file(operator: str, name: str, *, use_cache: bool = True) -> dict:
    url = OPERATOR_FILES[operator][name]
    ck = f"ipranges:{url}"
    got = cache_get("ipranges", ck, CACHE_TTL) if use_cache else None
    if got:
        return got
    r = http(url, timeout=30, ua="Mozilla/5.0 (compatible; seo-manager/1.0)", follow=True)
    if not r.ok:
        return {"operator": operator, "file": name, "url": url, "ok": False,
                "reason": f"HTTP {r.get('status')} {r.get('error') or ''}".strip()}
    ranges = parse_ranges(r.json())
    if ranges is None:
        return {"operator": operator, "file": name, "url": url, "ok": False,
                "reason": "body is not the published {creationTime, prefixes[]} shape - "
                          "moved or reshaped; refusing to read it as an empty list"}
    out = {"operator": operator, "file": name, "url": url, "ok": True,
           "creation_time": (r.json() or {}).get("creationTime"),
           "prefixes": ranges, "count": len(ranges)}
    if use_cache:
        cache_put("ipranges", ck, out)
    return out


def load_operator(operator: str, *, use_cache: bool = True) -> dict:
    files = {n: fetch_file(operator, n, use_cache=use_cache) for n in OPERATOR_FILES[operator]}
    readable = {n: f for n, f in files.items() if f["ok"]}
    return {"operator": operator, "files": files, "readable": len(readable),
            "unreadable": [n for n, f in files.items() if not f["ok"]],
            "prefixes": {n: f["prefixes"] for n, f in readable.items()}}


def match(ip: str, prefixes_by_file: dict[str, list[str]]) -> tuple[str | None, str | None]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None, None
    for name, plist in prefixes_by_file.items():
        for p in plist:
            try:
                if addr in ipaddress.ip_network(p, strict=False):
                    return name, p
            except ValueError:
                continue
    return None, None


def check(ip: str, bot_key: str, *, tables: dict[str, dict] | None = None,
          use_cache: bool = True) -> dict:
    """One address, one claimed bot -> verified / spoofed / unverifiable.

    `tables` lets a control inject fixtures; production loads on demand."""
    out = {"ip": ip, "bot": bot_key, "path": "cidr", "verified": None,
           "operator": None, "file": None, "prefix": None, "reason": None}
    op_file = BOT_FILE.get((bot_key or "").lower())
    if not op_file:
        out["reason"] = "operator publishes no IP-range list this tool knows - UNVERIFIABLE by CIDR"
        return out
    operator, expected_file = op_file
    out["operator"] = operator
    table = (tables or {}).get(operator) or load_operator(operator, use_cache=use_cache)
    if not table["prefixes"]:
        out["reason"] = (f"{operator} publishes ranges but none could be read "
                         f"({', '.join(table['unreadable']) or 'no files'}) - UNVERIFIABLE, "
                         f"not spoofed")
        out["unreadable_files"] = table["unreadable"]
        return out
    name, prefix = match(ip, table["prefixes"])
    if name:
        out.update(verified=True, file=name, prefix=prefix,
                   file_agrees_with_bot=(name == expected_file),
                   reason=(f"inside {operator}'s published {name} range {prefix}"
                           + ("" if name == expected_file else
                              f" - NOTE: {bot_key} is documented in `{expected_file}`, "
                              f"this address is in `{name}`")))
        return out
    if table["unreadable"]:
        # Part of the operator's list was unreadable. Absence from the part we
        # could read is not a forgery finding.
        out["reason"] = (f"not in the {operator} files that could be read, but "
                         f"{', '.join(table['unreadable'])} could not be - UNVERIFIABLE")
        out["unreadable_files"] = table["unreadable"]
        return out
    out.update(verified=False, file=expected_file,
               reason=f"{operator} publishes its ranges and this address is in none of them - SPOOFED")
    return out


def status(*, use_cache: bool = True) -> dict:
    rows = []
    for op in OPERATOR_FILES:
        t = load_operator(op, use_cache=use_cache)
        rows.append({"operator": op, "readable_files": t["readable"],
                     "unreadable": t["unreadable"],
                     "prefixes": sum(len(v) for v in t["prefixes"].values()),
                     "creation_times": {n: f.get("creation_time") for n, f in t["files"].items()
                                        if f["ok"]}})
    ok = any(r["readable_files"] for r in rows)
    if not ok:
        return refuse("ipranges-status", "no operator list could be read - every CIDR verdict "
                                         "would be `unverifiable`", operators=rows)
    return {"ok": True, "check": "ipranges-status", "operators": rows,
            "bots_covered": sorted(BOT_FILE),
            "note": "a `creationTime` is the operator's, not ours; a list older than a year "
                    "is still the published list, and a hit outside it is still SPOOFED"}


def run_control() -> dict:
    c = Controls("ipranges-control")
    fx = {"OpenAI": {"operator": "OpenAI", "unreadable": [],
                     "prefixes": {"gptbot": ["104.210.140.128/28", "2a01:4f8::/32"],
                                  "searchbot": ["13.66.216.176/28"]}},
          "Anthropic": {"operator": "Anthropic", "unreadable": ["bots"], "prefixes": {}},
          "Perplexity": {"operator": "Perplexity", "unreadable": ["perplexity-user"],
                         "prefixes": {"perplexitybot": ["107.20.236.150/32"]}}}

    # shape parser: the published shape, and the two that must NOT read as a list
    c.check("published_shape_parses",
            parse_ranges({"creationTime": "x", "prefixes": [{"ipv4Prefix": "1.2.3.0/24"},
                                                            {"ipv6Prefix": "2001:db8::/32"}]})
            == ["1.2.3.0/24", "2001:db8::/32"])
    c.check("an_html_redirect_page_is_not_an_empty_list", parse_ranges("<html>") is None)
    c.check("a_reshaped_document_is_unreadable_not_empty",
            parse_ranges({"creationTime": "x", "ranges": ["1.2.3.0/24"]}) is None)
    c.check("a_document_with_no_prefixes_is_unreadable",
            parse_ranges({"creationTime": "x", "prefixes": []}) is None)
    c.check("a_malformed_prefix_is_skipped_not_fatal",
            parse_ranges({"prefixes": [{"ipv4Prefix": "not-an-ip"}, {"ipv4Prefix": "9.9.9.9/32"}]})
            == ["9.9.9.9/32"])

    inside = check("104.210.140.130", "gptbot", tables=fx)
    c.check("an_address_inside_a_published_prefix_verifies",
            inside["verified"] is True and inside["prefix"] == "104.210.140.128/28", str(inside))
    c.check("the_expected_file_agreement_is_reported", inside["file_agrees_with_bot"] is True)
    v6 = check("2a01:4f8:1::1", "gptbot", tables=fx)
    c.check("ipv6_matches_too", v6["verified"] is True, str(v6))
    outside = check("8.8.8.8", "gptbot", tables=fx)
    c.check("an_address_outside_a_fully_read_list_is_spoofed",
            outside["verified"] is False and "SPOOFED" in outside["reason"], str(outside))
    cross = check("13.66.216.180", "gptbot", tables=fx)
    c.check("an_address_in_the_operators_other_file_verifies_with_a_note",
            cross["verified"] is True and cross["file_agrees_with_bot"] is False
            and "NOTE" in cross["reason"], str(cross))
    c.check("the_two_verdicts_are_distinguishable", inside["verified"] != outside["verified"])

    # ⚠ THE RULE THAT MATTERS: no list read -> no forgery finding.
    unread = check("8.8.8.8", "claudebot", tables=fx)
    c.check("an_unreadable_list_is_unverifiable_never_spoofed",
            unread["verified"] is None and "UNVERIFIABLE" in unread["reason"], str(unread))
    partial = check("8.8.8.8", "perplexity-user", tables=fx)
    c.check("a_partially_read_operator_cannot_prove_a_forgery",
            partial["verified"] is None and partial["unreadable_files"] == ["perplexity-user"],
            str(partial))
    nolist = check("8.8.8.8", "yandexbot", tables=fx)
    c.check("an_operator_with_no_list_is_unverifiable", nolist["verified"] is None
            and "UNVERIFIABLE" in nolist["reason"])
    bad = check("not.an.ip", "gptbot", tables=fx)
    c.check("a_non_address_is_not_spoofed_either", bad["verified"] is False or bad["verified"] is None)
    c.check("every_bot_file_points_at_a_real_operator_file",
            all(op in OPERATOR_FILES and f in OPERATOR_FILES[op] for op, f in BOT_FILE.values()))
    return c.verdict(operators=sorted(OPERATOR_FILES), bots_covered=len(BOT_FILE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    s = sub.add_parser("check", help="one address against the ranges its claimed operator publishes")
    s.add_argument("--ip", required=True, action="append")
    s.add_argument("--bot", required=True, help="bot key from crawllog.py's BOTS, e.g. gptbot")
    s.add_argument("--no-cache", action="store_true")
    s2 = sub.add_parser("status", help="which operators' lists can be read right now")
    s2.add_argument("--no-cache", action="store_true")
    sub.add_parser("control", help="prove unreadable never becomes spoofed (offline)")
    a = ap.parse_args()
    if a.action == "control":
        out = run_control()
    elif a.action == "status":
        out = status(use_cache=not a.no_cache)
    else:
        rows = [check(ip, a.bot, use_cache=not a.no_cache) for ip in a.ip]
        out = {"ok": True, "check": "ipranges-check", "results": rows,
               "verified": sum(1 for r in rows if r["verified"] is True),
               "spoofed": sum(1 for r in rows if r["verified"] is False),
               "unverifiable": sum(1 for r in rows if r["verified"] is None)}
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
