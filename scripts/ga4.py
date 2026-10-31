#!/usr/bin/env python3
"""ga4.py - Google Analytics 4: which AI assistants send people, to which
pages, and whether those visits do anything.

Search Console says what Google showed; access logs say what crawlers took;
neither says what a visitor from ChatGPT or Perplexity did once they arrived.
GA4 does, and every sibling MCP server surveyed on 2026-10-25 read it while
this skill did not. Stdlib only: it signs with the SAME service account as
gsc.py (gsc.access_token, Analytics scope), so there is still one signer.

    ga4.py properties                                   # what this key can read
    ga4.py --property 123456 metadata                   # the property's own schema
    ga4.py --property 123456 ai-referrals --start D --end D
    ga4.py --property 123456 landing --start D --end D [--limit 50]
    ga4.py --property 123456 realtime                   # last 30 min: is the tag firing?
    ga4.py control                                      # offline

Setup, once: enable the "Google Analytics Data API" (and, for `properties`,
the "Google Analytics Admin API") on the service account's GCP project, and
add its client_email to the GA4 property as a Viewer. Each missing step is a
NAMED state (`api_disabled` with the enable link, `no_access`), never "no
data".

Every report reads the property's metadata first and refuses if a field it
needs is not there: GA4 renamed `conversions` to `keyEvents`, and a report
built on a name the property no longer has returns zero, which reads as "AI
visitors never convert".
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gsc  # noqa: E402  - the signer and the HTTP error states
import stats  # noqa: E402
from backlinks import ai_host  # noqa: E402

SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
DATA = "https://analyticsdata.googleapis.com/v1beta"
ADMIN = "https://analyticsadmin.googleapis.com/v1beta"
PAGE_SIZE = 10000          # runReport's documented default page size


def call(url, payload=None, method=None, timeout=90, scope=SCOPE):
    return gsc.call(url, payload, method=method, timeout=timeout, scope=scope)


def out(payload, code=0):
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    sys.exit(code)


def classify_error(err: dict) -> dict:
    """Which 403 this is. Google answers "API not enabled" and "no access to
    this property" with the same status; only the message tells them apart."""
    detail = err.get("detail") or ""
    if err.get("http") == 403 and re.search(r"has not been used in project|is disabled", detail):
        m = re.search(r"https://console\.developers\.google\.com/apis/api/[^\s\"']+", detail)
        return {"state": "api_disabled", "enable_url": m.group(0) if m else None,
                "reason": "the API is not enabled on the service account's GCP project - "
                          "nothing about the property has been measured"}
    if err.get("http") == 403:
        return {"state": "no_access",
                "reason": "add the service account's client_email to the GA4 property "
                          "(Admin > Property access management) as a Viewer"}
    if err.get("http") == 404:
        return {"state": "no_such_property", "reason": "no GA4 property with that id"}
    return {"state": "cannot_ask", "reason": err.get("reason") or "request failed"}


def _fail(check: str, err: dict, **extra):
    out({"ok": False, "check": check, **classify_error(err), "http": err.get("http"), **extra}, 2)


def resolve_fields(meta: dict) -> tuple[dict | None, str | None]:
    """The names THIS property uses, or why the report cannot be built."""
    dims = {d.get("apiName") for d in meta.get("dimensions") or []}
    mets = {m.get("apiName") for m in meta.get("metrics") or []}
    missing = [n for n in ("sessionSource", "landingPage") if n not in dims]
    missing += [n for n in ("sessions", "engagedSessions") if n not in mets]
    key = "keyEvents" if "keyEvents" in mets else ("conversions" if "conversions" in mets else None)
    if key is None:
        missing.append("keyEvents|conversions")
    if missing:
        return None, (f"the property's schema has no {', '.join(missing)} - refusing rather than "
                      "reporting zeros for a field that does not exist")
    return {"source": "sessionSource", "page": "landingPage", "sessions": "sessions",
            "engaged": "engagedSessions", "key_events": key}, None


def _metadata(prop: str):
    return call(f"{DATA}/properties/{prop}/metadata")


