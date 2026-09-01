#!/usr/bin/env python3
"""remeasure.py - hypotheses with baselines, re-checked by re-running the SAME command.

A change ships and the honest question is "did it work". Answering that later is
where SEO programs quietly go wrong, in four specific ways this file exists to
prevent:

1. **Re-measuring with a DIFFERENT command.** Diffing two extraction regimes
   manufactures change. This skill has already produced a fake "100% page-1
   churn" exactly that way. So the argv is stored WITH the baseline and a
   re-check that does not match it is REFUSED, not quietly compared.

2. **Deciding the expected direction afterwards.** A hypothesis with no
   pre-registered direction and threshold is unfalsifiable: whatever comes back
   gets narrated into a result. Both are required at record time.

3. **Checking too early.** Crawl and ranking signals move on multi-week scales,
   and a check at day three reads noise as an answer. Every hypothesis carries a
   `not_before` and `check` refuses until it passes.

4. **Reading "cannot measure" as "no change".** The most expensive of the four,
   because it is invisible: a provider that is throttled and a metric that did
   not move produce the same empty screen. A re-check that cannot obtain a value
   returns `unmeasured`, never `no_change`.

    remeasure.py record --id guides-crawl --question "did bingbot start fetching /guides/?" \\
        --cmd "crawllog.py scan --glob '/var/log/caddy/*' --path /guides/" \\
        --metric bots.bingbot.hits --baseline 0 --expect increase --min-change 5 --after 21d
    remeasure.py due
    remeasure.py check --id guides-crawl
    remeasure.py list
    remeasure.py control

State lives in `.seo/remeasure.json` beside the rest. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402

HERE = Path(__file__).resolve().parent
DIRECTIONS = ("increase", "decrease", "unchanged")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_root(start: str | None = None) -> Path:
    p = Path(start or ".").resolve()
    for cand in [p, *p.parents]:
        if (cand / ".seo").is_dir():
            return cand
    return p


def store_path(root: str | None) -> Path:
    d = find_root(root) / ".seo"
    d.mkdir(parents=True, exist_ok=True)
    return d / "remeasure.json"


def load(root: str | None) -> dict:
    p = store_path(root)
    if not p.exists():
        return {"hypotheses": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d.setdefault("hypotheses", {})
        return d
    except Exception:                                             # noqa: BLE001
        return {"hypotheses": {}}


def save(root: str | None, data: dict) -> None:
    store_path(root).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")


_DUR = re.compile(r"^(\d+)([dwm])$")


def parse_after(s: str) -> int | None:
    """`21d` / `3w` / `2m` -> days. Returns None if it is not a duration."""
    m = _DUR.match((s or "").strip().lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"d": 1, "w": 7, "m": 30}[unit]


def dig(obj, path: str, missing_is_zero: bool = False):
    """`a.b.0.c` out of nested JSON. Returns (value, error).

    A missing key is an ERROR, never a zero. "the metric is not in the output"
    and "the metric is zero" are different states, and collapsing them is how a
    re-check reports a dramatic drop that never happened.

    `missing_is_zero` is the OPT-IN for the honest exception: some outputs are
    sparse maps where absence really does mean zero - `crawllog.py`'s
    `top_silos` omits a silo the bot never touched, and `/guides` being absent
    is precisely the finding. It must be a decision recorded WITH the hypothesis
    rather than a default, because as a default it silently converts every
    renamed key and every failed read into a zero."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                if missing_is_zero:
                    return 0, None
                return None, f"no key {part!r} at {path!r}"
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None, f"no index {part!r} at {path!r}"
        else:
            return None, f"cannot descend into {type(cur).__name__} at {part!r}"
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None, f"{path!r} is {type(cur).__name__}, not a number"
    return cur, None


def run_measurement(cmd: str, timeout: int = 900) -> dict:
    """Run a skill command and return its JSON. The argv is the identity of the
    measurement, so it is echoed back for the caller to compare."""
    # ⚠ shlex, not .split(). A stored command is round-tripped through a string,
    # and a whitespace split turns `serp.py 'cs 1.6 online'` into three
    # arguments - the measurement then fails with an argparse usage message,
    # which `record` correctly refuses on but which reads as a broken tool
    # rather than a quoting bug.
    try:
        parts = shlex.split(cmd)
    except ValueError as e:
        return {"ok": False, "error": f"unparseable command ({e})"}
    if not parts:
        return {"ok": False, "error": "empty command"}
    script = HERE / parts[0]
    argv = ([sys.executable, str(script)] + parts[1:]) if script.exists() else parts
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"no result in {timeout}s"}
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    body = (p.stdout or "").strip()
    if not body:
        return {"ok": False, "error": (p.stderr or "no output").strip()[:300]}
    try:
        return {"ok": True, "data": json.loads(body)}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output: {body[:200]}"}


