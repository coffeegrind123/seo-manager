# Workflow: programmatic (pages generated at scale)

**Cadence:** before shipping a generated silo, then quarterly. **Job:** decide
whether a template-generated set of pages is an asset or index bloat — and
decide it on **index evidence**, not on a similarity score.

A generated silo is the highest-leverage and highest-risk thing on an SEO site.
Thousands of pages from one template can own a long tail no hand-written site
can reach. The same thousands, done badly, are the textbook shape of scaled
content abuse.

The rest of this skill assumes hand-written pages: `sameness.py check` gates ONE
draft against the catalogue, and `pairwise audit` is O(n²) — fine for 60 guides,
impossible for 2,600 pages. This workflow is the scaled form.

---

## The rule this workflow exists to enforce

**A similarity number is a RISK signal. Indexation is the verdict. Get the
verdict before you act.**

This is where the thin-content literature will lead you wrong. Template share is
a property of the *writing*. Whether Google keeps, ranks and re-crawls a page is
a property of what Google *decided*. They come apart constantly, because a
generated page's unique value is often not prose at all — a map overview image,
a spec table, a live server list, a dataset. Shingles cannot see any of it.

> **Measured, and the reason this warning is here.** On a real 2,637-page
> generated silo, median unique-shingle ratio was **30.5%**, with 1,235 pages
> below the 30% hard line — a textbook thin-content diagnosis. URL Inspection on
> six sampled pages, including the *worst* one, returned **"Submitted and
> indexed", verdict PASS, self-canonical honoured**. No "Duplicate without
> user-selected canonical", no mass "Discovered – not indexed". Each variant
> carried a distinct overview image, md5-verified. Acting on the ratio alone
> would have deleted or noindexed pages Google was perfectly happy to keep.

So the sequence is always: **measure → get index evidence → only then act.**

---

## 1. Measure the corpus

```bash
python3 $SEO/sameness.py tiers --corpus public/seo/maps --top 15
```

O(n) — it builds a document-frequency map of every 5-word shingle across the
corpus, then scores each page by the share of its shingles that are *rare*
corpus-wide. 2,637 pages in ~30s.

Read:

| field | meaning |
|---|---|
| `unique_ratio.median` | the headline. Below `hard` (30%) most of the corpus is template. |
| `histogram_5pct_buckets` | shape matters more than the median. A long left tail is a *tier* to fix, not a corpus to condemn. |
| `counts.under_min_words` | pages too short to be a standalone answer |
| `exact_template_groups` | pages whose rare content is *identical* — these are genuinely duplicates |
| `worst` | where to look first |

`--include` / `--exclude-re` split tiers apart. **Measure curated and generated
tiers separately** — mixing them flatters the generated one and slanders the
curated one.

**Success criteria**: `tiers` returned a ratio distribution for the silo, with curated and generated tiers measured SEPARATELY. The histogram shape is read, not just the median.

---

## 2. Get the index evidence — mandatory, not optional

Use the `search-console` skill. Sample **5–10 pages spread across the ratio
range**, always including the worst.

1. **URL Inspection** on each.
   - "Submitted and indexed" on a low-ratio page ⇒ Google has judged it and kept
     it. The ratio is not what it is judging on. **Stop. Do not consolidate.**
   - "Duplicate without user-selected canonical" ⇒ **confirmation**. Act.
   - "Crawled – currently not indexed" across the tier ⇒ **confirmation**.
   - "Discovered – currently not indexed" ⇒ on a big silo this is *normal* crawl
     scheduling, not a quality verdict. Confirm with the crawl log before
     treating it as one.
2. **Crawl log** (`workflow-crawl-log.md`): if Googlebot still re-crawls the
   tier, it has not written it off.
3. **Impressions**: does the tier earn any? A tier with impressions is working
   regardless of its ratio.

**Only when the index evidence agrees with the measurement do you act.**

**Success criteria**: 5-10 pages spread across the ratio range — always including the worst — have a URL Inspection verdict, plus crawl-log and impression evidence for the tier. No action is taken until this evidence agrees with the measurement.

---

## 3. Act — in this order

Cheapest and most reversible first.

1. **Add unique value.** Almost always the right answer, and the only one that
   makes the pages *better*: a real per-record data point, an image, a table,
   something true of this record and no other.
2. **Consolidate.** Merge records too thin to stand alone into a parent page.
   The parent gets the links.
3. **Remove from the sitemap.** Stops advertising a page you do not stand
   behind. Reversible, costs nothing.
4. **`noindex`.** The real removal.
   ⚠ **Never `Disallow` a page you want de-indexed.** A blocked page cannot be
   re-crawled to see the `noindex`, so it stays indexed with no snippet —
   permanently. `noindex` first, wait for the re-crawl, block later if ever.
