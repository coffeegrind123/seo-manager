#!/usr/bin/env python3
"""Regression tests for headterms.py - the head-term ownership gate.

Expectations are written from the RULE (an owned phrase in a non-owner's title
or H1 is a collision; body copy and anchors are not), never copied from output.

    python3 test_headterms.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headterms as H  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def run(*argv, cwd=None):
    p = subprocess.run([sys.executable, str(HERE / "headterms.py"), *argv],
                       capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout)


print("matching")
check("contiguous token match", H.carries("Buy the Best Hiking Boots now", "best hiking boots"))
check("digits split on the dot both sides", H.carries("Model X 1.6 Review", "x 1.6"))
check("a longer word does not carry a shorter one", not H.carries("hikers' boots", "hike"))
check("empty phrase carries nothing", not H.carries("anything", "  "))

print("\nthe control")
code, out = run("control")
check("control passes", out.get("ok") is True and code == 0, out.get("failed"))

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / ".seo").mkdir()
    site = root / "site"
    (site / "guides").mkdir(parents=True)
    (site / "index.html").write_text('<title>Best Hiking Boots 2026</title><h1>Best hiking boots</h1>')
    (site / "guides" / "fit.html").write_text('<title>How boots should fit</title><h1>Best Hiking Boots fit guide</h1>')
    (site / "guides" / "care.html").write_text('<title>Boot care</title><h1>Care</h1><p><a href="/">best hiking boots</a></p>')

    print("\nrefusals")
    code, out = run("scan", "--root", str(site), cwd=d)
    check("missing map refuses (not 'no collisions')", out.get("control_failed") is True, out)
    (root / ".seo" / "headterms.json").write_text(json.dumps({"terms": []}))
    code, out = run("scan", "--root", str(site), cwd=d)
    check("empty map refuses", out.get("control_failed") is True, out)

    (root / ".seo" / "headterms.json").write_text(json.dumps(
        {"terms": [{"phrase": "best hiking boots", "owner": "/"}]}))
    code, out = run("scan", "--root", str(root / "nope"), cwd=d)
    check("a root with no pages refuses", out.get("control_failed") is True, out)

    print("\nscan")
    code, out = run("scan", "--root", str(site), "--fail-on-collision", cwd=d)
    check("the H1 intruder is the only collision", [c["url"] for c in out.get("collisions", [])] == ["/guides/fit"], out.get("collisions"))
    check("--fail-on-collision exits 1", code == 1)
    check("owner carries its phrase, so no owner warning", out.get("owners") == [], out.get("owners"))

    print("\ncheck (before writing a head)")
    code, out = run("check", "--url", "/guides/new", "--title", "Best Hiking Boots for Kids", cwd=d)
    check("a new title on the owned phrase collides", out.get("verdict") == "collides", out)
    code, out = run("check", "--url", "/", "--title", "Best Hiking Boots", cwd=d)
    check("the owner may use its own phrase", out.get("verdict") == "clear", out)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all headterms tests passed")
