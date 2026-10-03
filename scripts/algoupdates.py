#!/usr/bin/env python3
"""algoupdates.py - Google's update calendar, synced from Google's own dashboard.

`assets/google-updates.json` is what `decay.py --updates` and `drift.py
--updates` correlate a measurement window against. It was compiled by hand under
the belief "there is no API", and two things followed from that belief:

  1. It went STALE. It stopped at 2026-06-30 and missed the August and
     September 2026 spam updates - and silence about a window reads as "no
     update near this date", which is the opposite of unknown.
  2. It had NO END DATES. Rollout ends lived in `notes` prose ("Completed June
     2"), while both consumers windowed on an `ended` field no entry carried.
     A core update that began before a window and finished inside it never
     correlated, and one still rolling out was one day long.

The belief was wrong. Probed 2026-10-03:

    status.search.google.com/incidents.json
        JSON, the ~10 most recent incidents across every product, with exact
        `begin` / `end` timestamps (no `end` = still rolling out), the
        product (`service_name`: Ranking, Serving, ...) and Google's own text.
    status.search.google.com/products/<id>/history
        HTML, EVERY incident for a product back to 2021 (42 for Ranking), each
        with its incident id, start day and a duration ("2 days, 16 hours").
        A row with no duration is still open.
    status.search.google.com/incidents/<id>.json        -> 404 (does not exist)

So `sync` merges both into the ledger: exact timestamps where the JSON has
them, start + duration from the history page otherwise. Hand-written rows are
never deleted and their notes are never rewritten - a row is matched by
incident id, then by name, then by kind within three days, and only the
dashboard's own fields (`ended`, `status`, `incident`) are written onto it. A
start date the dashboard disagrees with is corrected and the old value kept in
`date_was`, because the dashboard is the policy's named source of truth.

SPAN SEMANTICS - the part both consumers get from here instead of re-deriving:

    ended set            [date, ended]        known
    status = ongoing     [date, today]        ongoing (overlaps every later window)
    rollout kind, no end [date, ?]            unknown -> POSSIBLY in window, named as such
    anything else        [date, date]         a point event (a doc change, a policy)

An unknown end is never guessed into a duration. It is reported in a separate
`possibly_in_window` list, bounded by the longest rollout the ledger has
actually measured - a number derived from the data, not chosen.

    algoupdates.py sync [--dry-run]       merge the dashboard into the ledger
    algoupdates.py status                 freshness, open rollouts, unverified claims
    algoupdates.py window --start D --end D
    algoupdates.py check-sources [--limit N]   do the cited Google URLs still answer
    algoupdates.py control                offline: parsers, merge and spans on real fixtures

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as htmlmod
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from providers import http, BROWSER_UA  # noqa: E402
from controls import Controls, refuse  # noqa: E402

DASH = "https://status.search.google.com"
INCIDENTS_JSON = f"{DASH}/incidents.json"

# Product ids are not on the dashboard's summary page; these two were read from
# `affected_products` in incidents.json. Others (Crawling, Indexing) are picked
# up from that feed the first time one of them has an incident.
PRODUCTS = {"Ranking": "rGHU1u87FJnkP6W2GwMi", "Serving": "pKUD9XkLn3TBLquSpQMD"}

LEDGER = Path(__file__).resolve().parent.parent / "assets" / "google-updates.json"
FIXTURES = Path(__file__).resolve().parent.parent / "assets" / "fixtures" / "statusdash"

# Kinds that ROLL OUT over days or weeks. A point event (a documentation
# change, a policy announcement) is one day by nature; one of these with no end
# date is unknown, not short.
ROLLOUT_KINDS = {"core", "spam", "core+spam", "discover", "reviews", "helpful-content",
                 "ranking", "serving"}

# The ledger had no freshness signal at all; a calendar three months behind
# read exactly like a quiet quarter.
STALE_DAYS = 30


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


# ------------------------------------------------------------------ parsers


def parse_duration(text: str) -> float | None:
    """'2 days, 16 hours' -> hours. None when there is no duration to read."""
    if not text:
        return None
    hours, found = 0.0, False
    for n, unit in re.findall(r"(\d+)\s*(day|hour|minute)s?", text, re.I):
        found = True
        hours += int(n) * {"day": 24, "hour": 1, "minute": 1 / 60}[unit.lower()]
    return hours if found else None


_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)


def _cell(row: str, suffix: str) -> str | None:
    m = re.search(r'class="[^"]*__' + suffix + r'"[^>]*>(.*?)</', row, re.S)
    return htmlmod.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else None


def parse_history(page: str, service: str) -> list[dict]:
    """One product's history page -> incidents.

    Keyed on the class SUFFIXES (`__summary-text`, `__date`, `__duration-text`)
    because the prefix is a build hash that changes between deploys."""
    out, seen = [], set()
    for row in _ROW.findall(page or ""):
        m = re.search(r"incidents/([A-Za-z0-9]+)", row)
        if not m or m.group(1) in seen:
            continue
        name, day = _cell(row, "summary-text"), _cell(row, "date")
        if not name or not day:
            continue
        try:
            start = dt.datetime.strptime(day, "%d %b %Y").date()
        except ValueError:
            continue
        seen.add(m.group(1))
        dur_text = _cell(row, "duration-text")
        hours = parse_duration(dur_text or "")
        # The page gives a start DAY and a duration, not a start time, so the
        # last affected day is rounded UP: "26 days, 15 hours" from an unknown
        # hour can end on the 27th day. Overlap is inclusive, so one day of
        # slack can only widen a correlation, never hide one.
        end = ((start + dt.timedelta(days=math.ceil(hours / 24))).isoformat()
               if hours is not None else None)
        out.append({"id": m.group(1), "name": name, "service": service,
                    "begin": start.isoformat(), "end": end,
                    "duration": dur_text, "ongoing": hours is None,
                    "precision": "day"})
    return out


def parse_incidents(rows) -> list[dict]:
    out = []
    for i in rows or []:
        if not isinstance(i, dict) or not i.get("id") or not i.get("begin"):
            continue
        texts = [u.get("text", "") for u in i.get("updates") or [] if isinstance(u, dict)]
        out.append({"id": i["id"], "name": i.get("external_desc") or "",
                    "service": i.get("service_name") or "",
                    "products": {p.get("title"): p.get("id")
                                 for p in i.get("affected_products") or [] if isinstance(p, dict)},
                    "begin": i["begin"][:10], "end": (i.get("end") or "")[:10] or None,
                    "ongoing": not i.get("end"), "severity": i.get("severity"),
                    "first_text": _strip_links(texts[-1]) if texts else "",
                    "precision": "timestamp"})
    return out


def _strip_links(t: str) -> str:
    return re.sub(r"\s*<https?://[^>]+>", "", t or "").strip()


def kind_of(name: str, service: str) -> str:
    n = (name or "").lower()
    if service and service.lower() != "ranking":
        return service.lower()
    if "core" in n and "spam" in n:
        return "core+spam"
    if "core update" in n:
        return "core"
    if "spam" in n:
        return "spam"
    if "discover" in n:
        return "discover"
    if "review" in n:
        return "reviews"
    if "helpful content" in n:
        return "helpful-content"
    return "ranking"


# ------------------------------------------------------------------- fetch


def fetch_dashboard(services=None) -> dict:
    """Both sources, each reported separately - one failing must not read as
    'the dashboard has no incidents'."""
    got = {"incidents": [], "history": {}, "errors": []}
    r = http(INCIDENTS_JSON, timeout=30, ua=BROWSER_UA, retries=1)
    try:
        js = json.loads(r.text()) if r.get("status") == 200 else None
    except Exception:
        js = None
    if isinstance(js, list):
        got["incidents"] = parse_incidents(js)
    else:
        got["errors"].append(f"incidents.json: HTTP {r.get('status')} {r.get('error') or ''}".strip())

    products = dict(PRODUCTS)
    for inc in got["incidents"]:
        products.update({k: v for k, v in inc["products"].items() if k and v})
    for svc, pid in products.items():
        if services and svc not in services:
            continue
        r = http(f"{DASH}/products/{pid}/history", timeout=30, ua=BROWSER_UA, retries=1)
        rows = parse_history(r.text(), svc) if r.get("status") == 200 else []
        if r.get("status") != 200:
            got["errors"].append(f"{svc} history: HTTP {r.get('status')}")
        elif not rows:
            got["errors"].append(f"{svc} history: page read, 0 incidents parsed - layout changed?")
        got["history"][svc] = rows
    return got


def combine(got: dict) -> list[dict]:
    """History rows, upgraded with the JSON's exact timestamps where both exist."""
    exact = {i["id"]: i for i in got["incidents"]}
    rows = {}
    for svc_rows in got["history"].values():
        for h in svc_rows:
            rows[h["id"]] = dict(h)
    for iid, i in exact.items():
        base = rows.get(iid, {})
        merged = {**base, **{k: i[k] for k in ("id", "name", "service", "begin", "end",
                                                "ongoing", "precision")}}
        if i.get("first_text"):
            merged["first_text"] = i["first_text"]
        rows[iid] = merged
    return sorted(rows.values(), key=lambda r: r["begin"])


