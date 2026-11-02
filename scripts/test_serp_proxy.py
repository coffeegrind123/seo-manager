#!/usr/bin/env python3
"""Tests for serp.Proxy's session selectors and serpd's "already selectored?" check.

Offline: no proxy is contacted. Expected URLs are written by hand from the two selector
shapes residential providers use (measured against real accounts):

    password style  <pass>[_country-<cc>]_session-<id>_lifetime-<min>
    username style  <user>[-country-<cc>]-lifetime-<min>-session-<id>

The username style is chosen with SEO_PROXY_SELECTORS=username (or the ~/.seo-proxy key of
the same name); anything else keeps the password style, so existing setups are unchanged.

    python3 test_serp_proxy.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# The operator's real ~/.seo-proxy must not steer these tests: point HOME at an empty dir.
_HOME = tempfile.mkdtemp(prefix="seo-proxy-test-")
os.environ["HOME"] = _HOME

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serp  # noqa: E402
import serpd  # noqa: E402

failures: list[str] = []


def check(label: str, cond, detail="") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        failures.append(label)


def pinned(p: serp.Proxy, token: str = "abc123") -> serp.Proxy:
    p.session = token
    return p


BASE = "http://user:pass@proxy.example.invalid:8080"
STICKY = "http://user:p%40ss@proxy.example.invalid:10000"

print("password style (default)")
os.environ.pop("SEO_PROXY_SELECTORS", None)
check("no country", pinned(serp.Proxy(BASE)).url()
      == "http://user:pass_session-abc123_lifetime-10@proxy.example.invalid:8080")
check("with country", pinned(serp.Proxy(BASE, country="de")).url()
      == "http://user:pass_country-de_session-abc123_lifetime-10@proxy.example.invalid:8080")
check("explicit style argument wins over the env",
      pinned(serp.Proxy(BASE, style="password")).url().endswith("pass_session-abc123_lifetime-10@proxy.example.invalid:8080"))

print("username style")
check("no country", pinned(serp.Proxy(BASE, style="username")).url()
      == "http://user-lifetime-10-session-abc123:pass@proxy.example.invalid:8080")
check("with country, password and its encoding untouched", pinned(serp.Proxy(STICKY, country="de", style="username")).url()
      == "http://user-country-de-lifetime-10-session-abc123:p%40ss@proxy.example.invalid:10000")
os.environ["SEO_PROXY_SELECTORS"] = "username"
check("chosen by SEO_PROXY_SELECTORS", pinned(serp.Proxy(BASE, country="nl")).url()
      == "http://user-country-nl-lifetime-10-session-abc123:pass@proxy.example.invalid:8080")
os.environ["SEO_PROXY_SELECTORS"] = "USERNAME "
check("case and whitespace tolerated", serp.Proxy(BASE).style == "username")
os.environ["SEO_PROXY_SELECTORS"] = "bogus"
check("an unknown value keeps the password style", serp.Proxy(BASE).style == "password")
os.environ.pop("SEO_PROXY_SELECTORS", None)
Path(_HOME, ".seo-proxy").write_text('SEO_PROXY_URL="http://u:p@x.invalid:1"\nSEO_PROXY_SELECTORS=username\n')
check("read from ~/.seo-proxy when the env is unset", serp.Proxy(BASE).style == "username")
os.environ["SEO_PROXY_SELECTORS"] = "password"
check("the env outranks ~/.seo-proxy", serp.Proxy(BASE).style == "password")
os.environ.pop("SEO_PROXY_SELECTORS", None)
Path(_HOME, ".seo-proxy").unlink()

print("rotate / label")
p = serp.Proxy(BASE, country="de", style="username")
first = p.url()
p.rotate()
check("rotate changes the session in the username", p.url() != first and "-session-" in p.url().split("@")[0])
check("label never carries credentials", "pass" not in p.label() and "user" not in p.label(), p.label())

print("serpd: is a URL already selectored?")
check("bare URL", not serpd.has_session_selector(BASE))
check("password-style selector", serpd.has_session_selector(pinned(serp.Proxy(BASE)).url()))
check("username-style selector", serpd.has_session_selector(pinned(serp.Proxy(BASE, style="username")).url()))
check("'session' in the HOST is not a selector",
      not serpd.has_session_selector("http://user:pass@session-1.proxy.example.invalid:8080"))
check("empty", not serpd.has_session_selector(""))

print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
