#!/usr/bin/env python3
"""Backlink and corpus MEASUREMENT - what actually points here, and who has us.

The skill already knows how to go and GET links (`workflow-backlinks-report.md`,
`backlink-playbook.md`). What it could not do is tell you what you already have,
because every backlink index worth reading is paid: Ahrefs, Majestic, Semrush,
DataForSEO. There is no free API that will hand you a link list, and this file
does not pretend otherwise - see `MEASURED, NOT ASSUMED` below.

What IS free, and in one case better than the paid indexes:

  referrers   Your own access log. A referring domain in the log is a link that
              a REAL PERSON followed - which is the only property of a backlink
              anyone actually wants. A link index tells you a link exists; the
              log tells you it works. It is complete for traffic-sending links,
              it costs nothing, and no competitor can see it.
              Its blind spot is exact and worth stating: a link nobody clicks
              is invisible here, and those still pass ranking signal. So this
              is a floor on your link profile, never a census of it.

  footprint   Common Crawl presence. CC is the corpus a large share of LLM
              training and AI retrieval is built on, so "are we in Common
              Crawl" is a GEO question with a hard answer, and it is the
              upstream half of what `geo-scan` measures downstream. A site
              absent from CC is invisible to every tool that reads CC, and no
              amount of on-page work changes that until it is crawled.

MEASURED, NOT ASSUMED (2026-08-01, from this container):

  index.commoncrawl.org CDX          ✅ works. Control `example.com` answered
                                     in 4.2s with real capture records.
                                     A domain genuinely absent answers 404
                                     `No Captures found` in ~4s - fast, clean,
                                     and NOT an error.
  an older index (CC-MAIN-2025-38)   ⚠️ 504 Gateway Time-out. CC's per-index
                                     backends age out. Always resolve the
                                     CURRENT index from collinfo.json rather
                                     than hardcoding one, and treat a 504 as an
                                     unavailable index, not an absent site.
  a non-existent index id            404 `No index found for collection` - a
                                     DIFFERENT 404 from the one above, and
                                     conflating them turns a typo into
                                     "we are not in Common Crawl".
  domain-ranks.txt.gz (host graph)   ❌ 2,385,402,702 bytes. Real backlink
                                     edges live here and it is not downloadable
                                     on demand. Do not build on it.

Because two distinct 404s mean opposite things, `footprint` runs a CONTROL
lookup of a domain that must be present. If the control fails, the run reports
`unknown`, never `absent`.

Usage:
    backlinks.py referrers --remote root@host --glob '/var/log/caddy/access*.log*' --site example.com
    backlinks.py footprint --domain example.com
    backlinks.py footprint --domain example.com --all-indexes --limit 5000

Stdlib only. Writes nothing - prospects are recorded through seostate.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import crawllog
except Exception as exc:                                    # pragma: no cover
    crawllog = None
    _IMPORT_ERR = exc

UA = "seo-manager-backlinks/1.0"
CONTROL_DOMAIN = "example.com"

# Referrers that are not backlinks. Search engines and the site itself are the
# two that would otherwise dominate every report and mean nothing.
SEARCH_HOSTS = (
    "google.", "bing.com", "duckduckgo.com", "yandex.", "baidu.com", "ecosia.org",
    "search.brave.com", "startpage.com", "qwant.com", "search.marginalia.nu",
    "searx", "lite.duckduckgo.com", "html.duckduckgo.com", "seznam.cz", "naver.com",
    # Added after a live run put these in the backlink table, where a search
    # engine is never a backlink - it is the absence of one.
    "search.yahoo.com", "yahoo.co", "so.com", "sogou.com", "360.cn", "mail.ru",
    "ask.com", "aol.com", "petalsearch.com", "onesearch.com", "lycos.com",
    # Same reason, 2026-09-01: both landed in the genuine-backlink table on a live
    # run. `ya.ru` is Yandex's SHORT domain and the `yandex.` prefix above does not
    # match it - the kind of gap that only a real log surfaces.
    "ya.ru", "kagi.com",
)

# Not backlinks either: the developer's own machine. A local client hitting the
# site during testing produced 30,799 "referrals" from `localhost:8282` on the
# first real run - thirty times the largest genuine referrer, and it would have
# sat at the top of the report as the site's best backlink.
LOCAL_HOSTS = (
    "localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal",
    "tauri.localhost", "host.containers.internal",
)


AI_HOSTS = (
    "chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "gemini.google.com",
    "copilot.microsoft.com", "you.com", "phind.com", "poe.com", "mistral.ai",
)


# A referring domain is NOT automatically a backlink, and treating it as one is
# how this report ends up dominated by things nobody linked. Measured on
# combatskirmish.net 2026-09-01: of 40 referring domains, 34 survived the naive
# filter and only about 5 were real - the rest were the site's OWN second domain,
# Cloudflare IPs on cPanel ports, throwaway *.workers.dev hosts, and attack probes
# whose forged Referer header made `wordpress.org -> /wp-login.php` look like an
# editorial link from wordpress.org.
#
# These four buckets are all "not a person following a link to your content", and
# each is a different fact about the log, so they are reported separately rather
# than merged into one "spam" pile.

# Paths that are attacks, not visits. A Referer on one of these is forged or
# incidental; the request was never a referral in the first place.
PROBE_PATHS = (
    "/wp-login.php", "/wp-admin", "/xmlrpc.php", "/wp-content/", "/wp-includes/",
    "/.env", "/.git/", "/.ssh", "/administrator", "/phpmyadmin", "/vendor/",
    "/config.json", "/.aws", "/.s3cfg", "/@fs/", "/actuator", "/solr/",
)

# Landing paths that are an embed or a hotlink rather than a page visit.
# ⚠ These are SITE-TUNED. They say nothing about a hotlink that lands on a
# generic root asset, and a referrer arriving on `/favicon.ico` was therefore
# classified `genuine` and counted as a backlink - an overcount of the one
# number this instrument exists to produce. Caught 2026-09-01 by the control.
ASSET_PREFIXES = (
    "/frontend/", "/api/", "/mi/", "/g/", "/gr/", "/e/", "/sounds/", "/dl/",
    "/game/", "/map-images/", "/sm/", "/m/",
)

# Site-independent, and kept deliberately narrow: an extension list that grew to
# include `.html` or `.php` would start discarding real pages, which is the
# opposite and more expensive error. Only file types that a browser fetches as a
# SUBRESOURCE, never as a destination someone linked to.
ASSET_SUFFIXES = (
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg",
    ".css", ".js", ".mjs", ".map", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".ogg", ".wav", ".mp4", ".webm",
)
# Root files a crawler or a browser requests on its own initiative. A Referer on
# one of these is never an editorial link.
ASSET_EXACT = ("/favicon.ico", "/robots.txt", "/sitemap.xml", "/manifest.json",
               "/apple-touch-icon.png", "/browserconfig.xml", "/ads.txt",
               "/.well-known/")

# Ports that appear on shared-hosting/cPanel referrer spam. A referrer arriving
# on one of these is a spam bot advertising itself in the log, not a link.
SPAM_PORTS = {"2052", "2053", "2082", "2083", "2086", "2087", "2095", "2096",
              "8080", "8443", "8880"}

# Free throwaway hosts used for referrer spam and scraping.
THROWAWAY_SUFFIXES = (".workers.dev", ".pages.dev", ".herokuapp.com", ".vercel.app")


# ⚠ `controls` IS IMPORTED LAZILY, INSIDE run_control() - NOT HERE.
# This module SHIPS ITS OWN SOURCE to a remote host over stdin for `--remote`
# runs, and that remote python has no sibling files. A module-level
# `from controls import ...` dies there with ModuleNotFoundError, and the
# failure surfaces as "remote scan failed" - which reads as an SSH, glob or log
# problem and sends you to the wrong system. Broke exactly that way 2026-09-01.


def _registrable(host: str) -> str:
    """Good-enough eTLD+1 for same-owner matching. Deliberately NOT a public-suffix
    list: this only decides whether to LABEL a row as self-referral, and the rows
    are all still reported, so a wrong answer costs a label rather than data."""
    bare = host.split(":", 1)[0]
    parts = [x for x in bare.split(".") if x]
    return ".".join(parts[-2:]) if len(parts) >= 2 else bare


def classify_referrer(host: str, landing: str, own: set[str]) -> str:
    """self | probe | asset | spam | genuine - in that order of precedence.

    Probe beats everything after self because an attack with a forged Referer is
    not a referral at all, whatever the landing path looks like."""
    bare = host.split(":", 1)[0]
    port = host.split(":", 1)[1] if ":" in host else ""
    if _registrable(bare) in own or bare in own:
        return "self"
    if any(landing.startswith(x) or landing.rstrip("/").endswith(x) for x in PROBE_PATHS):
        return "probe"
    # A bare IP is never an editorial link; with a cPanel-ish port it is textbook
    # referrer spam.
    if bare.replace(".", "").isdigit() or port in SPAM_PORTS:
        return "spam"
    if bare.endswith(THROWAWAY_SUFFIXES):
        return "spam"
    path = landing.split("?", 1)[0].split("#", 1)[0]
    if (path.startswith(ASSET_PREFIXES) or path.startswith(ASSET_EXACT)
            or path.lower().endswith(ASSET_SUFFIXES)):
        return "asset"
    return "genuine"


def run_control() -> dict:
    """Prove the referrer classifier still discriminates.

    A referring domain is NOT a backlink. On a live run 40 of 52 were the site's
    own second domain, an attack probe whose forged `Referer` made
    `wordpress.org -> /wp-login.php` look editorial, a hotlinked favicon, or
    referrer spam on a cPanel port. A classifier that called everything
    `genuine` would have reported 52 backlinks - a 4x overcount of the single
    number this instrument exists to produce."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from controls import Controls          # noqa: PLC0415 - see the
    # note at the top: a module-level import breaks the --remote path.
    c = Controls("backlinks-control")
    own = {"example.com", "example.net"}

    def cls(host, landing="/"):
        return classify_referrer(host, landing, own)

    c.check("a_real_editorial_link_is_genuine", cls("news.site.test", "/guides/x") == "genuine")
    c.check("own_domain_is_self", cls("www.example.com", "/") == "self")
    c.check("own_domain_at_a_subdomain_is_self", cls("cdn.example.net", "/") == "self")
    c.check("own_domain_on_a_port_is_self", cls("example.com:8443", "/") == "self")

    # The one that inverts the finding: a forged Referer on an attack path.
    c.check("an_attack_probe_is_not_editorial",
            cls("wordpress.org", "/wp-login.php") == "probe",
            "this exact row read as an editorial link from wordpress.org")
    c.check("the_same_host_linking_to_a_real_page_is_still_genuine",
            cls("wordpress.org", "/guides/x") == "genuine",
            "the probe rule must key on the PATH, not on the host")

    c.check("a_bare_ip_is_spam", cls("203.0.113.9", "/") == "spam")
    c.check("a_cpanel_port_is_spam", cls("someshop.test:2083", "/") == "spam")
    c.check("a_site_tuned_asset_prefix_is_not_a_backlink",
            cls("blog.test", "/mi/de_dust2.jpg") == "asset")
    c.check("a_hotlinked_root_asset_is_not_a_backlink",
            cls("blog.test", "/favicon.ico") == "asset")
    c.check("an_asset_by_extension_is_not_a_backlink",
            cls("blog.test", "/img/hero.png?v=2") == "asset",
            "the query string must not defeat the suffix match")
    # The narrowness matters more than the rule: an extension list that grew to
    # cover pages would DISCARD real backlinks, which is the costlier direction.
    c.check("a_real_page_is_never_mistaken_for_an_asset",
            cls("blog.test", "/guides/how-to-aim.html") == "genuine")
    c.check("a_directory_page_is_never_mistaken_for_an_asset",
            cls("blog.test", "/maps/de_dust2") == "genuine")

    c.check("precedence_self_beats_probe",
            cls("example.com", "/wp-login.php") == "self",
            "an own-domain hit on a probe path is still self, not an inbound attack")

    c.check("registrable_folds_a_subdomain",
            _registrable("a.b.example.com") == "example.com")
    c.check("registrable_leaves_a_bare_name_alone", _registrable("localhost") == "localhost")

    c.check("localhost_is_local", is_local("localhost") is True)
    c.check("private_ranges_are_local", is_local("192.168.1.5") is True
            and is_local("10.0.0.1") is True)
    c.check("a_public_host_is_not_local", is_local("news.site.test") is False)

    c.check("host_of_strips_www", host_of("https://www.Site.test/a") == "site.test")
    c.check("host_of_survives_junk", host_of("not a url") == "")
    return c.verdict()


