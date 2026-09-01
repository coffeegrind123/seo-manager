#!/usr/bin/env python3
"""test_all_suites.py - the one thing pytest CAN collect, so it cannot report
green while skipping the suite.

17 of the 18 suites here signal by exit code and define no `def test_*`
function, so `python3 -m pytest -q` collected 6 tests from one file and printed
"6 passed" - a clean green covering an eighteenth of the suite. This shim makes
the obvious command honest: pytest collects it, it runs `run_tests.py`, and a
failure anywhere fails the pytest run.

`run_tests.py` remains the real runner and needs no pytest.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_tests  # noqa: E402


def test_every_suite_passes():
    got = run_tests.run_all()
    assert got.get("ok"), json.dumps(
        {"failed": got.get("failed"), "reason": got.get("reason"),
         "rows": [r for r in got.get("rows", []) if r.get("state") != "pass"]},
        indent=2)


def test_runner_can_detect_a_failing_suite():
    """Without this, `test_every_suite_passes` could be green because the
    runner cannot fail - the control the runner carries, asserted here too."""
    got = run_tests.run_control()
    assert got.get("ok"), json.dumps(got, indent=2)


if __name__ == "__main__":
    test_every_suite_passes()
    test_runner_can_detect_a_failing_suite()
    print("all suite-runner tests passed")
