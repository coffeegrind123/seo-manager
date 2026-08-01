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
        args += ["--format", a.format, "--top", str(a.top)]
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

    class _Args:
        file = a.file
        glob = a.glob
    for path in crawllog.expand_inputs(_Args):
        try:
            fh = crawllog._open(path)
        except Exception as exc:
            die(f"cannot open {path}: {exc}")
        for line in fh:
            if not line.strip():
                continue
            rec = parser(line)
            if rec is None:
                continue
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

    rows = []
    for h, d in sorted(domains.items(), key=lambda kv: -kv[1]["hits"]):
        rows.append({
            "domain": h,
            "visits": d["hits"],
            "distinct_source_urls": len(d["sources"]),
            "top_source": d["sources"].most_common(1)[0][0] if d["sources"] else None,
            "top_landing": d["landing"].most_common(3),
            "first_seen": crawllog._iso(d["first"]),
            "last_seen": crawllog._iso(d["last"]),
        })

    print(json.dumps({
        "ok": True,
        "human_requests": total,
        "direct_or_no_referrer": direct,
        "internal_referrals": internal,
        "local_dev_referrals": local,
        "referring_domains": len(rows),
        "referral_visits": sum(r["visits"] for r in rows),
        "search_referrals": dict(search_hits.most_common(10)),
        "ai_assistant_referrals": dict(ai_hits.most_common(10)),
        "backlinks": rows[: a.top],
        "reading": {
            "backlinks": "Every row is a link a real person followed. These are PROVEN live and "
                         "proven to send traffic - stronger evidence than any index entry.",
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

    s = sub.add_parser("referrers", help="real, traffic-sending backlinks from your access log")
    s.add_argument("-f", "--file", action="append")
    s.add_argument("--glob", action="append", help="QUOTE IT")
    s.add_argument("--format", default="auto",
                   choices=["auto", "caddy", "json", "combined", "common"])
    s.add_argument("--site", help="your own domain, so internal referrals are excluded")
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