def _report_rows(prop: str, start: str, end: str, f: dict) -> tuple[list, dict, dict | None]:
    """Every row, paged by offset to rowCount."""
    body = {"dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": f["source"]}, {"name": f["page"]}],
            "metrics": [{"name": f["sessions"]}, {"name": f["engaged"]}, {"name": f["key_events"]}],
            "limit": PAGE_SIZE, "returnPropertyQuota": True}
    rows, offset, last = [], 0, {}
    while True:
        d, err = call(f"{DATA}/properties/{prop}:runReport", dict(body, offset=offset))
        if err:
            return rows, last, err
        last = d or {}
        page = last.get("rows") or []
        rows += page
        offset += len(page)
        if not page or offset >= int(last.get("rowCount") or 0):
            return rows, last, None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def ai_referrals(prop: str, start: str, end: str) -> dict:
    meta, err = _metadata(prop)
    if err:
        return {"ok": False, "check": "ga4-ai-referrals", **classify_error(err)}
    f, why = resolve_fields(meta or {})
    if why:
        return {"ok": False, "check": "ga4-ai-referrals", "state": "schema_changed", "reason": why}
    rows, last, err = _report_rows(prop, start, end, f)
    if err:
        return {"ok": False, "check": "ga4-ai-referrals", **classify_error(err),
                "rows_before_failure": len(rows)}
    by_host, by_page, total = {}, {}, 0.0
    for r in rows:
        src = (r.get("dimensionValues") or [{}])[0].get("value") or ""
        page = (r.get("dimensionValues") or [{}, {}])[1].get("value") or ""
        s, e, k = (_num(m.get("value")) for m in (r.get("metricValues") or [{}, {}, {}])[:3])
        total += s
        host = ai_host(src)
        if not host:
            continue
        h = by_host.setdefault(host, {"ai_host": host, "sessions": 0, "engaged_sessions": 0,
                                      "key_events": 0, "pages": set()})
        h["sessions"] += s
        h["engaged_sessions"] += e
        h["key_events"] += k
        h["pages"].add(page)
        p = by_page.setdefault(page, {"page": page, "sessions": 0, "key_events": 0, "hosts": set()})
        p["sessions"] += s
        p["key_events"] += k
        p["hosts"].add(host)
    ai = sum(h["sessions"] for h in by_host.values())
    lo, hi = stats.wilson(int(ai), int(total)) if total else (None, None)
    hosts = sorted(by_host.values(), key=lambda h: -h["sessions"])
    for h in hosts:
        h["pages"] = len(h["pages"])
        for k in ("sessions", "engaged_sessions", "key_events"):
            h[k] = int(h[k])
        h["engagement_rate"] = round(h["engaged_sessions"] / h["sessions"], 3) if h["sessions"] else None
    pages = sorted(by_page.values(), key=lambda p: -p["sessions"])
    for p in pages:
        p["hosts"] = sorted(p["hosts"])
        p["sessions"], p["key_events"] = int(p["sessions"]), int(p["key_events"])
    return {
        "ok": True, "check": "ga4-ai-referrals", "property": prop, "start": start, "end": end,
        "fields": f, "rows_read": len(rows),
        "ai_share_of_sessions": {"sessions": int(ai), "of": int(total),
                                 "rate": round(ai / total, 4) if total else None,
                                 "ci95": [round(lo, 5), round(hi, 5)] if lo is not None else None},
        "by_ai_host": hosts, "landing_pages": pages[:50],
        # GA4 flags thresholded / sampled reports in the response metadata; it
        # is passed through whole rather than read field by field.
        "response_metadata": last.get("metadata"),
        "property_quota": last.get("propertyQuota"),
        "reading": ("AI hosts are matched as hosts (backlinks.ai_host). A visit from an "
                    "assistant whose app strips the referrer lands in (direct), so this is a "
                    "FLOOR. `response_metadata` says whether GA4 thresholded or sampled the "
                    "report - read it before quoting a small number."),
    }


def landing(prop: str, start: str, end: str, limit: int) -> dict:
    meta, err = _metadata(prop)
    if err:
        return {"ok": False, "check": "ga4-landing", **classify_error(err)}
    f, why = resolve_fields(meta or {})
    if why:
        return {"ok": False, "check": "ga4-landing", "state": "schema_changed", "reason": why}
    rows, last, err = _report_rows(prop, start, end, f)
    if err:
        return {"ok": False, "check": "ga4-landing", **classify_error(err)}
    pages = {}
    for r in rows:
        page = (r.get("dimensionValues") or [{}, {}])[1].get("value") or ""
        s, e, k = (_num(m.get("value")) for m in (r.get("metricValues") or [{}, {}, {}])[:3])
        p = pages.setdefault(page, [0.0, 0.0, 0.0])
        p[0] += s
        p[1] += e
        p[2] += k
    top = sorted(pages.items(), key=lambda kv: -kv[1][0])[:limit]
    return {"ok": True, "check": "ga4-landing", "property": prop, "fields": f,
            "pages": [{"page": pg, "sessions": int(s), "engaged_sessions": int(e),
                       "key_events": int(k)} for pg, (s, e, k) in top],
            "response_metadata": last.get("metadata")}


