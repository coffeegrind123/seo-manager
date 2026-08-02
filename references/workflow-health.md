# Workflow: health (technical audit → queue)

**Cadence:** quarterly, and before any big push. **Job:** run the technical
audits that already exist as separate skills, and turn what they find into
queue items instead of a report nobody reads.

This skill deliberately does **not** reimplement technical auditing. There is a
full set of audit skills already, each better at its one job than a general
workflow would be. What was missing is the bridge: an audit produces findings, a
program needs *work items*, and nothing connected the two.

---

## 1. Know which tool you want

| Question | Use |
|---|---|
| One page, quick on-page check | `seo-audit` |
| One page, deep + PageSpeed + social | `seo-audit-full` |
| Whole site, everything, one report | `full-seo-audit` |
| One dimension across the site | the specific audit skill (below) |
| What bots actually did | **`workflow-crawl-log.md`** (this skill) |
| Generated silo at scale | **`workflow-programmatic.md`** (this skill) |
| One URL, right now, no crawl | **`pagecheck.py`** (this skill) |

`pagecheck.py` is the quick single-URL read, and it works on **any** URL —
including a competitor's, which the audit skills are not aimed at:

```bash
python3 $SEO/pagecheck.py schema  https://rival.example/page   # Google's own extractor
python3 $SEO/pagecheck.py html    https://oursite.example/page # W3C validity
python3 $SEO/pagecheck.py history https://rival.example/page --since 2026-05-01
python3 $SEO/pagecheck.py vitals  https://oursite.example/page # lab + real-user CWV
```

`history` is the one with no equivalent elsewhere in this skill: when a page
starts losing rank, it tells you whether the page-1 result that replaced you was
rewritten recently or has sat untouched for two years. ⚠ Read its caveats before
quoting a number — the version count is byte-level variation inside a recency
window, not an edit count.

`vitals` returns BOTH a lab run and `field_crux` — real-user 75th-percentile
LCP/CLS/INP, which is the ranking-relevant half. ⚠ **Empty `field_crux` is not a
zero and not a failure**: CrUX only reports origins with enough traffic to be
statistically meaningful, so `null` means *too few real users to measure*. On a
small site expect the lab numbers alone, and never report the absence as a
performance finding.

The single-dimension audits: `sitemap-audit`, `robots-txt-audit`,
`redirect-audit`, `internal-link-audit`, `external-link-audit`,
`meta-data-audit`, `heading-structure-audit`, `image-seo-audit`,
`schema-markup-audit`, `canonical-tag-audit`, `open-graph-audit`,
`mixed-content-audit`, `pagination-audit`, `soft-404-audit`,
`content-quality-audit`, `keyword-cannibalization-audit`,
`site-architecture-audit`, `core-web-vitals-audit`, `llms-txt-audit`.

**Do not run all nineteen by reflex.** Pick from what you already suspect —
`seostate.py overview`, the last crawl-log scan, and the last decay run will
point at two or three. A nineteen-part report is a way of not deciding anything.

**Success criteria**: Two or three audits are selected from evidence already in hand (`seostate.py overview`, the last crawl-log scan, the last decay run) — not all nineteen by reflex. An empty `field_crux` is recorded as unmeasurable, never as a performance finding.

---

## 2. Always run these three on a content site

They are the ones whose failure silently invalidates other work:

1. **`canonical-tag-audit`** — a canonical pointing at a 404 or a redirect makes
   a page **unindexable**, and the page looks completely fine when you load it.
2. **`sitemap-audit`** — 404s and noindexed URLs in a sitemap cost trust on
   every other URL in it.
3. **`redirect-audit`** — chains and loops eat crawl budget and lose signal.

Then the one everyone skips and shouldn't: **`internal-link-audit`**, because
orphan pages are the cheapest fix in SEO and nothing else surfaces them.

**Success criteria**: `canonical-tag-audit`, `sitemap-audit` and `redirect-audit` have all run, plus `internal-link-audit`, and each returned a read result rather than an error.

---

## 3. Triage — most findings are not work

An audit reports everything it can detect. A program acts on what changes an
outcome. Sort every finding into:

| Tier | Definition | Action |
|---|---|---|
| **Blocking** | pages cannot be indexed or served: canonical→404, noindex on a money page, mixed content, 5xx, sitemap of 404s | fix now, outside the queue |
| **Structural** | costs traffic across many pages: orphans, redirect chains, missing schema on a template, no H1 | one queue item per *template*, never per page |
| **Cosmetic** | real but low-yield: alt text on decorative images, meta description length | batch, or ignore honestly |
| **Noise** | true and irrelevant to this site | say so and move on |

**One queue item per template, not per page.** A generated silo will report the
same finding 2,000 times; it is *one* fix in the generator. Queueing 2,000 items
is how a queue becomes unusable.

**Success criteria**: Every finding is assigned exactly one tier. Structural findings are collapsed to one item per TEMPLATE, so a generated silo produces one row rather than thousands.

---

## 4. Queue it

```bash
python3 $SEO/seostate.py propose --type update \
  --keyword "<the affected page's keyword, or the template name>" \
  --rationale "health: <finding>, affects <N> pages via <template>; \
fix is <the change>" 
```

Blocking findings do **not** go in the queue. Fix them, then log the run.

**Success criteria**: Structural and cosmetic findings are in the queue with a rationale naming the finding, the page count and the template. Blocking findings are FIXED, not queued.

---

## 5. What the audits will NOT tell you

Worth knowing so the report is honest about its own edges:

- **What Googlebot actually did.** Every audit crawls *as itself*. Only the
  access log knows what the real bot fetched, how often, and what it was served
  → `workflow-crawl-log.md`.
- **Whether a page is indexed.** Auditors read the page; only Search Console
  knows Google's decision → the `search-console` skill.
- **Whether a thin page is a problem.** `content-quality-audit` measures the
  text. Indexation decides → `workflow-programmatic.md`.
- **Whether a fix worked.** That is `decay` and `drift`, weeks later.

**Success criteria**: The report states which questions the audits could not answer and which workflow or skill owns each.

---

## 6. Report

```bash
python3 $SEO/seostate.py log-run --workflow health --ok --summary "..."
```

Name which audits ran, the blocking count (and that they were fixed), the number
of queue items created, and **what you deliberately did not act on**. A finding
you consciously declined is a decision worth recording; an unexplained gap
between the audit and the queue is not.

**Success criteria**: The report names which audits ran, the blocking count and that they were fixed, the queue items created, and what was deliberately not acted on. The run is logged.
