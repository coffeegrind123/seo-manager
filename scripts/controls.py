#!/usr/bin/env python3
"""controls.py - the control primitive, and the audit that proves each
instrument carries one.

A negative result is only as good as its control. "Nothing found" and "the
reader is broken" serialise to the same JSON unless something separately proves
the reader can still find a thing that is known to be there.

This module exists because on 2026-09-01 seven instruments in this skill failed
their own controls in a single run, and every one of them would have shipped as
a confident finding about the SITE rather than a bug in the READER:

  * `urllib.robotparser.read()` reported every path blocked for every agent - a
    CDN 403 of the default Python UA becomes `disallow_all`, which is the same
    value as a real site-wide Disallow with no way to tell them apart.
  * a Caddy access-log grep for `GET /path` returned 0 for every path, because
    the logs are JSON (`"uri":"..."`).
  * `bing.py --days` was silently ignored on four subcommands, so `--days 7`
    and `--days 30` were byte-identical - which reads as "positions did not
    move".
  * `slop.py scan` said `warn` on 44 of 44 pages: it was counting `<title>`,
    JSON-LD, `<style>` and HTML comments as prose.
  * a numeric-contradiction probe reported zero conflicts across a silo and
    then could not find the known instance when handed it directly.

Every one was caught by a human happening to run a control. Nothing in the
code required one. These three primitives make the requirement structural:

    Controls()          accumulate named checks, emit ONE verdict
    refuse()            the standard shape for "cannot ask", which must never
                        share a code path with "the answer is no"
    uniform_verdict()   a population that ALL said the same thing IS the tell

and `controls.py audit` answers, by running them, which instruments in this
directory can currently prove they discriminate - and which cannot.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Scripts that are not instruments, or whose control IS the whole program.
NOT_INSTRUMENTS = {"controls.py"}


# --------------------------------------------------------------- the primitive
class Controls:
    """An accumulator for named control checks, emitting one verdict.

    Deliberately NOT a test framework. A control runs in production, on the
    real invocation, against synthetic input the instrument's own reader must
    handle - so a clean pass on a live site means something. `pytest` proves
    the rules were right when they were written; this proves the reader is
    still discriminating on THIS run, on THIS machine, against THIS input.
    """

    def __init__(self, name: str):
        self.name = name
        self.checks: dict[str, bool] = {}
        self.details: dict[str, str] = {}

    def check(self, label: str, ok, detail: str | None = None) -> bool:
        ok = bool(ok)
        self.checks[label] = ok
        if detail and not ok:
            self.details[label] = str(detail)[:400]
        return ok

    def probe(self, label: str, fn, expect=True) -> bool:
        """Run `fn()` and check its result, turning an EXCEPTION into a failed
        control rather than a crash. A control that dies takes the whole run
        with it and tells you nothing; a control that fails tells you the
        reader is broken, which is the entire point."""
        try:
            got = fn()
        except Exception as e:                                   # noqa: BLE001
            return self.check(label, False, f"{type(e).__name__}: {e}")
        return self.check(label, got == expect, f"got {got!r}, expected {expect!r}")

    @property
    def ok(self) -> bool:
        # An EMPTY control set is not a pass. A tool that declares a control
        # and then registers no checks is indistinguishable from one with no
        # control at all, and reads as green.
        return bool(self.checks) and all(self.checks.values())

    @property
    def failed(self) -> list[str]:
        return [k for k, v in self.checks.items() if not v]

    def verdict(self, **extra) -> dict:
        d = {"ok": self.ok, "check": self.name, "control_ok": self.ok,
             "checks": self.checks}
        if self.failed:
            d["failed"] = self.failed
            d["detail"] = {k: self.details[k] for k in self.failed if k in self.details}
            d["reason"] = (f"{len(self.failed)} of {len(self.checks)} control checks "
                           f"failed - this instrument cannot currently tell a real "
                           f"finding from a reader bug, so its output is not evidence")
        d.update(extra)
        return d


def refuse(check: str, reason: str, **extra) -> dict:
    """The standard shape for "cannot ask".

    Distinct from `{"ok": False}` on purpose: `ok: False` is a finding (the
    site has a problem), `control_failed: True` is a refusal (the instrument
    has a problem). Callers, dashboards and the run log must be able to tell
    them apart without reading prose."""
    return {"ok": False, "check": check, "control_failed": True, "reason": reason, **extra}


def guard_zero(check: str, found: int, ctl: Controls, subject: str, **extra) -> dict | None:
    """Gate a ZERO. Returns a refusal if the reader cannot prove itself, an
    annotated pass if the zero is real, and None if there is nothing to gate
    because the instrument found something.

    The asymmetry is the point: a POSITIVE finding carries its own evidence -
    you can go and look at the thing it found. A zero carries none."""
    if found > 0:
        return None
    if not ctl.ok:
        return refuse(check,
                      f"found 0 {subject}, and the control did not pass - this is "
                      f"'the reader is broken', not '{subject}: none'",
                      control_checks=ctl.checks, failed=ctl.failed, **extra)
    return {"ok": True, "check": check, "control_ok": True, "count": 0,
            "verdict": "measured-zero",
            "note": (f"0 {subject}, and the reader demonstrably still finds "
                     f"{subject} in synthetic input - so this zero is a measurement, "
                     f"not a silence"),
            "control_checks": ctl.checks, **extra}


def uniform_verdict(verdicts, *, min_population: int = 8, subject: str = "items") -> dict | None:
    """A whole population agreeing IS the tell.

    `slop.py scan` returned `warn` for 44 of 44 pages. Each page's verdict was
    individually plausible; the UNIFORMITY was the evidence, and nothing in the
    tool looked at it. Real corpora are mixed - a reader that returns one value
    for everything is usually measuring something other than what it claims
    (there, the page TEMPLATE rather than the prose).

    Returns None below `min_population`, because a small uniform sample is
    ordinary. Advisory: it cannot know a corpus is not genuinely uniform, so it
    reports a suspicion with its own falsifier attached, never a failure."""
    vals = list(verdicts)
    n = len(vals)
    if n < min_population:
        return None
    distinct = sorted({str(v) for v in vals})
    if len(distinct) > 1:
        return None
    return {
        "signal": "uniform_verdict",
        "severity": "warn",
        "population": n,
        "verdict": distinct[0],
        "message": (f"all {n} {subject} returned the same verdict ({distinct[0]!r}). "
                    f"That is more often a reader measuring the wrong thing than a "
                    f"genuinely uniform corpus"),
        "falsify": (f"hand the reader two {subject} you KNOW differ and confirm it "
                    f"separates them; if it does, the uniformity is real"),
    }


# ------------------------------------------------------------------- the audit
_CTL_SUB_RE = re.compile(r'add_parser\(\s*["\']control["\']')
_CTL_ARG_RE = re.compile(r'add_argument\(\s*["\']--control["\']')
_CTL_CHOICE_RE = re.compile(r'choices\s*=\s*\[[^\]]*["\']control["\']')
_SUB_RE = re.compile(r"add_subparsers\(")


def declares_control(path: Path) -> tuple[bool, str | None]:
    """(has a subcommand interface, how `control` is invoked or None).

    Static, on purpose: running `control` on a script that does not have it can
    mean running something else entirely - `authority.py control` would take
    "control" for a DOMAIN and hit the network.

    Two invocation shapes exist because two argument styles do. Scripts with
    subparsers take `control` as a subcommand; flag-style ones (serp, serpd,
    seodoctor, rankcheck, authority) take `--control`. Recognising only the
    first reported six controlled instruments as uncontrolled, which is the same
    class of error as the tools it audits."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (False, None)
    has_sub = bool(_SUB_RE.search(src))
    if _CTL_SUB_RE.search(src) or _CTL_CHOICE_RE.search(src):
        return (has_sub, "control")
    if _CTL_ARG_RE.search(src):
        return (has_sub, "--control")
    return (has_sub, None)


