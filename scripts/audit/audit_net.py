#!/usr/bin/env python3
"""
audit_net.py - the audit scripts' one way to fetch a URL, stdlib only.

The audit scripts came from seo-audit / seo-audit-full (JeffLi1993's
seo-audit-skill), which used `requests`. seo-manager does not install
anything, so their fetch helpers are rebuilt here on seo-manager's own
providers.http, keeping the shapes the scripts already consumed.

Redirects are followed HOP BY HOP, not by urllib: the audit reports the
chain, and every hop is re-checked against private addresses. The original
checked only the first host, and requests then followed a redirect to
127.0.0.1 or 10.x without looking.

    get(url, timeout, follow=True)  -> Response

Library only; no CLI.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from providers import http  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36 seo-manager-audit/1.0")
HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.5"}
MAX_REDIRECTS = 5
REDIRECT = (301, 302, 303, 307, 308)


@dataclass
class Response:
    status_code: int | None
    text: str | None
    url: str
    headers: dict = field(default_factory=dict)
    history: list = field(default_factory=list)   # [{"url", "status_code"}] per hop
    location: str | None = None
    error: str | None = None


def blocked_address(url: str) -> str | None:
    """Why this URL may not be fetched (it resolves to a private address), or
    None. AUDIT_ALLOW_PRIVATE=1 lifts it for local test servers only."""
    if os.environ.get("AUDIT_ALLOW_PRIVATE") == "1":
        return None
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError, UnicodeError):
        return None      # unresolvable: the fetch itself reports it
    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
        return f"Blocked: resolves to private IP ({ip})"
    return None


def _error(err: str | None, timeout: int) -> str:
    e = err or "request failed"
    if "timed out" in e.lower() or "TimeoutError" in e:
        return f"Timed out after {timeout}s"
    if "SSL" in e or "CERTIFICATE" in e:
        return f"SSL error: {e}"
    if "URLError" in e or "ConnectionError" in e or "Errno" in e:
        return f"Connection error: {e}"
    return f"Request failed: {e}"


def get(url: str, timeout: int = 20, follow: bool = True, headers: dict | None = None,
        params=None, max_redirects: int = MAX_REDIRECTS) -> Response:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = dict(HEADERS)
    ua = UA
    for k, v in (headers or {}).items():
        if k.lower() == "user-agent":
            ua = v
        else:
            h[k] = v
    history, cur = [], url
    for _ in range(max_redirects + 1):
        why = blocked_address(cur)
        if why:
            return Response(None, None, cur, history=history, error=why)
        r = http(cur, timeout=timeout, ua=ua, headers=h, follow=False)
        st = r.get("status")
        if st is None:
            return Response(None, None, cur, history=history, error=_error(r.get("error"), timeout))
        hdrs = r.get("headers") or {}
        loc = r.get("location") or hdrs.get("location")
        if follow and st in REDIRECT and loc:
            history.append({"url": cur, "status_code": st})
            cur = urllib.parse.urljoin(cur, loc)
            continue
        return Response(st, r.text(), cur, headers=hdrs, history=history,
                        location=urllib.parse.urljoin(cur, loc) if loc else None)
    return Response(None, None, cur, history=history,
                    error=f"Too many redirects (max {max_redirects})")
