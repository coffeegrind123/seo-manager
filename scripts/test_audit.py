#!/usr/bin/env python3
"""Regression tests for the audit workflow (scripts/audit/), offline.

The audit scripts came from seo-audit / seo-audit-full and were merged here on
2026-10-25. What these pin:
  - audit_net: stdlib fetching with the redirect chain, every hop re-checked
    against private addresses (the original checked only the first host and
    `requests` followed a redirect to 127.0.0.1 unchecked);
  - check-site: robots.txt read by agentcheck (exact tokens, combined groups,
    partial blocks) and judged by Google's status rules (a 404 robots.txt
    blocks nothing; it was a Fail), noindex by directives.py;
  - check-page: length is a display note, never a Warning (Google sets no limit);
  - render-advanced: a module that did not run is Not measured, never Pass.

    python3 test_audit.py
"""
from __future__ import annotations

import http.server
import importlib.util
import os
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "audit"
sys.path.insert(0, str(AUDIT))
sys.path.insert(0, str(HERE))
FAILS: list[str] = []


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), AUDIT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:220]}")
    if not cond:
        FAILS.append(name)


import audit_net  # noqa: E402

# ------------------------------------------------------------------ audit_net
ROUTES = {"/a": (301, {"Location": "/b"}, b""), "/b": (302, {"Location": "/c"}, b""),
          "/c": (200, {"Content-Type": "text/html"}, b"<p>c</p>"),
          "/loop": (301, {"Location": "/loop"}, b""),
          "/robots-404": (404, {}, b"nope")}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        st, hdrs, body = ROUTES.get(self.path, (404, {}, b""))
        self.send_response(st)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

print("audit_net: stdlib fetches with a redirect chain")
r = audit_net.get(BASE + "/a")
check("a private address is refused by default", r.error and "private" in r.error, r)
os.environ["AUDIT_ALLOW_PRIVATE"] = "1"
r = audit_net.get(BASE + "/a")
check("the chain is recorded hop by hop", r.status_code == 200 and [h["status_code"] for h in r.history] == [301, 302]
      and r.url.endswith("/c") and r.text == "<p>c</p>", r)
r = audit_net.get(BASE + "/a", follow=False)
check("follow=False returns the first hop and its Location", r.status_code == 301 and r.location == BASE + "/b", r)
r = audit_net.get(BASE + "/loop")
check("a redirect loop ends as too many redirects", r.error and "Too many redirects" in r.error, r)
os.environ.pop("AUDIT_ALLOW_PRIVATE")
check("every hop is re-checked: a public host redirecting to a private one is refused",
      audit_net.blocked_address("http://127.0.0.1/") is not None
      and audit_net.blocked_address("https://example.com/") is None)

# ------------------------------------------------------------------ check-site
cs = load("check-site")
print("\ncheck-site: robots.txt through agentcheck")
check("Disallow: / blocks all", cs._robots_blocks_all("User-agent: *\nDisallow: /\n"))
check("Disallow: / with Allow: /blog/ is not a full block",
      not cs._robots_blocks_all("User-agent: *\nDisallow: /\nAllow: /blog/\n"))
check("an Applebot group does not bind Googlebot or *",
      not cs._robots_blocks_all("User-agent: Applebot\nDisallow: /\n"))
check("a trailing comment does not become a rule",
      not cs._robots_blocks_all("User-agent: *\nDisallow: /private # not / at all\n"))

saved = cs._safe_fetch
try:
    cs._safe_fetch = lambda url, timeout: (404, "nope", None)
    r = cs.check_robots("https://x.test", 5)
    check("a 404 robots.txt is a Warning (nothing blocked), not a Fail", r["status"] == "warn"
          and "nothing is blocked" in r["detail"], r)
    cs._safe_fetch = lambda url, timeout: (503, "", None)
    r = cs.check_robots("https://x.test", 5)
    check("a 5xx robots.txt is a Fail (Google stops crawling for 12 hours)", r["status"] == "fail", r)
    cs._safe_fetch = lambda url, timeout: (200, "User-agent: *\nAllow: /cdn-cgi/\n\nUser-agent: *\nDisallow: /\n"
                                                 "Sitemap: https://x.test/s.xml\n", None)
    r = cs.check_robots("https://x.test", 5)
    check("a second * group (Cloudflare managed block) is combined: / closed is not a pass",
          r["status"] == "warn" and "disallowed" in r["detail"], r)
    cs._safe_fetch = lambda url, timeout: (200, "User-agent: *\nAllow: /\nSitemap: https://x.test/s.xml\n", None)
    r = cs.check_robots("https://x.test", 5)
    check("CONTROL: an open robots.txt with a Sitemap passes", r["status"] == "pass", r)
finally:
    cs._safe_fetch = saved

print("\ncheck-site: noindex through directives.py")
check("max-image-preview:none is not noindex",
      not cs._html_has_noindex('<meta name="robots" content="index, max-image-preview:none">'))
