#!/usr/bin/env python3
"""Regression tests for sitecheck.py - the health workflow's three "always run"
audits (canonical, sitemap, redirect) that pointed at skills not installed.

Every classifier is fired both ways against a fake network in
`sitecheck.run_control()`; this suite adds the cases that are about how
findings are COUNTED rather than whether they fire.

    python3 test_sitecheck.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sitecheck as S  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' ' + str(detail)[:200]) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


print("the offline control")
c = S.run_control()
check("sitecheck control passes", c["ok"], [k for k, v in c["checks"].items() if v is not True])


def fake_net(table):
    def fetch(u):
        st, loc, body = table.get(u, (404, None, ""))
        return {"url": u, "status": st, "location": loc, "body": body if st == 200 else "",
                "headers": {}, "ctype": "text/html", "error": None}
    return fetch


print("\nhost duplication is ONE finding, not two")
allserve = fake_net({f"{s}://{h}/": (200, None, f"<p>{s}{h}</p>")
                     for s in ("http", "https") for h in ("x.test", "www.x.test")})
rules = [f["rule"] for f in S.check_hosts("https://x.test", fetch=allserve)["findings"]]
check("four self-serving variants -> host_duplicate, no host_split echo",
      rules.count("host_duplicate") == 1 and "host_split" not in rules, rules)
split = fake_net({"https://x.test/": (200, None, "a"), "https://www.x.test/": (200, None, "b"),
                  "http://x.test/": (301, "https://x.test/", ""),
                  "http://www.x.test/": (301, "https://www.x.test/", "")})
rules = [f["rule"] for f in S.check_hosts("https://x.test", fetch=split)["findings"]]
check("two canonical hosts are named", "host_duplicate" in rules, rules)
clean = fake_net({"https://x.test/": (200, None, "a"), "https://www.x.test/": (301, "https://x.test/", ""),
                  "http://x.test/": (301, "https://x.test/", ""),
                  "http://www.x.test/": (301, "https://x.test/", "")})
check("CONTROL: one permanent hop from every variant is clean",
      S.check_hosts("https://x.test", fetch=clean)["findings"] == [])
dead = fake_net({})
dead_all = lambda u: {"url": u, "status": None, "location": None, "body": "", "headers": {},
                      "ctype": "", "error": "timeout"}
r = S.check_hosts("https://x.test", fetch=dead_all)
check("no variant answering is a REFUSAL, not a clean host", r.get("control_failed") is True, r)

print("\nsoft 404")
r = S.check_soft404("https://x.test", fetch=fake_net({"https://x.test/": (200, None, "home")}))
check("an unknown URL answering 404 is clean", r["findings"] == [], r)
catch = lambda u: {"url": u, "status": 200, "location": None, "body": "<p>home</p>",
                   "headers": {}, "ctype": "text/html", "error": None}
r = S.check_soft404("https://x.test", fetch=catch)
check("a catch-all 200 is a soft 404", [f["rule"] for f in r["findings"]] == ["soft_404"], r)

print("\nsampling")
check("sample spreads across the list, not the head", S.sample(list(range(100)), 4) == [0, 25, 50, 75])
check("sample 0 means everything", len(S.sample(list(range(7)), 0)) == 7)


def net4(table):
    """url -> (status, location, body, headers); body kept on every status, as
    a real challenge page carries one."""
    def fetch(u):
        st, loc, body, hdrs = table.get(u, (404, None, "", {}))
        return {"url": u, "status": st, "location": loc, "body": body, "headers": hdrs,
                "ctype": "text/html", "error": None}
    return fetch


def rules_of(fs):
    return sorted(f["rule"] for f in fs)


print("\na challenge or a rate limit is not a dead page")
cf = net4({"https://x.test/p": (403, None, "<html><title>Just a moment...</title></html>",
                                {"cf-mitigated": "challenge"})})
check("a Cloudflare 403 challenge is unverifiable, not sitemap_url_error",
      rules_of(S.audit_url("https://x.test/p", fetch=cf)) == ["sitemap_url_unverifiable"],
      S.audit_url("https://x.test/p", fetch=cf))
aws = net4({"https://x.test/p": (202, None, "", {"x-amzn-waf-action": "challenge"})})
check("an AWS WAF 202 challenge is unverifiable",
      rules_of(S.audit_url("https://x.test/p", fetch=aws)) == ["sitemap_url_unverifiable"],
      S.audit_url("https://x.test/p", fetch=aws))
rl = net4({"https://x.test/p": (429, None, "", {"retry-after": "120"})})
f = S.audit_url("https://x.test/p", fetch=rl)
check("a 429 is unverifiable and carries its Retry-After",
      rules_of(f) == ["sitemap_url_unverifiable"] and f[0]["evidence"].get("retry_after") == "120", f)
dead = net4({"https://x.test/p": (404, None, "<html>nope</html>", {})})
check("CONTROL: a real 404 is still sitemap_url_error",
      rules_of(S.audit_url("https://x.test/p", fetch=dead)) == ["sitemap_url_error"])
cc = net4({"https://x.test/a": (200, None, '<head><link rel="canonical" href="/b"></head>', {}),
           "https://x.test/b": (403, None, "", {"cf-mitigated": "challenge"})})
r = S.check_canonicals(["https://x.test/a"], 1, fetch=cc)
check("a challenged canonical target is unverifiable, not critical canonical_broken",
      rules_of(r["findings"]) == ["canonical_target_unverifiable"], r["findings"])

print("\ncanonicals: <head> only, one of them, and on this host")
body_only = '<html><head><title>t</title></head><body><link rel="canonical" href="/x"></body></html>'
facts = S.page_facts(body_only, {})
check("a rel=canonical in <body> is not the canonical (Google: <head> only)",
      facts["canonical"] is None and facts["canonical_in_body"] == "/x", facts)
cb = net4({"https://x.test/a": (200, None, body_only, {})})
check("...and is named", "canonical_in_body" in rules_of(S.check_canonicals(["https://x.test/a"], 1,
                                                                            fetch=cb)["findings"]))
two = ('<head><link rel="canonical" href="/a"><link rel="canonical" href="/b"></head>')
ct = net4({"https://x.test/a": (200, None, two, {}), "https://x.test/b": (200, None, "<p>b</p>", {})})
check("two different canonicals are a conflict",
      "canonical_conflict" in rules_of(S.check_canonicals(["https://x.test/a"], 1, fetch=ct)["findings"]))
import hreflang as HL  # noqa: E402
check("hreflang reads the same canonical sitecheck does (the first)",
      HL.parse_page(two, "https://x.test/a")["canonical"] == "/a" and S.page_facts(two, {})["canonical"] == "/a")
xh = net4({"https://x.test/p": (200, None, '<head><link rel="canonical" href="https://staging.x.test/p"></head>', {}),
           "https://staging.x.test/p": (200, None, "<p>p</p>", {})})
check("a canonical on another host is named by check_canonicals",
      rules_of(S.check_canonicals(["https://x.test/p"], 1, fetch=xh)["findings"]) == ["canonical_cross_host"],
      S.check_canonicals(["https://x.test/p"], 1, fetch=xh)["findings"])
ok = net4({"https://x.test/p": (200, None, '<head><link rel="canonical" href="https://x.test/c"></head>', {}),
           "https://x.test/c": (200, None, "<p>c</p>", {})})
check("CONTROL: a same-host canonical to a 200 is clean",
      S.check_canonicals(["https://x.test/p"], 1, fetch=ok)["findings"] == [])

print("\na relative Sitemap: line is resolved, never a crash")
import providers as PV  # noqa: E402
r = PV.http("/sitemap.xml")
check("providers.http on a relative URL returns an error, it does not raise",
      r.get("status") is None and r.get("error"), r)
orig = S.http
S.http = lambda *a, **k: PV.HttpResult(status=200, body=b"Sitemap: /sitemap.xml\nSitemap: https://cdn.x.test/s.xml\n")
try:
    got = S.sitemaps_for("https://x.test")
finally:
    S.http = orig
check("sitemaps_for resolves a relative Sitemap: against the origin",
      got == ["https://x.test/sitemap.xml", "https://cdn.x.test/s.xml"], got)

print("\nsitemap coverage is only claimed from a COMPLETE sitemap read")
import json as _json  # noqa: E402
import tempfile  # noqa: E402
import sitegraph as SG  # noqa: E402
g = SG.Graph()
a_, b_ = g.uid("/a"), g.uid("/b")
g.adj[a_].append((b_, g.aid("b"), 0))
g.adj[b_].append((a_, g.aid("a"), 0))
g.meta[a_] = {"exists": True, "robots": "", "canonical": None}
g.meta[b_] = {"exists": True, "robots": "", "canonical": None}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    _json.dump(g.to_json({}), fh)
gpath = fh.name
okhop = net4({"https://x.test/a": (200, None, "<p>a</p>", {}), "https://x.test/b": (200, None, "<p>b</p>", {})})
orig_rs = S.read_sitemaps
try:
    S.read_sitemaps = lambda src: {"urls": [{"loc": "https://x.test/a", "lastmod": None}],
                                   "files": [{"sitemap": "sm1.xml", "kind": "urlset", "entries": 1}],
                                   "failed": [{"sitemap": "sm2.xml", "status": 503, "error": "HTTP 503"}],
                                   "truncated": False}
    r = S.check_links(gpath, "https://x.test", "https://x.test/sitemap.xml", 10, 1, fetch=okhop)
    check("a failed child sitemap suppresses indexable_not_in_sitemap",
          "indexable_not_in_sitemap" not in rules_of(r["findings"])
          and "sitemap_partial_for_coverage" in rules_of(r["findings"]), r["findings"])
    S.read_sitemaps = lambda src: {"urls": [{"loc": "https://x.test/a", "lastmod": None}],
                                   "files": [{"sitemap": "sm1.xml", "kind": "urlset", "entries": 1}],
                                   "failed": [], "truncated": True}
    r = S.check_links(gpath, "https://x.test", "https://x.test/sitemap.xml", 10, 1, fetch=okhop)
    check("a truncated read suppresses it too", "indexable_not_in_sitemap" not in rules_of(r["findings"]),
          r["findings"])
    S.read_sitemaps = lambda src: {"urls": [{"loc": "https://x.test/a", "lastmod": None}],
                                   "files": [{"sitemap": "sm1.xml", "kind": "urlset", "entries": 1}],
                                   "failed": [], "truncated": False}
    r = S.check_links(gpath, "https://x.test", "https://x.test/sitemap.xml", 10, 1, fetch=okhop)
    check("CONTROL: a complete read still reports /b missing",
          "indexable_not_in_sitemap" in rules_of(r["findings"]), r["findings"])
finally:
    S.read_sitemaps = orig_rs

print("\na deep URL redirected to the homepage is named (Google: may be treated as a soft 404)")
hn = net4({"https://x.test/old/guide": (301, "https://x.test/", "", {}),
           "https://x.test/": (200, None, "<p>home</p>", {}),
           "https://x.test/old/a": (301, "https://x.test/new/a", "", {}),
           "https://x.test/new/a": (200, None, "<p>a</p>", {}),
           "http://x.test/": (301, "https://x.test/", "", {})})
rr = rules_of(S.classify_chain("https://x.test/old/guide", S.follow_chain("https://x.test/old/guide", fetch=hn)))
check("an old deep URL -> / is redirect_to_homepage", rr == ["redirect_to_homepage"], rr)
check("CONTROL: a deep URL -> its new deep URL is clean",
      S.classify_chain("https://x.test/old/a", S.follow_chain("https://x.test/old/a", fetch=hn)) == [])
check("CONTROL: the homepage's own scheme hop is not 'to the homepage'",
      S.classify_chain("http://x.test/", S.follow_chain("http://x.test/", fetch=hn)) == [])

print("\nthree-way coverage: sitemap x crawl x pages Google showed (iannuttall index-coverage, re-derived)")
g2 = SG.Graph()
for u in ("/a", "/b", "/c"):
    g2.meta[g2.uid(u)] = {"exists": True, "robots": "", "canonical": None}
g2.meta[g2.uid("/noidx")] = {"exists": True, "robots": "noindex", "canonical": None}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    _json.dump(g2.to_json({}), fh)
g2path = fh.name
gsc_rows = {"rows": [{"keys": ["https://x.test/a"], "impressions": 30},
                     {"keys": ["https://x.test/old?ref=1"], "impressions": 4},
                     {"keys": ["https://x.test/b"], "impressions": 0}],
            "row_count": 3, "truncated": False, "dimensions": ["page"]}
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    _json.dump(gsc_rows, fh)
gpath2 = fh.name
try:
    S.read_sitemaps = lambda src: {"urls": [{"loc": u, "lastmod": None} for u in
                                            ("https://x.test/a", "https://x.test/b", "https://x.test/orphan")],
                                   "files": [{"sitemap": "s.xml"}], "failed": [], "truncated": False}
    r = S.coverage("https://x.test", "https://x.test/sitemap.xml", g2path, gpath2)
    sets = r.get("sets", {})
    check("in the sitemap but not reached by the crawl", sets.get("sitemap_not_crawled") == ["https://x.test/orphan"], r)
    check("crawled and indexable but not in the sitemap", sets.get("crawled_not_in_sitemap") == ["https://x.test/c"], r)
    check("noindex pages are not counted as crawled-indexable",
          "https://x.test/noidx" not in sets.get("crawled_not_in_sitemap", []))
    check("in the sitemap but Google showed it nowhere",
          sets.get("sitemap_no_impressions") == ["https://x.test/b", "https://x.test/orphan"], r)
    check("Google shows a page the sitemap does not list", sets.get("impressions_not_in_sitemap")
          == ["https://x.test/old?ref=1"], r)
    S.read_sitemaps = lambda src: {"urls": [], "files": [{"sitemap": "s.xml"}],
                                   "failed": [{"sitemap": "t.xml", "status": 503}], "truncated": False}
    r = S.coverage("https://x.test", "https://x.test/sitemap.xml", g2path, gpath2)
    check("a partly read sitemap refuses the join", r.get("ok") is False, r)
    S.read_sitemaps = orig_rs
    trunc = dict(gsc_rows, truncated=True)
    with open(gpath2, "w") as fh:
        _json.dump(trunc, fh)
    S.read_sitemaps = lambda src: {"urls": [{"loc": "https://x.test/a", "lastmod": None}],
                                   "files": [{"sitemap": "s.xml"}], "failed": [], "truncated": False}
    r = S.coverage("https://x.test", "https://x.test/sitemap.xml", g2path, gpath2)
    check("a truncated Search Console export refuses the impressions sets", r.get("ok") is False, r)
finally:
    S.read_sitemaps = orig_rs

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all sitecheck tests passed")
