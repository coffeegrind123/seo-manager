#!/usr/bin/env python3
"""Regression tests for ga4.py - offline, against a fake Analytics API.

What these pin, each a way a GA4 read returns confident nonsense:
  - an API that is not enabled on the service account's project answers 403
    with the enable link in the body (measured 2026-10-25 on the Admin API);
    that is `api_disabled` with the link, never "no data" and never
    "no access";
  - a field renamed in GA4's schema (conversions -> keyEvents is the known one)
    would report 0 forever, so every report first reads the property's own
    metadata and refuses on a missing name;
  - AI referrals are matched as hosts (backlinks.ai_host), so bayou.com is not
    you.com, and every page of rows is read, not the first 10,000.

    python3 test_ga4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ga4  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:220]}")
    if not cond:
        FAILS.append(name)


DISABLED = {"http": 403, "reason": "permission denied", "detail": (
    '{"error": {"code": 403, "message": "Google Analytics Data API has not been used in project '
    '123456789012 before or it is disabled. Enable it by visiting https://console.developers.google.com'
    '/apis/api/analyticsdata.googleapis.com/overview?project=123456789012 then retry."}}')}
DENIED = {"http": 403, "reason": "permission denied",
          "detail": '{"error": {"code": 403, "message": "User does not have sufficient permissions for this property."}}'}

print("a 403 says WHICH 403")
s = ga4.classify_error(DISABLED)
check("an API not enabled is api_disabled, with the link to enable it",
      s["state"] == "api_disabled" and "analyticsdata.googleapis.com/overview?project=123456789012" in s["enable_url"], s)
check("CONTROL: a permissions 403 is no_access", ga4.classify_error(DENIED)["state"] == "no_access")
check("a 404 is no_such_property", ga4.classify_error({"http": 404, "detail": ""})["state"] == "no_such_property")

META = {"dimensions": [{"apiName": "sessionSource"}, {"apiName": "landingPage"}, {"apiName": "date"}],
        "metrics": [{"apiName": "sessions"}, {"apiName": "engagedSessions"}, {"apiName": "keyEvents"}]}

print("\nthe property's own schema is read before every report")
f, err = ga4.resolve_fields(META)
check("keyEvents is used when the property has it", err is None and f["key_events"] == "keyEvents", (f, err))
old = {"dimensions": META["dimensions"], "metrics": [{"apiName": "sessions"}, {"apiName": "engagedSessions"},
                                                     {"apiName": "conversions"}]}
check("conversions is the fallback on an older schema", ga4.resolve_fields(old)[0]["key_events"] == "conversions")
gone = {"dimensions": [{"apiName": "landingPage"}], "metrics": META["metrics"]}
f, err = ga4.resolve_fields(gone)
check("a missing sessionSource refuses instead of reporting zero", f is None and "sessionSource" in err, err)


def rows(*items):
    return [{"dimensionValues": [{"value": src}, {"value": page}],
             "metricValues": [{"value": str(s)}, {"value": str(e)}, {"value": str(k)}]}
            for src, page, s, e, k in items]


PAGES = [
    {"rows": rows(("chatgpt.com", "/a", 10, 6, 1), ("bayou.com", "/a", 50, 40, 0),
                  ("google", "/a", 900, 500, 9)), "rowCount": 5},
    {"rows": rows(("perplexity.ai", "/b", 4, 2, 0), ("copilot.com", "/a", 3, 3, 1)), "rowCount": 5},
]
sent = []


def fake(url, payload=None, method=None, timeout=90, scope=None):
    sent.append(payload)
    if url.endswith("/metadata"):
        return META, None
    off = (payload or {}).get("offset", 0)
    return (PAGES[0] if off == 0 else PAGES[1]), None


print("\nAI referrals")
ga4.call = fake
ga4.PAGE_SIZE = 3
r = ga4.ai_referrals("123", "2026-09-01", "2026-09-30")
hosts = {h["ai_host"]: h for h in r["by_ai_host"]}
check("every page is read (offset paging to rowCount)", r["rows_read"] == 5, r.get("rows_read"))
check("bayou.com is not you.com, and google is not an AI host",
      set(hosts) == {"chatgpt.com", "perplexity.ai", "copilot.com"}, sorted(hosts))
check("sessions and key events are summed per AI host",
      hosts["chatgpt.com"]["sessions"] == 10 and hosts["copilot.com"]["key_events"] == 1, hosts)
check("landing pages are ranked by AI sessions", [p["page"] for p in r["landing_pages"]] == ["/a", "/b"],
      r["landing_pages"])
check("the share of all sessions carries an interval",
      r["ai_share_of_sessions"]["sessions"] == 17 and r["ai_share_of_sessions"]["ci95"], r["ai_share_of_sessions"])
req = [p for p in sent if p and "dimensions" in p][0]
check("the request names the property's own field names",
      [d["name"] for d in req["dimensions"]] == ["sessionSource", "landingPage"]
      and [m["name"] for m in req["metrics"]] == ["sessions", "engagedSessions", "keyEvents"], req)

print("\nrealtime: the last 30 minutes, the only proof of a just-installed tag")
REALTIME = {"rows": [{"dimensionValues": [{"value": "Brazil"}, {"value": "Play Online Free"}],
                      "metricValues": [{"value": "2"}]},
                     {"dimensionValues": [{"value": "Singapore"}, {"value": "/servers"}],
                      "metricValues": [{"value": "1"}]}], "rowCount": 2}
seen = []


def fake_rt(url, payload=None, method=None, timeout=90, scope=None):
    seen.append(url)
    return REALTIME, None


ga4.call = fake_rt
r = ga4.realtime("123")
check("calls runRealtimeReport, not runReport", seen and seen[-1].endswith("/properties/123:runRealtimeReport"), seen)
check("active users are summed and broken down",
      r["active_users"] == 3 and r["by_country"] == {"Brazil": 2, "Singapore": 1}
      and r["top_pages"][0] == {"page": "Play Online Free", "active_users": 2}, r)
ga4.call = lambda *a, **k: ({"rows": [], "rowCount": 0}, None)
r = ga4.realtime("123")
check("no active users is a measured zero with a reading, not an error",
      r["ok"] is True and r["active_users"] == 0 and "tag" in r["reading"], r)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all ga4 tests passed")
