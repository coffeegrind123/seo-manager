#!/usr/bin/env python3
"""Regression tests for claims.py - product claims vs the ledger, and competitor framing.

    python3 test_claims.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import claims as C  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' ' + str(detail)) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def run(*argv, cwd=None):
    p = subprocess.run([sys.executable, str(HERE / "claims.py"), *argv],
                       capture_output=True, text=True, cwd=cwd)
    return p.returncode, json.loads(p.stdout)


print("sentence splitting")
check("two sentences", C.sentences(["One claim here. Another one!"]) == ["One claim here.", "Another one!"])
check("blocks never merge", len(C.sentences(["Heading", "Body text."])) == 2)

print("\nthe control")
code, out = run("control")
check("control passes", out.get("ok") is True and code == 0, out.get("failed"))

LEDGER = {
    "facts": [{"id": "mobile", "statement": "Works on phones.", "source": "README.md:1"}],
    "never": [{"id": "no-mobile", "pattern": r"\bnot (available|supported) on (phones|mobile)\b",
               "why": "mobile shipped", "source": "README.md:1"}],
    "competitors": ["Rival App"],
}

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / ".seo").mkdir()
    site = root / "site"
    site.mkdir()
    (site / "a.html").write_text('<html lang="en"><body><p>Sadly it is not available on phones.</p>'
                                 '<p>We recommend Rival App for teams.</p></body></html>')
    (site / "b.md").write_text("Rival App has a free tier.\n")

    print("\nrefusals")
    code, out = run("scan", "--root", str(site), cwd=d)
    check("missing ledger refuses", out.get("control_failed") is True, out)
    (root / ".seo" / "claims.json").write_text(json.dumps({"facts": []}))
    code, out = run("scan", "--root", str(site), cwd=d)
    check("a ledger with no rules and no competitors refuses", out.get("control_failed") is True, out)
    (root / ".seo" / "claims.json").write_text(json.dumps({"never": [{"id": "x", "pattern": "("}]}))
    code, out = run("lint", cwd=d)
    check("lint reports a broken pattern", out.get("ok") is False and out.get("errors"), out)

    (root / ".seo" / "claims.json").write_text(json.dumps(LEDGER))
    print("\nscan")
    code, out = run("scan", "--root", str(site), "--fail-on-finding", cwd=d)
    check("the stale claim is a finding", out.get("findings_by_rule") == {"no-mobile": 1}, out.get("findings"))
    check("--fail-on-finding exits 1", code == 1)
    check("the recommendation goes to review", out.get("review_total") == 1, out.get("review"))
    check("markdown is read too, and its neutral mention is counted",
          out.get("documents") == 2 and out.get("competitor_mentions") == 2, out)

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all claims tests passed")