def run_control(path: Path, timeout: int = 120, argv: str = "control") -> dict:
    try:
        p = subprocess.run([sys.executable, str(path), argv],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"state": "timeout", "detail": f"no verdict in {timeout}s"}
    body = (p.stdout or "").strip()
    try:
        j = json.loads(body)
    except Exception:
        return {"state": "unreadable",
                "detail": (body or (p.stderr or "").strip())[:300] or "no output"}
    if j.get("ok"):
        return {"state": "pass", "checks": len(j.get("checks") or {}) or None}
    return {"state": "fail", "failed": j.get("failed") or j.get("reason"),
            "checks": len(j.get("checks") or {}) or None}


def audit(run: bool = True, timeout: int = 120, directory: Path | None = None) -> dict:
    """Which instruments can currently prove they discriminate.

    `directory` exists so this can be pointed at a fixture tree. An audit whose
    only failure mode is untestable is the instrument it exists to catch."""
    here = Path(directory) if directory else HERE
    rows = []
    for path in sorted(here.glob("*.py")):
        if path.name.startswith("test_") or path.name in NOT_INSTRUMENTS:
            continue
        has_sub, how = declares_control(path)
        if not how:
            rows.append({"script": path.name, "state": "absent",
                         "note": ("nothing in this script can distinguish 'found nothing' "
                                  "from 'the reader is broken', so its zeros are not "
                                  "evidence")})
            continue
        row = {"script": path.name, "invoked_as": how}
        row.update(run_control(path, timeout, how) if run else {"state": "declared"})
        rows.append(row)

    by = {}
    for r in rows:
        by.setdefault(r["state"], []).append(r["script"])
    # PROVEN and DECLARED are different claims and must never share a number.
    # `--static` matches a regex against the source; it cannot tell a working
    # control from one that raises on import or returns garbage. Reporting both
    # under one `controlled` key made a static run's verdict byte-identical in
    # shape to an executed one - the audit committing the error it exists to find.
    proven = by.get("pass", [])
    declared_only = by.get("declared", [])
    declared = proven + declared_only
    broken = by.get("fail", []) + by.get("unreadable", []) + by.get("timeout", [])
    uncontrolled = by.get("absent", [])

    # The audit needs its own control, or it is exactly the instrument it is
    # complaining about. sitegraph and hreflang are KNOWN to carry one.
    # One of each invocation shape, so a detector that has forgotten either
    # one cannot report a clean sweep.
    known_good = [n for n in ("sitegraph.py", "serp.py") if (here / n).exists()]
    detector_ok = (all(declares_control(here / n)[1] for n in known_good)
                   if known_good else bool(rows))

    # `absent` counts against the verdict. An audit that reports ok:true while 22
    # instruments cannot falsify their own zeros is the same failure it exists to
    # catch. `no_subcommands` does not count - that is a structural n/a, not a gap.
    absent = by.get("absent", [])
    return {
        "ok": detector_ok and not broken and not absent,
        "check": "controls-audit",
        "mode": "executed" if run else "static",
        "controls_executed": bool(run),
        "proves": (
            "every declared control was RUN on this machine; `proven` is how many "
            "passed" if run else
            "NOTHING WAS RUN. `declared` counts instruments whose SOURCE declares a "
            "`control` entry point - a control that raises on import, returns "
            "garbage or agrees with the code by construction is counted here. This "
            "verdict cannot tell a working control from a broken one; drop --static "
            "for that"),
        "control_ok": detector_ok,
        "control": {"known_controlled": known_good, "detector_found_them": detector_ok,
                    "note": "if this is false the audit itself is the broken reader"},
        "summary": {"proven": len(proven), "declared": len(declared),
                    "broken": len(broken), "uncontrolled": len(uncontrolled),
                    "total": len(rows)},
        "uncontrolled": uncontrolled,
        "absent": absent,
        "broken": broken,
        "rows": rows,
        "note": ("`absent` is not a bug report about the script's answers - it means "
                 "nothing in the script can distinguish 'found nothing' from 'the "
                 "reader is broken', so its zeros are not evidence"),
    }


