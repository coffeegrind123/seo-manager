#!/usr/bin/env python3
"""run_tests.py - run every suite in this directory, and refuse to call a
partial run green.

WHY THIS EXISTS. The suites here are plain scripts: they do their work at
import/`main()` time and signal by EXIT CODE, and 17 of the 18 define no
`def test_*` function at all. `pytest` collects functions, so the obvious
command -

    python3 -m pytest -q          # "6 passed"

- reports a clean green having run ONE suite of eighteen. Nothing errors,
nothing is skipped, no warning appears. That is the exact failure this skill
is built around: a pass whose control never ran. There was also no documented
runner, so "18 suites green" was a claim nothing in the repo could reproduce.

So this discovers `test_*.py` by FILENAME, runs each in its own process, and
reads its exit code - the signal the suites actually emit. It also reports
which suites are invisible to pytest (`def test_` count of zero), because that
number is the reason this file exists and it should be measured on every run
rather than remembered.

    run_tests.py                  # run them all, JSON verdict, exit 1 on any failure
    run_tests.py --list           # what would run, and pytest visibility
    run_tests.py -k sitegraph     # substring filter
    run_tests.py control          # prove the runner can tell a failing suite from a passing one

An empty discovery REFUSES rather than reporting a clean sweep: zero suites and
eighteen passing suites must not serialise to the same verdict.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from controls import Controls, refuse

HERE = Path(__file__).resolve().parent

# The pytest shim runs THIS runner, so discovering it would recurse. It is the
# only file excluded, and it is excluded by name rather than by a pattern that
# could silently grow to swallow real suites.
SHIMS = {"test_all_suites.py"}

_DEF_TEST_RE = re.compile(r"^\s*def\s+test_\w*\s*\(", re.M)


def discover(directory: Path, pattern: str | None = None) -> list[Path]:
    out = []
    for p in sorted(directory.glob("test_*.py")):
        if p.name in SHIMS:
            continue
        if pattern and pattern not in p.name:
            continue
        out.append(p)
    return out


def pytest_visible(path: Path) -> int:
    """How many functions pytest would collect from this file.

    Zero means pytest runs NOTHING from a suite that this runner runs in full -
    which is the whole finding, so it is counted rather than asserted."""
    try:
        return len(_DEF_TEST_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return 0


def run_suite(path: Path, timeout: int = 300) -> dict:
    try:
        p = subprocess.run([sys.executable, str(path)], capture_output=True,
                           text=True, timeout=timeout, cwd=str(path.parent))
    except subprocess.TimeoutExpired:
        return {"suite": path.name, "state": "timeout", "exit": None,
                "detail": f"no verdict in {timeout}s"}
    tail = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    row = {"suite": path.name, "exit": p.returncode,
           "state": "pass" if p.returncode == 0 else "fail",
           "last_line": tail[-1] if tail else ""}
    if p.returncode != 0:
        # The failure, not a transcript of the run - the last few lines of each
        # stream are where a suite says what broke.
        row["stdout_tail"] = tail[-12:]
        row["stderr_tail"] = err[-12:]
    return row


def run_all(directory: Path = HERE, pattern: str | None = None,
            timeout: int = 300) -> dict:
    suites = discover(directory, pattern)
    if not suites:
        return refuse("run-tests",
                      f"no test_*.py found in {directory} - a run that discovered "
                      f"nothing is not a clean sweep",
                      directory=str(directory), pattern=pattern)

    rows = [run_suite(p, timeout) for p in suites]
    failed = [r["suite"] for r in rows if r["state"] != "pass"]
    invisible = [p.name for p in suites if pytest_visible(p) == 0]

    return {
        "ok": not failed,
        "check": "run-tests",
        "summary": {"total": len(rows), "passed": len(rows) - len(failed),
                    "failed": len(failed)},
        "failed": failed,
        "pytest": {
            "would_collect_from": len(suites) - len(invisible),
            "invisible_to_pytest": invisible,
            "note": ("these suites define no `def test_` function, so `pytest` "
                     "collects nothing from them and still exits 0 - use this "
                     "runner, not pytest, as the suite gate"),
        },
        "rows": rows,
    }


# ------------------------------------------------------------------ control
def _write(d: Path, name: str, body: str) -> Path:
    p = d / name
    p.write_text(body, encoding="utf-8")
    return p


def run_control() -> dict:
    """Prove the runner discriminates, against a fixture tree.

    A runner whose only failure mode is untestable is the instrument it exists
    to catch - so the fixtures include the exact shape that defeats pytest (a
    suite with no `def test_` function) and the shape that must never read as
    green (nothing discovered at all)."""
    c = Controls("run-tests-control")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # Both fixtures signal the way the real suites do: by exit code, with
        # no `def test_` function anywhere. pytest would collect zero from
        # either and report success.
        _write(d, "test_ok_fixture.py",
               "import sys\nprint('all fixture tests passed')\nsys.exit(0)\n")
        _write(d, "test_bad_fixture.py",
               "import sys\nprint('boom')\nsys.exit(1)\n")

        got = run_all(d)
        c.check("discovers_both_fixtures", got.get("summary", {}).get("total") == 2,
                f"total={got.get('summary')}")
        c.check("failing_suite_makes_verdict_not_ok", got.get("ok") is False)
        c.check("failing_suite_is_named", got.get("failed") == ["test_bad_fixture.py"],
                f"failed={got.get('failed')}")
        c.check("passing_suite_is_not_named",
                "test_ok_fixture.py" not in (got.get("failed") or []))
        c.check("failure_carries_its_output",
                any(r["suite"] == "test_bad_fixture.py" and "boom" in " ".join(
                    r.get("stdout_tail") or []) for r in got.get("rows", [])))

        # The discriminator against pytest, measured rather than asserted: both
        # fixtures are exit-code suites, so both must be reported invisible.
        c.check("counts_pytest_invisible_suites",
                sorted(got["pytest"]["invisible_to_pytest"])
                == ["test_bad_fixture.py", "test_ok_fixture.py"],
                str(got["pytest"]))

        # ...and a suite pytest CAN collect must not be counted as invisible,
        # or the count is just "every file" and proves nothing.
        _write(d, "test_visible_fixture.py",
               "def test_x():\n    assert True\n\n\nif __name__ == '__main__':\n"
               "    test_x()\n    print('ok')\n")
        got2 = run_all(d)
        c.check("collectable_suite_not_called_invisible",
                "test_visible_fixture.py" not in got2["pytest"]["invisible_to_pytest"],
                str(got2["pytest"]))

        # The shim must not run itself.
        _write(d, "test_all_suites.py", "import sys\nsys.exit(0)\n")
        got3 = run_all(d)
        c.check("shim_excluded_from_discovery",
                "test_all_suites.py" not in [r["suite"] for r in got3["rows"]])

    with tempfile.TemporaryDirectory() as td2:
        empty = run_all(Path(td2))
        c.check("empty_tree_refuses", empty.get("control_failed") is True,
                f"got {empty.get('ok')!r}/{empty.get('control_failed')!r}")
        c.check("empty_tree_is_not_ok", empty.get("ok") is False)

    return c.verdict()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("control", help="prove the runner tells a failing suite from a passing one")

    ap.add_argument("-k", dest="pattern", default=None,
                    help="only suites whose filename contains this substring")
    ap.add_argument("--list", action="store_true", help="what would run; do not run it")
    ap.add_argument("--timeout", type=int, default=300, help="per-suite timeout (s)")

    a = ap.parse_args()

    if a.cmd == "control":
        out = run_control()
    elif a.list:
        suites = discover(HERE, a.pattern)
        out = ({"ok": True, "check": "run-tests-list", "count": len(suites),
                "suites": [{"suite": p.name, "pytest_collects": pytest_visible(p)}
                           for p in suites]}
               if suites else
               refuse("run-tests-list", f"no test_*.py found in {HERE}"))
    else:
        out = run_all(HERE, a.pattern, a.timeout)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