def realtime(prop: str) -> dict:
    """Active users in the last 30 minutes, by country and page title. The standard
    reports lag hours behind on a new property, so this is the only same-day proof
    that a just-installed tag is sending hits (measured 2026-10-25: runReport had no
    rows while realtime already showed 18)."""
    d, err = call(f"{DATA}/properties/{prop}:runRealtimeReport",
                  {"dimensions": [{"name": "country"}, {"name": "unifiedScreenName"}],
                   "metrics": [{"name": "activeUsers"}], "limit": 250})
    if err:
        return {"ok": False, "check": "ga4-realtime", **classify_error(err)}
    by_country, by_page, total = {}, {}, 0
    for r in d.get("rows") or []:
        country, page = (v.get("value") for v in (r.get("dimensionValues") or [{}, {}])[:2])
        n = int(_num((r.get("metricValues") or [{}])[0].get("value")))
        total += n
        by_country[country] = by_country.get(country, 0) + n
        by_page[page] = by_page.get(page, 0) + n
    return {"ok": True, "check": "ga4-realtime", "property": prop, "window": "last 30 minutes",
            "active_users": total,
            "by_country": dict(sorted(by_country.items(), key=lambda kv: -kv[1])),
            "top_pages": [{"page": p_, "active_users": n} for p_, n in
                          sorted(by_page.items(), key=lambda kv: -kv[1])[:20]],
            "reading": ("0 here on a site with traffic means the tag is not firing: check the "
                        "page source for the measurement id and the browser console for a CSP "
                        "refusal of googletagmanager.com" if not total else
                        "the tag is live; the standard reports catch up within hours")}


def run_control() -> dict:
    from controls import Controls          # noqa: PLC0415
    c = Controls("ga4-control")
    dis = {"http": 403, "detail": "API has not been used in project 1 before or it is disabled. "
                                  "https://console.developers.google.com/apis/api/x/overview?project=1"}
    c.check("an_api_not_enabled_is_api_disabled", classify_error(dis)["state"] == "api_disabled")
    c.check("CONTROL_a_permission_403_is_no_access",
            classify_error({"http": 403, "detail": "insufficient permissions"})["state"] == "no_access")
    meta = {"dimensions": [{"apiName": "sessionSource"}, {"apiName": "landingPage"}],
            "metrics": [{"apiName": "sessions"}, {"apiName": "engagedSessions"}, {"apiName": "keyEvents"}]}
    c.check("CONTROL_a_full_schema_resolves", resolve_fields(meta)[1] is None)
    c.check("a_missing_field_refuses", resolve_fields({"dimensions": [], "metrics": []})[0] is None)
    c.check("ai_hosts_are_hosts_not_substrings", ai_host("bayou.com") is None and ai_host("you.com"))
    return c.verdict()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--property", help="GA4 property id (numbers only), or `ga4_property` "
                                       "in .seo/config.json")
    ap.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("control", help="offline: error states, schema guard, host matching")
    sub.add_parser("properties", help="GA4 properties this service account can read")
    sub.add_parser("metadata", help="the property's own dimension and metric names")
    sub.add_parser("realtime", help="active users in the last 30 minutes - is the tag firing?")
    for name in ("ai-referrals", "landing"):
        s = sub.add_parser(name)
        s.add_argument("--start", required=True)
        s.add_argument("--end", required=True)
        if name == "landing":
            s.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    if a.cmd == "control":
        res = run_control()
        out(res, 0 if res.get("ok") else 1)
    if a.cmd == "properties":
        d, err = call(f"{ADMIN}/accountSummaries")
        if err:
            _fail("ga4-properties", err)
        props = [{"property": p.get("property", "").split("/")[-1], "name": p.get("displayName"),
                  "account": acc.get("displayName")}
                 for acc in d.get("accountSummaries") or [] for p in acc.get("propertySummaries") or []]
        out({"ok": True, "check": "ga4-properties", "properties": props, "count": len(props)})
    prop = a.property
    if not prop:
        cfg = Path(a.root) / ".seo" / "config.json"
        if cfg.exists():
            try:
                prop = json.loads(cfg.read_text(encoding="utf-8")).get("ga4_property")
            except json.JSONDecodeError:
                prop = None
    if not prop or not str(prop).isdigit():
        out({"ok": False, "check": "ga4", "state": "cannot_ask",
             "reason": "pass --property <numeric id> or set ga4_property in .seo/config.json; "
                       "`ga4.py properties` lists them"}, 2)
    if a.cmd == "metadata":
        d, err = _metadata(prop)
        if err:
            _fail("ga4-metadata", err)
        out({"ok": True, "check": "ga4-metadata", "property": prop,
             "dimensions": sorted(x.get("apiName") for x in d.get("dimensions") or []),
             "metrics": sorted(x.get("apiName") for x in d.get("metrics") or [])})
    if a.cmd == "realtime":
        res = realtime(prop)
        out(res, 0 if res.get("ok") else 2)
    res = (ai_referrals(prop, a.start, a.end) if a.cmd == "ai-referrals"
           else landing(prop, a.start, a.end, a.limit))
    out(res, 0 if res.get("ok") else 2)


if __name__ == "__main__":
    main()
