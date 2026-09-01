#!/usr/bin/env python3
"""Regression tests for the control primitive and the control audit.

The primitive's own checks live in `controls.py control` and run on every
invocation. This file covers what that cannot: that the AUDIT still finds every
instrument's control, that it recognises BOTH invocation shapes, and that it
cannot report a clean sweep while an instrument is uncontrolled.

That last one is the whole risk. An audit whose failure mode is "reports
everything as fine" is the exact instrument it exists to catch.

    python3 test_controls.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from controls import (  # noqa: E402
    Controls, audit, declares_control, guard_zero, refuse, uniform_verdict,
)

FAILURES: list[str] = []


def check(label: str, cond, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:160]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    print("the primitive's own self-control:")
    p = subprocess.run([sys.executable, str(HERE / "controls.py"), "control"],
                       capture_output=True, text=True, timeout=120)
    self_ctl = json.loads(p.stdout or "{}")
    check("controls.py control passes", self_ctl.get("ok") is True, self_ctl.get("failed"))
    check("it registers a real number of checks", len(self_ctl.get("checks") or {}) >= 15)

    print("\ndetector - BOTH invocation shapes (recognising one is how six got missed):")
    check("a subcommand control is found",
          declares_control(HERE / "sitegraph.py")[1] == "control")
    check("a --control flag is found",
          declares_control(HERE / "serp.py")[1] == "--control")
    with tempfile.TemporaryDirectory() as td:
        blank = Path(td) / "blank.py"
        blank.write_text('import argparse\ns = argparse.ArgumentParser().add_subparsers()\n'
                         's.add_parser("scan")\n')
        has_sub, how = declares_control(blank)
        check("a script with no control is reported as having none", how is None)
        check("its subcommand interface is still seen", has_sub is True)
        nofile = Path(td) / "gone.py"
        check("an unreadable file does not crash the detector",
              declares_control(nofile) == (False, None))

    print("\nthe audit (static - it must not need the network):")
    a = audit(run=False)
    check("the audit carries its own control", a["control_ok"] is True,
          "if this is false the audit is the broken reader")
    check("every instrument declares a control", a["summary"]["uncontrolled"] == 0,
          f"uncontrolled: {a['uncontrolled']}")
    check("it found a real population", a["summary"]["total"] >= 20, a["summary"])
    check("both shapes appear in the rows",
          {r.get("invoked_as") for r in a["rows"]} == {"control", "--control"},
          str({r.get("invoked_as") for r in a["rows"]}))

    check("a clean sweep is reported as ok", a["ok"] is True, a.get("absent"))
    check("and it does not claim the controls ran",
          a["summary"]["proven"] == 0 and a["mode"] == "static", a["summary"])

    print("\nthe audit cannot report a clean sweep while something is uncontrolled:")
    # The failure mode that matters, exercised against a REAL fixture tree
    # rather than asserted about today's output. An audit whose only failure
    # mode is untestable is the instrument it exists to catch.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "good.py").write_text(
            'import argparse\n'
            's = argparse.ArgumentParser().add_subparsers()\n'
            's.add_parser("control")\n')
        (d / "bad.py").write_text(
            'import argparse\n'
            's = argparse.ArgumentParser().add_subparsers()\n'
            's.add_parser("scan")\n')
        f = audit(run=False, directory=d)
        check("an uncontrolled instrument makes the verdict not-ok", f["ok"] is False, f["summary"])
        check("it is named, not just counted", f["absent"] == ["bad.py"], str(f["absent"]))
        check("the declared one is still credited", f["summary"]["declared"] == 1, f["summary"])
        # A static run matched a regex; it ran nothing. Crediting that as
        # `proven` is the audit making the claim it exists to police.
        check("a static run proves nothing", f["summary"]["proven"] == 0, f["summary"])
        check("a static run says so in its verdict",
              f["mode"] == "static" and f["controls_executed"] is False, f.get("mode"))
        (d / "bad.py").unlink()
        f2 = audit(run=False, directory=d)
        check("removing it restores the clean verdict", f2["ok"] is True, f2["summary"])

    print("\nthe primitives:")
    empty = Controls("x")
    check("an empty control set is not a pass", empty.ok is False,
          "a tool that declares a control and registers none reads as green")
    good = Controls("x")
    good.check("a", True)
    check("guard_zero passes a zero backed by a control",
          (guard_zero("t", 0, good, "widgets") or {}).get("verdict") == "measured-zero")
    bad = Controls("x")
    bad.check("a", False)
    check("guard_zero refuses a zero with no control",
          (guard_zero("t", 0, bad, "widgets") or {}).get("control_failed") is True)
    check("guard_zero does not gate a positive finding",
          guard_zero("t", 7, bad, "widgets") is None,
          "a positive finding carries its own evidence - you can go and look at it")
    check("a refusal is distinguishable from a finding",
          refuse("t", "r")["control_failed"] is True and refuse("t", "r")["ok"] is False)

    print("\nthe uniform-verdict tell (the 44-of-44 lesson, generalised):")
    check("a whole population agreeing fires",
          (uniform_verdict(["warn"] * 44) or {}).get("population") == 44)
    check("one dissenter silences it", uniform_verdict(["warn"] * 43 + ["pass"]) is None)
    check("a small uniform sample does not fire", uniform_verdict(["warn"] * 3) is None,
          "a small uniform sample is ordinary, not evidence")
    check("it carries its own falsifier", "falsify" in (uniform_verdict(["warn"] * 44) or {}),
          "a suspicion that cannot be tested is not a finding")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all controls tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
