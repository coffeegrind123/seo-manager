# Workflow: decay (find the pages that are quietly losing)

**Cadence:** monthly. **Job:** find published pages that have lost ground, tell
them apart from pages whose *market* shrank, and queue only the ones a rewrite
can actually save.

Every other workflow here points forward. This one points backward, because on
any site older than a few months the highest-return work available is usually
not a new page — it is the page that ranked at #6, slipped to #14, and now earns
a tenth of what it did. Nothing alerts on that. Nothing broke. It is silent by
construction, and it compounds.

---

## The distinction the whole workflow exists for

Four things look identical in a clicks chart and want opposite responses.

| | impressions | position | response |
|---|---|---|---|
| **decay** | down | **worse** | rewrite — somebody out-answered you |
| **demand drop** | down | held or better | **nothing**. The query stopped being asked. |
| **cannibalisation** | down | worse | consolidate — *you* did this with a later publish |
| **settling** | down | n/a | nothing. A discovery burst decaying to a real level. |

**Only the first is decay.** A rewrite aimed at a demand drop is pure waste: the
page is performing exactly as well as before against a smaller market. Telling
them apart requires **position**, which is why this reads Search Console and not
an analytics tool.

---

## 1. Pull two periods

Use `gsc.py decay-export` (one command, both windows) or `gsc.py query`. You need the `page` dimension; add `query` to
unlock the cannibalisation check.

Compare **equal-length, recent, non-overlapping** windows — 28 and 28 is the
default. Avoid straddling a seasonal edge, and remember GSC data settles for
~3 days, so end the recent window there, not today.

```bash

**Success criteria**: Two equal-length, recent, non-overlapping GSC exports carrying the `page` dimension, with the recent window ending ~3 days back so the data has settled. Unequal windows invalidate everything downstream.
# two exports, previous then current
python3 $SEO/decay.py compare --previous prev.json --current cur.json \
  --previous-start 2026-06-01 --current-start 2026-06-29 --current-end 2026-07-26 \
  --pages .seo/pages.json \
  --updates ~/.claude/skills/seo-manager/assets/google-updates.json
```

One export carrying the `date` dimension splits here instead:

```bash
python3 $SEO/decay.py split --file rows.json --on 2026-06-29
```

---

## 2. Read `sitewide_signal` FIRST

If most measurable pages decayed together, that is **one** event — an algorithm
update, a technical regression, a crawl collapse — not N content problems. The
script says so explicitly when it sees the pattern.

Chasing it page by page is the expensive failure mode here: forty rewrites for a
cause no rewrite touches. Check, in order:

1. `algorithm_updates_in_window` — an overlap **raises the bar** for a content
   explanation. It never proves causation on its own; core updates run for weeks
   and something always overlaps.
2. `crawllog.py scan` — did crawl rate collapse in the same window?
3. Did anything ship? A deploy outage, a template change, a robots.txt edit.

Only when the site-wide explanations are excluded do you work the list.

**Success criteria**: `sitewide_signal` was read and the site-wide explanations (algorithm overlap, crawl collapse, a deploy) were each checked and excluded before any page was worked individually.

---

## 3. Work the `decay` list

Ordered by impressions lost, which is where the recoverable value is.

For each candidate worth saving, **re-run the SERP before committing a slot**:

```bash
python3 $SEO/serp.py "<the query it used to win>" --count 10 --target-domain <domain>
```

Then the ordinary bar from `quality-bar.md`:

- **Authority count 4+ on page 1 → let it go.** The query moved out of reach.
  Rewriting into a fight you cannot win is worse than doing nothing, because it
  costs a build slot *and* feels like progress.
- **Authority count 0–2 → queue it** as `type: update`.
- Ask what changed: is the winner more current, more complete, better shaped for
  the intent? The answer is the brief.

```bash
python3 $SEO/seostate.py propose --type update --keyword "<kw>" \
  --rationale "decay: 6->14, impressions 420->38 over 28d; page-1 authority count 1/10; \
winner covers <X> which we do not" --serp-notes "..."
```

An update still has to clear **information gain** and the **sameness gate**. A
refresh that only re-words is not a refresh.

**Success criteria**: Every candidate had its SERP re-read and was judged against the page-1 authority count, not its old position. Only candidates at authority 0-2 are queued, each with a rationale naming the position and impression delta and what the winner covers.

---

## 4. `demand_drop` — the honest empty result

These get **no queue items**. Say so in the report. A workflow that queues
demand drops manufactures work and buries the real decay list under it.

The one legitimate follow-up: if a whole cluster's demand fell, the *topic* may
be fading, which is a research-workflow input, not a rewrite.

**Success criteria**: Zero queue items were created from demand drops, and the report says so explicitly.

---

## 5. `lost` — verify before diagnosing

A page reporting zero impressions may not be a ranking story at all. Check, in
this order, before assuming anything:

1. Does it still return **200**? (`crawllog.py`, or curl it.)
2. Is it still in the sitemap, and still internally linked?
3. `noindex` / canonical accidentally pointing elsewhere?
4. **Was it ever crawled again?** A page Googlebot has not fetched in months
   cannot recover.

A technical cause is far more common here than a quality one, and it is cheaper
to fix.

**Success criteria**: Every `lost` page has been checked for status code, sitemap presence, internal links, `noindex`/canonical, and recent crawl BEFORE any quality explanation was offered.

---

## 6. Cannibalisation

```bash
python3 $SEO/decay.py cannibal --current gsc-page-query.json
```

Two of your own URLs ranking for one query splits click-through and link signal,
and Google re-picks between them per search. The fix is consolidation — decide
which page deserves the query and point the other at it. **Not** a rewrite of
both.

If the weaker URL is generated/programmatic and the stronger is curated, the
generated one canonicalises to the curated one, never the reverse.

**Success criteria**: Each cannibalised query has one page named as the winner and a consolidation action for the other. Generated pages canonicalise to curated ones, never the reverse.

---

## 7. Report

```bash
python3 $SEO/seostate.py log-run --workflow decay --ok --summary "..."
```

Name the counts in all four categories and the queue outcome. **A run that
queues nothing because everything was a demand drop is a clean, complete run** —
report it as one.

**Success criteria**: Counts named in all four categories (decay / demand drop / cannibalisation / settling) plus the queue outcome, and the run is logged. A run that queues nothing because everything was a demand drop is reported as a complete run.

---

## Traps

- **Never compare unequal windows.** 30 days against 14 halves every number and
  manufactures a site-wide decay event.
- **Never judge a page below `--min-impressions`.** Under ~10 baseline
  impressions, a swing to zero is noise wearing a percentage.
- **Position is impression-weighted, and must be.** Averaging GSC's per-row
  averages gives an obscure query at position 90 equal say with the main one, so
  a page's headline position moves while nothing about the page did.
- **Pages published inside the baseline window are excluded** (`--pages` +
  `--settle-days`). Their "before" number is a discovery burst, and every
  discovery burst decays. Counting them invents decay on your newest work.
