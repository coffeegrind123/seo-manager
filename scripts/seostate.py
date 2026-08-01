#!/usr/bin/env python3
"""Local state store for the seo-manager skill.

All state is plain JSON/JSONL files under `.seo/` in the site's own repo - no
database, no backend, no service to run. Every workflow reads and writes through this
CLI so the state shape stays consistent and git-diffable.

Layout (all under <repo>/.seo/):
    config.json      project config: name, domain, repo, mode, providers, prefs
    conventions.md   the site-facts file every workflow adapts from
    queue.json       suggestions queue (guides, tools, updates)
    keywords.json    tracked keywords
    pages.json       published pages this pipeline shipped
    ranks.jsonl      append-only rank checks
    trends.json      trend radar subjects
    backlinks.json   backlink prospects
    ai.jsonl         append-only AI-visibility snapshots
    profile.json     directory-ready product profile
    runs.jsonl       append-only run log

Stdlib only. Every command prints JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- primitives

SUGGESTION_STATUSES = ("pending", "approved", "rejected", "in_progress", "done")
SUGGESTION_TYPES = ("guide", "tool", "update", "backlink")
SUGGESTION_SOURCES = ("research", "manual", "trend-scan", "geo-scan", "backstop")
TREND_STATUSES = ("new", "expanding", "expanded", "dismissed")
PROSPECT_STATUSES = ("new", "contacted", "acquired", "rejected")
# Owner template controls. Stored as DISABLE-lists so an empty/missing value
# means "all defaults on" - the same contract a content_prefs table would give,
# where '{}' and every pre-migration row had to mean untouched.
GUIDE_ARCHETYPES = ("tutorial", "comparison", "data-study", "opinion", "reference")
GUIDE_BLOCKS = ("tldr", "comparison_table", "visuals", "faq")
BLOCK_NOTES = {
    "tldr": "Do NOT open with a TL;DR blockquote - the owner turned it off. The answer-first "
            "opening paragraph still applies.",
    "comparison_table": "Do NOT add a comparison table, even where a comparison exists.",
    "visuals": "Do NOT build bespoke visual components for this guide - the owner turned them off. "
               "Skip the VISUALS step entirely (the cover, if the repo generates one, still applies).",
    "faq": "Do NOT add an FAQ section - the owner turned it off. The FAQ mirror rule is therefore "
           "moot; do not emit FAQ structured data either.",
}


def normalize_prefs(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "house_rules": str(raw.get("house_rules") or "").strip(),
        "disabled_archetypes": [a for a in (raw.get("disabled_archetypes") or []) if a in GUIDE_ARCHETYPES],
        "disabled_blocks": [b for b in (raw.get("disabled_blocks") or []) if b in GUIDE_BLOCKS],
    }


def render_prefs_note(prefs: dict, kind: str = "guide") -> str:
    prefs = normalize_prefs(prefs)
    lines = ["### Owner content preferences", ""]
    if not (prefs["house_rules"] or prefs["disabled_archetypes"] or prefs["disabled_blocks"]):
        lines.append("The owner has not customized anything - the defaults in this workflow apply "
                     "unchanged.")
        return "\n".join(lines)
    if kind == "guide" and prefs["disabled_archetypes"]:
        off = ", ".join(prefs["disabled_archetypes"])
        on = ", ".join(a for a in GUIDE_ARCHETYPES if a not in prefs["disabled_archetypes"])
        lines.append(f"- **Shapes removed from rotation:** {off}. Never choose these archetypes; "
                     f"rotate among: {on}.")
    if kind == "guide":
        for b in prefs["disabled_blocks"]:
            lines.append(f"- {BLOCK_NOTES[b]}")
    if prefs["house_rules"]:
        lines += ["- **House rules (obey verbatim):**", "", "  " + prefs["house_rules"].replace("\n", "\n  ")]
    lines += ["", "These are preferences the playbook OBEYS. They never override the quality bar."]
    return "\n".join(lines)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value) -> int | None:
    ts = parse_ts(value)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - ts).days)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "untitled"


def die(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, indent=2))
    sys.exit(code)


def out(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------------- storage


class Store:
    """File-backed state rooted at <root>/.seo."""

    FILES = {
        "config": ("config.json", dict),
        "queue": ("queue.json", list),
        "keywords": ("keywords.json", list),
        "pages": ("pages.json", list),
        "trends": ("trends.json", list),
        "backlinks": ("backlinks.json", list),
        "profile": ("profile.json", dict),
    }
    STREAMS = {"ranks": "ranks.jsonl", "ai": "ai.jsonl", "runs": "runs.jsonl"}

    def __init__(self, root: Path | None = None):
        self.root = Path(root or find_root()).resolve()
        self.dir = self.root / ".seo"

    # -- files ------------------------------------------------------------
    def path(self, name: str) -> Path:
        if name in self.FILES:
            return self.dir / self.FILES[name][0]
        if name in self.STREAMS:
            return self.dir / self.STREAMS[name]
        raise KeyError(name)

    def load(self, name: str):
        kind = self.FILES[name][1]
        p = self.path(name)
        if not p.exists():
            return kind()
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "null")
        except json.JSONDecodeError as exc:
            die(f"{p} is not valid JSON ({exc}). Fix or delete it; nothing was written.")
        return data if isinstance(data, kind) else kind()

    def save(self, name: str, data):
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(p)

    # -- append-only streams ----------------------------------------------
    def append(self, name: str, row: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.path(name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def stream(self, name: str) -> list[dict]:
        p = self.path(name)
        if not p.exists():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    # -- conventions -------------------------------------------------------
    @property
    def conventions_path(self) -> Path:
        return self.dir / "conventions.md"

    def config(self) -> dict:
        cfg = self.load("config")
        if not cfg:
            die(
                f"No project at {self.dir}. Run: seostate.py init "
                "--name <site> --domain <domain> [--repo owner/name]"
            )
        return cfg


def find_root() -> Path:
    """Walk up from cwd looking for an existing .seo/, else use cwd."""
    env = os.environ.get("SEO_ROOT")
    if env:
        return Path(env)
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".seo" / "config.json").exists():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return cur


# ------------------------------------------------------------------ helpers


def find_row(rows: list[dict], ident: str) -> dict | None:
    """Match by id, id prefix, slug, or exact title (case-insensitive)."""
    ident = (ident or "").strip()
    if not ident:
        return None
    low = ident.lower()
    for row in rows:
        if row.get("id") == ident:
            return row
    for row in rows:
        if str(row.get("id", "")).startswith(ident) and len(ident) >= 4:
            return row
    for row in rows:
        if row.get("slug") == low or str(row.get("title", "")).lower() == low:
            return row
    for row in rows:
        if str(row.get("keyword", "")).lower() == low or str(row.get("domain", "")).lower() == low:
            return row
    return None


def parse_json_arg(value, field: str):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        die(f"--{field} must be valid JSON: {exc}")


def queue_sort_key(row: dict):
    """Build order: front-placed items first (queue_position), then oldest."""
    pos = row.get("queue_position")
    return (0 if pos is not None else 1, pos if pos is not None else 0, row.get("created_at") or "")


# --------------------------------------------------------------- commands


def cmd_init(store: Store, a):
    store.dir.mkdir(parents=True, exist_ok=True)
    cfg = store.load("config")
    cfg.update(
        {
            "name": a.name or cfg.get("name"),
            "domain": (a.domain or cfg.get("domain") or "").replace("https://", "").replace("http://", "").strip("/"),
            "github_repo": a.repo or cfg.get("github_repo"),
            "mode": a.mode or cfg.get("mode") or "semi",
            "auto_approve_tools": cfg.get("auto_approve_tools", False),
            "internal_linking": cfg.get("internal_linking", False),
            "auto_merge": cfg.get("auto_merge", False),
            "serp_provider": a.serp_provider or cfg.get("serp_provider") or "ddg",
            "gsc_property": a.gsc_property or cfg.get("gsc_property"),
            "location_code": cfg.get("location_code", 2840),
            "language_code": cfg.get("language_code", "en"),
            "site_launched_at": cfg.get("site_launched_at"),
            "dr": cfg.get("dr"),
            "dr_fetched_at": cfg.get("dr_fetched_at"),
            "created_at": cfg.get("created_at") or now(),
            "updated_at": now(),
        }
    )
    if not cfg["name"] or not cfg["domain"]:
        die("init needs --name and --domain")
    store.save("config", cfg)
    for name in ("queue", "keywords", "pages", "trends", "backlinks", "profile"):
        if not store.path(name).exists():
            store.save(name, store.FILES[name][1]())
    gitignore = store.dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("# secrets only - the state files are meant to be committed\n*.key\n.env\n", encoding="utf-8")
    out({"ok": True, "root": str(store.root), "state_dir": str(store.dir), "config": cfg})


def cmd_config(store: Store, a):
    cfg = store.config()
    if a.set:
        for pair in a.set:
            if "=" not in pair:
                die(f"--set expects key=value, got {pair!r}")
            key, _, raw = pair.partition("=")
            key = key.strip()
            raw = raw.strip()
            if raw.lower() in ("true", "false"):
                val = raw.lower() == "true"
            elif raw.lower() in ("null", "none", ""):
                val = None
            else:
                try:
                    val = json.loads(raw)
                except json.JSONDecodeError:
                    val = raw
            cfg[key] = val
        cfg["updated_at"] = now()
        store.save("config", cfg)
    conv = store.conventions_path
    cfg = dict(cfg)
    cfg["content_prefs"] = normalize_prefs(cfg.get("content_prefs"))
    cfg["conventions_file"] = str(conv) if conv.exists() else None
    cfg["kd_zones"] = kd_zones(cfg.get("dr"))
    cfg["volume_band"] = volume_band(cfg.get("dr"))
    out({"ok": True, "config": cfg})


# -- suggestions ------------------------------------------------------------


def cmd_propose(store: Store, a):
    rows = store.load("queue")
    kw = (a.keyword or "").strip().lower() or None
    if kw:
        dupe = next(
            (r for r in rows if (r.get("primary_keyword") or "").lower() == kw and r.get("status") != "rejected"),
            None,
        )
        if dupe and not a.allow_duplicate:
            out(
                {
                    "ok": False,
                    "duplicate": True,
                    "message": f"'{kw}' is already queued as {dupe['id']} ({dupe['status']}). "
                    "Pass --allow-duplicate to override.",
                    "existing": dupe,
                }
            )
            return
    row = {
        "id": new_id(),
        "type": a.type,
        "title": a.title,
        "slug": slugify(a.title),
        "primary_keyword": kw,
        "keyword_volume": a.volume,
        "keyword_difficulty": a.kd,
        "authority_count": a.authority_count,
        "intent": a.intent,
        "archetype": a.archetype,
        "rationale": a.rationale,
        "serp_notes": a.serp_notes,
        "spec": parse_json_arg(a.spec, "spec") or {},
        "source": a.source,
        "trend_topic_id": a.trend_topic_id,
        "status": "pending",
        "queue_position": None,
        "result_pr_url": None,
        "created_at": now(),
        "decided_at": None,
        "completed_at": None,
        "history": [{"at": now(), "status": "pending", "note": "proposed"}],
    }
    rows.append(row)
    store.save("queue", rows)
    out({"ok": True, "suggestion": row})


def cmd_update_suggestion(store: Store, a):
    rows = store.load("queue")
    row = find_row(rows, a.id)
    if not row:
        die(f"no suggestion matching {a.id!r}")
    cfg = store.config()
    requested = a.status
    coerced = False
    note = a.note or ""

    if requested == "approved" and row.get("type") == "tool" and not cfg.get("auto_approve_tools", False):
        if row.get("source") != "manual":
            requested = "pending"
            coerced = True
            note = (note + " | tool approvals are gated on this project - recorded as pending for the owner").strip(" |")
    if requested == "approved" and cfg.get("mode") == "semi" and row.get("source") in ("research", "trend-scan", "geo-scan", "backstop"):
        requested = "pending"
        coerced = True
        note = (note + " | semi mode: agent approvals are recorded as pending for the owner").strip(" |")
    if requested == "approved" and row.get("source") == "trend-scan":
        requested = "pending"
        coerced = True
        note = (note + " | trend takes are always the owner's call").strip(" |")

    row["status"] = requested
    if requested in ("approved", "rejected"):
        row["decided_at"] = now()
    if requested == "done":
        row["completed_at"] = now()
    for field in ("rationale", "serp_notes", "intent", "archetype"):
        val = getattr(a, field, None)
        if val:
            row[field] = val
    if a.pr_url:
        row["result_pr_url"] = a.pr_url
    if a.authority_count is not None:
        row["authority_count"] = a.authority_count
    if a.spec:
        row["spec"] = {**(row.get("spec") or {}), **(parse_json_arg(a.spec, "spec") or {})}
    row.setdefault("history", []).append({"at": now(), "status": requested, "note": note or None})
    store.save("queue", rows)
    out(
        {
            "ok": True,
            "coerced": coerced,
            "requested_status": a.status,
            "recorded_status": requested,
            "message": note or f"status -> {requested}",
            "suggestion": row,
        }
    )


def cmd_suggestions(store: Store, a):
    rows = store.load("queue")
    if a.status:
        rows = [r for r in rows if r.get("status") in a.status]
    if a.type:
        rows = [r for r in rows if r.get("type") == a.type]
    if a.source:
        rows = [r for r in rows if r.get("source") == a.source]
    rows = sorted(rows, key=queue_sort_key)
    if a.limit:
        rows = rows[: a.limit]
    out({"ok": True, "count": len(rows), "build_order": True, "suggestions": rows})


def cmd_reorder(store: Store, a):
    rows = store.load("queue")
    ids = [i.strip() for i in a.ids.split(",") if i.strip()]
    seen = []
    for pos, ident in enumerate(ids):
        row = find_row(rows, ident)
        if not row:
            die(f"no suggestion matching {ident!r}")
        row["queue_position"] = pos
        seen.append(row["id"])
    for row in rows:
        if row["id"] not in seen:
            row["queue_position"] = None
    store.save("queue", rows)
    out({"ok": True, "front_of_queue": seen})


# -- keywords ---------------------------------------------------------------


def cmd_track(store: Store, a):
    rows = store.load("keywords")
    added, updated = [], []
    payload = parse_json_arg(a.json, "json")
    items = payload if payload else [{"keyword": k.strip()} for k in (a.keywords or "").split(",") if k.strip()]
    for item in items:
        kw = str(item.get("keyword", "")).strip().lower()
        if not kw:
            continue
        existing = next((r for r in rows if r["keyword"] == kw), None)
        fields = {
            "search_volume": item.get("volume", item.get("search_volume")),
            "keyword_difficulty": item.get("kd", item.get("keyword_difficulty")),
            "intent": item.get("intent"),
            "cpc": item.get("cpc"),
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        if existing:
            existing.update(fields)
            existing["updated_at"] = now()
            updated.append(kw)
        else:
            rows.append({"id": new_id(), "keyword": kw, "status": "tracking", "target_url": item.get("target_url"), "created_at": now(), **fields})
            added.append(kw)
    store.save("keywords", rows)
    out({"ok": True, "added": added, "updated": updated, "total_tracked": len(rows)})


def cmd_keywords(store: Store, a):
    rows = [r for r in store.load("keywords") if a.all or r.get("status") == "tracking"]
    ranks = store.stream("ranks")
    latest: dict[str, dict] = {}
    for row in ranks:
        key = row.get("keyword")
        if key and (key not in latest or row.get("checked_at", "") > latest[key].get("checked_at", "")):
            latest[key] = row
    for row in rows:
        hit = latest.get(row["keyword"])
        row["latest_position"] = hit.get("position") if hit else None
        row["latest_checked_at"] = hit.get("checked_at") if hit else None
    out({"ok": True, "count": len(rows), "keywords": rows})


# -- ranks ------------------------------------------------------------------


def cmd_record_rank(store: Store, a):
    payload = parse_json_arg(a.json, "json")
    items = payload if payload else [{"keyword": a.keyword, "position": a.position, "url": a.url}]
    written = 0
    for item in items:
        kw = str(item.get("keyword", "")).strip().lower()
        if not kw:
            continue
        store.append(
            "ranks",
            {
                "keyword": kw,
                "position": item.get("position"),
                "url": item.get("url"),
                "provider": item.get("provider") or a.provider,
                "ai_overview": item.get("ai_overview"),
                "checked_at": item.get("checked_at") or now(),
            },
        )
        written += 1
    out({"ok": True, "recorded": written})


def cmd_rankings(store: Store, a):
    rows = store.stream("ranks")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    rows = [r for r in rows if (r.get("checked_at") or "") >= cutoff]
    by_kw: dict[str, list] = {}
    for row in rows:
        by_kw.setdefault(row["keyword"], []).append(row)
    summary = []
    for kw, series in sorted(by_kw.items()):
        series.sort(key=lambda r: r.get("checked_at") or "")
        positions = [r["position"] for r in series if r.get("position") is not None]
        first = next((r["position"] for r in series if r.get("position") is not None), None)
        last = series[-1].get("position")
        summary.append(
            {
                "keyword": kw,
                "latest": last,
                "best": min(positions) if positions else None,
                "first_in_window": first,
                "delta": (first - last) if (first is not None and last is not None) else None,
                "checks": len(series),
                "last_checked": series[-1].get("checked_at"),
                "url": series[-1].get("url"),
                "history": [[r.get("checked_at", "")[:10], r.get("position")] for r in series][-30:],
            }
        )
    summary.sort(key=lambda r: (r["latest"] is None, r["latest"] or 999))
    out({"ok": True, "days": a.days, "tracked": len(summary), "rankings": summary})


# -- pages ------------------------------------------------------------------


def cmd_log_page(store: Store, a):
    rows = store.load("pages")
    url = a.url.strip()
    existing = next((r for r in rows if r["url"] == url), None)
    row = existing or {"id": new_id(), "url": url, "created_at": now()}
    row.update(
        {
            "title": a.title or row.get("title"),
            "type": a.type or row.get("type") or "guide",
            "primary_keyword": (a.keyword or row.get("primary_keyword") or "").lower() or None,
            "pr_url": a.pr_url or row.get("pr_url"),
            "published_at": a.published_at or row.get("published_at") or now(),
            "archetype": a.archetype or row.get("archetype"),
            "information_gain": a.information_gain or row.get("information_gain"),
        }
    )
    # Indexing state. Google has no API for "please index this", so the honest
    # model is: IndexNow covers Bing/Yandex automatically, and the Google side
    # is a human click we merely TRACK so it stops being asked for twice.
    if a.index_requested:
        row["index_requested_at"] = now()
    if a.indexnow_submitted:
        row["indexnow_submitted_at"] = a.indexnow_submitted
    if a.indexed:
        row["indexed_at"] = now()
    if not existing:
        rows.append(row)
    store.save("pages", rows)
    out({"ok": True, "page": row, "total_pages": len(rows)})


def cmd_pages(store: Store, a):
    rows = store.load("pages")
    if a.type:
        rows = [r for r in rows if r.get("type") == a.type]
    for row in rows:
        row["age_days"] = days_since(row.get("published_at"))
        row["settled"] = (row["age_days"] or 0) >= 21
        row["indexnow_pending"] = not row.get("indexnow_submitted_at")
        row["google_request_pending"] = not row.get("index_requested_at")
    rows.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    out(
        {
            "ok": True,
            "count": len(rows),
            "settled_count": sum(1 for r in rows if r["settled"]),
            "pages": rows,
        }
    )


# -- pacing -----------------------------------------------------------------


def cmd_pacing(store: Store, a):
    pages = [p for p in store.load("pages") if p.get("type") == "guide"]
    dates = sorted([p.get("published_at") for p in pages if p.get("published_at")], reverse=True)
    latest = dates[0] if dates else None
    days = None
    if latest:
        ts = parse_ts(latest)
        if ts:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            n = datetime.now(timezone.utc)
            days = (n.date() - ts.date()).days
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    built_7d = sum(1 for d in dates if d >= week_ago)
    allowed = days is None or days >= 1
    if days is None:
        note = "No guide has shipped yet - today's slot is free, building is allowed."
    elif allowed:
        note = (
            f"Today's slot is free (last guide shipped {days} day(s) ago; the pace is one guide "
            "per day) - building is allowed today."
        )
    else:
        note = (
            "A guide already shipped today (the pace is one guide per day, the owner's own merges "
            "included) - do not build again today; the next slot opens tomorrow."
        )
    out(
        {
            "ok": True,
            "build_allowed": allowed,
            "days_since_last_guide": days,
            "guides_built_last_7d": built_7d,
            "note": note,
        }
    )


# -- trends -----------------------------------------------------------------


def cmd_trend_add(store: Store, a):
    rows = store.load("trends")
    title = a.title.strip()
    if any(r["title"].lower() == title.lower() for r in rows):
        out({"ok": True, "duplicate": True, "message": f"'{title}' is already on the radar - skipped."})
        return
    row = {
        "id": new_id(),
        "title": title,
        "status": "new",
        "evidence": {
            "why_now": a.why_now,
            "signals": [s.strip() for s in (a.signals or "").split("|") if s.strip()],
            "sources": [s.strip() for s in (a.sources or "").split("|") if s.strip()],
        },
        "seed_url": a.seed_url,
        "seed_stats": a.seed_stats,
        "created_at": now(),
        "expanded_at": None,
    }
    rows.append(row)
    store.save("trends", rows)
    out({"ok": True, "topic": row})


def cmd_trends(store: Store, a):
    rows = store.load("trends")
    if a.status:
        rows = [r for r in rows if r.get("status") in a.status]
    for row in rows:
        row["age_days"] = days_since(row.get("created_at"))
        row["stale"] = (row["age_days"] or 0) > 14 and row.get("status") == "new"
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    out({"ok": True, "count": len(rows), "topics": rows})


def cmd_trend_update(store: Store, a):
    rows = store.load("trends")
    row = find_row(rows, a.id)
    if not row:
        die(f"no trend topic matching {a.id!r}")
    row["status"] = a.status
    if a.status == "expanded":
        row["expanded_at"] = now()
    store.save("trends", rows)
    out({"ok": True, "topic": row})


def cmd_record_scan(store: Store, a):
    cfg = store.config()
    cfg["last_trend_scan_at"] = now()
    store.save("config", cfg)
    out({"ok": True, "last_trend_scan_at": cfg["last_trend_scan_at"]})


# -- backlinks --------------------------------------------------------------


def cmd_prospect_add(store: Store, a):
    rows = store.load("backlinks")
    domain = a.domain.lower().replace("https://", "").replace("http://", "").strip("/")
    if any(r["domain"] == domain for r in rows):
        out({"ok": True, "duplicate": True, "message": f"{domain} is already a prospect."})
        return
    row = {
        "id": new_id(),
        "domain": domain,
        "url": a.url,
        "domain_rating": a.dr,
        "link_type": a.link_type,
        "reason": a.reason,
        "outreach_angle": a.angle,
        "status": "new",
        "created_at": now(),
    }
    rows.append(row)
    store.save("backlinks", rows)
    out({"ok": True, "prospect": row})


def cmd_prospects(store: Store, a):
    rows = store.load("backlinks")
    if a.status:
        rows = [r for r in rows if r.get("status") in a.status]
    out({"ok": True, "count": len(rows), "prospects": rows})


def cmd_prospect_update(store: Store, a):
    rows = store.load("backlinks")
    row = find_row(rows, a.id)
    if not row:
        die(f"no prospect matching {a.id!r}")
    row["status"] = a.status
    if a.note:
        row["note"] = a.note
    row["updated_at"] = now()
    store.save("backlinks", rows)
    out({"ok": True, "prospect": row})


# -- AI visibility ----------------------------------------------------------


def cmd_record_ai(store: Store, a):
    entries = parse_json_arg(a.json, "json")
    if not isinstance(entries, list):
        die("--json must be a JSON array of {engine, query, has_ai_answer, cited, cited_url, answer_excerpt, citations}")
    stamp = now()
    for e in entries:
        store.append(
            "ai",
            {
                "engine": e.get("engine", "claude"),
                "query": e.get("query"),
                "has_ai_answer": bool(e.get("has_ai_answer", True)),
                "cited": bool(e.get("cited", False)),
                "cited_url": e.get("cited_url"),
                "answer_excerpt": e.get("answer_excerpt"),
                "citations": e.get("citations", []),
                "checked_at": e.get("checked_at") or stamp,
            },
        )
    out({"ok": True, "recorded": len(entries)})


def cmd_ai_visibility(store: Store, a):
    rows = store.stream("ai")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=a.days)).isoformat()
    rows = [r for r in rows if (r.get("checked_at") or "") >= cutoff]
    cfg = store.config()
    domain = (cfg.get("domain") or "").lower().replace("www.", "")
    by_engine: dict[str, dict] = {}
    gap: dict[str, int] = {}
    for row in rows:
        eng = by_engine.setdefault(row.get("engine", "unknown"), {"queries": 0, "cited": 0, "with_answer": 0})
        eng["queries"] += 1
        eng["cited"] += 1 if row.get("cited") else 0
        eng["with_answer"] += 1 if row.get("has_ai_answer") else 0
        if not row.get("cited"):
            for c in row.get("citations") or []:
                d = str(c.get("domain", "")).lower().replace("www.", "")
                if d and d != domain:
                    gap[d] = gap.get(d, 0) + 1
    for stats in by_engine.values():
        stats["citation_rate"] = round(stats["cited"] / stats["queries"], 3) if stats["queries"] else 0.0
    gap_domains = sorted(({"domain": d, "cited_on_queries": n} for d, n in gap.items()), key=lambda x: -x["cited_on_queries"])
    recent_queries = sorted({r.get("query") for r in rows if r.get("query")})
    out(
        {
            "ok": True,
            "days": a.days,
            "snapshots": len(rows),
            "by_engine": by_engine,
            "gap_domains": gap_domains[:25],
            "prior_queries": recent_queries,
        }
    )


# -- profile / conventions ---------------------------------------------------


def cmd_profile(store: Store, a):
    prof = store.load("profile")
    if a.json:
        payload = parse_json_arg(a.json, "json") or {}
        limits = {"tagline": 60, "short_description": 160}
        problems = []
        for field, cap in limits.items():
            if payload.get(field) and len(payload[field]) > cap:
                problems.append(f"{field} is {len(payload[field])} chars (max {cap})")
        long_desc = payload.get("long_description") or ""
        if long_desc and not (300 <= len(long_desc) <= 600):
            problems.append(f"long_description is {len(long_desc)} chars (needs 300-600)")
        if problems:
            die("profile rejected: " + "; ".join(problems))
        prof.update(payload)
        prof["updated_at"] = now()
        store.save("profile", prof)
    out({"ok": True, "profile": prof})


def cmd_prefs(store: Store, a):
    """Read or set the owner's template controls, and render the note the build
    playbooks must obey."""
    cfg = store.config()
    prefs = normalize_prefs(cfg.get("content_prefs"))
    changed = False
    if a.house_rules is not None:
        prefs["house_rules"] = a.house_rules
        changed = True
    if a.disable_archetype:
        bad = [x for x in a.disable_archetype if x not in GUIDE_ARCHETYPES]
        if bad:
            die(f"unknown archetype(s) {bad}; valid: {list(GUIDE_ARCHETYPES)}")
        prefs["disabled_archetypes"] = sorted(set(a.disable_archetype))
        changed = True
    if a.disable_block:
        bad = [x for x in a.disable_block if x not in GUIDE_BLOCKS]
        if bad:
            die(f"unknown block(s) {bad}; valid: {list(GUIDE_BLOCKS)}")
        prefs["disabled_blocks"] = sorted(set(a.disable_block))
        changed = True
    if a.reset:
        prefs = normalize_prefs({})
        changed = True
    if changed:
        cfg["content_prefs"] = prefs
        cfg["updated_at"] = now()
        store.save("config", cfg)
    # Refuse to disable EVERY shape - the builder would have nothing to rotate.
    if len(prefs["disabled_archetypes"]) >= len(GUIDE_ARCHETYPES):
        die("that disables every guide shape - the builder would have nothing to choose. "
            "Leave at least one enabled.")
    out({"ok": True, "content_prefs": prefs,
         "guide_note": render_prefs_note(prefs, "guide"),
         "tool_note": render_prefs_note(prefs, "tool")})


def cmd_conventions(store: Store, a):
    p = store.conventions_path
    if a.write:
        content = sys.stdin.read() if a.write == "-" else Path(a.write).read_text(encoding="utf-8")
        store.dir.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        out({"ok": True, "written": str(p), "bytes": len(content)})
        return
    if not p.exists():
        out({"ok": False, "exists": False, "path": str(p), "message": "No conventions file - run the setup workflow first."})
        return
    out({"ok": True, "exists": True, "path": str(p), "content": p.read_text(encoding="utf-8")})


# -- authority zones ---------------------------------------------------------


def kd_zones(dr) -> dict:
    d = dr or 0
    if d >= 35:
        return {"auto_approve_below": 35, "pending_below": 45}
    if d >= 20:
        return {"auto_approve_below": 25, "pending_below": 35}
    if d >= 10:
        return {"auto_approve_below": 15, "pending_below": 25}
    return {"auto_approve_below": 10, "pending_below": 20}


def volume_band(dr) -> dict:
    d = dr or 0
    if d >= 35:
        return {"floor": 500, "ceiling": None, "soft_edge": None}
    if d >= 20:
        return {"floor": 300, "ceiling": 3000, "soft_edge": 6000}
    if d >= 10:
        return {"floor": 200, "ceiling": 1500, "soft_edge": 3000}
    return {"floor": 100, "ceiling": 800, "soft_edge": 1500}


# -- overview / next actions -------------------------------------------------


def cmd_overview(store: Store, a):
    cfg = store.config()
    queue = store.load("queue")
    pages = store.load("pages")
    keywords = store.load("keywords")
    ranks = store.stream("ranks")
    latest: dict[str, dict] = {}
    for row in ranks:
        k = row.get("keyword")
        if k and (k not in latest or row.get("checked_at", "") > latest[k].get("checked_at", "")):
            latest[k] = row

    def bucket(t, s):
        return [r for r in queue if r.get("type") == t and r.get("status") == s]

    page_rows = []
    for p in sorted(pages, key=lambda r: r.get("published_at") or "", reverse=True):
        age = days_since(p.get("published_at"))
        rank = latest.get((p.get("primary_keyword") or "").lower(), {})
        page_rows.append(
            {
                "url": p.get("url"),
                "title": p.get("title"),
                "type": p.get("type"),
                "keyword": p.get("primary_keyword"),
                "archetype": p.get("archetype"),
                "age_days": age,
                "settled": (age or 0) >= 21,
                "position": rank.get("position"),
            }
        )
    settled = [p for p in page_rows if p["settled"]]
    out(
        {
            "ok": True,
            "project": {
                "name": cfg.get("name"),
                "domain": cfg.get("domain"),
                "repo": cfg.get("github_repo"),
                "mode": cfg.get("mode"),
                "auto_approve_tools": cfg.get("auto_approve_tools"),
                "internal_linking": cfg.get("internal_linking"),
                "serp_provider": cfg.get("serp_provider"),
                "dr": cfg.get("dr"),
                "kd_zones": kd_zones(cfg.get("dr")),
                "volume_band": volume_band(cfg.get("dr")),
                "conventions": store.conventions_path.exists(),
            },
            "queue": {
                "guides_approved": len(bucket("guide", "approved")),
                "guides_pending": len(bucket("guide", "pending")),
                "tools_approved": len(bucket("tool", "approved")),
                "tools_pending": len(bucket("tool", "pending")),
                "in_progress": len([r for r in queue if r.get("status") == "in_progress"]),
                "done": len([r for r in queue if r.get("status") == "done"]),
            },
            "tracked_keywords": len(keywords),
            "pages_total": len(page_rows),
            "pages_settled": len(settled),
            "outcome_sample_ready": len(settled) >= 10,
            "guides": [p for p in page_rows if p["type"] == "guide"],
            "tools": [p for p in page_rows if p["type"] == "tool"],
        }
    )


def cmd_next_actions(store: Store, a):
    cfg = store.config()
    queue = store.load("queue")
    pages = store.load("pages")
    actions = []
    if not store.conventions_path.exists():
        actions.append({"priority": 1, "action": "Run the setup workflow - .seo/conventions.md does not exist yet."})
    approved_guides = [r for r in queue if r.get("type") == "guide" and r.get("status") == "approved"]
    pending_guides = [r for r in queue if r.get("type") == "guide" and r.get("status") == "pending"]
    approved_tools = [r for r in queue if r.get("type") == "tool" and r.get("status") == "approved"]
    pending_tools = [r for r in queue if r.get("type") == "tool" and r.get("status") == "pending"]
    if len(approved_guides) < 7:
        actions.append(
            {
                "priority": 2,
                "action": f"Run research - the guide tank holds {len(approved_guides)}/7 approved ideas "
                f"({len(pending_guides)} pending).",
            }
        )
    if len(approved_tools) + len(pending_tools) < 1:
        actions.append({"priority": 2, "action": "Run research - the tool queue is empty and the weekly tool build has nothing to ship."})
    if cfg.get("mode") == "semi" and pending_guides:
        actions.append({"priority": 3, "action": f"Review {len(pending_guides)} pending guide ideas - semi mode waits on your call."})
    if approved_guides:
        actions.append({"priority": 3, "action": f"Run build-guide - '{sorted(approved_guides, key=queue_sort_key)[0]['title']}' is next in the queue."})
    if approved_tools and cfg.get("auto_approve_tools"):
        actions.append({"priority": 4, "action": f"Run build-tool - '{approved_tools[0]['title']}' is approved and waiting."})
    stale = [r for r in queue if r.get("status") == "in_progress" and (days_since(r.get("history", [{}])[-1].get("at")) or 0) > 1]
    for row in stale:
        actions.append({"priority": 1, "action": f"'{row['title']}' has been in_progress for over a day - a build died mid-run. Reset it to approved or finish it."})
    if not pages:
        actions.append({"priority": 5, "action": "Nothing published yet - the first build-guide run starts the rank/traffic history."})
    unsubmitted = [p for p in pages if p.get("url") and not p.get("indexnow_submitted_at")]
    if unsubmitted:
        actions.append({"priority": 4, "action": f"{len(unsubmitted)} published page(s) never pinged to "
                        "IndexNow (free, instant, Bing/Yandex): scripts/indexnow.py ping --pending"})
    ungoogled = [p for p in pages if p.get("url") and not p.get("index_requested_at")]
    if ungoogled:
        actions.append({"priority": 5, "action": f"{len(ungoogled)} page(s) awaiting the Google "
                        "'Request indexing' click: scripts/indexnow.py google-steps"})
    dr_age = days_since(cfg.get("dr_fetched_at"))
    if cfg.get("dr") is None or (dr_age or 99) > 7:
        actions.append({"priority": 5, "action": "Refresh the site's authority score: scripts/authority.py --domain <domain> --save"})
    actions.sort(key=lambda x: x["priority"])
    out({"ok": True, "next_actions": actions})


def cmd_log_run(store: Store, a):
    store.append(
        "runs",
        {
            "workflow": a.workflow,
            "ok": not a.failed,
            "summary": a.summary,
            "detail": parse_json_arg(a.detail, "detail"),
            "at": now(),
        },
    )
    out({"ok": True, "logged": a.workflow})


def cmd_runs(store: Store, a):
    rows = store.stream("runs")[-a.limit :]
    out({"ok": True, "count": len(rows), "runs": rows})


# --------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seostate.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="repo root (defaults to nearest .seo/ or .git/, else cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create/update .seo/ for this repo")
    s.add_argument("--name")
    s.add_argument("--domain")
    s.add_argument("--repo")
    s.add_argument("--mode", choices=["auto", "semi"])
    # Keep in sync with serp.py's --provider list, minus nothing: an init that
    # refuses a provider serp.py accepts is a trap the operator hits at setup.
    s.add_argument("--serp-provider",
                   choices=["ddg", "serpd", "browser", "searxng", "brave", "serpapi", "dataforseo", "none"])
    s.add_argument("--gsc-property")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("config", help="read or set project config")
    s.add_argument("--set", action="append", metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("propose", help="add a suggestion to the queue")
    s.add_argument("--type", required=True, choices=SUGGESTION_TYPES)
    s.add_argument("--title", required=True)
    s.add_argument("--keyword")
    s.add_argument("--volume", type=int)
    s.add_argument("--kd", type=float)
    s.add_argument("--authority-count", type=int)
    s.add_argument("--intent", choices=["commercial", "comparison", "informational", "transactional"])
    s.add_argument("--archetype")
    s.add_argument("--rationale", required=True)
    s.add_argument("--serp-notes")
    s.add_argument("--spec", help="JSON brief")
    s.add_argument("--source", default="research", choices=SUGGESTION_SOURCES)
    s.add_argument("--trend-topic-id")
    s.add_argument("--allow-duplicate", action="store_true")
    s.set_defaults(fn=cmd_propose)

    s = sub.add_parser("update", help="change a suggestion's status")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=SUGGESTION_STATUSES)
    s.add_argument("--note")
    s.add_argument("--pr-url")
    s.add_argument("--rationale")
    s.add_argument("--serp-notes")
    s.add_argument("--intent")
    s.add_argument("--archetype")
    s.add_argument("--authority-count", type=int)
    s.add_argument("--spec")
    s.set_defaults(fn=cmd_update_suggestion)

    s = sub.add_parser("suggestions", help="list the queue in build order")
    s.add_argument("--status", nargs="*", choices=SUGGESTION_STATUSES)
    s.add_argument("--type", choices=SUGGESTION_TYPES)
    s.add_argument("--source", choices=SUGGESTION_SOURCES)
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_suggestions)

    s = sub.add_parser("reorder", help="front-place ids (comma separated) in build order")
    s.add_argument("ids")
    s.set_defaults(fn=cmd_reorder)

    s = sub.add_parser("track", help="track keywords")
    s.add_argument("--keywords", help="comma separated")
    s.add_argument("--json", help='[{"keyword":..,"volume":..,"kd":..,"intent":..}]')
    s.set_defaults(fn=cmd_track)

    s = sub.add_parser("keywords", help="list tracked keywords with latest position")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_keywords)

    s = sub.add_parser("record-rank", help="append rank check(s)")
    s.add_argument("--keyword")
    s.add_argument("--position", type=int)
    s.add_argument("--url")
    s.add_argument("--provider")
    s.add_argument("--json", help='[{"keyword":..,"position":..,"url":..}]')
    s.set_defaults(fn=cmd_record_rank)

    s = sub.add_parser("rankings", help="rank history summary")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(fn=cmd_rankings)

    s = sub.add_parser("log-page", help="record a published page")
    s.add_argument("--url", required=True)
    s.add_argument("--title")
    s.add_argument("--type", choices=["guide", "tool", "landing"])
    s.add_argument("--keyword")
    s.add_argument("--pr-url")
    s.add_argument("--published-at")
    s.add_argument("--archetype")
    s.add_argument("--information-gain")
    s.add_argument("--index-requested", action="store_true",
                   help="stamp that Search Console 'Request indexing' was done for this page")
    s.add_argument("--indexnow-submitted", metavar="ISO_TS", help="stamp an IndexNow submission")
    s.add_argument("--indexed", action="store_true",
                   help="stamp that Google verifiably has the page (URL Inspection said so)")
    s.set_defaults(fn=cmd_log_page)

    s = sub.add_parser("pages", help="list published pages")
    s.add_argument("--type", choices=["guide", "tool", "landing"])
    s.set_defaults(fn=cmd_pages)

    s = sub.add_parser("pacing", help="today's publishing slot verdict")
    s.set_defaults(fn=cmd_pacing)

    s = sub.add_parser("trend-add", help="put a subject on the radar")
    s.add_argument("--title", required=True)
    s.add_argument("--why-now", required=True)
    s.add_argument("--signals", help="pipe-separated")
    s.add_argument("--sources", help="pipe-separated")
    s.add_argument("--seed-url")
    s.add_argument("--seed-stats")
    s.set_defaults(fn=cmd_trend_add)

    s = sub.add_parser("trends", help="list radar subjects")
    s.add_argument("--status", nargs="*", choices=TREND_STATUSES)
    s.set_defaults(fn=cmd_trends)

    s = sub.add_parser("trend-update", help="change a subject's status")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=TREND_STATUSES)
    s.set_defaults(fn=cmd_trend_update)

    s = sub.add_parser("record-scan", help="stamp the last trend scan time")
    s.set_defaults(fn=cmd_record_scan)

    s = sub.add_parser("prospect-add", help="add a backlink prospect")
    s.add_argument("--domain", required=True)
    s.add_argument("--url")
    s.add_argument("--dr", type=float)
    s.add_argument("--link-type", choices=["dofollow", "nofollow", "unknown"], default="unknown")
    s.add_argument("--reason", required=True)
    s.add_argument("--angle")
    s.set_defaults(fn=cmd_prospect_add)

    s = sub.add_parser("prospects", help="list backlink prospects")
    s.add_argument("--status", nargs="*", choices=PROSPECT_STATUSES)
    s.set_defaults(fn=cmd_prospects)

    s = sub.add_parser("prospect-update", help="change a prospect's status")
    s.add_argument("id")
    s.add_argument("--status", required=True, choices=PROSPECT_STATUSES)
    s.add_argument("--note")
    s.set_defaults(fn=cmd_prospect_update)

    s = sub.add_parser("record-ai", help="record AI-visibility samples")
    s.add_argument("--json", required=True)
    s.set_defaults(fn=cmd_record_ai)

    s = sub.add_parser("ai-visibility", help="AI citation rate + gap domains")
    s.add_argument("--days", type=int, default=90)
    s.set_defaults(fn=cmd_ai_visibility)

    s = sub.add_parser("profile", help="read or write the directory profile")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("prefs", help="owner template controls the build playbooks obey")
    s.add_argument("--house-rules", help="free-text instructions injected verbatim into the builds")
    s.add_argument("--disable-archetype", nargs="*", choices=list(GUIDE_ARCHETYPES),
                   help="guide shapes to remove from the rotation")
    s.add_argument("--disable-block", nargs="*", choices=list(GUIDE_BLOCKS),
                   help="skeleton parts to drop from every guide")
    s.add_argument("--reset", action="store_true", help="back to all defaults on")
    s.set_defaults(fn=cmd_prefs)

    s = sub.add_parser("conventions", help="read or write .seo/conventions.md")
    s.add_argument("--write", metavar="PATH_OR_-", help="write from a file, or - for stdin")
    s.set_defaults(fn=cmd_conventions)

    s = sub.add_parser("overview", help="everything a run needs at start")
    s.set_defaults(fn=cmd_overview)

    s = sub.add_parser("next-actions", help="what to do next, ranked")
    s.set_defaults(fn=cmd_next_actions)

    s = sub.add_parser("log-run", help="record a workflow run outcome")
    s.add_argument("--workflow", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--detail")
    s.add_argument("--failed", action="store_true")
    s.set_defaults(fn=cmd_log_run)

    s = sub.add_parser("runs", help="recent run log")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_runs)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = Store(Path(args.root) if args.root else None)
    args.fn(store, args)


if __name__ == "__main__":
    main()