5. **Delete.** Only for pages with no impressions, no links and no reason to
   exist. 410 beats 404.

**Success criteria**: Actions were taken cheapest-first, and nothing was `Disallow`ed that was meant to be de-indexed. If the index evidence disagreed with the ratio, the correct outcome is NO action.

---

## 4. Gates for a NEW generated silo

Before shipping one:

| Gate | Threshold | Action |
|---|---|---|
| Unique content per page | < 30% | **HARD STOP** — do not ship without a stated justification for what the unique non-prose value is |
| Unique content per page | 30–40% | Warning — sample-review before shipping |
| Words per page | < 300 | Flag for review |
| Pages in one release | > 100 | Sample-review 5–10% by hand |
| Pages in one release | > 500 | Explicit owner approval, and **stage the rollout** |

**Stage it.** 50–100 pages, then watch indexation for 2–4 weeks before
expanding. Shipping 2,000 pages at once means that if the tier is judged badly
you learn it at full scale with nothing to compare against.

**The standalone-value test**, which is worth more than any threshold: *would
this page be worth publishing if no other page in the set existed?* If the only
honest answer is "it completes the set", it is bloat.

**Case-collision check on any generated file tree** — if the build host's
filesystem is case-insensitive (macOS, WSL, a 9p mount) and production is Linux,
two records differing only in case silently overwrite each other and the survivor
carries the *other* one's canonical. The result is a page whose canonical points
at a 404, which cannot be indexed at all, while the sitemap advertises both.
Fold case-duplicate identities at generation time and log every drop.

**Success criteria**: Every gate in the table has a recorded pass/fail, the standalone-value test is answered in writing, and any release over 100 pages is staged rather than shipped whole. A case-collision fold ran over the generated tree.

---

## 5. Structural requirements

- **Self-referencing canonical** on every generated page.
- **Sitemap**: split at 50,000 URLs / 50 MB; `lastmod` = the data's update time,
  not the build time; never list a `noindex` URL.
  ⚠ **Verify the lastmod rule rather than stating it — it is the one here most
  likely to be violated by a generator that looks correct.** One command:
  ```bash
  curl -s https://example.com/sitemap.xml | grep -oE "https://[^<]+\.xml" \
    | xargs -I{} curl -s {} | grep -oE "<lastmod>[^<]+</lastmod>" | sort | uniq -c
  ```
  **One date with the full URL count beside it is the failure**, and it is silent:
  Google uses `lastmod` only while it is consistently accurate, so a tree that
  stamps every URL with the build date has no freshness signal at all rather than
  a slightly stale one. Measured on combatskirmish.net 2026-09-01: all **5,388**
  URLs carried the same date, defended in the generator by a comment reasoning
  that "the whole tree genuinely is regenerated in a single pass — every page's
  content is as new as this run". Regenerated is not modified, and at that site's
  observed Googlebot rate (~55 fetches/day) one full pass over the sitemap takes
  ~97 days, which is exactly when you want to tell a crawler which URLs moved.
  **The fix that is not fiction: hash each generated page and keep a committed
  `url -> {hash, date}` manifest** — an unchanged hash keeps its stored date, a
  changed or new one takes today. The date is then provable from the tree, and
  URLs with no generated file behind them (SPA routes, server-rendered tiers whose
  data really does change daily) keep the build date honestly. Control it by
  running the generator **twice**: the second run must report zero changed and
  produce byte-identical sitemaps.
- **Internal links**: hub → spokes, plus 3–5 genuinely related siblings.
  ⚠ Resolve every sibling link **per record** — a related-items block that
  assumes all siblings share the current page's URL shape (locale, tier, prefix)
  emits dead links at scale, and they are invisible until the crawl log shows
  the 404s.
- **Localised variants**: only ship a locale whose content is genuinely
  translated. A locale that falls back to English body copy under an
  `hreflang="xx"` claim is a near-duplicate of the English page on another URL —
  worse than not having the locale. Gate complete-or-absent.

**Success criteria**: Self-canonical on every page, a valid split sitemap with data-time `lastmod` and no `noindex` URLs, sibling links resolved per record, and no locale shipped that falls back to English.

---

## 6. Report

```bash
python3 $SEO/seostate.py log-run --workflow programmatic --ok --summary "..."
```

State the measurement, **the index evidence, and whether they agreed**. A run
that measures a scary ratio and then finds the pages indexed and re-crawled is a
**complete, successful run with no action** — that is the most valuable outcome
this workflow produces, because it is the one that stops expensive unnecessary
work.

**Success criteria**: The report states the measurement, the index evidence, and WHETHER THEY AGREED. A scary ratio plus indexed, re-crawled pages is reported as a complete successful run with no action. The run is logged.
