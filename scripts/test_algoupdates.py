#!/usr/bin/env python3
"""Regression tests for the algorithm-update ledger and its window correlation.

The bug these were written against: `decay.py` and `drift.py` decided whether a
rollout overlapped a measurement window from an `ended` field that NO ledger
entry carried - the end dates lived in `notes` prose ("Completed June 2"). So a
core update that began before the window and finished inside it never
correlated, and an update still rolling out was a single day long. Both read as
"no update near this date", which is the opposite of the truth.

    python3 test_algoupdates.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import decay  # noqa: E402
import algoupdates as AU  # noqa: E402
import argparse
import tempfile

LEDGER = HERE.parent / "assets" / "google-updates.json"
FIX = HERE.parent / "assets" / "fixtures" / "statusdash"
FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:200]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def names(rows):
    return [r["name"] for r in rows]


def test_window_semantics():
    print("window overlap uses the whole rollout, not its first day:")
    ups, err = decay.load_updates(str(LEDGER))
    check("the committed ledger loads", err is None and ups, err)

    # March 2026 core update: 27 Mar -> 8 Apr. A window of 1-30 April starts
    # after the rollout began and before it ended.
    hit = decay.updates_in_window(ups, "2026-04-01", "2026-04-30")
    check("a rollout that began BEFORE the window and ended inside it correlates",
          any("March 2026 Core" in n or "March 2026 core" in n for n in names(hit)), names(hit))

    # The May 2026 core update ran 21 May -> 2 Jun.
    hit = decay.updates_in_window(ups, "2026-05-25", "2026-05-31")
    check("a window wholly INSIDE a rollout correlates",
          any("May 2026" in n for n in names(hit)), names(hit))

    ongoing = [{"date": "2026-09-24", "name": "Sept spam", "kind": "spam",
                "status": "ongoing", "ended": None}]
    hit = decay.updates_in_window(ongoing, "2026-09-28", "2026-10-02")
    check("a rollout still IN PROGRESS overlaps every later window", names(hit) == ["Sept spam"],
          hit)

    done = [{"date": "2026-08-18", "name": "Aug spam", "kind": "spam",
             "status": "completed", "ended": "2026-08-21"}]
    check("CONTROL: a finished rollout does not overlap a later window",
          decay.updates_in_window(done, "2026-09-01", "2026-09-30") == [])
    check("CONTROL: a finished rollout overlaps a window touching its last day",
          names(decay.updates_in_window(done, "2026-08-21", "2026-08-30")) == ["Aug spam"])


def test_control_and_freshness():
    print("\nthe offline control, and a stale calendar named as stale:")
    c = AU.run_control()
    check("algoupdates control passes on captured dashboard fixtures", c.get("ok"),
          [k for k, v in (c.get("checks") or {}).items() if v is not True])
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "l.json"
        led.write_text(json.dumps({"_provenance": {"last_synced": "2026-01-01"},
                                   "updates": [{"date": "2026-01-01", "name": "x", "kind": "core"}]}))
        st = AU.cmd_status(argparse.Namespace(ledger=str(led)))
        check("a calendar synced months ago is stale", st["stale"] is True, st)
        check("a rollout with no end is listed as unknown, not as short",
              st["rollouts_with_unknown_end"] == ["x"], st)
        led.write_text(json.dumps({"updates": []}))
        check("a NEVER-synced calendar is stale, not fresh",
              AU.cmd_status(argparse.Namespace(ledger=str(led)))["stale"] is True)
    real = AU.cmd_status(argparse.Namespace(ledger=str(LEDGER)))
    check("the committed calendar carries the August 2026 spam update",
          any(u["name"].lower().startswith("august 2026 spam")
              for u in AU.load_ledger()[0]["updates"]))
    check("no unverified[] claim leaks into updates[]",
          not ({u.get("claim") for u in AU.load_ledger()[0].get("unverified", [])}
               & {u.get("name") for u in AU.load_ledger()[0]["updates"]}))
    check("every updates[] row cites a Google-owned host",
          all(re.match(r"https://([a-z.]*\.)?(google\.com|web\.dev|blog\.google|"
                       r"developer\.chrome\.com|googleusercontent\.com)/", u.get("source") or "")
              for u in AU.load_ledger()[0]["updates"]),
          [u["source"] for u in AU.load_ledger()[0]["updates"]
           if not re.match(r"https://([a-z.]*\.)?(google\.com|web\.dev|blog\.google|"
                           r"developer\.chrome\.com|googleusercontent\.com)/", u.get("source") or "")])
    check("status is readable", real.get("ok"))


def main() -> int:
    test_window_semantics()
    test_control_and_freshness()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all algoupdates tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
