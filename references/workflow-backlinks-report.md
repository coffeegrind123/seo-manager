# Workflows: backlinks + report

Two short on-demand workflows.

---

# Workflow: backlinks `<keyword or competitor domain>`

**Job:** find realistic domains that might link to this site, with a concrete
reason and an outreach angle for each.

Start with the curated list — `references/backlink-playbook.md` is researched,
ordered by value, and personalized from `.seo/profile.json`. **Prospecting is what
you do after that list is worked**, not instead of it.

## 1. Trace

Find who links to the pages currently ranking for the target keyword.

**With DataForSEO** (`DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` set) — the real
backlinks endpoints give referring domains per URL. This is the only path that
sees an actual link graph.

**Free path** — you cannot read a link graph for free, so do not pretend to.
What you CAN do, and what actually finds reachable prospects:

```bash
# who writes ABOUT this space and links out
python3 scripts/serp.py "<keyword> \"tools\" OR \"resources\" OR \"roundup\"" --count 20
python3 scripts/serp.py "best <category> tools" --count 20
python3 scripts/serp.py "<competitor> alternatives" --count 20
python3 scripts/serp.py "\"submit\" OR \"add your\" <category> directory" --count 20
```

Listicles, roundups, resource pages and "alternatives" pages are the reachable
half of any link graph — they exist to link out, they update, and their authors
answer email. The unreachable half (a DR 90 publisher's editorial mention) was
never a prospect for a young site anyway.

Say in the report which path you used. **"No backlink data source configured" is
an honest answer; an invented referring-domain count is not.**

## 2. Filter

Keep relevant, plausibly-reachable prospects: niche blogs, tool directories,
newsletters, community lists, comparison pages that already list competitors.

**Drop**: spam farms, giants that will never respond, anything whose page exists
only to sell link insertions (see the playbook's do-not-buy list — a paid
insertion is the same purchase whether you find it through a marketplace or a
"prospect").

## 3. Queue

```bash
python3 scripts/seostate.py prospect-add \
  --domain example.com --url "https://example.com/best-x-tools" \
  --link-type unknown \
  --reason "links to <competitor>'s guide; we have the newer/better page on <topic>" \
  --angle "email the author: their #4 pick shut down in March, offer ours as the replacement"
```

The **reason must be concrete**. "High DR, relevant niche" is not a reason — it is
a restatement of the filter. A reason names the specific page, the specific gap,
and what you are offering.

## 4. Output

A prospect table with a suggested outreach angle per row, ordered by how likely
each is to actually answer. Then:

```bash
python3 scripts/seostate.py prospects
python3 scripts/seostate.py log-run --workflow backlinks --summary "<N prospects queued>"
```

---

# Workflow: report

**Job:** a rank + traffic summary with next actions. Plain English, numbers
included, no fluff.

## 1. Pull

```bash
python3 scripts/seostate.py rankings --days 30
python3 scripts/seostate.py overview
python3 scripts/seostate.py ai-visibility --days 90
python3 scripts/seostate.py next-actions
```

Plus 28 days of Search Console via the `search-console` skill (clicks,
impressions, CTR, position — by query and by page).

## 2. Summarize

- **What moved up, what moved down**, what entered or left the tracked depth.
  Mind the depth caveat: with a page-1-only provider, "not found" means outside
  the top 10, **not** outside the top 100. Do not report a phantom drop.
- **Clicks / impressions trend** over the 28 days, and which pages drove it.
- **Striking distance** — anything at position 11–20 with real impressions is the
  cheapest win available; name them.
- **CTR underperformers** — position ≤ 10 with impressions but almost no clicks is
  a title/description problem, not a ranking problem. `keywords.py gsc` flags
  these as `ctr_underperformer`.
- **AI visibility** — citation rate and which domains are being cited instead.
- **The 2–3 highest-leverage next actions**, concrete: "X sits at position 8 —
  refresh it with a current-docs pass", "approve the pending tool idea", "the
  guide queue holds 2 of 7 — run research".

## 3. Close the loop

Anything the report reveals about *targeting* belongs in the next research run's
step 0, not just in the report. If comparison long-tails are landing top-20 while
commercial head terms stall, say so plainly — that is the steer the next run
needs, and stating it here is how it survives the week.

```bash
python3 scripts/seostate.py log-run --workflow report --summary "<one line>"
```