# ------------------------------------------------------------------- ledger


def load_ledger(path: Path = LEDGER) -> tuple[dict | None, str | None]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"cannot read {path}: {exc}"
    if not isinstance(d, dict) or not isinstance(d.get("updates"), list):
        return None, f"{path} has no `updates` list"
    return d, None


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _incident_id(row: dict) -> str | None:
    if row.get("incident"):
        return row["incident"]
    m = re.search(r"status\.search\.google\.com/incidents/([A-Za-z0-9]+)", row.get("source") or "")
    return m.group(1) if m else None


def match_row(updates: list[dict], inc: dict) -> dict | None:
    by_id = [u for u in updates if _incident_id(u) == inc["id"]]
    if by_id:
        return by_id[0]
    # A row already bound to an incident is that incident and no other. The
    # dashboard reuses generic titles ("Serving is experiencing an ongoing
    # issue") for unrelated incidents years apart, and matching those by name
    # made each sync overwrite the previous incident's row with the next one.
    nn = _norm_name(inc["name"])
    by_name = [u for u in updates if not _incident_id(u)
               and _norm_name(u.get("name")) == nn]
    if by_name:
        return by_name[0]
    k = kind_of(inc["name"], inc["service"])
    if k not in ROLLOUT_KINDS:
        return None
    for u in updates:
        if not _incident_id(u) and u.get("kind") == k and abs((_d(u["date"]) - _d(inc["begin"])).days) <= 3:
            return u
    return None


