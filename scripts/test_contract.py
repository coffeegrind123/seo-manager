#!/usr/bin/env python3
"""Regression tests for the SEO contract guard.

Same argument as test_hreflang.py: the live site passes this check cleanly, and
a checker hard-wired to return "pass" produces byte-identical output. So every
rule is fired here against synthetic snapshots, plus the three behaviours that
are easy to get subtly wrong and impossible to notice in production:

  - the LIFECYCLE (open -> still open -> auto-resolved)
  - the OUTAGE GUARD (a deploy window is not a regression)
  - `noindex` delivered by HEADER rather than by meta tag

Run: python3 test_contract.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


URL = "https://x.test/p"


def snap(**kw):
    base = {
        "url": URL, "status": 200, "canonical": "https://x.test/p",
        "meta_robots": "index,follow", "header_robots": None,
        "title": "T", "description": "D", "h1": ["H"], "h1_count": 1,
        "h2_count": 4, "words": 800, "schema_types": ["Article", "Organization"],
        "hreflang_count": 3, "og": {"og:title": "t", "og:image": "i"},
        "internal_links": 40, "body_hash": "aaa",
    }
    base.update(kw)
    return base


def rules(old, new) -> set:
    return {f["rule"] for f in contract.diff_snapshot(old, new)}


def sev_of(old, new, rule):
    for f in contract.diff_snapshot(old, new):
        if f["rule"] == rule:
            return f["severity"]
    return None


print("the happy path must be CLEAN (or nothing below means anything)")
check("identical snapshots produce no findings", not rules(snap(), snap()), str(rules(snap(), snap())))

print("\nevery critical rule must fire")
check("page_now_unavailable", "page_now_unavailable" in rules(snap(), snap(status=404)))
check("page_now_redirects", "page_now_redirects" in rules(
    snap(), snap(status=301, location="https://x.test/q")))
check("noindex_added (meta)", "noindex_added" in rules(snap(), snap(meta_robots="noindex,follow")))
check("noindex_added (HEADER - invisible to a markup-only checker)",
      "noindex_added" in rules(snap(), snap(header_robots="noindex")))
check("'none' counts as noindex", "noindex_added" in rules(snap(), snap(meta_robots="none")))
check("canonical_removed", "canonical_removed" in rules(snap(), snap(canonical=None)))
check("canonical_changed", "canonical_changed" in rules(
    snap(), snap(canonical="https://x.test/other")))
check("canonical pointing away is CRITICAL, not a warning",
      sev_of(snap(), snap(canonical="https://x.test/other"), "canonical_changed") == "critical")
check("title_removed", "title_removed" in rules(snap(), snap(title=None)))
check("h1_removed", "h1_removed" in rules(snap(), snap(h1=[], h1_count=0)))
check("schema_removed", "schema_removed" in rules(snap(), snap(schema_types=["Organization"])))
check("hreflang_removed", "hreflang_removed" in rules(snap(), snap(hreflang_count=0)))

print("\nwarnings")
check("title_changed", "title_changed" in rules(snap(), snap(title="T2")))
check("h1_changed", "h1_changed" in rules(snap(), snap(h1=["H2"])))
check("content_shrank", "content_shrank" in rules(snap(), snap(words=300, body_hash="b")))
check("og_tag_removed", "og_tag_removed" in rules(snap(), snap(og={"og:title": "t"})))
check("internal_links_dropped", "internal_links_dropped" in rules(snap(), snap(internal_links=5)))
check("description_removed", "description_removed" in rules(snap(), snap(description=None)))
check("hreflang_count_changed", "hreflang_count_changed" in rules(snap(), snap(hreflang_count=9)))

print("\nrecovery is reported, not silently swallowed")
check("page_recovered", "page_recovered" in rules(snap(status=404), snap()))
check("noindex_removed", "noindex_removed" in rules(snap(meta_robots="noindex"), snap()))

print("\nnoise control - these must NOT fire")
check("a small wording change is not 'content_shrank'",
      "content_shrank" not in rules(snap(), snap(words=780, body_hash="b")))
check("schema ADDED is not a removal",
      "schema_removed" not in rules(snap(), snap(schema_types=["Article", "Organization", "FAQPage"])))
check("a 404 that stays a 404 emits nothing",
      not rules(snap(status=404), snap(status=404)))
check("an unavailable page does not ALSO emit content findings",
      rules(snap(), snap(status=500)) == {"page_now_unavailable"})

print("\nlifecycle: open -> still open -> auto-resolve")
store: dict = {}
f1 = [{"severity": "critical", "rule": "noindex_added", "path": "/p", "url": URL, "detail": "d"}]
r1 = contract.apply_lifecycle(store, f1, "t1")
check("first sighting opens", len(r1["opened"]) == 1 and not r1["still_open"])
r2 = contract.apply_lifecycle(store, f1, "t2")
check("second sighting stays open, does not re-open",
      not r2["opened"] and len(r2["still_open"]) == 1, str(r2))
check("seen_count increments", store["findings"]["/p::noindex_added"]["seen_count"] == 2)
r3 = contract.apply_lifecycle(store, [], "t3")
check("disappearance auto-resolves", len(r3["resolved"]) == 1, str(r3))
check("resolved rows carry resolved_at",
      store["findings"]["/p::noindex_added"].get("resolved_at") == "t3")
r4 = contract.apply_lifecycle(store, f1, "t4")
check("a recurrence re-opens", len(r4["opened"]) == 1, str(r4))
check("first_seen is reset on re-open, not left stale",
      store["findings"]["/p::noindex_added"]["first_seen"] == "t4")

print("\nthe outage guard: a deploy window is NOT a regression")


def fake_check(baseline_statuses, live_statuses, max_fail_share=0.34, tmp=None):
    import json as _j
    sd = Path(tmp)
    sd.mkdir(parents=True, exist_ok=True)
    urls = list(baseline_statuses)
    store = {"name": "t", "urls": urls, "baselined_at": "t0", "findings": {},
             "snapshots": {u: snap(url=u, status=s) for u, s in baseline_statuses.items()}}
    (sd / "t.json").write_text(_j.dumps(store))

    def fake_capture_many(us, workers=6, timeout=25):
        return {u: snap(url=u, status=live_statuses[u]) for u in us}

    real = contract.capture_many
    contract.capture_many = fake_capture_many
    try:
        return contract.cmd_check(sd, "t", 6, max_fail_share)
    finally:
        contract.capture_many = real


import tempfile  # noqa: E402

tmp = tempfile.mkdtemp()
allurls = {f"https://x.test/{i}": 200 for i in range(10)}

res = fake_check(allurls, {u: 404 for u in allurls}, tmp=tmp + "/a")
check("a site-wide 404 refuses a verdict",
      res.get("ok") is False and res.get("verdict") == "site_wide_failure", str(res)[:140])

partial = {u: (404 if i < 2 else 200) for i, u in enumerate(allurls)}
res = fake_check(allurls, partial, tmp=tmp + "/b")
check("2 of 10 failing IS reported as a regression",
      res.get("ok") is True and res.get("counts", {}).get("critical") == 2, str(res.get("counts")))

res = fake_check(allurls, {u: 200 for u in allurls}, tmp=tmp + "/c")
check("all healthy is a clean pass",
      res.get("ok") is True and res.get("verdict") == "pass", str(res.get("counts")))

print("\nbaseline refuses to snapshot a fully broken site")


def fake_baseline(statuses, tmp):
    def fake_capture_many(us, workers=6, timeout=25):
        return {u: snap(url=u, status=statuses[u]) for u in us}
    real = contract.capture_many
    contract.capture_many = fake_capture_many
    try:
        return contract.cmd_baseline(list(statuses), Path(tmp), "b", 6)
    finally:
        contract.capture_many = real


Path(tmp + "/d").mkdir(parents=True, exist_ok=True)
r = fake_baseline({u: 503 for u in allurls}, tmp + "/d")
check("baselining an all-503 site is refused", r.get("ok") is False, str(r)[:120])
r = fake_baseline(allurls, tmp + "/d")
check("baselining a healthy site works", r.get("ok") is True, str(r)[:120])

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all contract tests passed")
