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
import os
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controls import Controls, refuse  # noqa: E402
import stats  # noqa: E402

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


# ---------------------------------------------------------------- metric paths
# A metric path is dotted segments; a segment is a NAME, one or more row
# SELECTORS, or a name followed by selectors:
#
#     queries[query=best hiking boots].avg_position
#     \_____/\_______________________/ \__________/
#      name    selector: field=value      name
#
#     segment   := name? selector*          (at least one of the two)
#     name      := dict key | list index (0, -1) | legacy row identity (bingbot)
#     selector  := '[' field '=' value ']'  exact, case-sensitive string match
#     value     := "quoted"  -> \" and \\ are the only escapes
#                | unquoted  -> everything up to the closing ], with \] \\ \" escapes
#
# Dots and spaces inside [...] are literal, so `[query=a.b c]` needs no quotes;
# quoting is for a value containing ] or ", or to make leading/trailing spaces
# and the empty string visible (`[query=""]`). Outside brackets \. \[ \\ escape a
# key that contains those characters. Several selectors on ONE segment must all
# hold on the same row (`rows[query=x][page=/a]`) - the way to disambiguate a
# selector that matches two rows. A number or bool field is compared by its JSON
# text: `[rank=3]` matches 3, never 3.0 or "03"; `[code=007]` matches the string
# "007" and not the number 7.

_ESC_NAME = ".[\\"
_ESC_FIELD = "=]\\"
_ESC_VALUE = '"]\\'