# ------------------------------------------------------------- self-control
def self_control() -> dict:
    c = Controls("controls-self")
    empty = Controls("x")
    c.check("empty_control_set_is_not_a_pass", empty.ok is False)

    good = Controls("x")
    good.check("a", True)
    c.check("all_true_passes", good.ok is True)
    good.check("b", False)
    c.check("one_false_fails", good.ok is False)
    c.check("failed_names_the_check", good.failed == ["b"])

    thrower = Controls("x")
    thrower.probe("boom", lambda: (_ for _ in ()).throw(ValueError("nope")))
    c.check("probe_turns_exception_into_failed_check", thrower.ok is False)
    c.check("probe_records_the_exception", "boom" in thrower.details)

    r = refuse("t", "because")
    c.check("refusal_is_not_ok", r["ok"] is False)
    c.check("refusal_is_flagged_control_failed", r["control_failed"] is True)

    bad = Controls("x")
    bad.check("a", False)
    z = guard_zero("t", 0, bad, "widgets")
    c.check("zero_with_failed_control_refuses", z is not None and z.get("control_failed") is True)
    z2 = guard_zero("t", 0, _passing(), "widgets")
    c.check("zero_with_passing_control_is_a_measured_zero",
            z2 is not None and z2.get("verdict") == "measured-zero")
    c.check("nonzero_is_not_gated", guard_zero("t", 3, _passing(), "widgets") is None)

    c.check("uniform_fires_on_a_whole_population",
            (uniform_verdict(["warn"] * 44) or {}).get("signal") == "uniform_verdict")
    c.check("uniform_reports_the_population_size",
            (uniform_verdict(["warn"] * 44) or {}).get("population") == 44)
    c.check("mixed_population_does_not_fire",
            uniform_verdict(["warn"] * 43 + ["pass"]) is None)
    c.check("small_uniform_sample_does_not_fire",
            uniform_verdict(["warn"] * 3) is None)

    # The detector must find a control that IS there, and must not invent one
    # that is not. Without the negative half it would "pass" by saying yes to
    # everything - which is the failure mode this whole module is about.
    if (HERE / "serp.py").exists():
        c.check("detector_finds_a_flag_control",
                declares_control(HERE / "serp.py")[1] == "--control")
    if (HERE / "sitegraph.py").exists():
        c.check("detector_finds_a_subcommand_control",
                declares_control(HERE / "sitegraph.py")[1] == "control")
    # A STATIC audit must not be mistakable for an executed one. Both return
    # ok:true on a healthy tree, so the distinction has to be carried in the
    # verdict itself rather than left to the reader of `rows`.
    with tempfile.TemporaryDirectory() as ts:
        d = Path(ts)
        (d / "good.py").write_text(
            "import argparse, json, sys\n"
            "ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd')\n"
            "sub.add_parser('control')\n"
            "a = ap.parse_args()\n"
            "print(json.dumps({'ok': True, 'check': 'x', 'checks': {'a': True}}))\n")
        st, ex = audit(run=False, directory=d), audit(run=True, directory=d)
        c.check("static_run_proves_nothing", st["summary"]["proven"] == 0, str(st["summary"]))
        c.check("static_run_still_counts_the_declaration", st["summary"]["declared"] == 1)
        c.check("executed_run_proves_it", ex["summary"]["proven"] == 1, str(ex["summary"]))
        c.check("the_two_modes_are_distinguishable",
                st["mode"] == "static" and ex["mode"] == "executed"
                and st["controls_executed"] is False and ex["controls_executed"] is True)

    # ...and a control that DECLARES itself but blows up must survive --static
    # and fail the executed run. That asymmetry is the whole reason for the split.
    with tempfile.TemporaryDirectory() as tb:
        d = Path(tb)
        (d / "broken.py").write_text(
            "import argparse\n"
            "ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd')\n"
            "sub.add_parser('control')\n"
            "raise SystemExit('this control is broken')\n")
        st2, ex2 = audit(run=False, directory=d), audit(run=True, directory=d)
        c.check("static_cannot_see_a_broken_control", st2["ok"] is True, str(st2["summary"]))
        c.check("executed_catches_the_broken_control", ex2["ok"] is False, str(ex2["summary"]))
        c.check("broken_control_is_named", ex2["broken"] == ["broken.py"], str(ex2["broken"]))

    with tempfile.TemporaryDirectory() as td:
        blank = Path(td) / "blank.py"
        blank.write_text("import argparse\n"
                         "s = argparse.ArgumentParser().add_subparsers()\n"
                         's = s.add_parser("scan")\n')
        has_sub, how = declares_control(blank)
        c.check("detector_sees_a_subcommand_interface", has_sub is True)
        c.check("detector_does_not_invent_a_control", how is None)
    return c.verdict()


def _passing() -> Controls:
    c = Controls("x")
    c.check("a", True)
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="which instruments can prove they discriminate")
    a.add_argument("--static", action="store_true",
                   help="detect declarations only, do not run them")
    a.add_argument("--timeout", type=int, default=120)
    a.add_argument("--dir", help="audit a different scripts directory (for fixtures)")
    sub.add_parser("control", help="fire every primitive at synthetic input")
    args = ap.parse_args()
    r = (audit(run=not args.static, timeout=args.timeout, directory=args.dir)
         if args.cmd == "audit" else self_control())
    print(json.dumps(r, indent=2, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
