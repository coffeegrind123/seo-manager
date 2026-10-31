#!/usr/bin/env python3
"""Regression tests for rank tracking: what a rank row MEANS, and when a fall
is a drop.

Two bugs these were written against (2026-10-03):

1. `rankcheck.py` promised that every stored row "carries `depth_checked` so a
   future run never mistakes" not-in-the-top-10 for not-in-the-top-100 - and
   `seostate.py record-rank` silently DROPPED the field. Every null position on
   disk had lost the depth that made it readable. The exit country was never
   stored either, though the non-negotiables require every position claim to
   name it.
2. A fall was reported off one read. One SERP read is one sample of a
   personalised, A/B-tested page; rankme.fast confirms a drop with a second
   read before alerting, and so does this now.

    python3 test_rankcheck.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rankcheck as R  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{(' ' + str(detail)[:200]) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def seostate(root, *args):
    p = subprocess.run([sys.executable, str(HERE / "seostate.py"), "--root", root, *args],
                       capture_output=True, text=True)
    return json.loads(p.stdout or "{}")


print("storage keeps what makes a null position readable")
with tempfile.TemporaryDirectory() as td:
    seostate(td, "init", "--domain", "example.com")
    rows = [{"keyword": "kw", "position": None, "url": None, "provider": "ddg",
             "depth_checked": 10, "exit_country": "us", "rank_state": "out_of_range",
             "checked_at": "2026-10-01T00:00:00+00:00"}]
    seostate(td, "record-rank", "--json", json.dumps(rows))
    stored = [json.loads(x) for x in (Path(td) / ".seo" / "ranks.jsonl").read_text().splitlines() if x]
    check("depth_checked survives record-rank", stored and stored[0].get("depth_checked") == 10, stored)
    check("exit_country survives record-rank", stored and stored[0].get("exit_country") == "us", stored)
    check("rank_state survives record-rank", stored and stored[0].get("rank_state") == "out_of_range", stored)
    rk = seostate(td, "rankings", "--days", "3650")
    row = (rk.get("rankings") or [{}])[0]
    check("rankings reports the latest state and depth, not a bare null",
          row.get("latest_state") == "out_of_range" and row.get("latest_depth") == 10, row)

print("\nrank states - four, never a fake position 101")
check("a position is ranked", R.rank_state(3, 20) == "ranked")
check("no position within a read depth is out_of_range", R.rank_state(None, 20) == "out_of_range")
check("a failed read is not_measured", R.rank_state(None, 0) == "not_measured")

print("\ndrop candidates (prev, now) - previously unranked is never a drop")
d = R.classify_move
check("top-10 -> out of top-10 is a drop", d(7, 14, 20) == "drop")
check("a 5-place fall is a drop", d(12, 17, 20) == "drop")
check("a 4-place fall inside page 2 is not", d(12, 16, 20) is None)
check("ranked -> beyond the checked depth is a drop", d(8, None, 20) == "drop")
check("ranked at 15 -> nothing in a 10-deep read is UNKNOWN, not a drop",
      d(15, None, 10) == "unknown_depth")
check("previously unranked -> unranked is never a drop", d(None, None, 20) is None)
check("previously unranked -> ranked is a gain, not a drop", d(None, 9, 20) is None)
check("a rise is not a drop", d(9, 3, 20) is None)

print("\nsettling a candidate with a second read")
s = R.settle_drop
check("second read agrees -> confirmed", s(prev=7, first=14, second=15, depth=20) == "confirmed")
check("second read back near the old position -> volatile", s(prev=7, first=14, second=8, depth=20) == "volatile")
check("second read failed -> unconfirmed", s(prev=7, first=14, second="failed", depth=20) == "unconfirmed")

print("\nthe offline control")
c = R.run_control()
check("rankcheck control passes", c["ok"], [k for k, v in c["checks"].items() if v is not True])

print()
if FAILS:
    print(f"FAILED {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("all rankcheck tests passed")
