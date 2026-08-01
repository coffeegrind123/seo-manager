#!/usr/bin/env python3
"""Regression tests for the free-provider integrations.

These exist because every failure they cover is SILENT. A domain the link
graph has never seen, scored as DR 0, does not raise anything - it just
quietly widens the KD ceiling and re-scopes every keyword decision downstream.
Same shape as the SERP relevance guard in test_guards.py: the dangerous
failure is the one that looks like a successful measurement.

Run: python3 test_providers.py     (no network, no keys)
"""

from __future__ import annotations

import json
import sys

import authority
import trendfeeds

FAILURES = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name} - got {got!r}")
    if not ok:
        FAILURES.append(f"{name}: got {got!r}, wanted {want!r}")


def main():
    print("open pagerank: absence must never become a zero")

    # The exact row the live API returns for a domain it has never seen.
    # Response SHAPES exactly as the live API returns them; the domain names are
    # neutral stand-ins.
    absent = [{"domain": "young-site.example", "found": False, "open_page_rank": None,
               "rank": None, "referring_domains": None, "history": None}]
    present = [{"domain": "known-site.example", "found": True, "open_page_rank": 3.44,
                "rank": 785188, "referring_domains": 58, "history": None}]

    orig_bulk, orig_secret = authority.openpagerank_bulk, authority.read_secret
    authority.read_secret = lambda *a, **k: "opr_live_test"
    try:
        authority.openpagerank_bulk = lambda d, key=None: absent
        got = authority.from_openpagerank("young-site.example")
        check("found=false returns an error, not a result", bool(got.get("error")), True)
        check("found=false never yields dr", got.get("dr"), None)

        authority.openpagerank_bulk = lambda d, key=None: present
        got = authority.from_openpagerank("known-site.example")
        check("a real score becomes a DR", got.get("dr"), 34)
        check("referring domains are carried through", got.get("referring_domains"), 58)
    finally:
        authority.openpagerank_bulk, authority.read_secret = orig_bulk, orig_secret

    # A missing key must be "no verdict" (None -> next rung), never a zero.
    authority.read_secret = lambda *a, **k: ""
    try:
        check("no key at all is a skip, not a score", authority.from_openpagerank("x.com"), None)
    finally:
        authority.read_secret = orig_secret

    print("\nopen pagerank: the response is NOT positionally aligned with the request")
    # Measured: httpbin.org sent alone returns an EMPTY results array - omitted,
    # not found:false. Zipping request to response by index therefore credits
    # one domain with another's authority, and the number looks plausible.
    got = authority._reconcile(["a.com", "b.com", "c.com"], [{"domain": "b.com", "found": True,
                                                             "open_page_rank": 5.0}])
    check("one row per requested domain, in order", [r["domain"] for r in got], ["a.com", "b.com", "c.com"])
    check("an omitted domain is marked no_data", got[0].get("no_data"), True)
    check("...and is never a measured zero", got[0].get("open_page_rank"), None)
    check("the row that DID answer keeps its score", got[1].get("open_page_rank"), 5.0)

    # Measured: search.marginalia.nu returns a row for marginalia.nu. Taken at
    # face value that credits a subdomain with its parent's authority.
    got = authority._reconcile(["blog.example.com"], [{"domain": "example.com", "found": True,
                                                      "open_page_rank": 9.0}])
    check("a subdomain folded to its apex says so", got[0].get("answered_for"), "example.com")
    check("...and remembers what was asked", got[0].get("requested"), "blog.example.com")

    print("\nopen pagerank: the 0-10 scale maps onto the 0-100 DR scale")
    check("10.0 -> 100", authority._opr_dr(10.0), 100)
    check("3.44 -> 34", authority._opr_dr(3.44), 34)
    check("None stays None", authority._opr_dr(None), None)
    check("garbage stays None", authority._opr_dr("n/a"), None)

    print("\nopen pagerank: 'not enough history' is not 'flat'")
    check("no history at all", authority._opr_trend(None), None)
    short = [{"date": f"2026-{m:02d}-01", "open_page_rank": 5.0} for m in range(1, 7)]
    check("under 13 months of history", authority._opr_trend(short), None)
    rising = [{"date": f"2025-{m:02d}-01", "open_page_rank": 5.0} for m in range(1, 13)]
    rising += [{"date": "2026-01-01", "open_page_rank": 5.6}]
    t = authority._opr_trend(rising)
    check("a real climb reads as rising", t and t["direction"], "rising")
    flat = [{"date": f"2025-{m:02d}-01", "open_page_rank": 5.0} for m in range(1, 14)]
    t = authority._opr_trend(flat)
    check("an unchanged series reads as flat", t and t["direction"], "flat")

    print("\nbing: an ErrorCode envelope arrives as HTTP 200 and must not read as data")
    import bing
    # Measured shapes. NotAuthorized is the control that makes an empty
    # backlink list trustworthy; if it ever starts returning empty instead,
    # every "we have no backlinks" verdict silently becomes unfounded.
    orig_call = bing.call
    try:
        bing.call = lambda m, k, **kw: {"ok": False, "error": "ERROR!!! NotAuthorized", "error_code": 14}
        got = bing.backlinks("k", "https://notours.com/", 10)
        check("an unowned site never reports zero backlinks", got.get("ok"), False)

        bing.call = lambda m, k, **kw: {"ok": True, "d": {"Links": [], "TotalPages": 0}}
        got = bing.backlinks("k", "https://ours.com/", 10)
        check("an owned-but-empty site says WHY it is empty", "empty_means" in got, True)
        check("...and is still a successful read", got.get("ok"), True)

        # A query Bing has never seen returns Query:null with zeroes. Reporting
        # that as bing_impressions=0 would be a measured zero that was never
        # measured.
        bing.call = lambda m, k, **kw: {"ok": True, "d": {"Query": None, "Impressions": 0, "BroadImpressions": 0}}
        got = bing.keyword("k", "zzbogus", "us", "en-US", "2026-05-01", "2026-08-01")
        check("an unknown query is null, not a measured zero", got.get("bing_impressions"), None)
        check("...and says so explicitly", got.get("known_to_bing"), False)

        bing.call = lambda m, k, **kw: {"ok": True, "d": {"Query": "cs", "Impressions": 749, "BroadImpressions": 1353}}
        got = bing.keyword("k", "cs", "us", "en-US", "2026-05-01", "2026-08-01")
        check("a real query keeps its count", got.get("bing_impressions"), 749)
        check("every keyword result carries the not-Google caveat", "not Google" in got.get("caveat", ""), True)
    finally:
        bing.call = orig_call

    print("\ntrend feeds: a refusal must never look like an empty niche")
    f = trendfeeds._fail("google-trends-rss", 429, "quota")
    check("a refusal is not ok", f["ok"], False)
    check("a refusal keeps its status code", f["status"], 429)
    check("a refusal says so in words", "REFUSED" in f["note"], True)

    # ------------------------------------------------------------------ tranco
    print("\ntranco: absent from the list is a measurement, not a rank of zero")
    orig_fetch = authority.fetch
    try:
        authority.fetch = lambda url, **kw: (200, json.dumps({"ranks": []}))
        got = authority.from_tranco("young-site.example")
        check("empty ranks -> in_list false", got.get("in_list"), False)
        check("...and rank stays None, never 0", got.get("rank"), None)
        check("...and it is not an error either", got.get("error"), None)

        # A refusal must NOT be reported as "not in the list".
        authority.fetch = lambda url, **kw: (503, "upstream down")
        got = authority.from_tranco("x.example")
        check("a 503 is an error, not an absence", bool(got.get("error")), True)
        check("...and never claims in_list", got.get("in_list"), None)

        # Direction must come from the oldest-vs-newest rank, sorted by DATE -
        # the API does not promise an order, and trusting the array order
        # inverts the trend for any domain returned newest-first.
        authority.fetch = lambda url, **kw: (200, json.dumps({"ranks": [
            {"date": "2026-07-31", "rank": 100}, {"date": "2026-06-22", "rank": 500}]}))
        got = authority.from_tranco("climbing.example")
        check("newest rank wins regardless of array order", got.get("rank"), 100)
        check("a climb from 500 to 100 reads as improving", got.get("direction"), "improving")
        check("popularity is flagged as NOT a DR", "NOT a DR" in got.get("means", ""), True)
    finally:
        authority.fetch = orig_fetch

    # ----------------------------------------------------- engine agreement
    print("\nkeyword expansion: a silent engine is not evidence of no demand")
    import keywords

    class A:  # argparse stand-in
        seed = ["widget"]; groups = ["question"]; tools = False; alphabet = False
        must_contain = None; hl = "en"; gl = "us"; source = "chrome"
        engines = ["google", "bing", "amazon"]; sort = "agreement"
        limit = 10; max_calls = 1; delay = 0

    # google + bing answer, amazon is dead. Agreement must be scored out of the
    # TWO that answered - scoring out of three would quietly mark every phrase
    # as weakly-corroborated because one endpoint was down.
    def fake_ac(q, hl="en", gl="us", source="chrome", engine=None):
        return {"google": ["widget guide"], "bing": ["widget guide"], "amazon": []}.get(engine, [])

    orig_ac = keywords.autocomplete
    captured = {}
    try:
        keywords.autocomplete = fake_ac
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            keywords.cmd_expand(A())
        captured = json.loads(buf.getvalue())
    finally:
        keywords.autocomplete = orig_ac
    check("a silent engine is excluded from the denominator",
          captured.get("engines_answering"), ["bing", "google"])
    check("...and is reported rather than hidden", captured.get("engines_silent"), ["amazon"])
    row = (captured.get("results") or [{}])[0]
    check("both live engines agreeing scores 2", row.get("engine_agreement"), 2)
    check("...which is 100% of the engines that answered", row.get("engine_agreement_pct"), 100)
    check("agreement is labelled ordinal, not a volume",
          "not a volume" in captured.get("engine_agreement_note", ""), True)

    # ------------------------------------------------------- schema parsing
    print("\npagecheck: the schema validator's node shape is typeGroup/types, not type")
    import pagecheck
    from providers import HttpResult
    # The exact shape the live validator returns (trimmed). Reading node["type"]
    # here yields None and reports a page full of schema as having none.
    payload = {"isRendered": True, "numObjects": 1, "totalNumErrors": 0, "totalNumWarnings": 0,
               "tripleGroups": [{"nodes": [{
                   "typeGroup": "WebSite",
                   "types": [{"pred": "itemtype", "value": "WebSite"}],
                   "properties": [{"pred": "name", "value": "X", "errors": []}],
                   "nodeProperties": [{"pred": "publisher", "value": [
                       {"typeGroup": "Organization",
                        "types": [{"pred": "itemtype", "value": "Organization"}],
                        "properties": [], "nodeProperties": []}]}]}]}]}
    orig_http = pagecheck.http
    try:
        pagecheck.http = lambda *a, **k: HttpResult(status=200, body=json.dumps(payload).encode())
        got = pagecheck.check_schema("https://x.example/")
        check("the top-level type is found", got["types"].get("WebSite"), 1)
        check("a NESTED type is found too", got["types"].get("Organization"), 1)
        check("...so the count is not zero", got.get("type_count"), 2)

        pagecheck.http = lambda *a, **k: HttpResult(status=200, body=json.dumps(
            {"isRendered": True, "numObjects": 0, "tripleGroups": []}).encode())
        got = pagecheck.check_schema("https://bare.example/")
        check("a page with no schema is a successful read", got.get("ok"), True)
        check("...reporting zero types", got.get("type_count"), 0)
        check("...and saying an absence needs a positive control",
              "KNOW has schema" in got.get("empty_means", ""), True)

        pagecheck.http = lambda *a, **k: HttpResult(status=500, body=b"nope")
        got = pagecheck.check_schema("https://down.example/")
        check("a 500 is NOT zero structured data", got.get("ok"), False)
    finally:
        pagecheck.http = orig_http

    # ------------------------------------------------- gzipped error bodies
    print("\nproviders: an error body is gzipped too, and its MESSAGE is the useful part")
    import gzip as _gz
    import providers as P
    msg = {"error": {"message": "PageSpeed Insights API has not been used in project 1 before"}}
    fake = HttpResult(status=403, body=_gz.decompress(_gz.compress(json.dumps(msg).encode())))
    check("a decompressed error body still parses", fake.json()["error"]["message"][:9], "PageSpeed")
    check("the XSSI guard is stripped before parsing",
          HttpResult(status=200, body=b")]}'\n{\"a\":1}").json(), {"a": 1})

    # ------------------------------------------------------ control failure
    print("\nproviders: a probe whose CONTROL fails is unusable, not merely quiet")
    row = P._run_probe(("fake", "test", "free", False, lambda: (True, "200 n=5", False), "x"))
    check("main call ok + control failed -> control_failed", row["state"], "control_failed")
    row = P._run_probe(("fake", "test", "free", False, lambda: (True, "200 n=5", True), "x"))
    check("both ok -> usable", row["state"], "usable")
    row = P._run_probe(("fake", "test", "free", False, lambda: (None, "no key", None), "x"))
    check("no credential -> unconfigured, never failing", row["state"], "unconfigured")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("all provider tests passed")


if __name__ == "__main__":
    main()