# ------------------------------------------------------------------- commands
def record(a) -> dict:
    days = parse_after(a.after)
    if days is None:
        return refuse("remeasure-record",
                      f"--after {a.after!r} is not a duration (use 14d / 3w / 2m)")
    if a.expect not in DIRECTIONS:
        return refuse("remeasure-record", f"--expect must be one of {DIRECTIONS}")

    baseline, source, err = a.baseline, "given", None
    if baseline is None:
        got = run_measurement(a.cmd, a.timeout)
        if not got["ok"]:
            return refuse("remeasure-record",
                          f"cannot take a baseline now: {got['error']}",
                          hint=("a hypothesis with no baseline is not falsifiable - fix "
                                "the measurement first, or pass --baseline explicitly"))
        baseline, err = dig(got["data"], a.metric,
                            getattr(a, "missing_is_zero", False))
        source = "measured"
        if err:
            return refuse("remeasure-record", f"metric {a.metric!r} not found: {err}",
                          hint="run the command yourself and copy the exact JSON path")

    d = load(a.root)
    if a.id in d["hypotheses"] and not a.force:
        return refuse("remeasure-record",
                      f"{a.id!r} already exists - re-recording would overwrite the "
                      f"baseline, which destroys the only thing that makes the later "
                      f"check meaningful",
                      existing=d["hypotheses"][a.id], hint="pass --force if you mean it")

    row = {
        "id": a.id, "question": a.question, "cmd": a.cmd, "metric": a.metric,
        "baseline": baseline, "baseline_source": source, "recorded_at": now(),
        "expect": a.expect, "min_change": a.min_change,
        "missing_is_zero": bool(getattr(a, "missing_is_zero", False)),
        "not_before": (date.today() + timedelta(days=days)).isoformat(),
        "after": a.after, "note": a.note, "state": "open", "checks": [],
    }
    d["hypotheses"][a.id] = row
    save(a.root, d)
    return {"ok": True, "check": "remeasure-record", "recorded": row,
            "note": ("The direction and threshold are fixed NOW, before the answer is "
                     "known. That is what makes the later check a test rather than a "
                     "narration of whatever turned up.")}


def _verdict(row: dict, value: float) -> tuple[str, str]:
    base, mind, exp = row["baseline"], row["min_change"], row["expect"]
    delta = value - base
    moved = abs(delta) >= mind
    if exp == "unchanged":
        return (("confirmed", f"moved {delta:+g}, within the {mind:g} tolerance")
                if not moved else
                ("refuted", f"moved {delta:+g}, beyond the {mind:g} tolerance"))
    if not moved:
        return "no_change", (f"moved {delta:+g}, under the {mind:g} threshold set when "
                             f"the hypothesis was recorded")
    wanted_up = exp == "increase"
    if (delta > 0) == wanted_up:
        return "confirmed", f"moved {delta:+g} ({exp} predicted)"
    return "refuted", f"moved {delta:+g}, the OPPOSITE of the predicted {exp}"


