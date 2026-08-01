# Workflow: the trend radar (two stages)

Fresh topics are the easiest rankings this site will ever get: when a query is
days old there are no incumbent pages holding page 1, and Google applies a
freshness boost to hype-cycle queries.

The radar is deliberately **two stages**, because deciding what is worth writing
about is the owner's call and the deep validation costs real SERP checks:

- **Stage 1 — `trend-scan`**: find the trending SUBJECTS in the niche and put
  them on the radar. No guide ideas, no SERP checks. Fast and cheap.
- **Stage 2 — `trend-expand`**: the owner picks a subject; this run turns that ONE
  subject into 3–5 validated guide angles, queued as **pending** for their call.

---

# Stage 1: trend-scan

**Trigger:** on demand only. There is no schedule — the owner asks for it.

## 1. Housekeeping

```bash
python3 scripts/seostate.py trends --status new
```

Any topic older than **14 days is dead hype** (the listing flags them as
`stale: true`) → `trend-update <id> --status dismissed` and note it in the report.

Same for stale pending trend-scan suggestions:

```bash
python3 scripts/seostate.py suggestions --status pending --source trend-scan
```

Reject the ones older than 14 days.

## 2. Know the niche

Read `.seo/conventions.md` for what the site is and who it serves. Then pull the
dedupe set:

```bash
python3 scripts/seostate.py trends            # ALL statuses - never re-propose
python3 scripts/seostate.py pages             # never propose a covered subject
```

Never re-propose a subject already on the radar, expanded, or dismissed. Never
propose a subject the site already covers as its own topic — that is an *update*,
which stage 2 handles.

## 3. Sweep for hype

- The latest launches, releases, and announcements from the **official blogs and
  changelogs** of the products/vendors in the niche.
