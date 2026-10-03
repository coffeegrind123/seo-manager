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

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all sitecheck tests passed")