def is_local(host: str) -> bool:
    bare = host.split(":", 1)[0]
    if bare in LOCAL_HOSTS or bare.endswith(".localhost") or bare.endswith(".local"):
        return True
    if bare.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "169.254.")):
        return True
    return False


def host_of(url: str) -> str:
    try:
        h = urlsplit(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def die(msg, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}, indent=2))
    sys.exit(2)


# ------------------------------------------------------------- referrers

def cmd_referrers(a):
    if crawllog is None:
        die(f"crawllog.py must sit beside this file (import failed: {_IMPORT_ERR})")

    if a.remote:
        src = Path(__file__).read_text()
        args = ["referrers"]
        for g in a.glob or []:
            args += ["--glob", g]
        for f in a.file or []:
            args += ["--file", f]
        if a.days:
            args += ["--days", str(a.days)]
        if a.site:
            args += ["--site", a.site]
        for o in a.own or []:
            args += ["--own", o]
        args += ["--format", a.format, "--top", str(a.top)]
        # ⚠ EVERY referrers flag must be reconstructed here. A flag added to the
        # parser and forgotten in this list does not error - it is silently
        # dropped, and only on --remote runs, which is how this command is
        # normally used. --own shipped that way on 2026-09-01 and the report
        # went on counting the site's own second domain as its top backlink.
        # test_backlinks.py asserts this list against the parser.
        import shlex
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
        if a.ssh_key:
            cmd += ["-i", os.path.expanduser(a.ssh_key)]
        # Ship BOTH files - referrers imports crawllog, which will not exist
        # on the far side. Concatenating them into one stdin program keeps the
        # whole thing a single ssh round trip and needs nothing installed.
        base = (Path(__file__).resolve().parent / "crawllog.py").read_text()
        # Drop crawllog's own __main__ guard: in the merged file it sits in the
        # MIDDLE, so it would run crawllog's argparse (which has never heard of
        # `referrers`) before this half is even defined.
        combined = base.split('if __name__ == "__main__":')[0]
        combined += "\n\n# ---- backlinks.py appended ----\n"
        # `from __future__` is only legal at the top of a module, and this half
        # is being pasted at line ~1100. crawllog's own future-import already
        # applies to the merged module, so drop this one rather than move it.
        appended = "\n".join(
            l for l in src.splitlines()
            if not l.startswith("from __future__")
        )
        combined += appended.replace("import crawllog", "crawllog = sys.modules[__name__]")
        cmd += [a.remote, "python3 - " + " ".join(shlex.quote(x) for x in args)]
        p = subprocess.run(cmd, input=combined, capture_output=True, text=True, timeout=a.timeout)
        if p.returncode != 0:
            # A STRUCTURED refusal from the remote half (no input, nothing parsed)
            # arrives on stdout as JSON and is the actually-useful diagnosis. Passing
            # it through beats reporting `stderr: "", returncode: 2`, which tells the
            # reader nothing and invites them to guess at the cause - which is exactly
            # what happened when this was first hit (the missing --glob was misread as
            # a --format problem).
            try:
                remote_err = json.loads(p.stdout)
            except Exception:
                remote_err = None
            if isinstance(remote_err, dict) and remote_err.get("ok") is False:
                remote_err["remote"] = a.remote
                print(json.dumps(remote_err, indent=2))
                sys.exit(2)
            die("remote referrer scan failed", stderr=p.stderr[-2000:], returncode=p.returncode)
        sys.stdout.write(p.stdout)
        return

    parser = crawllog.make_parser(a.format, None, None)
    from datetime import datetime, timezone
    cutoff = datetime.now(timezone.utc).timestamp() - a.days * 86400 if a.days else None

    own = (a.site or "").lower()
    own = own[4:] if own.startswith("www.") else own

    domains: dict[str, dict] = {}
    search_hits = Counter()
    ai_hits = Counter()
    internal = 0
    local = 0
    direct = 0
    total = 0
    lines_read = 0
    parsed_records = 0

    class _Args:
        file = a.file
        glob = a.glob
        remote = getattr(a, "remote", None)
    _inputs = crawllog.expand_inputs(_Args)
    # REFUSE rather than report zeros. Without this, `referrers --remote host`
    # with no --glob read nothing and returned human_requests: 0 /
    # referring_domains: 0 - which reads as "this site has no backlinks", the
    # single most damaging false negative this script can produce. Measured
    # 2026-08-01: the same host with --glob gave 1,060,154 and 27.
    if not _inputs:
        print(json.dumps(crawllog.no_input_error(_Args), indent=2))
        sys.exit(2)
    for path in _inputs:
        try:
            fh = crawllog._open(path)
        except Exception as exc:
            die(f"cannot open {path}: {exc}")
        for line in fh:
            if not line.strip():
                continue
            lines_read += 1
            rec = parser(line)
            if rec is None:
                continue
            parsed_records += 1
            if cutoff and rec["ts"] and rec["ts"].timestamp() < cutoff:
                continue
            # Bots do not follow links the way people do, and their referrer is
            # usually absent or self-referential. Counting them would turn a
            # crawler's internal traversal into a "backlink".
            key, _label, _cat = crawllog.classify_ua(rec.get("ua") or "")
            if key is not None:
                continue
            total += 1
            ref = (rec.get("referer") or "").strip()
            if not ref:
                direct += 1
                continue
            h = host_of(ref)
            if not h:
                continue
            if own and (h == own or h.endswith("." + own) or h.rstrip(".") == own):
                internal += 1
                continue
            if is_local(h):
                local += 1
                continue
            if any(s in h for s in SEARCH_HOSTS):
                search_hits[h] += 1
                continue
            if any(s in h for s in AI_HOSTS):
                ai_hits[h] += 1
                continue
            d = domains.get(h)
            if d is None:
                d = domains[h] = {"hits": 0, "landing": Counter(), "sources": Counter(),
                                  "first": None, "last": None}
            d["hits"] += 1
            d["landing"][rec["uri"].split("?", 1)[0]] += 1
            d["sources"][ref[:180]] += 1
            if rec["ts"]:
                t = rec["ts"].timestamp()
                d["first"] = t if d["first"] is None else min(d["first"], t)
                d["last"] = t if d["last"] is None else max(d["last"], t)

    own = {_registrable(a.site)} if a.site else set()
    own |= {_registrable(x) for x in (a.own or [])}
    rows, excluded = [], []
    for h, d in sorted(domains.items(), key=lambda kv: -kv[1]["hits"]):
        top_landing = d["landing"].most_common(3)
        row = {
            "domain": h,
            "visits": d["hits"],
            "distinct_source_urls": len(d["sources"]),
            "top_source": d["sources"].most_common(1)[0][0] if d["sources"] else None,
            "top_landing": top_landing,
            "first_seen": crawllog._iso(d["first"]),
            "last_seen": crawllog._iso(d["last"]),
        }
        kind = classify_referrer(h, top_landing[0][0] if top_landing else "", own)
        if kind == "genuine":
            rows.append(row)
        else:
            row["excluded_as"] = kind
            excluded.append(row)

    # Files resolved but nothing came out of them: an empty log, a wrong --format,
    # or a rotated-away window. Same rule as above - that is a fact about the
    # INPUT, and reporting it as zero backlinks would be a fact about the site.
    if parsed_records == 0:
        print(json.dumps({
            "ok": False,
            "error": "input read but nothing parsed - no verdict available",
            "files_scanned": _inputs,
            "lines_read": lines_read,
            "parsed_records": 0,
            "format": a.format,
            # Two very different causes, and naming the wrong one sends the reader
            # off to debug --format when the real answer is a missing --glob. On a
            # --remote run the script itself arrives on stdin, so expand_inputs sees
            # a pipe and returns ["-"] - already consumed, hence 0 lines.
            "fix": (
                ("no --glob/--file was given, so this fell back to stdin and read nothing. "
                 "Pass --glob '/var/log/caddy/access*.log*' (QUOTE it, so your local shell "
                 "does not expand it before it reaches the remote host).")
                if _inputs == ["-"] else
                ("the files matched but nothing parsed: the log is empty, or --format does "
                 "not match it. Caddy writes JSON (one object per line); apache/nginx "
                 "default to combined. Try --format caddy or --format combined, and check "
                 "the files are not all rotated out of --days.")
            ),
        }, indent=2))
        sys.exit(2)

    print(json.dumps({
        "ok": True,
        "lines_read": lines_read,
        "human_requests": total,
        "direct_or_no_referrer": direct,
        "internal_referrals": internal,
        "local_dev_referrals": local,
        "referring_domains": len(rows),
        "referral_visits": sum(r["visits"] for r in rows),
        "excluded_domains": len(excluded),
        "excluded_by_reason": dict(Counter(r["excluded_as"] for r in excluded)),
        "search_referrals": dict(search_hits.most_common(10)),
        "ai_assistant_referrals": dict(ai_hits.most_common(10)),
        "backlinks": rows[: a.top],
        # Kept, not hidden: a misclassification has to be VISIBLE to be fixable,
        # and one person's referrer spam is another's small niche forum.
        "excluded": excluded[: a.top],
        "reading": {
            "backlinks": "Every row is a link a real person followed. These are PROVEN live and "
                         "proven to send traffic - stronger evidence than any index entry. "
                         "`excluded` holds the rows that did NOT qualify, with the reason: a "
                         "second domain you own, an attack probe whose forged Referer made it "
                         "look like a link, a hotlinked asset, or referrer spam. Read those "
                         "before trusting the count - the classifier is heuristic, and it is "
                         "better to relabel a row than to have the real links buried.",
            "excluded_by_reason": "self = your own registrable domain (pass --own for a second "
                                  "one). probe = the landing path is an exploit path, so the "
                                  "Referer is forged and no referral happened. asset = a "
                                  "hotlinked image/API, not a page visit. spam = a bare IP, a "
                                  "cPanel-style port, or a throwaway host.",
            "ai_assistant_referrals": "A visit whose referrer is an assistant means you were CITED "
                                      "and the citation was clicked. This is the hardest possible "
                                      "GEO evidence, and it is the downstream half of what "
                                      "crawllog.py's ai_search/ai_user categories measure upstream.",
            "blind_spot": "A backlink nobody clicks does not appear here and still passes ranking "
                          "signal. Treat this as a FLOOR on the link profile, never a census.",
            "next": "Record the ones worth keeping with `seostate.py prospect-add`, and look at "
                    "what the top referrers have in common - that is the seam that works.",
        },
    }, indent=2, ensure_ascii=False))