def merge(ledger: dict, incidents: list[dict], now: str) -> dict:
    ups = ledger["updates"]
    added, updated = [], []
    for inc in incidents:
        status = "ongoing" if inc["ongoing"] else "completed"
        fields = {"ended": inc["end"], "status": status, "incident": inc["id"]}
        row = match_row(ups, inc)
        if row is None:
            k = kind_of(inc["name"], inc["service"])
            new = {"date": inc["begin"], "name": inc["name"], "kind": k,
                   "source": f"{DASH}/incidents/{inc['id']}",
                   "notes": inc.get("first_text") or (
                       f"Synced from the Search Status Dashboard ({inc['service']}); "
                       f"duration {inc.get('duration') or 'open'}."),
                   **fields, "synced": now}
            ups.append(new)
            added.append({"name": new["name"], "date": new["date"], "kind": k,
                          "status": status, "ended": inc["end"]})
            continue
        changes = {f: [row.get(f), v] for f, v in fields.items() if row.get(f) != v}
        if row.get("date") != inc["begin"]:
            changes["date"] = [row.get("date"), inc["begin"]]
            row.setdefault("date_was", row.get("date"))
            row["date"] = inc["begin"]
        if changes:
            row.update(fields)
            row["synced"] = now
            updated.append({"name": row.get("name"), "changes": changes})
    ups.sort(key=lambda u: u.get("date", ""))
    return {"added": added, "updated": updated}


