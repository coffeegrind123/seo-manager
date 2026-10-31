#!/usr/bin/env python3
"""Regression tests for the Search Console client.

Every case is either a bug this file hit while being written, or a distinction
that makes the API return confident nonsense. Nothing here touches the network.

    python3 test_gsc.py
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gsc  # noqa: E402

FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:160]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    print("\nRS256, in stdlib - the signer Google never tells you is broken:")
    der, n, e, d = gsc._test_pkcs8()
    pem = "-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + \
          "\n-----END PRIVATE KEY-----"
    pn, pe, pd = gsc.rsa_key_from_pem(pem)
    check("pkcs8 round-trips through the parser", (pn, pe, pd) == (n, e, d))
    sig = gsc.rs256(b"payload", n, d)
    check("a signature verifies against its own public key",
          gsc.rs256_verify(b"payload", sig, n, e))
    check("CONTROL a signature over other bytes does not verify",
          not gsc.rs256_verify(b"payload2", sig, n, e),
          "a verifier that always returns True proves nothing")
    check("the signed block is exactly one modulus wide",
          len(sig) == (n.bit_length() + 7) // 8,
          "a short signature is silently left-padded by the server and rejected "
          "as invalid_grant, which reads as a revoked key")
    # A PKCS#1 body inside a PKCS#8 header is the shape that yields the version
    # integer as the modulus. Feeding the parser a non-key must raise, not
    # return a plausible tuple.
    try:
        gsc.rsa_key_from_pem("-----BEGIN PRIVATE KEY-----\n"
                             + base64.b64encode(b"\x30\x03\x02\x01\x00").decode()
                             + "\n-----END PRIVATE KEY-----")
        raised = False
    except Exception:
        raised = True
    check("a truncated key raises rather than returning a tiny modulus", raised)

    print("\nGSC returns counts as STRINGS:")
    check("a string count is coerced", gsc._int("5388") == 5388)
    check("a missing count is None, never 0", gsc._int(None) is None,
          "0 would report an empty sitemap")
    check("a non-numeric count is None", gsc._int("many") is None)
    check("CONTROL the raw comparison really does invert", "9" > "5388",
          "if this were false the coercion would be cargo cult")

    print("\nThe 2-3 day processing lag - an empty window is not zero traffic:")
    today = date.today()
    check("a window ending today is flagged", gsc.lag_note(str(today)) is not None)
    check("a window ending inside the lag is flagged",
          gsc.lag_note(str(today - timedelta(days=1))) is not None)
    check("CONTROL an old window is not flagged",
          gsc.lag_note(str(today - timedelta(days=30))) is None,
          "flagging everything carries no information")
    check("the edge day itself is clean",
          gsc.lag_note(str(today - timedelta(days=gsc.DATA_LAG_DAYS))) is None)
    check("an unparseable date is None rather than a false all-clear",
          gsc.lag_note("not-a-date") is None)

    print("\nProperty identity - two properties, different data:")
    check("a domain property encodes its colon",
          gsc.enc("sc-domain:x.test") == "sc-domain%3Ax.test",
          "an unencoded colon addresses a different resource")
    check("a URL property is not the same string",
          gsc.enc("sc-domain:x.test") != gsc.enc("https://x.test/"))
    check("a sitemap URL is fully encoded for the path segment",
          gsc.enc("https://x.test/sitemap.xml")
          == "https%3A%2F%2Fx.test%2Fsitemap.xml")

    print("\nThe CLI refuses rather than inventing:")
    p = subprocess.run([sys.executable, str(HERE / "gsc.py"), "control"],
                       capture_output=True, text=True, timeout=180)
    try:
        doc = json.loads(p.stdout)
        check("control emits JSON and passes", doc.get("ok") is True, p.stdout[:200])
    except json.JSONDecodeError:
        check("control emits JSON and passes", False, p.stdout[:200])

    # A query whose whole window is inside the lag must refuse, not return [].
    p = subprocess.run([sys.executable, str(HERE / "gsc.py"),
                        "--property", "sc-domain:example.test", "query",
                        "--start", str(today), "--end", str(today)],
                       capture_output=True, text=True, timeout=180)
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError:
        doc = {}
    check("a fully-lagged window is refused, not answered with zero rows",
          doc.get("state") == "cannot_ask" and p.returncode != 0,
          f"rc={p.returncode} {str(doc)[:160]}")
    check("and the refusal names the last complete day",
          bool(doc.get("last_complete_day")),
          "without it the operator cannot tell how far back to ask")

    # sitemap-submit must never fire without --yes.
    p = subprocess.run([sys.executable, str(HERE / "gsc.py"),
                        "--property", "sc-domain:example.test", "sitemap-submit",
                        "--sitemap", "https://example.test/sitemap.xml"],
                       capture_output=True, text=True, timeout=180)
    try:
        doc = json.loads(p.stdout)
    except json.JSONDecodeError:
        doc = {}
    check("sitemap-submit is a dry run without --yes", doc.get("state") == "dry_run",
          str(doc)[:160])
    check("and the dry run shows the exact call it would make",
          "PUT " in str(doc.get("would_call", "")))
    check("a relative sitemap path is refused",
          json.loads(subprocess.run(
              [sys.executable, str(HERE / "gsc.py"), "--property", "sc-domain:x.test",
               "sitemap-submit", "--sitemap", "/sitemap.xml"],
              capture_output=True, text=True, timeout=180).stdout).get("state")
          == "cannot_ask")

    test_fifth_pass()
    print()
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all gsc tests passed")
    return 0


def _run_cmd(fn, **kw):
    """Run a cmd_* with stdout captured; returns (payload, exit code)."""
    import argparse
    import contextlib
    import io
    a = argparse.Namespace(property="sc-domain:x.test", root=None, **kw)
    buf = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf):
        try:
            fn(a)
        except SystemExit as e:
            code = e.code or 0
    return json.loads(buf.getvalue()), code


def test_fifth_pass() -> None:
    """2026-10-25, each checked on developers.google.com/webmaster-tools that day:
    the API states its own data cut-off (metadata.first_incomplete_date with
    dataState=all + a date dimension), filters are AND-only, aggregationType is
    auto/byPage/byProperty/byNewsShowcasePanel, and URL Inspection allows 2,000
    calls per site per day and 600 per minute."""
    print("\nthe data cut-off comes from the API, not from a fixed 3-day guess:")
    sent = []

    def fake(url, payload=None, method=None, timeout=90):
        sent.append({"url": url, "payload": payload, "method": method})
        if payload and payload.get("dataState") == "all":
            # The LIVE shape (2026-10-25): camelCase, not the reference's snake_case.
            return {"rows": [{"keys": ["2026-10-20"]}], "metadata": {"firstIncompleteDate": "2026-10-23"}}, None
        return {"rows": []}, None
    saved = gsc.call
    gsc.call = fake
    try:
        edge = gsc.data_edge("sc-domain:x.test")
        check("first_incomplete_date - 1 is the last complete day",
              edge == {"last_complete_day": "2026-10-22", "source": "api",
                       "first_incomplete_date": "2026-10-23"}, edge)
        check("the probe asked with dataState=all and a date dimension",
              sent[0]["payload"]["dataState"] == "all" and sent[0]["payload"]["dimensions"] == ["date"],
              sent[0]["payload"])
        gsc.call = lambda *a_, **k: (None, {"http": 429, "reason": "quota"})
        edge = gsc.data_edge("sc-domain:x.test")
        check("CONTROL: an unreadable cut-off falls back, and SAYS it is assumed",
              edge["source"] == "assumed" and edge["last_complete_day"]
              == str(date.today() - timedelta(days=gsc.DATA_LAG_DAYS)), edge)

        print("\nfilters and aggregation reach the API in its own shape:")
        f, err = gsc.parse_filters(["query:contains:vpn", "country:equals:deu"])
        check("--filter dim:op:expr becomes one AND group",
              err is None and f == [{"groupType": "and", "filters": [
                  {"dimension": "query", "operator": "contains", "expression": "vpn"},
                  {"dimension": "country", "operator": "equals", "expression": "deu"}]}], (f, err))
        check("an expression may itself contain colons",
              gsc.parse_filters(["page:includingRegex:^https://x.test/a"])[0][0]["filters"][0]["expression"]
              == "^https://x.test/a")
        check("an unknown operator is refused, not sent",
              gsc.parse_filters(["query:startsWith:a"])[1] is not None)
        check("an unknown dimension is refused, not sent", gsc.parse_filters(["date:equals:x"])[1] is not None)
        sent.clear()
        gsc.call = fake
        start = str(date.today() - timedelta(days=40))
        end = str(date.today() - timedelta(days=10))
        payload, code = _run_cmd(gsc.cmd_query, start=start, end=end, dimensions="query", limit=100,
                                 search_type="web", filter=["query:contains:vpn"], aggregation="byPage",
                                 fresh=False)
        body = [x["payload"] for x in sent if x["payload"] and x["payload"].get("dimensions") == ["query"]][0]
        check("cmd_query sends the filter group and the aggregation",
              body.get("dimensionFilterGroups", [{}])[0].get("filters", [{}])[0].get("expression") == "vpn"
              and body.get("aggregationType") == "byPage", body)
        check("cmd_query reports where its cut-off came from", payload.get("data_edge", {}).get("source") == "api",
              payload.get("data_edge"))

        print("\nURL Inspection is refused BEFORE the quota, not discovered after it:")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            q = gsc.InspectQuota(Path(td) / "q.json", per_day=3, per_minute=100)
            allowed, refused = q.take("sc-domain:x.test", ["u1", "u2"])
            check("within the daily quota everything is allowed", allowed == ["u1", "u2"] and refused == [])
            allowed, refused = q.take("sc-domain:x.test", ["u3", "u4"])
            check("past it, the rest is refused by name", allowed == ["u3"] and refused == ["u4"], (allowed, refused))
            q2 = gsc.InspectQuota(Path(td) / "q.json", per_day=3, per_minute=100)
            check("the count survives a new process", q2.take("sc-domain:x.test", ["u5"])[1] == ["u5"])
            check("CONTROL: another property has its own quota",
                  q2.take("https://y.test/", ["u6"])[0] == ["u6"])

        print("\nsitemap-delete is a dry run until --yes, and reads back:")
        sent.clear()
        payload, code = _run_cmd(gsc.cmd_sitemap_delete, sitemap="https://x.test/old.xml", yes=False)
        check("without --yes nothing is deleted", payload.get("state") == "dry_run"
              and not any(x["method"] == "DELETE" for x in sent), payload)
        held = {"n": 1}

        def fake_del(url, payload=None, method=None, timeout=90):
            sent.append({"url": url, "payload": payload, "method": method})
            if method == "DELETE":
                held["n"] = 0
                return {}, None
            if held["n"]:
                return {"path": "https://x.test/old.xml"}, None
            return None, {"http": 404, "reason": "gone"}
        gsc.call = fake_del
        payload, code = _run_cmd(gsc.cmd_sitemap_delete, sitemap="https://x.test/old.xml", yes=True)
        check("with --yes it deletes and confirms the feed is gone",
              payload.get("deleted") is True and payload.get("confirmed_absent") is True, payload)
    finally:
        gsc.call = saved


if __name__ == "__main__":
    sys.exit(main())