# ------------------------------------------------------------- footprint

def fetch_json(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def cc_indexes(limit):
    status, body = fetch_json("https://index.commoncrawl.org/collinfo.json", timeout=45)
    if status != 200:
        return None, f"collinfo.json returned {status}"
    try:
        cols = json.loads(body)
    except Exception as exc:
        return None, f"collinfo.json is not JSON: {exc}"
    return [c["id"] for c in cols][:limit], None


def cc_query(index_id, domain, limit):
    q = urllib.parse.urlencode({"url": domain, "matchType": "domain",
                                "output": "json", "limit": limit})
    status, body = fetch_json(f"https://index.commoncrawl.org/{index_id}-index?{q}")
    if status == 200:
        rows = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return {"state": "present", "captures": rows}
    if status == 404:
        # Two different 404s with opposite meanings. Distinguishing them is the
        # entire reason this function exists rather than a bare status check.
        if "No index found" in body:
            return {"state": "no_such_index", "detail": body[:200]}
        return {"state": "absent", "detail": body[:200]}
    if status in (502, 503, 504) or status == 0:
        return {"state": "unavailable", "detail": body[:200]}
    return {"state": "error", "http": status, "detail": body[:200]}


def cmd_footprint(a):
    ids, err = cc_indexes(a.indexes if a.all_indexes else 1)
    if ids is None:
        die(f"cannot list Common Crawl indexes: {err}")

    control = cc_query(ids[0], CONTROL_DOMAIN, 3)
    if control["state"] != "present":
        print(json.dumps({
            "ok": False,
            "error": "CONTROL FAILED - the Common Crawl index did not return captures for "
                     f"{CONTROL_DOMAIN}, which is certainly in the corpus.",
            "control": control,
            "index": ids[0],
            "verdict": "unknown",
            "reading": "Without a passing control, an 'absent' result for your own domain is "
                       "indistinguishable from a broken index. No conclusion is available.",
        }, indent=2))
        sys.exit(3)

    per_index = {}
    all_urls = set()
    statuses = Counter()
    mimes = Counter()
    for idx in ids:
        r = cc_query(idx, a.domain, a.limit)
        caps = r.get("captures") or []
        for c in caps:
            all_urls.add((c.get("url") or "").split("#")[0])
            statuses[str(c.get("status"))] += 1
            mimes[c.get("mime") or "?"] += 1
        per_index[idx] = {"state": r["state"], "captures": len(caps),
                          "detail": r.get("detail") if r["state"] != "present" else None}

    present = [i for i, v in per_index.items() if v["state"] == "present"]
    absent = [i for i, v in per_index.items() if v["state"] == "absent"]

    if present:
        verdict = "present"
        note = (f"{len(all_urls)} distinct URLs across {len(present)} index(es). "
                f"The site is in the corpus that a large share of LLM training and AI "
                f"retrieval draws on.")
    elif absent:
        verdict = "absent"
        note = ("Not in Common Crawl. Every tool built on the CC corpus - and a meaningful "
                "part of LLM pretraining - cannot see this site at all. This is upstream of "
                "everything geo-scan measures: you cannot be cited from a corpus you are not "
                "in. It is also NOT something on-page work fixes; CC has to crawl you, which "
                "follows from being linked and being crawlable, not from better content.")
    else:
        verdict = "unknown"
        note = "No index answered conclusively. Retry - CC's per-index backends time out."

    print(json.dumps({
        "ok": verdict != "unknown",
        "domain": a.domain,
        "control": {"domain": CONTROL_DOMAIN, "state": control["state"],
                    "captures": len(control.get("captures") or [])},
        "indexes_checked": ids,
        "per_index": per_index,
        "verdict": verdict,
        "distinct_urls": len(all_urls),
        "sample_urls": sorted(all_urls)[: a.top],
        "http_status_seen": dict(statuses.most_common(6)),
        "mime_seen": dict(mimes.most_common(6)),
        "note": note,
        "limits": "Common Crawl's LINK GRAPH (who links to whom) lives in a 2.4 GB file and is "
                  "deliberately not used here. This measures PRESENCE, not backlinks. For real "
                  "link counts the only accurate sources are paid; for links that demonstrably "
                  "work, use `backlinks.py referrers`.",
    }, indent=2, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("control", help="prove the referrer classifier discriminates").set_defaults(
        fn=lambda a: print(json.dumps(run_control(), indent=2, ensure_ascii=False)))

    s = sub.add_parser("referrers", help="real, traffic-sending backlinks from your access log")
    s.add_argument("-f", "--file", action="append")
    s.add_argument("--glob", action="append", help="QUOTE IT")
    s.add_argument("--format", default="auto",
                   choices=["auto", "caddy", "json", "combined", "common"])
    s.add_argument("--site", help="your own domain, so internal referrals are excluded")
    s.add_argument("--own", action="append",
                   help="ANOTHER domain you own, so its referrals are labelled self "
                        "rather than counted as a backlink. Repeatable.")
    s.add_argument("--days", type=int)
    s.add_argument("--top", type=int, default=40)
    s.add_argument("--remote", help="user@host - runs the scan there")
    s.add_argument("--ssh-key")
    s.add_argument("--timeout", type=int, default=900)
    s.set_defaults(fn=cmd_referrers)

    s = sub.add_parser("footprint", help="Common Crawl presence (the corpus AI reads)")
    s.add_argument("--domain", required=True)
    s.add_argument("--all-indexes", action="store_true", help="check several recent indexes")
    s.add_argument("--indexes", type=int, default=4, help="how many with --all-indexes")
    s.add_argument("--limit", type=int, default=1000, help="captures per index")
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(fn=cmd_footprint)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