# -------------------------------------------------------------------- spans


def span(u: dict, now: str) -> tuple[str, str | None, str]:
    start = u.get("date", "")
    if u.get("ended"):
        return start, u["ended"], "known"
    if u.get("status") == "ongoing":
        return start, now, "ongoing"
    if u.get("kind") in ROLLOUT_KINDS:
        return start, None, "unknown"
    return start, start, "point"


def longest_rollout_days(updates: list[dict]) -> int | None:
    lens = [(_d(u["ended"]) - _d(u["date"])).days for u in updates
            if u.get("ended") and u.get("date")]
    return max(lens) if lens else None


def correlate(updates: list[dict], start: str, end: str, now: str | None = None) -> dict:
    """Which updates overlap [start, end] - and, separately, which MIGHT."""
    now = now or today()
    certain, possible = [], []
    lookback = longest_rollout_days(updates)
    for u in updates or []:
        s, e, k = span(u, now)
        if not s:
            continue
        row = {**u, "span": [s, e], "span_kind": k}
        if k == "unknown":
            if s <= end and (lookback is None or (_d(start) - _d(s)).days <= lookback):
                possible.append(row)
        elif not (e < start or s > end):
            certain.append(row)
    return {"in_window": certain, "possibly_in_window": possible,
            "lookback_days": lookback,
            "lookback_basis": "the longest rollout with a measured end in this ledger"}


# ---------------------------------------------------------------- commands


def cmd_sync(a) -> dict:
    ledger, err = load_ledger(Path(a.ledger))
    if err:
        return refuse("algoupdates-sync", err)
    got = fetch_dashboard()
    incidents = combine(got)
    if not incidents:
        return refuse("algoupdates-sync", "the dashboard could not be read - nothing merged, "
                      "and the ledger is NOT current", errors=got["errors"])
    ctl = Controls("algoupdates-live")
    ctl.check("a_known_2021_incident_is_in_the_history",
              any(i["name"].lower() == "november 2021 core update" for i in incidents),
              "the parser read the page but not the rows it is known to hold")
    if not ctl.ok:
        return refuse("algoupdates-sync", "history parser control failed - refusing to merge "
                      "a partial read as the whole calendar", control=ctl.verdict())
    now = today()
    res = merge(ledger, incidents, now)
    prov = ledger.setdefault("_provenance", {})
    prov["last_synced"] = now
    prov["entries_complete_through"] = now
    prov["how_to_top_up"] = (
        "Run `algoupdates.py sync`: it merges status.search.google.com/incidents.json "
        "(exact timestamps, recent) and each product's /history page (every incident "
        "since 2021). Documentation and product changes are not on the dashboard - add "
        "those by hand, newest last, only with a Google-owned source URL.")
    if not a.dry_run:
        Path(a.ledger).write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n",
                                  encoding="utf-8")
    return {"ok": True, "check": "algoupdates-sync", "dry_run": a.dry_run,
            "dashboard_incidents": len(incidents), "errors": got["errors"],
            "partial": bool(got["errors"]), **res, "control": ctl.verdict()}


def cmd_status(a) -> dict:
    ledger, err = load_ledger(Path(a.ledger))
    if err:
        return refuse("algoupdates-status", err)
    now = today()
    prov = ledger.get("_provenance") or {}
    synced = prov.get("last_synced")
    age = (_d(now) - _d(synced)).days if synced else None
    ups = ledger["updates"]
    open_ = [u for u in ups if u.get("status") == "ongoing"]
    unknown = [u["name"] for u in ups if span(u, now)[2] == "unknown"]
    return {"ok": True, "check": "algoupdates-status", "entries": len(ups),
            "newest": max((u["date"] for u in ups), default=None),
            "last_synced": synced, "age_days": age,
            "stale": age is None or age > STALE_DAYS,
            "stale_rule": f"never synced, or synced more than {STALE_DAYS} days ago",
            "ongoing": [{"name": u["name"], "since": u["date"]} for u in open_],
            "rollouts_with_unknown_end": unknown,
            "unverified_claims": [{"date": u.get("date"), "claim": u.get("claim"),
                                   "status": u.get("status")}
                                  for u in ledger.get("unverified") or []]}