def _read_until(path: str, i: int, stops: str, escapable: str, what: str) -> tuple[str, int]:
    out = []
    while i < len(path) and path[i] not in stops:
        ch = path[i]
        if ch == "\\":
            if i + 1 >= len(path) or path[i + 1] not in escapable:
                raise ValueError(f"bad escape at position {i} in {what} "
                                 f"(only {' '.join(escapable)} may follow a backslash)")
            out.append(path[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), i


def _read_selector(path: str, i: int) -> tuple[tuple[str, str], int]:
    """`path[i]` is '['. Returns ((field, value), index after ']')."""
    open_at = i
    field, i = _read_until(path, i + 1, "=]", _ESC_FIELD, "a selector field")
    if i >= len(path) or path[i] != "=":
        raise ValueError(f"selector at position {open_at} has no '=' - write [field=value]")
    if not field:
        raise ValueError(f"selector at position {open_at} has an empty field name")
    i += 1
    if i < len(path) and path[i] == '"':
        buf, i = [], i + 1
        while True:
            if i >= len(path):
                raise ValueError(f"unterminated quote in the selector at position {open_at}")
            ch = path[i]
            if ch == "\\":
                if i + 1 >= len(path) or path[i + 1] not in '"\\':
                    raise ValueError(f"bad escape at position {i} in a quoted value "
                                     '(only \\" and \\\\ are escapes)')
                buf.append(path[i + 1])
                i += 2
                continue
            if ch == '"':
                i += 1
                break
            buf.append(ch)
            i += 1
        value = "".join(buf)
        if i >= len(path) or path[i] != "]":
            raise ValueError(f"expected ']' after the quoted value at position {i}")
    else:
        value, i = _read_until(path, i, "]", _ESC_VALUE, "a selector value")
        if i >= len(path):
            raise ValueError(f"selector at position {open_at} is not closed with ']'")
    return (field, value), i + 1


def parse_path(path: str) -> list[tuple[str | None, tuple[tuple[str, str], ...]]]:
    """Parse a metric path into [(name or None, ((field, value), ...)), ...].
    Raises ValueError naming the position - a malformed path must be refused
    when it is RECORDED, not discovered as `unmeasured` three weeks later."""
    if not isinstance(path, str) or not path:
        raise ValueError("empty metric path")
    segs, i = [], 0
    while True:
        seg_at = i
        name, i = _read_until(path, i, ".[", _ESC_NAME, "a key")
        had_name = i > seg_at
        sels = []
        while i < len(path) and path[i] == "[":
            sel, i = _read_selector(path, i)
            sels.append(sel)
        if not had_name and not sels:
            raise ValueError(f"empty segment at position {seg_at}")
        segs.append((name if had_name else None, tuple(sels)))
        if i == len(path):
            return segs
        if path[i] != ".":
            raise ValueError(f"unexpected {path[i]!r} at position {i} - a selector "
                             "must be followed by '.', '[' or the end of the path")
        i += 1
        if i == len(path):
            raise ValueError("the path ends with '.'")


def _quote_value(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt_sels(sels) -> str:
    """Canonical text of a segment's selectors: every value quoted, so the
    same row written `[q=a b]` or `[q="a b"]` prints - and compares - alike."""
    def field(f: str) -> str:
        return "".join("\\" + ch if ch in _ESC_FIELD else ch for ch in f)
    return "".join(f"[{field(f)}={_quote_value(v)}]" for f, v in sels)


def _sel_text(v) -> str | None:
    """The string a selector compares against. JSON text for numbers and bools,
    so a numeric-looking field is selectable at all; None/dict/list never match -
    a row with no identity value cannot be the row a hypothesis named."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return None


def resolve(obj, path: str, missing_is_zero: bool = False):
    """`dig` with the error's KIND: (value, error, kind), kind one of None,
    'syntax', 'missing', 'no_match', 'ambiguous', 'type'. `check` needs the kind:
    an ambiguous selector is a refusal (the path no longer names one row), a
    miss is `unmeasured`, and the two must not read the same."""
    try:
        segs = parse_path(path)
    except ValueError as e:
        return None, f"malformed metric path {path!r}: {e}", "syntax"
    # missing_is_zero answers for the SPARSE MAP AT THE LEAF, never for a row a
    # selector names. A selector picks a row out of a list, and a missing row is
    # not a zero: the list is usually a top-N, so absence means "below the cut",
    # a renamed or re-cased field, a typo in the selector, or a truncated
    # response - and for a position metric a zero would read as better than #1.
    # So the flag only covers dict keys AFTER the last selector.
    last_sel = max((k for k, (_, sels) in enumerate(segs) if sels), default=-1)
    cur = obj
    for k, (part, sels) in enumerate(segs):
        if part is not None:
            if isinstance(cur, dict):
                if part not in cur:
                    if missing_is_zero and k > last_sel:
                        return 0, None, None
                    return None, f"no key {part!r} at {path!r}", "missing"
                cur = cur[part]
            elif isinstance(cur, list):
                # A positional index into a list that is SORTED BY VALUE is a
                # measurement that silently re-points itself: `bots.0` was bingbot
                # today and is whatever ranks first at re-check time, so the verdict
                # compares two different bots and calls it a change. So a non-numeric
                # segment addresses the element whose own identity field matches it -
                # which is also what this script's own --help has always documented
                # (`bots.bingbot.hits`) and what it did not implement until
                # 2026-09-01, refusing the documented example as "no index".
                if part.isdigit() or (part[:1] == "-" and part[1:].isdigit()):
                    try:
                        cur = cur[int(part)]
                    except IndexError:
                        return None, f"no index {part!r} at {path!r}", "missing"
                else:
                    match = [el for el in cur if isinstance(el, dict) and any(
                        str(el.get(f, "")).lower() == part.lower()
                        for f in ("key", "id", "name", "bot", "slug"))]
                    if len(match) != 1:
                        return None, (f"{part!r} matches {len(match)} elements at {path!r} - "
                                      "a metric must name exactly one"), (
                                          "ambiguous" if match else "no_match")
                    cur = match[0]
            else:
                return None, f"cannot descend into {type(cur).__name__} at {part!r}", "type"
        if not sels:
            continue
        where = f"{part or ''}{_fmt_sels(sels)}"
        if not isinstance(cur, list):
            return None, (f"selector {where} needs a list of rows, found "
                          f"{type(cur).__name__}"), "type"
        hits = [j for j, el in enumerate(cur) if isinstance(el, dict)
                and all(f in el and _sel_text(el[f]) == v for f, v in sels)]
        if not hits:
            return None, (f"no row matches {where} among {len(cur)} rows at {path!r} - "
                          "a row that is not there is UNMEASURED, never a zero"), "no_match"
        if len(hits) > 1:
            shown = ", ".join(map(str, hits[:8])) + (", ..." if len(hits) > 8 else "")
            return None, (f"{where} matches {len(hits)} rows (indices {shown}) at {path!r} - "
                          "refusing to pick one; add another [field=value] to the same "
                          "segment to name exactly one row"), "ambiguous"
        cur = cur[hits[0]]
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None, f"{path!r} is {type(cur).__name__}, not a number", "type"
    return cur, None, None


def dig(obj, path: str, missing_is_zero: bool = False):
    """The metric at `path` out of nested JSON (grammar above). Returns
    (value, error).

    A missing key is an ERROR, never a zero. "the metric is not in the output"
    and "the metric is zero" are different states, and collapsing them is how a
    re-check reports a dramatic drop that never happened.

    `missing_is_zero` is the OPT-IN for the honest exception: some outputs are
    sparse maps where absence really does mean zero - `crawllog.py`'s
    `top_silos` omits a silo the bot never touched, and `/guides` being absent
    is precisely the finding. It must be a decision recorded WITH the hypothesis
    rather than a default, because as a default it silently converts every
    renamed key and every failed read into a zero. It never zeroes a row a
    selector failed to find (see `resolve`)."""
    value, err, _ = resolve(obj, path, missing_is_zero)
    return value, err


def run_measurement(cmd: str, timeout: int = 900) -> dict:
    """Run a skill command and return its JSON. The argv is the identity of the
    measurement, so it is echoed back for the caller to compare."""
    # ⚠ shlex, not .split(). A stored command is round-tripped through a string,
    # and a whitespace split turns `serp.py 'red running shoes'` into three
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
def same_experiment(cmd: str, metric: str) -> tuple:
    """The identity of an experiment is (what it runs, what it reads).

    Not the id - an id is a label somebody chooses, and two people describing
    the same intervention choose two labels. `shlex` normalisation matters
    because the same command differs by quoting and `~` expansion between
    sessions: `--ssh-key ~/.ssh/k` and `--ssh-key /home/u/.ssh/k` are one
    experiment.
    """
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    norm = [os.path.expanduser(t) for t in parts]
    return (tuple(norm), *_metric_identity(metric))


def _metric_identity(metric: str) -> tuple:
    """What a metric path READS, for duplicate detection: the leaf name plus
    every row selector, canonically quoted.

    The leaf alone was the identity, so that `bots.0.hits` and `bots.bingbot.hits`
    - one number addressed two ways - collide. A selector cannot be dropped the
    same way: `queries[query=a].avg_position` and `queries[query=b].avg_position`
    share a leaf and are two different experiments, and splitting on '.' also
    cut a selector value like `a.b c` in half. A positional index carries no
    identity, so it stays out."""
    try:
        segs = parse_path(metric)
    except ValueError:
        return (metric, ())
    leaf = segs[-1][0] if segs[-1][0] is not None else ""
    return (leaf, tuple(_fmt_sels(sels) for _, sels in segs if sels))


def record(a) -> dict:
    days = parse_after(a.after)
    if days is None:
        return refuse("remeasure-record",
                      f"--after {a.after!r} is not a duration (use 14d / 3w / 2m)")
    if a.expect not in DIRECTIONS:
        return refuse("remeasure-record", f"--expect must be one of {DIRECTIONS}")
    for flag, pth in (("--metric", a.metric), ("--denominator", getattr(a, "denominator", None))):
        if pth is None:
            continue
        try:
            parse_path(pth)
        except ValueError as e:
            return refuse("remeasure-record", f"{flag} {pth!r} is not a valid path: {e}",
                          hint=("dotted keys, numeric indices, and [field=value] row "
                                'selectors; quote a value holding ] or " as [field="..."]'))

    # Checked BEFORE the baseline is measured: taking a measurement to register
    # a duplicate wastes the call and, worse, produces a SECOND baseline for one
    # intervention taken at a different moment. Two hypotheses about one change
    # can disagree in three weeks and nothing says which to believe.
    existing = load(a.root)["hypotheses"]
    mine = same_experiment(a.cmd, a.metric)
    dupes = [h for h in existing.values()
             if h.get("state") == "open" and h["id"] != a.id
             and same_experiment(h["cmd"], h["metric"]) == mine]
    if dupes and not a.force:
        return refuse("remeasure-record",
                      f"{dupes[0]['id']!r} is already open on the same command and "
                      "metric - two baselines for one intervention can disagree, and "
                      "nothing would say which to believe",
                      existing=dupes[0],
                      hint=("re-check or close the existing one. Pass --force only if "
                            "this really is a different question about the same numbers"))

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
        if getattr(a, "denominator", None):
            a.baseline_n, derr = dig(got["data"], a.denominator, False)
            if derr:
                return refuse("remeasure-record", f"denominator {a.denominator!r} not found: {derr}")
    if getattr(a, "denominator", None) and getattr(a, "baseline_n", None) is None:
        return refuse("remeasure-record", "--denominator needs --baseline-n when --baseline is given")

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
        "denominator": getattr(a, "denominator", None),
        "baseline_n": getattr(a, "baseline_n", None),
        "not_before": (date.today() + timedelta(days=days)).isoformat(),
        "after": a.after, "note": a.note, "state": "open", "checks": [],
    }
    d["hypotheses"][a.id] = row
    save(a.root, d)
    return {"ok": True, "check": "remeasure-record", "recorded": row,
            "note": ("The direction and threshold are fixed NOW, before the answer is "
                     "known. That is what makes the later check a test rather than a "
                     "narration of whatever turned up.")}


def close(a) -> dict:
    """Retire a hypothesis with a stated reason.

    There was no way to do this, so a hypothesis registered by mistake stayed
    open forever and turned up in every `due` list - which is how a queue stops
    being read. A reason is REQUIRED: closing without one is indistinguishable
    from closing because the answer was inconvenient.
    """
    d = load(a.root)
    row = d["hypotheses"].get(a.id)
    if not row:
        return refuse("remeasure-close", f"no hypothesis {a.id!r}",
                      known=sorted(d["hypotheses"]))
    if row.get("state") != "open":
        return refuse("remeasure-close",
                      f"{a.id!r} is already {row.get('state')!r}", existing=row)
    row["state"] = "closed"
    row["closed_at"] = now()
    row["closed_reason"] = a.reason
    save(a.root, d)
    return {"ok": True, "check": "remeasure-close", "closed": row,
            "note": ("closed, not deleted - the record of what was asked and why it "
                     "was dropped is the part worth keeping")}


def _rate_verdict(row: dict, x: float, n: float) -> tuple[str, str]:
    """A RATE hypothesis (`--denominator`): the move must clear min_change AND
    be outside noise - the Newcombe 95% interval of the difference must exclude
    zero. 3/10 -> 5/10 moves 0.2 and is a coin toss."""
    cmp_ = stats.compare_rates(int(row["baseline"]), int(row["baseline_n"]), int(x), int(n))
    if cmp_["verdict"] == "unknown":
        return "unmeasured", "an empty denominator is no measurement"
    delta, mind, exp = cmp_["point"], row["min_change"], row["expect"]
    lo, hi = cmp_["ci95"]
    desc = (f"{row['baseline']:g}/{row['baseline_n']:g} -> {x:g}/{n:g} "
            f"(change {delta:+.4f}, 95% CI [{lo}, {hi}])")
    if exp == "unchanged":
        return (("refuted", f"{desc}: a real move beyond the {mind:g} tolerance")
                if cmp_["verdict"] != "no_detectable_change" and abs(delta) >= mind
                else ("confirmed", f"{desc}: no move beyond noise and tolerance"))
    if cmp_["verdict"] == "no_detectable_change":
        return "no_detectable_change", f"{desc}: the interval spans zero - noise at this n"
    if abs(delta) < mind:
        return "no_change", f"{desc}: real, but under the {mind:g} threshold"
    wanted_up = exp == "increase"
    if (delta > 0) == wanted_up:
        return "confirmed", f"{desc} ({exp} predicted)"
    return "refuted", f"{desc}, the OPPOSITE of the predicted {exp}"


def _verdict(row: dict, value: float, n: float | None = None) -> tuple[str, str]:
    if row.get("denominator"):
        return _rate_verdict(row, value, n or 0)
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

    value, err, kind = resolve(got["data"], row["metric"], row.get("missing_is_zero", False))
    if kind == "ambiguous":
        # REFUSED, not compared and not zeroed: the path that named one row at
        # baseline now names several, so any value picked would be a guess about
        # which row the hypothesis meant. Still logged, still open.
        row["checks"].append({"at": now(), "state": "unmeasured", "kind": kind, "reason": err})
        save(a.root, d)
        return refuse("remeasure-check", err, id=a.id, state="unmeasured", kind=kind,
                      question=row["question"], metric=row["metric"],
                      hint=("the hypothesis stays open; close it and record one whose "
                            "selector names exactly one row"))
    if err:
        entry = {"at": now(), "state": "unmeasured", "kind": kind, "reason": err}
        row["checks"].append(entry)
        save(a.root, d)
        return {"ok": False, "check": "remeasure-check", "id": a.id, "state": "unmeasured",
                "kind": kind, "question": row["question"], "reason": err,
                "note": ("the metric path is missing from the output - that is a changed "
                         "tool or a failed read, NEVER a value of zero")}

    n = None
    if row.get("denominator"):
        n, nerr = dig(got["data"], row["denominator"], False)
        if nerr:
            entry = {"at": now(), "state": "unmeasured", "reason": f"denominator: {nerr}"}
            row["checks"].append(entry)
            save(a.root, d)
            return {"ok": False, "check": "remeasure-check", "id": a.id, "state": "unmeasured",
                    "question": row["question"], "reason": f"denominator: {nerr}"}
    verdict, why = _verdict(row, value, n)
    entry = {"at": now(), "state": verdict, "value": value, "n": n,
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

    _selector_controls(c)

    q = run_measurement("controls.py control")
    c.check("a_simple_command_runs", q.get("ok") is True, str(q.get("error"))[:120])
    # The quoting round-trip. Without shlex this reports 3 tokens, not 2.
    import shlex as _sh
    c.check("a_quoted_argument_survives_the_round_trip",
            _sh.split("serp.py 'red running shoes' --count 20")[1] == "red running shoes",
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


def _selector_controls(c: Controls) -> None:
    """Row selectors. Every expected value below is a literal written from the
    fixture by hand, never read back through `dig` - a control that derives its
    expectation from the code under test passes whatever the code does."""
    rows = {"queries": [
        {"query": "best hiking boots", "avg_position": 8.0, "clicks": 12},
        {"query": "trail map.v2 guide", "avg_position": 3.5, "clicks": 40},
        {"query": "tent", "page": "/a", "avg_position": 6.0, "rank": 3, "code": "007"},
        {"query": "tent", "page": "/b", "avg_position": 9.0, "rank": 7, "code": 7},
    ]}
    c.check("a_selector_names_a_row_by_content",
            dig(rows, "queries[query=best hiking boots].avg_position") == (8.0, None))
    c.check("dots_and_spaces_in_an_unquoted_value_are_literal",
            dig(rows, "queries[query=trail map.v2 guide].clicks") == (40, None))
    c.check("a_quoted_value_reads_the_same_row",
            dig(rows, 'queries[query="trail map.v2 guide"].clicks') == (40, None))
    c.check("a_numeric_field_is_selectable_as_a_string",
            dig(rows, "queries[rank=3].avg_position") == (6.0, None))
    c.check("a_numeric_looking_string_matches_only_the_string",
            dig(rows, "queries[code=007].avg_position") == (6.0, None)
            and dig(rows, "queries[code=7].avg_position") == (9.0, None))
    c.check("a_selector_is_exact_not_case_folded",
            dig(rows, "queries[query=Best Hiking Boots].avg_position")[0] is None)

    _, err, kind = resolve(rows, "queries[query=no such query].avg_position")
    c.check("a_zero_match_selector_is_unmeasured_not_zero",
            kind == "no_match" and bool(err), f"{kind}: {err}")
    v, _, kind = resolve(rows, "queries[query=no such query].avg_position",
                         missing_is_zero=True)
    c.check("missing_is_zero_never_invents_a_selected_row",
            v is None and kind == "no_match",
            "a top-N list omits a row below the cut; zero would read as 'better than #1'")
    v, _, _ = resolve({"queries": [{"query": "x", "by_silo": {}}]},
                      "queries[query=x].by_silo./guides", missing_is_zero=True)
    c.check("CONTROL_missing_is_zero_still_covers_the_leaf_map_after_a_selector", v == 0)

    _, err, kind = resolve(rows, "queries[query=tent].avg_position")
    c.check("a_two_match_selector_is_refused_as_ambiguous",
            kind == "ambiguous" and "2 rows" in str(err), str(err))
    c.check("a_second_selector_on_the_segment_disambiguates",
            dig(rows, "queries[query=tent][page=/b].avg_position") == (9.0, None))
    c.check("CONTROL_the_old_index_syntax_still_resolves",
            dig(rows, "queries.1.clicks") == (40, None)
            and dig(rows, "queries.-1.avg_position") == (9.0, None))

    for bad in ("queries[query=x", "queries[query].a", "queries[=x].a",
                'queries[query="x].a', 'queries[query="x"y].a', "a..b", "a.",
                "queries[query=x]y.a", "queries[query=a\qb].a"):
        _, _, kind = resolve(rows, bad)
        c.check(f"malformed_path_is_a_syntax_error:{bad}", kind == "syntax", str(kind))

    a_ = "bing.py pagequeries --url https://example.com/"
    c.check("experiments_differ_by_selector_not_just_leaf",
            same_experiment(a_, "queries[query=a].avg_position")
            != same_experiment(a_, "queries[query=b].avg_position"))
    c.check("the_same_selector_quoted_two_ways_is_one_experiment",
            same_experiment(a_, "queries[query=a.b c].avg_position")
            == same_experiment(a_, 'queries[query="a.b c"].avg_position'))

    # END TO END. The target row is at index 0 at baseline and index 3 at
    # re-check, with a different row moved into index 0. The command string is
    # byte-identical across both runs; only the file it prints changes.
    import tempfile as _tf
    base = {"queries": [
        {"query": "best hiking boots", "avg_position": 8.0},
        {"query": "rain jacket", "avg_position": 11.0},
        {"query": "camp stove", "avg_position": 20.0},
        {"query": "trail map.v2 guide", "avg_position": 30.0}]}
    later = {"queries": [
        {"query": "rain jacket", "avg_position": 15.0},
        {"query": "camp stove", "avg_position": 18.0},
        {"query": "trail map.v2 guide", "avg_position": 25.0},
        {"query": "best hiking boots", "avg_position": 4.0}]}
    with _tf.TemporaryDirectory() as td:
        (Path(td) / ".seo").mkdir()
        payload = Path(td) / "payload.json"
        payload.write_text(json.dumps(base), encoding="utf-8")
        # A script path, because run_measurement runs `<script> args` under this
        # interpreter - the same way every skill instrument is run.
        emitter = Path(td) / "emit.py"
        emitter.write_text("import sys\nprint(open(sys.argv[1]).read())\n", encoding="utf-8")
        emit = f"{shlex.quote(str(emitter))} {shlex.quote(str(payload))}"

        class S:
            root = td
            question = "did the homepage move up for the query?"
            cmd = emit
            baseline = None
            expect = "decrease"
            min_change = 2.0
            after = "7d"
            note = None
            force = False
            missing_is_zero = False
            timeout = 60

        class Sel(S):
            id = "sel"
            metric = "queries[query=best hiking boots].avg_position"

        class Idx(S):
            id = "idx"
            metric = "queries.0.avg_position"

        rs, ri = record(Sel()), record(Idx())
        c.check("selector_baseline_reads_the_target_row",
                rs.get("ok") and rs["recorded"]["baseline"] == 8.0, str(rs)[:160])
        c.check("index_baseline_reads_the_same_row_today",
                ri.get("ok") and ri["recorded"]["baseline"] == 8.0, str(ri)[:160])
        payload.write_text(json.dumps(later), encoding="utf-8")

        class SelF(Sel):
            force = True
            cmd = None

        class IdxF(Idx):
            force = True
            cmd = None

        cs, ci = check(SelF()), check(IdxF())
        c.check("a_moved_row_is_still_the_row_compared",
                cs.get("value") == 4.0 and cs.get("delta") == -4.0
                and cs.get("state") == "confirmed", str(cs)[:200])
        c.check("CONTROL_the_index_path_silently_compares_a_different_row",
                ci.get("value") == 15.0 and ci.get("state") == "refuted",
                "if this fails the fixture no longer exercises the re-sort trap")

        dup = dict(later)
        dup["queries"] = later["queries"] + [{"query": "best hiking boots", "avg_position": 5.0}]
        payload.write_text(json.dumps(dup), encoding="utf-8")

        class Amb(Sel):
            id = "amb"
            question = "same row, now duplicated"
            force = True
        record_amb = record(type("AmbRec", (Amb,), {"baseline": 8.0})())
        ca = check(type("AmbChk", (Amb,), {"cmd": None})())
        c.check("a_selector_that_now_matches_two_rows_refuses_at_check",
                record_amb.get("ok") and ca.get("control_failed") is True
                and ca.get("kind") == "ambiguous" and "2 rows" in str(ca.get("reason")),
                str(ca)[:200])
        c.check("and_the_refused_hypothesis_stays_open",
                load(td)["hypotheses"]["amb"]["state"] == "open")

        payload.write_text(json.dumps(base), encoding="utf-8")
        r_nm = record(type("NoMatch", (S,), {
            "id": "nm", "metric": "queries[query=absent row].avg_position"})())
        c.check("a_zero_match_selector_refuses_a_baseline",
                r_nm.get("control_failed") is True and "no row matches" in str(r_nm.get("reason")),
                str(r_nm)[:160])


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
    r.add_argument("--metric", required=True,
                   help=("dotted JSON path into that output. Name a row of a re-sorted "
                         "list by content, not position: 'queries[query=red shoes].clicks', "
                         "'rows[q=\"a.b ] c\"][page=/x].ctr'. Zero matching rows is "
                         "unmeasured; two or more is refused"))
    r.add_argument("--baseline", type=float,
                   help="omit to measure it now (preferred - it uses the same command)")
    r.add_argument("--expect", required=True, choices=list(DIRECTIONS))
    r.add_argument("--min-change", type=float, required=True,
                   help="movement below this is `no_change`; fixed NOW, not later")
    r.add_argument("--after", required=True, help="14d / 3w / 2m")
    r.add_argument("--missing-is-zero", action="store_true",
                   help="the metric lives in a SPARSE map where absence genuinely means "
                        "zero (e.g. crawllog top_silos). Off by default: as a default it "
                        "turns every renamed key and failed read into a zero. Never zeroes "
                        "a row a [field=value] selector failed to find.")
    r.add_argument("--denominator",
                   help="dotted path to the metric's DENOMINATOR (e.g. answers_seen for "
                        "answers_citing_us): the check then needs a statistically real move, "
                        "not just one past --min-change (which is then on the RATE)")
    r.add_argument("--baseline-n", type=float, help="the denominator, when --baseline is given")
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
    cl = sub.add_parser("close", help="retire a hypothesis, with a stated reason")
    cl.add_argument("--id", required=True)
    cl.add_argument("--reason", required=True,
                    help="why it is being dropped - required, and kept in the record")

    sub.add_parser("control", help="prove the verdict logic and refusals discriminate")
    return ap


def main(argv=None) -> int:
    ap = _parser()
    a = ap.parse_args(argv)
    fn = {"record": record, "close": close, "check": check, "due": due, "list": listing,
          "control": lambda _a: run_control()}[a.action]
    out = fn(a)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
