# Workflow: drift (what changed on page 1)

**Cadence:** fortnightly, or on demand when rankings move. **Job:** watch the
whole SERP rather than only your own position, so a competitor's move or a SERP
feature change is visible while it is still early.

`rankcheck.py` tracks one number per keyword: where you sit. That is the number
you report, and it is a poor early-warning system — by the time *your* position
moves, the cause is weeks old. The SERP around you moves first.

---

## 1. Snapshot

```bash
python3 $SEO/seodoctor.py
python3 $SEO/serpd.py --start                     # NO trailing &
python3 $SEO/drift.py snapshot \
  --keywords-from .seo/keywords.json \
  --out .seo/drift/$(date -u +%F).json
```

Dated file, committed with the rest of `.seo/`. The whole workflow is a diff
between two of these, so the first run produces no findings and that is correct.

**`unread` keywords are excluded from the snapshot, never stored empty.** A
refused read is a failed read, not an empty page 1 — storing it as one makes the
next diff report a total page-1 wipeout followed by a full recovery. Re-run the
unread ones (`seodoctor.py --hard` if they persist) before moving on.

**Success criteria**: A dated snapshot exists under `.seo/drift/` and every `unread` keyword was re-run until it read or was excluded. No keyword is stored with an empty page 1. On a first run, zero findings is the correct outcome.

---

## 2. Compare

```bash
python3 $SEO/drift.py compare \
  --before .seo/drift/2026-07-01.json \
  --after  .seo/drift/2026-08-01.json \
  --updates ~/.claude/skills/seo-manager/assets/google-updates.json \
  --target <your-domain>
```

**Success criteria**: Two dated snapshots were diffed with `--target` set to your own domain and the update calendar supplied.

---

## 3. Read `verdict` first — it decides everything after it

| verdict | means | response |
|---|---|---|
| **SITE-WIDE VOLATILITY** | mean churn ≥ 40% | The index is moving. **Do not start rewriting.** Wait for it to settle and re-measure. |
| **LOCALISED MOVEMENT** | calm overall, some SERPs churned hard | Those individual queries are being re-decided. Work them one at a time. |
| **STABLE** | page 1 is broadly the same domains | Any position change you see is genuinely yours. |

This is the distinction a single keyword's history cannot give you: **did my
page move, or did everything move?** They look identical from one rank chart and
demand opposite responses.

**Success criteria**: The verdict is read and drives the response. Under SITE-WIDE VOLATILITY no rewrite is proposed at all — the run ends with "re-measure in two weeks".

---

## 4. `recurring_entrants` — the competitive early warning

A domain entering page 1 on **several** of your keywords in one window is a
competitor moving into your space. One keyword is noise; three is a strategy.

This is the highest-value output here, because it arrives while you still
outrank them. Look at what they published, then decide: defend the cluster with
depth, or concede it and hunt narrower.

`recurring_exits` matters too — a domain leaving several page 1s at once has
usually been hit by something, and whatever it is may apply to you.

**Success criteria**: Every domain entering 3+ of your page 1s is named, with what they published, and a stated decision to defend or concede. `recurring_exits` is checked for a cause that may apply to you.

---

## 5. `ai_overview_changes` — the trap this catches

`gained` means **clicks will fall at an unchanged position.** An AI Overview
appearing is the largest single click-through event that can happen to a query.

Read that against `decay`: a page whose position held and whose clicks fell, on
a query that just gained an AI Overview, is **not decaying**. Rewriting it fixes
nothing. The honest responses are to target the overview itself (concise,
extractable, well-structured answers) or to accept the query is worth less now.

**Success criteria**: Every query that GAINED an AI Overview is excluded from the decay path, and the response is restructuring or acceptance — never a rewrite.

---

## 6. `algorithm_updates_in_window` — how to read it honestly

An overlap **raises the bar** for a content explanation. It does not supply one.

Core updates run for weeks, so something almost always overlaps; treating every
overlap as causation is astrology. Its real job is the reverse — to stop you
inventing a content story for a fortnight when the whole index was moving, and
to stop forty rewrites over a week Google had already announced.

The calendar (`assets/google-updates.json`) carries a Google-owned source URL on
every entry. It has **no API and needs manual top-up** —
`_provenance.how_to_top_up` says how, and `_provenance.entries_complete_through`
says where its coverage stops. It is not authoritative about anything it
does not list; absence of an update in the window is not evidence there was none.

**Success criteria**: An overlap is reported as raising the bar for a content explanation, never as a cause. The absence of a listed update is explicitly not reported as evidence that nothing happened.

---

## 7. Act

| Signal | Response |
|---|---|
| Site-wide volatility | Wait. Re-measure in two weeks. Nothing else. |
| A recurring entrant on a cluster you own | Read their page. Depth, freshness or a better answer shape — decide which, then queue an `update`. |
| AI Overview gained | Restructure for extractability, or accept the lower ceiling. Not a rewrite. |
| Your own position slipped on a stable SERP | A real, isolated loss. Straight into the `decay` path. |
| A keyword whose page 1 turned over completely | Intent may have shifted. Re-run the remit test before defending it. |

**Success criteria**: Every signal in the table has a stated response, and queue items exist only for the rows the table sends to the queue.

---

## 8. Report

```bash
python3 $SEO/seostate.py log-run --workflow drift --ok --summary "..."
```

Give the window, the mean churn, the verdict, and any recurring entrant by name.
**A STABLE verdict is a real finding** — it means position changes in the same
window were genuinely about your pages, which is what makes the decay workflow's
conclusions trustworthy.

**Success criteria**: The report gives the window, mean churn, the verdict, and any recurring entrant by name. A STABLE verdict is reported as a real finding, not as "nothing to say". The run is logged.