def check(a) -> dict:
    d = load(a.root)
    row = d["hypotheses"].get(a.id)
    if not row:
        return refuse("remeasure-check", f"no hypothesis {a.id!r}",
                      known=sorted(d["hypotheses"]))

    today = date.today().isoformat()
    if today < row["not_before"] and not a.force:
        return refuse("remeasure-check",
                      f"not due until {row['not_before']} (recorded {row['recorded_at'][:10]}, "
                      f"window {row['after']}). Checking early reads noise as an answer.",
                      id=a.id, question=row["question"],
                      hint="pass --force only if you have a reason the window changed")

    if a.cmd and a.cmd != row["cmd"]:
        return refuse("remeasure-check",
                      "the command does not match the one the baseline was taken with. "
                      "Diffing two extraction regimes manufactures change - this skill "
                      "has already produced a fake '100% page-1 churn' that way.",
                      baseline_cmd=row["cmd"], given=a.cmd)

    got = run_measurement(row["cmd"], a.timeout)
    if not got["ok"]:
        # ⚠ THE ONE THAT LOOKS LIKE A RESULT. A throttled provider and a metric
        # that did not move produce the same empty screen.
        entry = {"at": now(), "state": "unmeasured", "reason": got["error"]}
        row["checks"].append(entry)
        save(a.root, d)
        return {"ok": False, "check": "remeasure-check", "id": a.id, "state": "unmeasured",
                "question": row["question"], "reason": got["error"],
                "note": ("UNMEASURED is not NO CHANGE. The hypothesis stays open and "
                         "nothing about it has been learned.")}

    value, err = dig(got["data"], row["metric"], row.get("missing_is_zero", False))
    if err:
        entry = {"at": now(), "state": "unmeasured", "reason": err}
        row["checks"].append(entry)
        save(a.root, d)
        return {"ok": False, "check": "remeasure-check", "id": a.id, "state": "unmeasured",
                "question": row["question"], "reason": err,
                "note": ("the metric path is missing from the output - that is a changed "
                         "tool or a failed read, NEVER a value of zero")}

    verdict, why = _verdict(row, value)
    entry = {"at": now(), "state": verdict, "value": value,
             "baseline": row["baseline"], "delta": value - row["baseline"], "why": why}
    row["checks"].append(entry)
    if verdict in ("confirmed", "refuted"):
        row["state"] = verdict
    save(a.root, d)
    return {"ok": True, "check": "remeasure-check", "id": a.id,
            "question": row["question"], "state": verdict,
            "baseline": row["baseline"], "value": value,
            "delta": value - row["baseline"], "expected": row["expect"],
            "min_change": row["min_change"], "why": why,
            "measured_with": row["cmd"], "metric": row["metric"],
            "note": ("`no_change` means the metric moved less than the threshold FIXED "
                     "WHEN THE HYPOTHESIS WAS RECORDED - it is a real answer, not a "
                     "missing one. `unmeasured` is the missing one.")}


def due(a) -> dict:
    d = load(a.root)
    today = date.today().isoformat()
    rows = [r for r in d["hypotheses"].values() if r["state"] == "open"]
    ready = [r for r in rows if r["not_before"] <= today]
    waiting = [r for r in rows if r["not_before"] > today]
    return {
        "ok": True, "check": "remeasure-due", "today": today,
        "ready": [{"id": r["id"], "question": r["question"], "due": r["not_before"],
                   "baseline": r["baseline"], "expect": r["expect"],
                   "cmd": r["cmd"]} for r in sorted(ready, key=lambda r: r["not_before"])],
        "waiting": [{"id": r["id"], "question": r["question"], "due": r["not_before"],
                     "days_left": (date.fromisoformat(r["not_before"]) - date.today()).days}
                    for r in sorted(waiting, key=lambda r: r["not_before"])],
        "closed": [{"id": r["id"], "state": r["state"], "question": r["question"]}
                   for r in d["hypotheses"].values() if r["state"] != "open"],
        "note": ("`waiting` is not a backlog. A crawl or ranking hypothesis checked "
                 "early reads noise as an answer, which is worse than not checking."),
    }


def listing(a) -> dict:
    d = load(a.root)
    return {"ok": True, "check": "remeasure-list",
            "count": len(d["hypotheses"]),
            "hypotheses": sorted(d["hypotheses"].values(),
                                 key=lambda r: r["recorded_at"])}