def cmd_window(a) -> dict:
    ledger, err = load_ledger(Path(a.ledger))
    if err:
        return refuse("algoupdates-window", err)
    return {"ok": True, "check": "algoupdates-window", "start": a.start, "end": a.end,
            **correlate(ledger["updates"], a.start, a.end)}


def cmd_check_sources(a) -> dict:
    ledger, err = load_ledger(Path(a.ledger))
    if err:
        return refuse("algoupdates-check-sources", err)
    ctl = Controls("algoupdates-sources")
    fake = http("https://developers.google.com/search/blog/2026/13/no-such-post-9f2b",
                timeout=20, ua=BROWSER_UA)
    real = http("https://developers.google.com/search/updates", timeout=20, ua=BROWSER_UA)
    ctl.check("a_known_google_page_answers_200", real.get("status") == 200, str(real.get("status")))
    ctl.check("a_fabricated_google_url_does_not", fake.get("status") != 200, str(fake.get("status")))
    if not ctl.ok:
        return refuse("algoupdates-check-sources", "the probe cannot tell a live source from a "
                      "dead one from here", control=ctl.verdict())
    srcs = sorted({u["source"] for u in ledger["updates"] if u.get("source")})
    rows = []
    for s in srcs[: a.limit]:
        r = http(s, timeout=20, ua=BROWSER_UA, retries=1)
        rows.append({"source": s, "status": r.get("status"), "final_url": r.get("url")})
    dead = [r for r in rows if r["status"] != 200]
    return {"ok": True, "check": "algoupdates-check-sources", "checked": len(rows),
            "of": len(srcs), "dead": dead, "control": ctl.verdict()}