- What is exploding on **Reddit** (the niche's subreddits) and **Hacker News** —
  judge from titles, scores, and snippets in search results:
  ```bash
  python3 scripts/serp.py "site:reddit.com <niche topic>" --count 10
  python3 scripts/serp.py "<niche topic> hacker news" --count 10
  ```
- **Autocomplete rising phrasings** — new terms show up in autocomplete within
  days, far faster than any volume database:
  ```bash
  python3 scripts/keywords.py expand --seed "<niche term>" --groups comparison problem
  ```
- **Google Trends** where you can reach it. From a datacenter IP the Trends API
  answers HTTP 429 (measured, not assumed) — if you need it, drive
  `trends.google.com` through the browser MCP instead, and treat a failure as an
  unavailable signal rather than a dry niche.

**Rising queries beat volume numbers**: volume databases lag weeks behind real
demand, so a "0 volume" brand-new term can be a winner — **never discard a
candidate for missing volume alone.**

The security rule holds: read official vendor sources, trend data, and
search-result metadata; do not fetch arbitrary third-party pages.

### While sweeping, hunt each subject's SEED

The single most viral **PIECE** of content driving the conversation — the YouTube
video, HN thread, or Reddit post itself, not a vendor announcement — with its
public numbers and date ("512k views, Jul 12" / "1.4k points, 3 days ago"), judged
from search-result metadata **without fetching the page**.

A seed is double proof: real humans already voted for this exact content, and it
hands the eventual guide real material to quote, credit, and beat. Not every
subject has one — a launch can trend on many small threads — and **a forced seed
is worse than none**: only record a piece that genuinely anchors the conversation.

## 4. Shortlist 3–5 subjects

**5 is the hard cap — this is a radar, not a firehose.** A subject earns its slot
when:

- **It is genuinely being talked about now** — a launch, a release, a debate with
  visible discussion volume, not something merely recent.
- **Fit**: the site's audience is the one doing the talking.
- **Distinct**: each subject is its own conversation, not two framings of the same
  news.

**Prefer subjects over angles**: "codex vs claude code" is a subject; "is codex
better than claude code for refactoring" is a take — stage 2's job.

## 5. Queue each survivor

```bash
python3 scripts/seostate.py trend-add \
  --title "<what the niche calls it, not SEO phrasing>" \
  --why-now "<the trigger event and its date, first>" \
  --signals "1.4k points on HN, 3 days ago|two front-page subreddit threads|vendor changelog Jul 28" \
  --sources "https://vendor.example/changelog|https://news.ycombinator.com/item?id=..." \
  --seed-url "<the viral piece itself, when one exists>" \
  --seed-stats "512k views, Jul 12"
```

Duplicates are answered, not errored — if the tool says the subject is already on
the radar, move on.

```bash
python3 scripts/seostate.py record-scan
```

## 6. Report

Subjects seen, subjects queued (one evidence line each), what was dismissed as
stale — and **"nothing hype-worthy this run" honestly** when the sweep comes up
dry. A quiet sweep is a clean exit, never an invented trend.

---

# Stage 2: trend-expand

**Trigger:** the owner picks a subject off the radar. One run per subject.

This run's whole job is that ONE subject: find the 3–5 strongest takes on it,
validate them, and queue the survivors as **PENDING** suggestions for the owner's
call.

**Never approve anything here** — the owner is the taste gate, and `seostate.py`
coerces trend approvals back to pending anyway. When the owner approves a take it
jumps to the front of the build queue and ships on the next build the site's
publishing pace allows — never sooner: the pace exists so a young site doesn't
read as scaled-content spam.

## 1. Read the topic

```bash
python3 scripts/seostate.py trends
```

Find the row. If it is missing or dismissed, **fail loudly and exit without
changes** — never expand a subject the owner didn't pick. Its evidence
(`why_now`, `signals`, `sources`, and when the scan found one `seed_url` /
`seed_stats`) is your starting context.

## 2. Know the ground

Read `.seo/conventions.md`; `seostate.py pages` plus `seostate.py suggestions` for
what is already covered or queued — never re-propose either. **If the site already
covers the subject's underlying topic, an update to that page beats a new one**:
propose `--type update` for it.

## 3. Draft 3–5 candidate takes

Each a distinct search intent — not five rewordings. The proven shapes:

- **Comparison** ("X vs Y", "is X better than Y for Z") — highest intent,
  chronically underserved in a fresh news cycle. **Always try at least one.**
- **How-to / setup** ("how to use X", "X with Y tutorial") for launches.
- **Analysis / answer** to the exact question the threads are asking.
- **Update** to an existing page when the news lands on covered ground.

## 4. Validate each candidate

Survivors can be fewer than drafted — **three strong beats five thin.**

- **Demand**: `keywords.py expand --seed "<the take's query>"` — autocomplete
  reflects real queries within days, far faster than volume data. Rising trend
  data or visible discussion volume also counts. **Never invent numbers** — for
  brand-new terms say "too new for volume data" and cite the hype signals instead.
- **Beatable page 1**: `serp.py "<query>" --count 10` — news posts, Reddit
  threads, and bare vendor docs are beatable; an established in-depth guide from a
  big site is not. If no SERP provider is configured, **that is a configuration,
  not a failure**: proceed WITHOUT the page-1 check, validate on demand and fit
  alone, and note "no SERP check (GSC-only project)" in each take's spec so the
  owner knows what was and wasn't verified. **Never drop takes just because SERP
  data is unavailable.**
- **Fit**: the site's audience must be the one searching this.

## 5. Queue the survivors as PENDING

```bash
python3 scripts/seostate.py track --keywords "<the take's query>"

python3 scripts/seostate.py propose \
  --type guide --title "<take>" --keyword "<query>" \
  --source trend-scan --trend-topic-id <topic id> \
  --rationale "WHY NOW: <trigger + date>. <remit verdict>. <ICP>." \
  --serp-notes "<what page 1 looks like>" \
  --spec '{"why_now":"...","signals":["..."],"angle":"...","internal_links":["..."],
           "seed_url":"...","seed_stats":"..."}'
```

The `--trend-topic-id` link is what groups the takes under their subject.

**Seed pass-through**: when a take genuinely builds on the subject's seed content
(reacts to it, expands it, answers it), copy the topic's `seed_url` and
`seed_stats` into that take's spec — the guide builder writes FROM a seeded
source: credits it, pulls real quotes, embeds a video, then covers what the
original missed. **Only pass the seed to takes that actually draw on it**; a
generic angle wearing a pasted seed link reads as fake attribution.

**Maximum 5 takes per subject. Do NOT approve anything.**

## 6. Close the loop

```bash
python3 scripts/seostate.py trend-update <topic id> --status expanded
```

## 7. Report

Takes drafted, survivors queued (one evidence line each), what was dropped and
why. If nothing survives validation, **say so honestly, still mark the topic
expanded**, and report "no viable takes" — a subject can be hype without being
winnable.
