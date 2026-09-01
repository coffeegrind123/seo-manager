#!/usr/bin/env python3
"""Free, keyless technical checks for ANY url - plus the archive history of it.

Four jobs, three of which need no key and no account at all:

  html     W3C Nu validator - is the markup actually valid
  schema   validator.schema.org - what structured data Google's own extractor
           finds on the page (this is the same service the Rich Results Test
           uses; it is keyless and it answers for any public URL)
  history  Wayback: first capture, last capture, how often the page has
           CHANGED, and the change closest to a date you care about
  vitals   PageSpeed Insights - Core Web Vitals. The only one needing a
           credential, and it does NOT need a new key: see psi_token().

`history` is the one worth knowing about. When a page starts losing rank, the
question that actually matters is "what changed, and was it us or them" - and
the Wayback CDX index answers it for free, for any URL, including competitors'.
A page-1 competitor whose digest changed three weeks before your decline is a
different story from one that has not been touched in two years.

Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, read_secret, cache_get, cache_put  # noqa: E402
from controls import Controls  # noqa: E402

GSC_SA = os.environ.get("GSC_SERVICE_ACCOUNT", str(Path.home() / ".gsc_service_account.json"))


# --------------------------------------------------------------- credentials


def _sa_access_token(sa: dict, scope: str) -> str:
    """RS256 JWT -> OAuth access token using openssl. No third-party deps."""
    def b64(b: bytes) -> bytes:
        return base64.urlsafe_b64encode(b).rstrip(b"=")

    now = int(time.time())
    header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = b64(json.dumps({
        "iss": sa["client_email"], "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode())
    signing_input = header + b"." + claim
    key_file = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False)
    try:
        key_file.write(sa["private_key"])
        key_file.close()
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_file.name],
                             input=signing_input, capture_output=True, check=True).stdout
    finally:
        os.unlink(key_file.name)
    assertion = (signing_input + b"." + b64(sig)).decode()
    r = http("https://oauth2.googleapis.com/token",
             data=urllib.parse.urlencode({
                 "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                 "assertion": assertion}).encode(),
             headers={"Content-Type": "application/x-www-form-urlencoded"})
    j = r.json() or {}
    if "access_token" not in j:
        raise RuntimeError(f"token exchange {r.get('status')}: {str(j)[:200]}")
    return j["access_token"]


def psi_token() -> tuple[str | None, str]:
    """A bearer token PSI will accept, or an explanation.

    ⚠ The scope matters, and getting it wrong hides the real problem. Measured
    2026-08-01 against the live API with the existing GSC service account:

        cloud-platform        -> 403 "Request had insufficient authentication scopes"
        webmasters.readonly   -> 403 "Request had insufficient authentication scopes"
        openid                -> 403 "PageSpeed Insights API has not been used in
                                 project <N> before or it is disabled"

    Only the third message is the truth. The first two read like a permissions
    problem that needs a different credential; the actual blocker is a free,
    one-click Enable on the project that already owns the service account. So
    this mints with `openid` deliberately - a worse-looking error that is a far
    more useful one.
    """
    key = read_secret("GOOGLE_API_KEY", "~/.google_api_key")
    if key:
        return f"key:{key}", "GOOGLE_API_KEY"
    try:
        sa = json.loads(Path(GSC_SA).read_text())
    except Exception as e:
        return None, f"no GOOGLE_API_KEY and no service account at {GSC_SA} ({e})"
    try:
        return _sa_access_token(sa, "openid"), f"service account {sa.get('client_email')}"
    except Exception as e:
        return None, f"service-account token mint failed: {e}"


# -------------------------------------------------------------------- checks


def check_html(url: str) -> dict:
    r = http("https://validator.w3.org/nu/?out=json&doc=" + urllib.parse.quote(url, safe=""),
             timeout=60, retries=1)
    j = r.json()
    if not isinstance(j, dict) or "messages" not in j:
        return {"ok": False, "check": "html", "url": url, "status": r.get("status"),
                "error": "validator did not return a message list",
                "body_head": r.text()[:200]}
    msgs = j["messages"]
    errors = [m for m in msgs if m.get("type") == "error"]
    warnings = [m for m in msgs if m.get("subType") == "warning" or m.get("type") == "info"]
    return {
        "ok": True, "check": "html", "url": url,
        "errors": len(errors), "warnings": len(warnings),
        "top_errors": [{"line": m.get("lastLine"), "message": m.get("message", "")[:160]}
                       for m in errors[:10]],
        "note": "HTML validity is not a ranking factor on its own. It matters when an "
                "error breaks parsing of something that IS - a truncated <head>, an "
                "unclosed element swallowing structured data, a broken canonical.",
    }


# Rich-result types Google has RETIRED. The markup stays valid schema.org and
# the validator is perfectly happy with it - which is exactly the problem: a
# page can pass structured-data validation cleanly while every type on it is
# one Google stopped rendering. Nothing in the validator says so.
#
# `effect` is what removal does, and it is the field that stops this becoming
# cargo-cult advice: a dead rich result is not a reason to rip out markup that
# other consumers still read. Sourced from Google's own announcements; the
# table and its citations are in `references/schema-gates.md`.
DEPRECATED_TYPES = {
    "FAQPage": ("2026-05-07", "Rich results fully retired for all sites.",
                "Not a defect. Keep it if non-SERP consumers read it; do not add it "
                "for search benefit. For genuine user-submitted Q&A use QAPage."),
    "HowTo": ("2023-09", "Rich result removed from desktop and mobile.",
              "The vocabulary is still valid; there is no SERP effect. Clear <h2> step "
              "headings do the comprehension work now."),
    "ClaimReview": ("2025-06-12", "Fact-check rich result retired; Google ignores the markup.",
                    "No replacement."),
    "VehicleListing": ("2025-06-12", "Dealer-inventory rich cards no longer render.",
                       "Use Product if the vehicle is sold online."),
    "EstimatedSalary": ("2025-06-12", "Salary rich result retired.",
                        "JobPosting with baseSalary still works for specific roles."),
    "OccupationalAggregateRating": ("2025-06-12", "Retired with EstimatedSalary.", "None."),
    "SpecialAnnouncement": ("2025-07-31", "The COVID-era emergency card was retired.",
                            "Use Event if time-bounded, else Article/WebPage."),
    "LearningVideo": ("2025-06-12", "Retired.", "VideoObject still renders."),
    "PracticeProblem": ("2026-01", "Rich Results Test, Search Console reporting and the "
                                   "appearance filter dropped support.", "None."),
}

# Explicitly NOT deprecated, because it is the one people remove by mistake.
NOT_DEPRECATED_NOTE = {
    "Dataset": "NOT discontinued - Dataset markup is consumed by Dataset Search (live), "
               "just not by Google Search rich results. Do not remove it.",
}


def schema_gate(types: dict) -> list[dict]:
    """Flag retired rich-result types found on a page. Never a hard failure."""
    out = []
    for t, n in (types or {}).items():
        bare = t.split("/")[-1].split("#")[-1]
        if bare in DEPRECATED_TYPES:
            since, what, instead = DEPRECATED_TYPES[bare]
            out.append({"severity": "info", "type": bare, "count": n,
                        "retired": since, "detail": what, "guidance": instead})
        elif bare in NOT_DEPRECATED_NOTE:
            out.append({"severity": "info", "type": bare, "count": n,
                        "retired": None, "detail": NOT_DEPRECATED_NOTE[bare],
                        "guidance": "Leave it in place."})
    out.sort(key=lambda r: (r["retired"] or "", r["type"]))
    return out


def run_control() -> dict:
    """Prove the schema gate and the credential resolver discriminate.

    The gate's answers matter more than they look: a RETIRED rich-result type is
    a reason not to ADD it, and only sometimes a reason to remove it. A gate that
    flagged everything, or nothing, would turn that judgement into noise."""
    c = Controls("pagecheck-control")

    c.check("a_retired_type_is_flagged",
            len(schema_gate({"FAQPage": 1})) == 1, str(schema_gate({"FAQPage": 1})))
    c.check("a_current_type_is_not_flagged",
            schema_gate({"Article": 1}) == [], str(schema_gate({"Article": 1})))
    c.check("a_fully_qualified_type_is_recognised",
            len(schema_gate({"https://schema.org/FAQPage": 1})) == 1,
            "the extractor returns fully-qualified URIs, not bare names")
    c.check("an_empty_page_produces_no_findings", schema_gate({}) == [])
    c.check("a_retired_finding_carries_its_retirement_date",
            (schema_gate({"FAQPage": 1})[0].get("retired") or "") != "")
    c.check("a_retired_finding_is_never_a_hard_failure",
            all(r["severity"] == "info" for r in schema_gate({"FAQPage": 1, "HowTo": 1})))
    c.check("the_gate_discriminates_rather_than_flagging_everything",
            len(schema_gate({"FAQPage": 1, "Article": 1, "VideoGame": 1})) == 1)
    c.check("the_registry_is_populated", len(DEPRECATED_TYPES) >= 3)

    # CREDENTIALS. "No key" must be an explanation, never a score. A vitals
    # check that silently returned zeros would read as a catastrophically slow
    # page rather than as an unasked question.
    tok, why = psi_token()
    c.check("credential_resolution_always_explains_itself", bool(why))
    c.check("a_missing_credential_is_none_not_an_empty_string",
            tok is None or isinstance(tok, str))
    return c.verdict(psi_credential=why if not tok else "present",
                     note="the gate is proven offline; whether PSI/CrUX ANSWERS is a "
                          "separate question and is reported per-call")


def check_schema(url: str) -> dict:
    r = http("https://validator.schema.org/validate?url=" + urllib.parse.quote(url, safe=""),
             method="POST", data=b"", timeout=90, retries=1)
    j = r.json()
    if not isinstance(j, dict) or "tripleGroups" not in j:
        return {"ok": False, "check": "schema", "url": url, "status": r.get("status"),
                "error": "schema validator did not return tripleGroups",
                "body_head": r.text()[:200]}

    # ⚠ The node shape is `typeGroup` (a string) + `types` (a list of
    # {pred,value}) - NOT a `type` key. Reading `node["type"]` returns None for
    # every node, so a page carrying perfect structured data reports ZERO types
    # and reads as "this page has no schema". Caught by running the parser
    # against nytimes.com, which certainly does. Nested objects live under
    # `nodeProperties`, so the walk has to recurse or it under-counts.
    counts: dict[str, int] = {}
    errors: list[dict] = []

    def walk(node):
        tg = node.get("typeGroup") or ""
        for t in node.get("types", []) or []:
            name = t.get("value") or tg
            if name:
                counts[name] = counts.get(name, 0) + 1
        for err in node.get("errors", []) or []:
            errors.append({"type": tg, "property": None,
                           "error": (err.get("errorType") or str(err))[:140]})
        for prop in node.get("properties", []) or []:
            for err in prop.get("errors", []) or []:
                errors.append({"type": tg, "property": prop.get("pred"),
                               "error": (err.get("errorType") or str(err))[:140]})
        for prop in node.get("nodeProperties", []) or []:
            for err in prop.get("errors", []) or []:
                errors.append({"type": tg, "property": prop.get("pred"),
                               "error": (err.get("errorType") or str(err))[:140]})
            for child in prop.get("value", []) or []:
                if isinstance(child, dict):
                    walk(child)

    for group in j.get("tripleGroups", []):
        for node in group.get("nodes", []) or []:
            walk(node)

    return {
        "ok": True, "check": "schema", "url": url,
        "rendered": j.get("isRendered"),
        "types": counts, "type_count": sum(counts.values()),
        "objects": j.get("numObjects"),
        "errors": errors[:20],
        "error_count": j.get("totalNumErrors", len(errors)),
        "warning_count": j.get("totalNumWarnings"),
        "deprecated": schema_gate(counts),
        "deprecated_means": "Retired rich-result types are reported as INFO, never as a "
                            "failure: the markup is still valid schema.org and the "
                            "validator will never mention it. A dead rich result is a "
                            "reason not to ADD the type, and only sometimes a reason to "
                            "remove it - see `guidance` on each row.",
        "empty_means": "zero types is a REAL answer (the page has no structured data), "
                       "not a failed read - the call returned tripleGroups. Verify with a "
                       "page you KNOW has schema before reporting an absence.",
    }


def check_vitals(url: str, strategy="mobile") -> dict:
    tok, source = psi_token()
    if not tok:
        return {"ok": False, "check": "vitals", "url": url, "error": source,
                "how_to_fix": "Either export GOOGLE_API_KEY, or enable the PageSpeed "
                              "Insights API on the project that owns the GSC service "
                              "account (free, no card): "
                              "https://console.developers.google.com/apis/api/"
                              "pagespeedonline.googleapis.com/overview"}
    base = ("https://www.googleapis.com/pagespeedonline/v5/runPagespeed?"
            + urllib.parse.urlencode({"url": url, "strategy": strategy, "category": "performance"}))
    if tok.startswith("key:"):
        r = http(base + "&key=" + tok[4:], timeout=180)
    else:
        r = http(base, headers={"Authorization": f"Bearer {tok}"}, timeout=180)
    j = r.json() or {}
    if not r.ok:
        msg = str((j.get("error") or {}).get("message", ""))[:300]
        return {"ok": False, "check": "vitals", "url": url, "status": r.get("status"),
                "credential": source, "error": msg,
                "how_to_fix": ("enable the PageSpeed Insights API on the project named in the "
                               "error above - it is free and needs no card"
                               if "has not been used in project" in msg else
                               "check the credential")}
    lr = j.get("lighthouseResult") or {}
    audits = lr.get("audits") or {}
    loading = (j.get("loadingExperience") or {}).get("metrics") or {}

    def lab(name):
        a = audits.get(name) or {}
        return {"value": a.get("numericValue"), "display": a.get("displayValue")}

    def field(name):
        m = loading.get(name) or {}
        return {"p75": m.get("percentile"), "category": m.get("category")}

    return {
        "ok": True, "check": "vitals", "url": url, "strategy": strategy, "credential": source,
        "performance_score": ((lr.get("categories") or {}).get("performance") or {}).get("score"),
        "lab": {"lcp": lab("largest-contentful-paint"), "cls": lab("cumulative-layout-shift"),
                "tbt": lab("total-blocking-time"), "fcp": lab("first-contentful-paint"),
                "speed_index": lab("speed-index")},
        "field_crux": {"lcp": field("LARGEST_CONTENTFUL_PAINT_MS"),
                       "cls": field("CUMULATIVE_LAYOUT_SHIFT_SCORE"),
                       "inp": field("INTERACTION_TO_NEXT_PAINT")},
        "note": "field_crux is REAL-USER data and is the ranking-relevant one; lab is a "
                "single synthetic run. Empty field data means the URL has too little "
                "traffic for CrUX - that is not a score of zero.",
    }


# ------------------------------------------------------------------- history


def _cdx(url: str, *, limit=0, collapse="digest", extra=None) -> tuple[list, dict]:
    params = {"url": url, "output": "json", "fl": "timestamp,original,digest,statuscode,length"}
    if collapse:
        params["collapse"] = collapse
    if limit:
        params["limit"] = str(limit)
    if extra:
        params.update(extra)
    r = http("https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params),
             timeout=90, retries=1, backoff=5)
    j = r.json()
    meta = {"status": r.get("status"), "ms": r.get("ms")}
    if not isinstance(j, list):
        return [], {**meta, "error": "CDX did not return a row list", "head": r.text()[:160]}
    if not j:
        return [], meta
    header, rows = j[0], j[1:]
    return [dict(zip(header, row)) for row in rows], meta


def check_history(url: str, since: str | None = None, limit: int = 20000) -> dict:
    """Capture history, with re-crawls of unchanged content filtered out.

    `collapse=digest` is what makes this usable: the CDX index drops captures
    whose content hash matches the one before, so the rows are the moments the
    page's bytes differed - not the 4,000 times a bot re-fetched it unchanged.

    ⚠ Measured caveat, and it matters before quoting the number at anyone:
    collapse only merges ADJACENT identical digests. A page that alternates
    between two variants (an A/B test, a rotating ad slot, a timestamp in the
    footer) produces a new row every single capture, so the count reflects
    "byte-level variation" and not "editorial rewrites". example.com comes back
    with 63,263 rows across 24 years for exactly this reason. Use it to COMPARE
    pages and to locate a change near a date - never as a literal edit count.
    """
    # ⚠ A positive `limit` returns the OLDEST N rows. On a long-lived URL that
    # silently answers every recency question with the year 2003: example.com
    # capped at 20,000 reported `last_capture: 2022` and "0 versions since
    # 2026-01-01" while the page was in fact captured this morning. CDX takes a
    # NEGATIVE limit for "the most recent N", which is the window we actually
    # want; the first capture is then one extra cheap call.
    changes, meta = _cdx(url, collapse="digest", limit=-abs(limit))
    if meta.get("error"):
        return {"ok": False, "check": "history", "url": url, **meta}

    # Control: this endpoint must answer 200 with ZERO rows for a URL that was
    # never archived. Without that, "never archived" and "CDX is down" are the
    # same observation - and this skill does not allow those to share a path.
    ctrl_rows, ctrl_meta = _cdx("zqxjkvwprtl-nonexistent-xyz.example/never", collapse=None, limit=1)
    control_ok = ctrl_meta.get("status") == 200 and not ctrl_rows
    if not control_ok:
        return {"ok": False, "check": "history", "url": url,
                "error": "control failed - cannot distinguish 'never archived' from 'CDX unavailable'",
                "control": ctrl_meta, "rows_seen": len(changes)}

    if not changes:
        return {"ok": True, "check": "history", "url": url, "archived": False,
                "distinct_versions": 0, "control_ok": True,
                "empty_means": "genuinely never archived (control passed), not a failed read"}

    def ts(row):
        return row.get("timestamp", "")

    truncated = len(changes) >= abs(limit)
    oldest, meta_first = _cdx(url, collapse=None, limit=1)
    first_capture = ts(oldest[0]) if oldest else ts(changes[0])
    last = changes[-1]
    window_start = ts(changes[0])
    recent = [c for c in changes if since is None or ts(c) >= since.replace("-", "")]
    return {
        "ok": True, "check": "history", "url": url, "archived": True, "control_ok": True,
        "distinct_versions_in_window": len(changes),
        "truncated": truncated,
        "window_start": window_start,
        "first_capture": first_capture, "last_capture": ts(last),
        "versions_since": ({"since": since, "count": len(recent),
                            "reliable": not truncated or since.replace("-", "") >= window_start}
                           if since else None),
        "recent_versions": [{"timestamp": ts(c), "status": c.get("statuscode"),
                             "bytes": c.get("length")} for c in changes[-10:]],
        "note": "counts byte-level variants inside the most-recent window, NOT editorial "
                "rewrites, and they also depend on how often the archive happened to visit "
                "- a low number can mean a stable page OR a rarely crawled one. When "
                "truncated is true the window starts at window_start, so a versions_since "
                "older than that is NOT reliable and says so. Compare pages against each "
                "other and use recent_versions to locate a change near a date; never quote "
                "the raw count as 'times updated'.",
    }


# ---------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("html", "schema", "vitals", "history", "all"):
        s = sub.add_parser(name)
        s.add_argument("url")
        if name in ("vitals", "all"):
            s.add_argument("--strategy", default="mobile", choices=["mobile", "desktop"])
        if name in ("history", "all"):
            s.add_argument("--since", help="YYYY-MM-DD - count versions after this date")
            s.add_argument("--limit", type=int, default=20000, help="max CDX rows")
    sub.add_parser("control", help="prove the schema gate discriminates (no network)")
    a = p.parse_args()

    if a.cmd == "control":
        out = run_control()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out.get("ok") else 1
    if a.cmd == "html":
        out = check_html(a.url)
    elif a.cmd == "schema":
        out = check_schema(a.url)
    elif a.cmd == "vitals":
        out = check_vitals(a.url, a.strategy)
    elif a.cmd == "history":
        out = check_history(a.url, a.since, a.limit)
    else:
        out = {"ok": True, "check": "all", "url": a.url,
               "html": check_html(a.url),
               "schema": check_schema(a.url),
               "history": check_history(a.url, a.since, a.limit),
               "vitals": check_vitals(a.url, a.strategy)}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