def run_control() -> dict:
    c = Controls("algoupdates-control")
    hist = parse_history((FIXTURES / "ranking-history.html").read_text(encoding="utf-8"), "Ranking")
    names = {h["name"]: h for h in hist}
    c.check("history_rows_parse", len(hist) >= 3, str(len(hist)))
    nov = names.get("November 2021 core update") or {}
    c.check("a_finished_rollout_gets_its_end", nov.get("begin") == "2021-11-17"
            and nov.get("end") == "2021-11-30", str(nov))
    sep = names.get("September 2026 spam update") or {}
    c.check("a_row_with_no_duration_is_ongoing", sep.get("ongoing") is True and sep.get("end") is None,
            str(sep))
    c.check("duplicate_incident_rows_are_read_once", len(hist) == len({h['id'] for h in hist}))
    c.check("an_empty_page_is_zero_rows_not_an_error", parse_history("", "Ranking") == [])

    inc = parse_incidents(json.loads((FIXTURES / "incidents.json").read_text(encoding="utf-8")))
    aug = next((i for i in inc if i["name"] == "August 2026 spam update"), {})
    c.check("json_timestamps_parse", aug.get("begin") == "2026-08-18" and aug.get("end") == "2026-08-21",
            str(aug))
    c.check("json_open_incident_is_ongoing",
            any(i["ongoing"] and "September" in i["name"] for i in inc))
    c.check("a_serving_incident_is_kind_serving",
            any(kind_of(i["name"], i["service"]) == "serving" for i in inc))

    # Merge: a hand row matched by NAME keeps its notes and source, gains an end.
    led = {"updates": [{"date": "2021-11-17", "name": "November 2021 Core Update", "kind": "core",
                        "source": "https://developers.google.com/x", "notes": "hand"}]}
    res = merge(led, combine({"incidents": inc, "history": {"Ranking": hist}}), "2026-10-03")
    row = next(u for u in led["updates"] if u["name"] == "November 2021 Core Update")
    c.check("merge_keeps_hand_notes_and_source", row["notes"] == "hand"
            and row["source"] == "https://developers.google.com/x")
    c.check("merge_writes_the_end_onto_the_hand_row", row.get("ended") == "2021-11-30", str(row))
    c.check("merge_adds_incidents_it_did_not_have",
            any(a_["name"] == "August 2026 spam update" for a_ in res["added"]), str(res["added"]))
    gen = {"updates": []}
    two = [{"id": "AAA", "name": "Serving is experiencing an ongoing issue", "service": "Serving",
            "begin": "2023-10-26", "end": "2023-10-26", "ongoing": False},
           {"id": "BBB", "name": "Serving is experiencing an ongoing issue", "service": "Serving",
            "begin": "2025-10-03", "end": "2025-10-06", "ongoing": False}]
    merge(gen, two, "2026-10-03")
    merge(gen, two, "2026-10-03")
    c.check("two_incidents_with_one_generic_title_stay_two_rows",
            sorted(u["incident"] for u in gen["updates"]) == ["AAA", "BBB"], str(gen))
    c.check("a_partial_day_duration_rounds_the_end_up",
            parse_history('<tr><a href="incidents/X1"></a><span class="a__summary-text">S</span>'
                          '<td class="a__date">26 Aug 2025</td>'
                          '<span class="a__duration-text">26 days, 15 hours</span></tr>',
                          "Ranking")[0]["end"] == "2025-09-22")
    again = merge(led, combine({"incidents": inc, "history": {"Ranking": hist}}), "2026-10-03")
    c.check("merge_is_idempotent", again == {"added": [], "updated": []}, str(again))

    # Spans: the bug.
    ups = [{"date": "2026-03-27", "ended": "2026-04-08", "kind": "core", "name": "a"},
           {"date": "2026-09-24", "status": "ongoing", "kind": "spam", "name": "b"},
           {"date": "2026-06-01", "kind": "core", "name": "c"},
           {"date": "2026-06-05", "kind": "documentation", "name": "d"}]
    w = correlate(ups, "2026-04-01", "2026-04-30", "2026-10-03")
    c.check("overlap_uses_the_end_not_the_start", [u["name"] for u in w["in_window"]] == ["a"])
    w = correlate(ups, "2026-09-28", "2026-10-02", "2026-10-03")
    c.check("an_ongoing_rollout_overlaps_a_later_window", [u["name"] for u in w["in_window"]] == ["b"])
    w = correlate(ups, "2026-06-03", "2026-06-10", "2026-10-03")
    c.check("an_unknown_end_is_possible_not_certain",
            [u["name"] for u in w["possibly_in_window"]] == ["c"]
            and [u["name"] for u in w["in_window"]] == ["d"], str(w))
    c.check("lookback_is_derived_from_measured_rollouts", w["lookback_days"] == 12)

    led, err = load_ledger()
    c.check("the_committed_ledger_loads", err is None, err)
    c.check("an_unreadable_ledger_is_an_error_not_empty",
            load_ledger(Path("/nonexistent.json"))[0] is None)
    return c.verdict()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sync", "status", "window", "check-sources"):
        p = sub.add_parser(name)
        p.add_argument("--ledger", default=str(LEDGER))
        if name == "sync":
            p.add_argument("--dry-run", action="store_true")
        if name == "window":
            p.add_argument("--start", required=True)
            p.add_argument("--end", required=True)
        if name == "check-sources":
            p.add_argument("--limit", type=int, default=200)
    sub.add_parser("control", help="offline: parsers, merge and spans on captured fixtures")
    a = ap.parse_args()
    out = {"sync": cmd_sync, "status": cmd_status, "window": cmd_window,
           "check-sources": cmd_check_sources}.get(a.cmd)
    res = out(a) if out else run_control()
    print(json.dumps(res, indent=2, ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
