#!/usr/bin/env python3
"""Regression tests for the re-measure runner.

Every case is one of the four ways "did the change work?" gets answered wrongly,
or a bug this file shipped in its first hour.

    python3 test_remeasure.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remeasure as R  # noqa: E402

FAILURES: list[str] = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' - ' + str(detail)[:150]) if detail else ''}")
    if not cond:
        FAILURES.append(label)


def row(**kw):
    d = {"baseline": 100.0, "min_change": 10.0, "expect": "increase"}
    d.update(kw)
    return d


def main() -> int:
    print("the verdict is decided by a direction fixed BEFORE the answer was known:")
    check("predicted direction confirms", R._verdict(row(), 130)[0] == "confirmed")
    check("opposite direction refutes", R._verdict(row(), 60)[0] == "refuted")
    check("under the threshold is no_change", R._verdict(row(), 105)[0] == "no_change")
    check("a decrease hypothesis is not just an increase one backwards",
          R._verdict(row(expect="decrease"), 50)[0] == "confirmed"
          and R._verdict(row(expect="decrease"), 150)[0] == "refuted")
    check("an unchanged hypothesis can be refuted",
          R._verdict(row(expect="unchanged"), 140)[0] == "refuted")
    check("the three verdicts are distinct values",
          len({R._verdict(row(), v)[0] for v in (130, 60, 105)}) == 3)

    print("\na missing metric is NOT a zero - this one reports a collapse that")
    print("never happened, and it looks exactly like a real finding:")
    check("present is read", R.dig({"a": {"b": 5}}, "a.b") == (5, None))
    check("missing is an error", R.dig({"a": {}}, "a.b")[0] is None)
    check("null is an error", R.dig({"a": {"b": None}}, "a.b")[0] is None)
    check("a bool is not a number", R.dig({"a": {"b": True}}, "a.b")[0] is None,
          "True arithmetics as 1 and would silently become a metric")
    check("a real zero survives", R.dig({"a": {"b": 0}}, "a.b") == (0, None))
    check("sparse maps can opt in to missing==zero",
          R.dig({"a": {}}, "a.b", missing_is_zero=True) == (0, None),
          "crawllog's top_silos omits a silo the bot never touched, and that "
          "absence IS the finding")
    check("but only by opting in", R.dig({"a": {}}, "a.b")[0] is None)
    # Addressing a list element by identity rather than position. A positional
    # index into a value-sorted list re-points itself between runs, so the verdict
    # would compare two different bots and call the difference a change.
    _bots = {"bots": [{"key": "bingbot", "hits": 20, "top_silos": {"/play": 502}},
                      {"key": "googlebot", "hits": 5}]}
    check("a list element is addressable by its key",
          R.dig(_bots, "bots.bingbot.top_silos./play") == (502, None))
    check("CONTROL a positional index still works",
          R.dig(_bots, "bots.0.hits") == (20, None))
    check("CONTROL identity addressing picks the RIGHT element",
          R.dig(_bots, "bots.googlebot.hits") == (5, None))
    check("a name matching nothing is an error, not a zero",
          R.dig(_bots, "bots.nosuchbot.hits")[0] is None)
    check("and not a zero even when missing_is_zero is on",
          R.dig(_bots, "bots.nosuchbot.hits", missing_is_zero=True)[0] is None,
          "missing_is_zero is for a sparse MAP; a named row that does not exist is a broken metric")
    # Duplicate detection: the identity of an experiment is what it RUNS and
    # what it READS, not the label somebody gave it.
    a = "crawllog.py scan --ssh-key ~/.ssh/k --bot bingbot"
    b = "crawllog.py scan --ssh-key " + __import__("os").path.expanduser("~/.ssh/k") + " --bot bingbot"
    check("the same command written two ways is one experiment",
          R.same_experiment(a, "bots.0.top_silos./play")
          == R.same_experiment(b, "bots.bingbot.top_silos./play"),
          "quoting and ~ expansion differ between sessions; the intervention does not")
    check("CONTROL a different command is a different experiment",
          R.same_experiment(a, "bots.0.top_silos./play")
          != R.same_experiment(a + " --days 30", "bots.0.top_silos./play"))
    check("CONTROL a different metric leaf is a different experiment",
          R.same_experiment(a, "bots.0.top_silos./play")
          != R.same_experiment(a, "bots.0.top_silos./guides"),
          "otherwise every hypothesis over one command would collide")

    check("an ambiguous name is refused rather than guessed",
          R.dig({"bots": [{"key": "x", "hits": 1}, {"key": "x", "hits": 2}]},
                "bots.x.hits")[0] is None)


    print("\nquoting: a stored command is round-tripped through a string:")
    check("a quoted argument survives",
          __import__("shlex").split("serp.py 'cs 1.6 online' --count 20")[1] == "cs 1.6 online",
          "a whitespace split makes this three arguments and the measurement fails "
          "with an argparse usage message that reads like a broken tool")
    check("an unbalanced quote is an error, not a crash",
          R.run_measurement("serp.py 'unclosed").get("ok") is False)

    print("\nargparse: the subcommand and the measurement command are different things:")
    ns = R._parser().parse_args(["record", "--id", "x", "--question", "q",
                                 "--cmd", "crawllog.py scan --days 14", "--metric", "a.b",
                                 "--expect", "increase", "--min-change", "1", "--after", "7d"])
    check("--cmd survives argparse", ns.cmd == "crawllog.py scan --days 14",
          "a subparser dest of 'cmd' overwrites it, and the dispatch then looks up "
          "the whole crawllog invocation as a subcommand name")
    check("the subcommand is on its own attribute", ns.action == "record")

    print("\nthe four refusals:")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / ".seo").mkdir()

        class A:
            root = td
            id = "h1"
            question = "q"
            cmd = "controls.py control"
            metric = "checks.all_true_passes"
            baseline = 100.0
            expect = "increase"
            min_change = 10.0
            after = "14d"
            note = None
            force = False
            missing_is_zero = False
            timeout = 60

        check("recording succeeds", R.record(A())["ok"] is True)
        check("re-recording refuses - overwriting a baseline destroys the only "
              "thing that makes the later check mean anything",
              R.record(A()).get("control_failed") is True)

        class B(A):
            id = "h2"
            after = "whenever"
        check("a non-duration window refuses", R.record(B()).get("control_failed") is True)

        class C(A):
            id = "h3"
            expect = "sideways"
        check("an invalid direction refuses", R.record(C()).get("control_failed") is True)

        check("checking before the window refuses - a crawl hypothesis checked at "
              "day three reads noise as an answer",
              R.check(A()).get("control_failed") is True)

        class D(A):
            force = True
            cmd = "a completely different command"
        m = R.check(D())
        check("a changed command refuses rather than comparing",
              m.get("control_failed") is True,
              "diffing two extraction regimes manufactures change - this skill has "
              "already produced a fake '100% page-1 churn' that way")
        check("the mismatch shows both commands", bool(m.get("baseline_cmd")) and bool(m.get("given")))

        class E(A):
            force = True
            cmd = None
            metric = "checks.no_such_key"
        u = R.check(E())
        check("an unobtainable value is `unmeasured`, never `no_change`",
              u.get("state") == "unmeasured", str(u)[:130])

        d = R.due(A())
        check("an unripe hypothesis waits rather than showing ready",
              not d["ready"] and any(x["id"] == "h1" for x in d["waiting"]))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all remeasure tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
