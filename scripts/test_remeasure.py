#!/usr/bin/env python3
"""Regression tests for the re-measure runner.

Every case is one of the four ways "did the change work?" gets answered wrongly,
or a bug this file shipped in its first hour.

    python3 test_remeasure.py
"""
from __future__ import annotations

import json
import shlex
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

    print("\na RATE metric needs its denominator, and a significant move:")
    rr = row(baseline=3, baseline_n=10, min_change=0.1, expect="increase", denominator="n")
    check("3/10 -> 5/10 moved 0.2 but is noise at n=10 -> no_detectable_change",
          R._verdict(rr, 5, 10)[0] == "no_detectable_change", R._verdict(rr, 5, 10))
    rr = row(baseline=30, baseline_n=1000, min_change=0.05, expect="increase", denominator="n")
    check("30/1000 -> 120/1000 is a confirmed increase", R._verdict(rr, 120, 1000)[0] == "confirmed",
          R._verdict(rr, 120, 1000))
    check("and a large opposite move refutes",
          R._verdict(row(baseline=120, baseline_n=1000, min_change=0.05, expect="increase",
                         denominator="n"), 30, 1000)[0] == "refuted")
    check("a significant move under min_change is still no_change",
          R._verdict(row(baseline=300, baseline_n=10000, min_change=0.05, expect="increase",
                         denominator="n"), 400, 10000)[0] == "no_change")
    check("an empty denominator is unmeasured, never a verdict",
          R._verdict(rr, 5, 0)[0] == "unmeasured")
    check("CONTROL: without a denominator the plain threshold rule still applies",
          R._verdict(row(), 130)[0] == "confirmed")

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
    _bots = {"bots": [{"key": "bingbot", "hits": 20, "top_silos": {"/app": 502}},
                      {"key": "googlebot", "hits": 5}]}
    check("a list element is addressable by its key",
          R.dig(_bots, "bots.bingbot.top_silos./app") == (502, None))
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
          R.same_experiment(a, "bots.0.top_silos./app")
          == R.same_experiment(b, "bots.bingbot.top_silos./app"),
          "quoting and ~ expansion differ between sessions; the intervention does not")
    check("CONTROL a different command is a different experiment",
          R.same_experiment(a, "bots.0.top_silos./app")
          != R.same_experiment(a + " --days 30", "bots.0.top_silos./app"))
    check("CONTROL a different metric leaf is a different experiment",
          R.same_experiment(a, "bots.0.top_silos./app")
          != R.same_experiment(a, "bots.0.top_silos./guides"),
          "otherwise every hypothesis over one command would collide")

    check("an ambiguous name is refused rather than guessed",
          R.dig({"bots": [{"key": "x", "hits": 1}, {"key": "x", "hits": 2}]},
                "bots.x.hits")[0] is None)

    print("\nrow selectors: a re-sorted list addressed by CONTENT, not position.")
    print("Expected values are literals read off the fixture, never dig() output:")
    pq = {"queries": [
        {"query": "best hiking boots", "avg_position": 8.0},
        {"query": "a.b c", "avg_position": 2.0},
        {"query": "x]y", "avg_position": 13.0},
        {"query": "tent", "page": "/a", "avg_position": 6.0, "rank": 3},
        {"query": "tent", "page": "/b", "avg_position": 9.0, "rank": 30, "code": "007"},
    ]}
    check("a selector picks the row", R.dig(pq, "queries[query=best hiking boots].avg_position")
          == (8.0, None))
    check("dots and spaces need no quotes", R.dig(pq, "queries[query=a.b c].avg_position")
          == (2.0, None))
    check("the quoted form reads the same row", R.dig(pq, 'queries[query="a.b c"].avg_position')
          == (2.0, None))
    check("a ] in a value: quoted", R.dig(pq, 'queries[query="x]y"].avg_position') == (13.0, None))
    check("a ] in a value: escaped", R.dig(pq, "queries[query=x\\]y].avg_position") == (13.0, None))
    check("a numeric field is matched by its JSON text",
          R.dig(pq, "queries[rank=3].avg_position") == (6.0, None)
          and R.dig(pq, "queries[rank=3.0].avg_position")[0] is None,
          "3 and 3.0 are different text; a fuzzy numeric compare would make [rank=3] hit both")
    check("a numeric-looking STRING is a string",
          R.dig(pq, "queries[code=007].avg_position") == (9.0, None)
          and R.dig(pq, "queries[code=7].avg_position")[0] is None)
    _, e0, k0 = R.resolve(pq, "queries[query=missing row].avg_position")
    check("zero matches is unmeasured, and says so", k0 == "no_match" and "no row" in e0, e0)
    check("zero matches is NOT zero even with missing_is_zero",
          R.dig(pq, "queries[query=missing row].avg_position", missing_is_zero=True)[0] is None,
          "a top-N list omits rows below its cut; a zero position would read as better than #1")
    check("CONTROL missing_is_zero still zeroes the leaf map below a selector",
          R.dig({"q": [{"k": "x", "m": {}}]}, "q[k=x].m.gone", missing_is_zero=True) == (0, None))
    check("CONTROL but not a missing CONTAINER above a selector",
          R.dig({}, "queries[query=x].avg_position", missing_is_zero=True)[0] is None)
    _, e2, k2 = R.resolve(pq, "queries[query=tent].avg_position")
    check("two matches refuse, naming the count", k2 == "ambiguous" and "2 rows" in e2, e2)
    check("two selectors on a segment must hold on one row",
          R.dig(pq, "queries[query=tent][page=/b].avg_position") == (9.0, None))
    check("CONTROL the index syntax is untouched",
          R.dig(pq, "queries.0.avg_position") == (8.0, None)
          and R.dig(pq, "queries.-1.rank") == (30, None))
    for bad in ("queries[query=x", 'queries[query="x', "queries[query].v",
                "queries[query=x]junk.v", "queries..v"):
        check(f"malformed {bad!r} is a syntax error", R.resolve(pq, bad)[2] == "syntax")
    c_ = "bing.py pagequeries --url https://example.com/"
    check("different selectors are different experiments",
          R.same_experiment(c_, "queries[query=a].avg_position")
          != R.same_experiment(c_, "queries[query=b].avg_position"),
          "the leaf alone collides every query on one page")
    check("one selector spelled two ways is one experiment",
          R.same_experiment(c_, "queries[query=a.b c].clicks")
          == R.same_experiment(c_, 'queries[query="a.b c"].clicks'))

    print("\nend to end through argparse: the row moves from index 0 to index 3:")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / ".seo").mkdir()
        payload, emitter = Path(td) / "p.json", Path(td) / "emit.py"
        emitter.write_text("import sys\nprint(open(sys.argv[1]).read())\n", encoding="utf-8")
        payload.write_text(json.dumps({"queries": [
            {"query": "best hiking boots", "avg_position": 8.0},
            {"query": "rain jacket", "avg_position": 11.0},
            {"query": "camp stove", "avg_position": 20.0},
            {"query": "tent", "avg_position": 30.0}]}), encoding="utf-8")
        cmd = f"{shlex.quote(str(emitter))} {shlex.quote(str(payload))}"
        common = ["--cmd", cmd, "--expect", "decrease", "--min-change", "2", "--after", "7d"]
        ns_s = R._parser().parse_args(["--root", td, "record", "--id", "sel", "--question", "q",
                                       "--metric", "queries[query=best hiking boots].avg_position",
                                       *common])
        ns_i = R._parser().parse_args(["--root", td, "record", "--id", "idx", "--question", "q",
                                       "--metric", "queries.0.avg_position", *common])
        rs, ri = R.record(ns_s), R.record(ns_i)
        check("both baselines read 8.0 today",
              rs.get("recorded", {}).get("baseline") == 8.0
              and ri.get("recorded", {}).get("baseline") == 8.0, f"{rs} {ri}"[:200])
        payload.write_text(json.dumps({"queries": [
            {"query": "rain jacket", "avg_position": 15.0},
            {"query": "camp stove", "avg_position": 18.0},
            {"query": "tent", "avg_position": 25.0},
            {"query": "best hiking boots", "avg_position": 4.0}]}), encoding="utf-8")
        cs = R.check(R._parser().parse_args(["--root", td, "check", "--id", "sel", "--force"]))
        ci = R.check(R._parser().parse_args(["--root", td, "check", "--id", "idx", "--force"]))
        check("the selector compares the SAME row: 8.0 -> 4.0, confirmed",
              cs.get("value") == 4.0 and cs.get("delta") == -4.0 and cs.get("state") == "confirmed",
              str(cs)[:200])
        check("CONTROL the index compares a different row: 8.0 -> 15.0, 'refuted'",
              ci.get("value") == 15.0 and ci.get("state") == "refuted",
              "this is the silent failure the selector exists to prevent")
        bad = R._parser().parse_args(["--root", td, "record", "--id", "bad", "--question", "q",
                                      "--metric", "queries[query=x", "--baseline", "1",
                                      *common])
        rb = R.record(bad)
        check("a malformed path is refused at record time, even with --baseline given",
              rb.get("control_failed") is True and "not a valid path" in rb.get("reason", ""),
              str(rb)[:160])

    print("\nquoting: a stored command is round-tripped through a string:")
    check("a quoted argument survives",
          __import__("shlex").split("serp.py 'red running shoes' --count 20")[1] == "red running shoes",
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
