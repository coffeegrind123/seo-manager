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

    print()
    if FAILURES:
        print("FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all gsc tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
