#!/usr/bin/env python3
"""The on-page SEO contract: baseline it, then prove a deploy did not break it.

Every other measurement in this skill asks the outside world a question. This
one asks the SITE a question, and it is the only one that can catch the failure
mode nobody notices: a deploy that ships a `noindex`, drops a schema block,
rewrites a canonical, or serves a 404 where a page used to be. None of that
shows up in rank data for weeks, and by then the cause is twenty commits back.

  baseline   snapshot the contract for a set of URLs
  check      re-fetch, diff against the baseline, emit findings
  history    what has been baselined and checked, and when
  resolve    close a finding by hand (the auto-resolver handles the rest)

WHAT MAKES THIS DIFFERENT FROM `drift.py`: drift.py watches THEIR page 1.
This watches YOUR markup. They share a word and nothing else.

THREE THINGS IT DOES THAT A NAIVE DIFF DOES NOT:

1. **Redirects are not followed.** A canonical that quietly starts 301-ing is
   the single most common silent regression, and following the redirect makes
   it look like a healthy 200. `follow=False` throughout.

2. **`X-Robots-Tag` is read from the HEADER as well as the meta tag.** A
   `noindex` delivered by header is invisible to every checker that only parses
   markup, and it deindexes exactly as hard.

3. **Findings have a LIFECYCLE keyed `(path, rule)`** rather than being a fresh
   diff each run. A finding opens once, stays open across checks while it is
   still true, and AUTO-RESOLVES the moment the page stops tripping it. So
   `check` answers "what is broken now" instead of "what changed since the last
   time I happened to run this", which is the question you actually have after
   a deploy.

A DEPLOY WINDOW IS NOT A REGRESSION - and this is the trap this tool would
otherwise walk straight into. A release that swaps a symlink can serve 404s
site-wide for minutes. Checking inside that window reports every page as
critically broken. `check` therefore refuses to record a verdict when MORE than
`--max-fail-share` of the set is non-200 at once, on the grounds that a site
that just lost every page has not "regressed" - it is mid-deploy or down, and
those need a different response than a content fix.

Stdlib only.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, BROWSER_UA  # noqa: E402
from hreflang import parse_page, _norm  # noqa: E402

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_root() -> Path:
    env = os.environ.get("SEO_ROOT")
    if env:
        return Path(env)
    cur = Path.cwd().resolve()
    for c in [cur, *cur.parents]:
        if (c / ".seo" / "config.json").exists() or (c / ".git").exists():
            return c
    return cur


def state_dir(explicit: str | None) -> Path:
    d = Path(explicit) if explicit else find_root() / ".seo" / "contract"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------- capture


ROBOTS_DIRECTIVES = ("noindex", "nofollow", "none", "noarchive", "nosnippet",
                     "noimageindex", "unavailable_after", "max-snippet")


def capture(url: str, timeout: int = 25) -> dict:
    """The contract for one URL. Redirects are REPORTED, never followed."""
    r = http(url, timeout=timeout, ua=BROWSER_UA, retries=1, follow=False)
    st = r.get("status")
    snap = {"url": url, "status": st, "fetched_at": now_iso(),
            "location": r.get("location"), "error": r.get("error")}
    if st != 200:
        return snap

    doc = r.text()
    p = parse_page(doc, url)

    # X-Robots-Tag is a real indexing directive and it is NOT in the markup, so
    # a checker that only parses HTML reports a deindexed page as healthy.
    ctype = r.get("ctype") or ""
    hdr_robots = (r.get("headers") or {}).get("x-robots-tag")

    og = {}
    for tag in re.findall(r"<meta\b[^>]*>", doc, re.I):
        a = dict(re.findall(r'([a-zA-Z:-]+)\s*=\s*"([^"]*)"', tag))
        prop = (a.get("property") or a.get("name") or "").lower()
        if prop.startswith("og:") or prop.startswith("twitter:"):
            og[prop] = a.get("content", "")

    body_text = re.sub(r"<[^>]+>", " ", re.sub(
        r"<(script|style|noscript)\b.*?</\1>", " ", doc, flags=re.I | re.S))
    body_text = re.sub(r"\s+", " ", htmllib.unescape(body_text)).strip()

    snap.update({
        "content_type": ctype,
        "canonical": _norm(p["canonical"]) if p["canonical"] else None,
        "meta_robots": (p["robots"] or "").lower() or None,
        "header_robots": (hdr_robots or "").lower() or None,
        "title": p["title"],
        "description": p["description"],
        "h1": p["h1"][:3],
        "h1_count": len(p["h1"]),
        "h2_count": p["h2_count"],
        "words": p["words"],
        "schema_types": p["schema_types"],
        "hreflang_count": len(p["alternates"]),
        "og": og,
        "internal_links": len(re.findall(r'<a\b[^>]*href="(?:/|' +
                                         re.escape(urllib.parse.urlsplit(url).netloc) +
                                         r')[^"]*"', doc, re.I)),
        "body_hash": hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16],
    })
    return snap


def capture_many(urls: list[str], workers: int = 6, timeout: int = 25) -> dict:
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(capture, u, timeout): u for u in urls}
        for f in concurrent.futures.as_completed(futs):
            s = f.result()
            out[s["url"]] = s
    return {u: out[u] for u in urls if u in out}


# --------------------------------------------------------------------- rules


def _indexable(snap: dict) -> bool:
    blob = " ".join(filter(None, [snap.get("meta_robots"), snap.get("header_robots")]))
    return "noindex" not in blob and "none" not in blob


def diff_snapshot(old: dict, new: dict) -> list[dict]:
    """Every regression rule, in one place, with its severity and its reason."""
    f = []
    path = urllib.parse.urlsplit(new["url"]).path or "/"

    def add(sev, rule, detail, fix=None):
        f.append({"severity": sev, "rule": rule, "path": path, "url": new["url"],
                  "detail": detail, **({"fix": fix} if fix else {})})

    o_st, n_st = old.get("status"), new.get("status")
    if o_st == 200 and n_st != 200:
        if n_st and 300 <= n_st < 400:
            add("critical", "page_now_redirects",
                f"was 200, now HTTP {n_st} -> {new.get('location')}",
                "If the move is intended, update internal links, the sitemap and any "
                "canonical pointing here. If it is not, this is a routing regression.")
        else:
            add("critical", "page_now_unavailable",
                f"was 200, now HTTP {n_st or new.get('error')}",
                "A URL that was indexed and now 4xx/5xx loses its ranking and any link "
                "equity pointing at it.")
        return f
    if o_st != 200 and n_st == 200:
        add("info", "page_recovered", f"was HTTP {o_st}, now 200")
        return f
    if n_st != 200:
        return f

    if _indexable(old) and not _indexable(new):
        add("critical", "noindex_added",
            f"meta robots={new.get('meta_robots')!r} header={new.get('header_robots')!r}",
            "This removes the page from the index entirely. If it came from a staging "
            "config leaking into production, check every other template too.")
    elif not _indexable(old) and _indexable(new):
        add("info", "noindex_removed", "the page is indexable again")

    oc, nc = old.get("canonical"), new.get("canonical")
    if oc and not nc:
        add("critical", "canonical_removed", f"was {oc}",
            "Without a self-canonical, duplicate URLs (query strings, trailing slashes) "
            "compete with the page.")
    elif oc != nc and nc:
        sev = "critical" if _norm(nc) != _norm(new["url"]) else "warning"
        add(sev, "canonical_changed", f"{oc} -> {nc}",
            "A canonical pointing away from this page hands its ranking to the target. "
            "That is correct only if it is deliberate.")
    if nc and _norm(nc) != _norm(new["url"]) and oc and _norm(oc) == _norm(old["url"]):
        add("critical", "canonical_now_points_elsewhere",
            f"the page was self-canonical and now points at {nc}")

    if old.get("title") and not new.get("title"):
        add("critical", "title_removed", f"was {old['title']!r}")
    elif old.get("title") != new.get("title"):
        add("warning", "title_changed", f"{old.get('title')!r} -> {new.get('title')!r}")

    if old.get("description") and not new.get("description"):
        add("warning", "description_removed", f"was {old['description'][:70]!r}")
    elif old.get("description") != new.get("description"):
        add("info", "description_changed",
            f"{(old.get('description') or '')[:50]!r} -> {(new.get('description') or '')[:50]!r}")

    if old.get("h1_count", 0) > 0 and new.get("h1_count", 0) == 0:
        add("critical", "h1_removed", f"was {old.get('h1')}")
    elif old.get("h1") and new.get("h1") and old["h1"] != new["h1"]:
        add("warning", "h1_changed", f"{old['h1']} -> {new['h1']}")

    lost = sorted(set(old.get("schema_types") or []) - set(new.get("schema_types") or []))
    gained = sorted(set(new.get("schema_types") or []) - set(old.get("schema_types") or []))
    if lost:
        add("critical", "schema_removed", f"types no longer present: {', '.join(lost)}",
            "A dropped structured-data block silently removes rich-result eligibility.")
    if gained:
        add("info", "schema_added", f"new types: {', '.join(gained)}")

    oh, nh = old.get("hreflang_count", 0), new.get("hreflang_count", 0)
    if oh > 0 and nh == 0:
        add("critical", "hreflang_removed", f"had {oh} alternates, now none")
    elif oh and nh and abs(nh - oh) >= max(2, round(oh * 0.25)):
        add("warning", "hreflang_count_changed", f"{oh} -> {nh} alternates")

    ow, nw = old.get("words", 0), new.get("words", 0)
    if ow >= 150 and nw < ow * 0.7:
        add("warning", "content_shrank",
            f"{ow} -> {nw} words ({nw / ow:.0%} of baseline)",
            "A drop this size usually means a section stopped rendering rather than "
            "being edited down.")
    elif ow and nw and old.get("body_hash") != new.get("body_hash"):
        add("info", "content_changed", f"{ow} -> {nw} words")

    for key in ("og:title", "og:description", "og:image"):
        if (old.get("og") or {}).get(key) and not (new.get("og") or {}).get(key):
            add("warning", "og_tag_removed", f"{key} is gone")

    ol, nl = old.get("internal_links", 0), new.get("internal_links", 0)
    if ol >= 10 and nl < ol * 0.5:
        add("warning", "internal_links_dropped", f"{ol} -> {nl} internal links")

    return f


# ----------------------------------------------------------------- lifecycle


def _key(f: dict) -> str:
    return f"{f['path']}::{f['rule']}"


def apply_lifecycle(store: dict, current: list[dict], checked_at: str) -> dict:
    """Open new findings, keep open ones open, auto-resolve what stopped tripping."""
    open_now = {_key(f): f for f in current}
    prior = {k: v for k, v in (store.get("findings") or {}).items()}

    opened, still_open, resolved = [], [], []
    for k, f in open_now.items():
        if k in prior and prior[k].get("state") == "open":
            prior[k].update({"last_seen": checked_at, "detail": f["detail"],
                             "severity": f["severity"], "seen_count":
                                 prior[k].get("seen_count", 1) + 1})
            still_open.append(prior[k])
        else:
            prior[k] = {**f, "state": "open", "first_seen": checked_at,
                        "last_seen": checked_at, "seen_count": 1}
            opened.append(prior[k])

    for k, v in prior.items():
        if v.get("state") == "open" and k not in open_now:
            v["state"] = "resolved"
            v["resolved_at"] = checked_at
            resolved.append(v)

    store["findings"] = prior
    return {"opened": opened, "still_open": still_open, "resolved": resolved}


# ------------------------------------------------------------------ commands


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_baseline(urls: list[str], sd: Path, name: str, workers: int) -> dict:
    snaps = capture_many(urls, workers=workers)
    ok = [s for s in snaps.values() if s.get("status") == 200]
    if not ok:
        return {"ok": False, "check": "contract-baseline", "name": name,
                "error": "no URL returned 200 - refusing to baseline a broken snapshot",
                "statuses": {u: s.get("status") for u, s in snaps.items()}}
    path = sd / f"{name}.json"
    store = _load(path)
    store.update({
        "name": name, "created_at": store.get("created_at") or now_iso(),
        "baselined_at": now_iso(), "urls": urls,
        "snapshots": snaps,
        "history": (store.get("history") or []) + [
            {"event": "baseline", "at": now_iso(), "urls": len(urls),
             "ok": len(ok), "non_200": len(snaps) - len(ok)}],
        "findings": store.get("findings") or {},
    })
    _save(path, store)
    return {"ok": True, "check": "contract-baseline", "name": name, "file": str(path),
            "urls": len(urls), "captured_200": len(ok),
            "non_200": {u: s.get("status") for u, s in snaps.items()
                        if s.get("status") != 200},
            "note": "Non-200 URLs are recorded as they are; a later recovery to 200 is "
                    "reported as page_recovered rather than treated as the norm."}


def cmd_check(sd: Path, name: str, workers: int, max_fail_share: float) -> dict:
    path = sd / f"{name}.json"
    store = _load(path)
    if not store.get("snapshots"):
        return {"ok": False, "check": "contract-check", "name": name,
                "error": f"no baseline named {name!r} - run `baseline` first",
                "available": sorted(p.stem for p in sd.glob("*.json"))}

    urls = store.get("urls") or list(store["snapshots"])
    fresh = capture_many(urls, workers=workers)
    checked_at = now_iso()

    was_ok = [u for u in urls if (store["snapshots"].get(u) or {}).get("status") == 200]
    now_bad = [u for u in was_ok if (fresh.get(u) or {}).get("status") != 200]
    share = (len(now_bad) / len(was_ok)) if was_ok else 0.0
    if was_ok and share > max_fail_share:
        return {
            "ok": False, "check": "contract-check", "name": name, "checked_at": checked_at,
            "verdict": "site_wide_failure",
            "error": (f"{len(now_bad)} of {len(was_ok)} previously-200 URLs are non-200 "
                      f"({share:.0%} > {max_fail_share:.0%}) - refusing to record a "
                      f"regression verdict"),
            "detail": ("A site-wide outage is not an SEO regression, and recording it as "
                       "one opens a critical finding on every page that then has to be "
                       "resolved by hand. A release that re-extracts a directory and "
                       "swaps a symlink can 404 everything for minutes; re-run once the "
                       "deploy has settled."),
            "statuses": {u: (fresh.get(u) or {}).get("status") for u in now_bad[:20]},
        }

    findings = []
    for u in urls:
        old, new = store["snapshots"].get(u), fresh.get(u)
        if not old or not new:
            continue
        findings.extend(diff_snapshot(old, new))

    life = apply_lifecycle(store, findings, checked_at)
    store["last_checked_at"] = checked_at
    store["history"] = (store.get("history") or []) + [
        {"event": "check", "at": checked_at, "urls": len(urls),
         "opened": len(life["opened"]), "still_open": len(life["still_open"]),
         "resolved": len(life["resolved"])}]
    _save(path, store)

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["path"], f["rule"]))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    verdict = ("pass" if not findings else
               "fail" if counts.get("critical") else
               "warn" if counts.get("warning") else "pass")
    return {
        "ok": True, "check": "contract-check", "name": name, "checked_at": checked_at,
        "baselined_at": store.get("baselined_at"),
        "verdict": verdict, "urls": len(urls), "counts": counts,
        "opened": life["opened"], "still_open": [
            {k: v for k, v in f.items() if k != "state"} for f in life["still_open"]],
        "resolved": [{"path": f["path"], "rule": f["rule"],
                      "first_seen": f.get("first_seen"), "resolved_at": f.get("resolved_at")}
                     for f in life["resolved"]],
        "note": "Findings are keyed (path, rule) and auto-resolve when the page stops "
                "tripping them, so this answers 'what is broken now', not 'what changed "
                "since I last looked'.",
    }


def cmd_history(sd: Path, name: str | None) -> dict:
    names = [name] if name else sorted(p.stem for p in sd.glob("*.json"))
    out = []
    for n in names:
        s = _load(sd / f"{n}.json")
        if not s:
            continue
        openf = [v for v in (s.get("findings") or {}).values() if v.get("state") == "open"]
        out.append({
            "name": n, "urls": len(s.get("urls") or []),
            "baselined_at": s.get("baselined_at"), "last_checked_at": s.get("last_checked_at"),
            "open_findings": len(openf),
            "open_by_severity": {sev: sum(1 for v in openf if v["severity"] == sev)
                                 for sev in ("critical", "warning", "info")
                                 if any(v["severity"] == sev for v in openf)},
            "events": (s.get("history") or [])[-8:],
        })
    return {"ok": True, "check": "contract-history", "baselines": out,
            "state_dir": str(sd)}


def cmd_resolve(sd: Path, name: str, path_: str, rule: str) -> dict:
    fp = sd / f"{name}.json"
    store = _load(fp)
    k = f"{path_}::{rule}"
    f = (store.get("findings") or {}).get(k)
    if not f:
        return {"ok": False, "error": f"no finding {k!r} in {name!r}",
                "open": [kk for kk, vv in (store.get("findings") or {}).items()
                         if vv.get("state") == "open"]}
    f["state"] = "resolved"
    f["resolved_at"] = now_iso()
    f["resolved_by"] = "manual"
    _save(fp, store)
    return {"ok": True, "check": "contract-resolve", "resolved": k,
            "note": "A manual resolve does NOT re-baseline. If the new markup is the "
                    "intended contract, run `baseline` so the next check compares "
                    "against it."}


# ---------------------------------------------------------------------- main


def _urls(a) -> list[str]:
    urls = list(a.url or [])
    if getattr(a, "sitemap", None):
        from hreflang import urls_from_sitemap
        urls += urls_from_sitemap(a.sitemap, limit=a.max_urls)
    if getattr(a, "urls_file", None):
        raw = (sys.stdin.read() if a.urls_file == "-"
               else Path(a.urls_file).read_text(encoding="utf-8"))
        urls += [x.strip() for x in raw.splitlines() if x.strip() and not x.startswith("#")]
    return list(dict.fromkeys(urls))[:getattr(a, "max_urls", 200)]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state-dir", help="default <repo>/.seo/contract")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="snapshot the contract")
    b.add_argument("--url", action="append")
    b.add_argument("--sitemap")
    b.add_argument("--urls-file")
    b.add_argument("--name", default="default")
    b.add_argument("--max-urls", type=int, default=200)
    b.add_argument("--workers", type=int, default=6)

    c = sub.add_parser("check", help="diff the live site against the baseline")
    c.add_argument("--name", default="default")
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--max-fail-share", type=float, default=0.34,
                   help="above this share of previously-200 URLs failing, refuse a "
                        "verdict and report an outage instead (default 0.34)")

    h = sub.add_parser("history")
    h.add_argument("--name")

    rs = sub.add_parser("resolve", help="close a finding by hand")
    rs.add_argument("--name", default="default")
    rs.add_argument("--path", required=True)
    rs.add_argument("--rule", required=True)

    a = p.parse_args()
    sd = state_dir(a.state_dir)

    if a.cmd == "baseline":
        urls = _urls(a)
        out = (cmd_baseline(urls, sd, a.name, a.workers) if urls else
               {"ok": False, "error": "no URLs - pass --url, --sitemap or --urls-file"})
    elif a.cmd == "check":
        out = cmd_check(sd, a.name, a.workers, a.max_fail_share)
    elif a.cmd == "history":
        out = cmd_history(sd, a.name)
    else:
        out = cmd_resolve(sd, a.name, a.path, a.rule)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
