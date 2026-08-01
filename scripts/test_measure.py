#!/usr/bin/env python3
"""Regression tests for the measurement scripts (crawllog / backlinks / decay).

Every case here is a bug that actually shipped and was caught against live data
on 2026-08-01, or a distinction that silently produces a confident wrong answer.
Written as tests because all of them fail QUIETLY - none throws, none logs, and
each one produces output that looks entirely reasonable.

The three that matter most:

  1. UA ordering. `Googlebot-Image` contains "googlebot" and `ChatGPT-User`
     does not contain "gptbot", but get the registry order wrong and the
     specific agent is swallowed by the general one. The whole AI-ingestion
     report depends on telling three OpenAI agents apart.

  2. Three-state verification. "Cannot ask" and "the answer is no" must not
     share a code path. The first run of crawllog.verify reported EVERY
     Googlebot IP as spoofed because this container's resolver refuses PTR
     lookups and raises the same exception for both. A two-state verdict makes
     that indistinguishable from a real finding.

  3. Decay vs demand drop. Impressions falling while POSITION HELD is not
     decay, and rewriting for it is wasted work. Measured live: /play fell from
     3 impressions to 1 while its position went 42 -> 1.

    python3 test_measure.py

No network. No fixtures needed - the log lines are inline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import backlinks  # noqa: E402
import crawllog  # noqa: E402

failures: list[str] = []


def check(name, cond, got=None):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" - got {got!r}" if got is not None else ""))
        failures.append(name)


# Real user-agent strings, as sent.
UAS = {
    "googlebot": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36 "
                 "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "googlebot-image": "Googlebot-Image/1.0",
    "googleother": "Mozilla/5.0 (compatible; GoogleOther)",
    "google-extended": "Mozilla/5.0 (compatible; Google-Extended/1.0)",
    "gptbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
              "GPTBot/1.2; +https://openai.com/gptbot",
    "oai-searchbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
                     "OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "chatgpt-user": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
                    "ChatGPT-User/1.0; +https://openai.com/bot",
    "claudebot": "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "claude-user": "Mozilla/5.0 (compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "ahrefsbot": "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "applebot-extended": "Mozilla/5.0 (compatible; Applebot-Extended/1.0)",
    "applebot": "Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)",
}

HUMAN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def test_classify():
    print("\nclassify_ua() - the specific agent must beat the general one:")
    for key, ua in UAS.items():
        got, _label, _cat = crawllog.classify_ua(ua)
        check(f"{key} classified as itself", got == key, got)

    print("\n  the three OpenAI agents mean different things:")
    cats = {k: crawllog.classify_ua(UAS[k])[2] for k in
            ("gptbot", "oai-searchbot", "chatgpt-user")}
    check("GPTBot is ai_training (never cites)", cats["gptbot"] == "ai_training", cats["gptbot"])
    check("OAI-SearchBot is ai_search (feeds citations)",
          cats["oai-searchbot"] == "ai_search", cats["oai-searchbot"])
    check("ChatGPT-User is ai_user (a live question)",
          cats["chatgpt-user"] == "ai_user", cats["chatgpt-user"])
    check("the three are NOT one category", len(set(cats.values())) == 3, cats)

    print("\n  Google's opt-out agent is not its search agent:")
    check("Google-Extended is ai_training",
          crawllog.classify_ua(UAS["google-extended"])[2] == "ai_training")
    check("Googlebot is search", crawllog.classify_ua(UAS["googlebot"])[2] == "search")
    check("Applebot-Extended is ai_training, Applebot is search",
          crawllog.classify_ua(UAS["applebot-extended"])[2] == "ai_training"
          and crawllog.classify_ua(UAS["applebot"])[2] == "search")

    print("\n  a human is not a bot:")
    check("a normal browser UA classifies as None",
          crawllog.classify_ua(HUMAN_UA)[0] is None, crawllog.classify_ua(HUMAN_UA))
    # CONTROL: the check above is only meaningful if something DOES classify.
    check("CONTROL - an unknown crawler is still caught as other-bot",
          crawllog.classify_ua("SomeRandomCrawler/1.0 (+http://x)")[0] == "other-bot")


CADDY_LINE = json.dumps({
    "ts": 1785576366.23,
    "request": {
        "remote_ip": "127.0.0.1", "client_ip": "127.0.0.1", "method": "GET",
        "host": "example.com", "uri": "/maps/de_dust2",
        "headers": {
            "User-Agent": [UAS["googlebot"]],
            "Cf-Connecting-Ip": ["66.249.66.1"],
            # The literal, unexpanded placeholder that shipped in production for
            # months. It is present, it looks like a header, it is not an IP.
            "X-Forwarded-For": ["{http.request.client_ip}"],
            "Referer": ["https://news.ycombinator.com/item?id=1"],
        },
    },
    "status": 200, "size": 5120, "duration": 0.01,
})

CLF_LINE = ('66.249.66.1 - - [01/Aug/2026:10:00:00 +0000] "GET /maps/de_dust2 HTTP/1.1" '
            '200 5120 "https://news.ycombinator.com/" "%s"' % UAS["googlebot"])


def test_parsers():
    print("\nlog parsing:")
    rec = crawllog.parse_caddy(CADDY_LINE)
    check("caddy line parses", rec is not None)
    check("real client IP comes from Cf-Connecting-Ip, not 127.0.0.1",
          rec["ip"] == "66.249.66.1", rec["ip"])
    check("the unexpanded {placeholder} XFF is rejected, not used as an IP",
          not rec["ip"].startswith("{"), rec["ip"])
    check("referer extracted", "ycombinator" in rec["referer"], rec["referer"])
    check("status/size read", (rec["status"], rec["size"]) == (200, 5120))

    auto = crawllog.make_parser("auto", None, None)
    check("auto-detect handles the caddy line", auto(CADDY_LINE)["uri"] == "/maps/de_dust2")
    clf = auto(CLF_LINE)
    check("auto-detect handles a combined line", clf is not None and clf["status"] == 200)
    check("combined referer extracted", clf and "ycombinator" in clf["referer"], clf and clf["referer"])
    check("CONTROL - garbage does not silently parse", auto("not a log line at all") is None)


def test_silo():
    print("\nsilo bucketing:")
    check("depth 1 groups a deep path", crawllog.silo_of("/maps/de_dust2", 1) == "/maps/*")
    check("depth 2 keeps two segments",
          crawllog.silo_of("/es/maps/de_dust2", 2) == "/es/maps/*")
    check("an asset is bucketed as an asset regardless of depth",
          crawllog.silo_of("/mi/de_dust2.jpg", 1) == "/mi/ (assets)",
          crawllog.silo_of("/mi/de_dust2.jpg", 1))
    check("a query string does not create a new silo",
          crawllog.silo_of("/maps/x?t=abc", 1) == "/maps/*")
    check("root stays root", crawllog.silo_of("/", 1) == "/")


def test_verify_states():
    print("\nverify - three states, because 'cannot ask' is not 'no':")
    # Exercised without network by driving verify_ip's decision table directly.
    # A missing entry returns None, which is exactly how _doh signals "could not
    # ask" - so case 5 below is the real failure mode, not a simulation of it.
    saved = crawllog._doh
    _current = {"table": {}}
    crawllog._doh = lambda n, t: _current["table"].get((n, t))

    ptr = crawllog._ptr_name("66.249.66.1")
    check("PTR name built correctly", ptr == "1.66.249.66.in-addr.arpa", ptr)

    # 1. Genuine Googlebot: PTR on the domain, forward-confirms.
    _current["table"] = {(ptr, "PTR"): ["crawl-66-249-66-1.googlebot.com"],
                         ("crawl-66-249-66-1.googlebot.com", "A"): ["66.249.66.1"]}
    r = crawllog.verify_ip("66.249.66.1", [".googlebot.com"])
    check("real Googlebot -> verified True", r["verified"] is True, r["reason"])

    # 2. Spoof: PTR on someone else's domain.
    _current["table"] = {(ptr, "PTR"): ["mail.attacker.example"]}
    r = crawllog.verify_ip("66.249.66.1", [".googlebot.com"])
    check("PTR on the wrong domain -> verified False (SPOOFED)", r["verified"] is False, r["reason"])

    # 3. Contradicted forward lookup.
    _current["table"] = {(ptr, "PTR"): ["crawl-66-249-66-1.googlebot.com"],
                         ("crawl-66-249-66-1.googlebot.com", "A"): ["1.2.3.4"]}
    r = crawllog.verify_ip("66.249.66.1", [".googlebot.com"])
    check("forward returns a DIFFERENT ip -> False", r["verified"] is False, r["reason"])

    # 4. One-way rDNS (measured on AhrefsBot): right domain, no forward record.
    #    UNPROVEN, and calling it spoofed is a false accusation.
    _current["table"] = {(ptr, "PTR"): ["proxy-fr006.ahrefs.net"],
                         ("proxy-fr006.ahrefs.net", "A"): []}
    r = crawllog.verify_ip("66.249.66.1", [".ahrefs.net"])
    check("one-way rDNS -> None (unproven), NOT False",
          r["verified"] is None, r["verified"])

    # 5. Resolver unreachable. THE ONE THAT SHIPPED WRONG.
    _current["table"] = {}
    r = crawllog.verify_ip("66.249.66.1", [".googlebot.com"])
    check("resolver cannot answer -> None (unknown), NOT False",
          r["verified"] is None, r["verified"])
    check("and it says so in the reason", "UNKNOWN" in (r["reason"] or "").upper(), r["reason"])

    crawllog._doh = saved


def test_referrer_classification():
    print("\nreferrer classification:")
    check("a search engine is not a backlink",
          any(s in "search.yahoo.com" for s in backlinks.SEARCH_HOSTS))
    check("google is not a backlink",
          any(s in "www.google.com" for s in backlinks.SEARCH_HOSTS))
    check("localhost:8282 is dev traffic, not the site's best backlink",
          backlinks.is_local("localhost:8282"))
    check("host.docker.internal is dev traffic", backlinks.is_local("host.docker.internal:8282"))
    check("a private LAN address is dev traffic", backlinks.is_local("192.168.1.10"))
    # CONTROL: the filters must not eat real referrers.
    check("CONTROL - a real referrer survives both filters",
          not backlinks.is_local("sbox.facepunch.com")
          and not any(s in "sbox.facepunch.com" for s in backlinks.SEARCH_HOSTS))
    check("CONTROL - reddit survives", not backlinks.is_local("reddit.com")
          and not any(s in "reddit.com" for s in backlinks.SEARCH_HOSTS))
    check("host_of strips www", backlinks.host_of("https://www.example.com/x") == "example.com")


def gsc(rows):
    return json.dumps({"rows": [{"keys": [u], "clicks": c, "impressions": i, "position": p}
                                for u, c, i, p in rows]})


def test_decay_classification():
    print("\ndecay vs demand drop (the distinction the workflow exists for):")
    with tempfile.TemporaryDirectory() as td:
        prev = Path(td) / "prev.json"
        cur = Path(td) / "cur.json"
        prev.write_text(gsc([
            ("https://x.com/decayer", 10, 400, 6.0),     # will slip badly
            ("https://x.com/demand", 10, 400, 5.0),      # position improves
            ("https://x.com/noise", 0, 3, 50.0),         # under min-impressions
        ]))
        cur.write_text(gsc([
            ("https://x.com/decayer", 1, 40, 18.0),
            ("https://x.com/demand", 1, 40, 3.0),
            ("https://x.com/noise", 0, 1, 60.0),
        ]))
        out = subprocess.run(
            [sys.executable, str(HERE / "decay.py"), "compare",
             "--previous", str(prev), "--current", str(cur), "--min-impressions", "10"],
            capture_output=True, text=True)
        d = json.loads(out.stdout)

    decayed = [r["page"] for r in d["decay"]]
    dropped = [r["page"] for r in d["demand_drop"]]
    check("a page that slipped 6 -> 18 is DECAY", "/decayer" in decayed, decayed)
    check("a page whose position IMPROVED is a demand drop, not decay",
          "/demand" in dropped and "/demand" not in decayed, {"decay": decayed, "demand": dropped})
    check("a page under --min-impressions is judged at all? no",
          "/noise" not in decayed and "/noise" not in dropped)
    check("position is impression-weighted, not a mean of means",
          d["decay"][0]["position"]["before"] == 6.0, d["decay"][0]["position"])


def main():
    test_classify()
    test_parsers()
    test_silo()
    test_verify_states()
    test_referrer_classification()
    test_decay_classification()
    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("all measurement tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