check("`follow,none` is noindex", cs._html_has_noindex('<meta name="robots" content="follow,none">'))
check("a commented-out noindex is not noindex", not cs._html_has_noindex('<!-- <meta name="robots" content="noindex"> -->'))

# ------------------------------------------------------------------ check-page
cp = load("check-page")
print("\ncheck-page: length is a display note (Google sets no limit)")
T78 = "Play Chess Online - Free Multiplayer Chess Games in Your Browser | Example Chess"
r = cp._check_title(T78, "play chess online")
check("a 78-char title is not a Warning on length alone", r["status"] == "pass", r)
check("...but says the end may be cut on display", "device width" in r["detail"], r["detail"])
check("a short but real title is not a Warning", cp._check_title("Acme tools", "acme tools")["status"] == "pass")
check("CONTROL: a placeholder-length title still fails", cp._check_title("Home")["status"] == "fail")
check("CONTROL: a missing title still fails", cp._check_title(None)["status"] == "fail")
r = cp._check_meta_description("Play chess online free in your browser. " * 5, "chess")
check("a 200-char description is not a Warning on length alone", r["status"] == "pass", r)
check("...and says the end may be cut", "device width" in r["detail"], r["detail"])
check("CONTROL: a missing description still fails", cp._check_meta_description(None)["status"] == "fail")

# ------------------------------------------------------------- render-advanced
ra = load("render-advanced")


def ok(data):
    return {"state": "ok", "data": data}


print("\nrender-advanced: a module that did not run is Not measured")
out = ra.render({"instruments": {"agentcheck": {"state": "no_key", "reason": "needs KEY"}}})
card = {c["section"]: c for c in out["scorecard"]}
check("its section's status is na", card["AI & Agent Readiness"]["status"] == "na", card["AI & Agent Readiness"])
check("it is in no summary list", not any("AI & Agent Readiness" in x for v in out["summary"].values() for x in v))
check("the reason is printed", "needs KEY" in out["fragments"]["ai_readiness_html"])
run = {"instruments": {"sitecheck": ok({
    "parts": {"hosts": {"ok": True, "variants": [1, 2]}, "soft404": {"ok": True, "status": 404, "verdict": "HTTP 404"},
              "headers": {"ok": True}, "sitemap": [{"ok": True, "entries": 3, "audited": 3}],
              "canonicals": {"ok": True, "checked": 3}, "variants": {"ok": True, "checked": 2}},
    "findings": [{"severity": "critical", "rule": "canonical_broken", "url": "https://x.test/a",
                  "detail": "canonical answers 404", "fix": "fix it"},
                 {"severity": "low", "rule": "no_framing_policy", "url": "https://x.test/", "detail": "framable"}]})}}
out = ra.render(run)
check("a hoisted critical finding lands on its own part, as Fail",
      "Canonical targets (sitemap sample)" in " ".join(out["summary"]["critical"]), out["summary"])
check("a low finding is Info", "status-info" in out["fragments"]["indexability_html"])
check("text is escaped", "<script>" not in ra.render({"instruments": {"sitecheck": ok({
    "parts": {"headers": {"ok": True}}, "findings": [{"severity": "medium", "rule": "x",
                                                      "detail": "<script>alert(1)</script>"}]})}})
                                         ["fragments"]["indexability_html"])
run = {"instruments": {
    "pagecheck": ok({"vitals": {"ok": True, "field_crux": {"inp": {"p75": 240, "category": "AVERAGE"},
                                                          "cls": {"p75": 6, "category": "FAST"}}}}),
    "vitals_origin_history": ok({"state": "answered", "origin": "https://x.test", "form_factor": "PHONE",
                                 "metrics": {"interaction_to_next_paint": {"latest_p75": 4968, "trend": "insufficient"}}})}}
out = ra.render(run)
check("field vitals keep their scope apart",
      "Field Core Web Vitals (real users): INP p75 (origin, phones)" in out["summary"]["critical"]
      and "Field Core Web Vitals (real users): INP p75 (this URL)" in out["summary"]["warnings"], out["summary"])
check("PSI's CLS x100 is shown as a CLS value", "0.06 (FAST)" in out["fragments"]["field_vitals_html"])

out = ra.render({"instruments": {"sitegraph_target": ok({"found": True, "inbound_links": 3, "linking_pages": 2,
                                                          "contextual_links": 1, "click_depth": 1}),
                                  "sitegraph_broken": ok({"targets_without_a_page": 0, "worst": [],
                                                          "unreadable": [{"url": "/modes", "linked_from": 78}]})}})
check("an unreadable crawl target is Info, never a broken-link Fail",
      "Internal Links" not in " ".join(out["summary"]["critical"]) and "/modes" in out["fragments"]["link_graph_html"],
      out["summary"])

srv.shutdown()
print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all audit tests passed")