# -------------------------------------------------------------------- control
def run_control() -> dict:
    """Prove the verdict logic and the refusals discriminate, offline."""
    c = Controls("remeasure-control")

    def row(**kw):
        base = {"baseline": 100.0, "min_change": 10.0, "expect": "increase"}
        base.update(kw)
        return base

    c.check("a_move_in_the_predicted_direction_confirms",
            _verdict(row(), 130)[0] == "confirmed")
    c.check("a_move_the_other_way_refutes",
            _verdict(row(), 60)[0] == "refuted", str(_verdict(row(), 60)))
    c.check("a_move_under_the_threshold_is_no_change",
            _verdict(row(), 105)[0] == "no_change")
    c.check("a_decrease_hypothesis_is_confirmed_by_a_decrease",
            _verdict(row(expect="decrease"), 50)[0] == "confirmed")
    c.check("a_decrease_hypothesis_is_refuted_by_an_increase",
            _verdict(row(expect="decrease"), 150)[0] == "refuted")
    c.check("an_unchanged_hypothesis_is_confirmed_by_stability",
            _verdict(row(expect="unchanged"), 103)[0] == "confirmed")
    c.check("an_unchanged_hypothesis_is_refuted_by_movement",
            _verdict(row(expect="unchanged"), 140)[0] == "refuted")
    c.check("the_three_verdicts_are_distinct",
            len({_verdict(row(), 130)[0], _verdict(row(), 60)[0],
                 _verdict(row(), 105)[0]}) == 3)
    c.check("every_verdict_carries_its_reasoning",
            all(_verdict(row(), v)[1] for v in (130, 60, 105)))

    # A missing metric is NOT a zero. This is the one that reports a dramatic
    # collapse that never happened.
    v, err = dig({"a": {"b": 5}}, "a.b")
    c.check("a_present_metric_is_read", v == 5 and err is None)
    v2, err2 = dig({"a": {}}, "a.b")
    c.check("a_missing_metric_is_an_error_not_zero", v2 is None and bool(err2), str(err2))
    v3, err3 = dig({"a": {"b": None}}, "a.b")
    c.check("a_null_metric_is_an_error_not_zero", v3 is None and bool(err3))
    v4, err4 = dig({"a": {"b": True}}, "a.b")
    c.check("a_boolean_is_not_a_number", v4 is None and bool(err4),
            "True would arithmetic as 1 and silently become a metric")
    v5, _ = dig({"a": [{"b": 7}]}, "a.0.b")
    c.check("list_indices_work", v5 == 7)
    v6, err6 = dig({"a": {"b": 0}}, "a.b")
    c.check("a_genuine_zero_is_still_read", v6 == 0 and err6 is None,
            "the guard must not swallow a real measured zero")
    v7, err7 = dig({"a": {}}, "a.b", missing_is_zero=True)
    c.check("sparse_maps_can_opt_into_missing_meaning_zero",
            v7 == 0 and err7 is None,
            "crawllog's top_silos omits a silo the bot never touched, and that "
            "absence IS the finding")
    v8, err8 = dig({"a": {}}, "a.b")
    c.check("but_it_is_off_by_default", v8 is None and bool(err8),
            "as a default it converts every renamed key into a zero")

    q = run_measurement("controls.py control")
    c.check("a_simple_command_runs", q.get("ok") is True, str(q.get("error"))[:120])
    # The quoting round-trip. Without shlex this reports 3 tokens, not 2.
    import shlex as _sh
    c.check("a_quoted_argument_survives_the_round_trip",
            _sh.split("serp.py 'cs 1.6 online' --count 20")[1] == "cs 1.6 online",
            "a whitespace split makes this three arguments and the measurement fails")
    c.check("an_unbalanced_quote_is_an_error_not_a_crash",
            run_measurement("serp.py 'unclosed").get("ok") is False)

    c.check("durations_parse", parse_after("21d") == 21 and parse_after("3w") == 21
            and parse_after("2m") == 60)
    c.check("a_non_duration_is_refused", parse_after("soon") is None)

    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        (Path(td) / ".seo").mkdir()

        class A:
            root = td
            id = "h1"
            question = "did it move?"
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

        r = record(A())
        c.check("recording_stores_the_command_with_the_baseline",
                r["ok"] and r["recorded"]["cmd"] == "controls.py control")
        c.check("the_direction_is_fixed_at_record_time",
                r["recorded"]["expect"] == "increase")
        again = record(A())
        c.check("re_recording_refuses_rather_than_overwriting_the_baseline",
                again.get("control_failed") is True, str(again)[:120])

        class B(A):
            after = "whenever"
            id = "h2"
        c.check("a_bad_window_is_refused", record(B()).get("control_failed") is True)

        class C(A):
            expect = "sideways"
            id = "h3"
        c.check("an_invalid_direction_is_refused", record(C()).get("control_failed") is True)

        class D(A):
            force = False
        chk = check(D())
        c.check("checking_before_the_window_refuses", chk.get("control_failed") is True,
                str(chk.get("reason"))[:100])
        c.check("the_early_refusal_names_the_due_date",
                "not due until" in str(chk.get("reason", "")))

        class E(A):
            force = True
            cmd = "some other command"
        mism = check(E())
        c.check("a_changed_command_is_refused_not_compared",
                mism.get("control_failed") is True, str(mism.get("reason"))[:120])
        c.check("the_mismatch_refusal_shows_both_commands",
                bool(mism.get("baseline_cmd")) and bool(mism.get("given")))

        class F(A):
            force = True
            cmd = None
            metric = "checks.no_such_key_at_all"
        miss = check(F())
        c.check("a_missing_metric_reports_unmeasured_not_no_change",
                miss.get("state") == "unmeasured", str(miss)[:140])
        c.check("unmeasured_is_explicitly_not_no_change",
                "NEVER a value of zero" in str(miss.get("note", ""))
                or "not NO CHANGE" in str(miss.get("note", "")))

        # ARGPARSE ITSELF, not a hand-built namespace. The two are not the same
        # test: a fake object cannot collide, and the collision is the bug.
        ns = _parser().parse_args(["record", "--id", "x", "--question", "q",
                                   "--cmd", "crawllog.py scan --days 14",
                                   "--metric", "a.b", "--expect", "increase",
                                   "--min-change", "1", "--after", "7d"])
        c.check("the_measurement_command_survives_argparse",
                getattr(ns, "cmd", None) == "crawllog.py scan --days 14",
                f"got {getattr(ns, 'cmd', None)!r} - a subparser dest of 'cmd' "
                f"overwrites --cmd with the subcommand name")
        c.check("the_subcommand_is_on_its_own_attribute",
                getattr(ns, "action", None) == "record", str(getattr(ns, "action", None)))

        d = due(A())
        c.check("an_unripe_hypothesis_waits_rather_than_showing_ready",
                any(x["id"] == "h1" for x in d["waiting"]) and not d["ready"])
        c.check("the_wait_reports_days_left",
                isinstance(d["waiting"][0]["days_left"], int))
    return c.verdict()


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="repo root holding .seo/")
    # ⚠ dest="action", NOT "cmd". `record --cmd` is the measurement command, and
    # a subparser dest of "cmd" silently overwrites it - the dispatch then looks
    # up the whole crawllog invocation as if it were a subcommand name and dies
    # with a KeyError containing the user's own command line. A hand-built
    # namespace in a control cannot catch this, because it never runs argparse;
    # `_parse` below does.
    sub = ap.add_subparsers(dest="action", required=True)

    r = sub.add_parser("record", help="register a falsifiable hypothesis with a baseline")
    r.add_argument("--id", required=True)
    r.add_argument("--question", required=True, help="the question, in plain words")
    r.add_argument("--cmd", required=True,
                   help="the skill command that answers it, e.g. \"bing.py pages\"")
    r.add_argument("--metric", required=True, help="dotted JSON path into that output")
    r.add_argument("--baseline", type=float,
                   help="omit to measure it now (preferred - it uses the same command)")
    r.add_argument("--expect", required=True, choices=list(DIRECTIONS))
    r.add_argument("--min-change", type=float, required=True,
                   help="movement below this is `no_change`; fixed NOW, not later")
    r.add_argument("--after", required=True, help="14d / 3w / 2m")
    r.add_argument("--missing-is-zero", action="store_true",
                   help="the metric lives in a SPARSE map where absence genuinely means "
                        "zero (e.g. crawllog top_silos). Off by default: as a default it "
                        "turns every renamed key and failed read into a zero.")
    r.add_argument("--note")
    r.add_argument("--force", action="store_true", help="overwrite an existing baseline")
    r.add_argument("--timeout", type=int, default=900)

    ck = sub.add_parser("check", help="re-run the SAME command and judge the hypothesis")
    ck.add_argument("--id", required=True)
    ck.add_argument("--cmd", help="asserted command - refused if it differs from the baseline's")
    ck.add_argument("--force", action="store_true", help="check before the window is up")
    ck.add_argument("--timeout", type=int, default=900)

    sub.add_parser("due", help="what is ready to re-check, and what is still waiting")
    sub.add_parser("list", help="every hypothesis")
    sub.add_parser("control", help="prove the verdict logic and refusals discriminate")
    return ap


def main(argv=None) -> int:
    ap = _parser()
    a = ap.parse_args(argv)
    fn = {"record": record, "check": check, "due": due, "list": listing,
          "control": lambda _a: run_control()}[a.action]
    out = fn(a)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
