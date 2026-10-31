#!/usr/bin/env python3
"""Regression tests for providers.http as a transport, against a local server.

The bugs these were written against (2026-10-25):
  - no body cap and no decompression cap: 194 KB of gzip on the wire became a
    200 MB body and 401 MB RSS, through the one function every third-party
    fetch in this skill uses (competitors, geo, backlinks, sitecheck);
  - an error response (403, 429, 202-challenge's cousins) came back with no
    headers at all, so `cf-mitigated: challenge` or `x-amzn-waf-action` was
    unreadable and every challenge looked like a plain refusal.

    python3 test_http.py
"""
from __future__ import annotations

import gzip
import http.server
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import providers  # noqa: E402

FAILS: list[str] = []
MIB = 1024 * 1024


def check(name: str, cond: bool, detail="") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' ' + str(detail)[:200]}")
    if not cond:
        FAILS.append(name)


BOMB = gzip.compress(b"\0" * (8 * MIB), 9)
SMALL = gzip.compress(b"hello world", 9)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/bomb":
            self._send(200, BOMB, {"Content-Encoding": "gzip"})
        elif self.path == "/big":
            self._send(200, b"x" * (2 * MIB), {})
        elif self.path == "/small":
            self._send(200, SMALL, {"Content-Encoding": "gzip"})
        elif self.path == "/challenge":
            self._send(403, b"<html>Just a moment...</html>", {"cf-mitigated": "challenge"})
        elif self.path == "/gzerr":
            self._send(503, gzip.compress(b"enable the API"), {"Content-Encoding": "gzip"})

    def _send(self, code, body, hdrs):
        self.send_response(code)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

print("body and decompression caps")
r = providers.http(BASE + "/bomb", max_bytes=MIB)
check("a gzip bomb over the cap is refused, not inflated",
      r.get("status") is None and "max_bytes" in (r.get("error") or "")
      and len(r.get("body") or b"") <= MIB, {k: r.get(k) for k in ("status", "error")})
r = providers.http(BASE + "/big", max_bytes=MIB)
check("a plain body over the cap is refused",
      r.get("status") is None and "max_bytes" in (r.get("error") or ""), {k: r.get(k) for k in ("status", "error")})
r = providers.http(BASE + "/small", max_bytes=MIB)
check("CONTROL: a small gzip body under the cap is decoded intact",
      r.get("status") == 200 and r.get("body") == b"hello world", r)
r = providers.http(BASE + "/bomb")
check("the default cap is big enough for an 8 MiB body (a sitemap may be 50 MB)",
      r.get("status") == 200 and len(r.get("body") or b"") == 8 * MIB, {k: r.get(k) for k in ("status", "error")})

print("\nerror responses keep their headers and decoded body")
r = providers.http(BASE + "/challenge", follow=False)
check("a 403 carries its headers", (r.get("headers") or {}).get("cf-mitigated") == "challenge", r.get("headers"))
r = providers.http(BASE + "/gzerr", retry_on=())
check("CONTROL: a gzipped error body is still decoded", r.get("body") == b"enable the API", r.get("body"))

srv.shutdown()
print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all http tests passed")
