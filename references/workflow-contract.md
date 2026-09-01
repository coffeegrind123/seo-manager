# Workflow: contract (the deploy guard)

**Cadence:** baseline once per intentional change, **check after every deploy**.
**Job:** catch the deploy that silently shipped a `noindex`, dropped a schema
block, rewrote a canonical, or 404'd a page that used to rank.

This is the only workflow here that is a **guard** rather than an
investigation. It has one question — *did we break something we already had* —
and it should answer it in seconds.

> **Not to be confused with `drift`.** `drift.py` watches **their** page 1.
> `contract.py` watches **your** markup. They share a word and nothing else.

Why it earns its place: every regression it catches is invisible for weeks.
Rankings decay slowly, so by the time the graph moves the cause is twenty
commits back and nobody connects them. The markup diff is available the moment
the deploy finishes and costs one fetch per URL.

---

## 0. Or run the whole post-deploy sequence at once

`contract.py` is step 2 of four, and the other three are the ones that get
skipped. `postdeploy.py` runs them in the only safe order:

```bash
python3 $SEO/postdeploy.py --root .            # checks only, announces nothing
python3 $SEO/postdeploy.py --root . --yes      # ...and announce
```

1. **Health gate** — `/`, `/sitemap.xml`, `/robots.txt` must all return 200.
   A deploy that swaps a release directory can 404 the entire site for
   minutes, and announcing inside that window points every engine at a dead
   site. It has happened: a sitemap submit landed in the window, came back
   with errors, and the CDN then served a cached `/robots.txt` 404 for four
   hours. A query string cannot evict that; only a purge can.
2. **Contract check** — this document. Deliberately BEFORE the submit, so a
   recrawl is never invited onto markup that just regressed.
3. **IndexNow** — Bing, Yandex, Seznam, Naver, DuckDuckGo-via-Bing.
4. **Google** — `gsc.py sitemap-submit`. The only programmatic nudge Google
   offers, and it asks for a FEED re-read rather than for a URL to be indexed.

Then it writes a line to `.seo/postdeploy.jsonl`. That receipt exists because
the question *"was IndexNow pinged for that deploy?"* turned out to have no
answer anywhere: Google records `lastSubmitted` so its side was provable, Bing's
URL-submission quota is a different channel that says nothing about IndexNow,
and IndexNow itself returns 200 and keeps no history you can query. An action
with no receipt cannot be audited.

**Submissions are a dry run until `--yes`; the checks always run.** A non-zero
exit means something needs reading — most often an open contract warning, which
is the gate doing its job.

---

## 1. Choose the URL set

Small and representative beats exhaustive. One page per template is the right
shape — a template regression hits every page built from it, so the second page
of the same type adds cost and no information.

A good set: the homepage, one page per content type, the highest-traffic page,
one localised page, and anything with structured data you depend on.

```bash
SEO=~/.claude/skills/seo-manager/scripts
python3 $SEO/contract.py baseline --name prod \
  --url https://example.com/ \
  --url https://example.com/guides/some-guide \
  --url https://example.com/tools/some-tool

**Success criteria**: `.seo/contract/<name>.json` exists and covers one URL per template, and the file is staged for commit. A baseline that lives on one machine is not a baseline.
# or seed from the sitemap:
python3 $SEO/contract.py baseline --name prod --sitemap https://example.com/sitemap.xml --max-urls 40
```

State lives in `<repo>/.seo/contract/<name>.json`. **Commit it** — that is what
makes the baseline shared rather than one machine's opinion.

---

## 2. Check after the deploy has settled

```bash
python3 $SEO/contract.py check --name prod
```

⚠ **Wait for the deploy to finish first.** A release that re-extracts a
directory and swaps a symlink can serve 404s site-wide for minutes. Checking
inside that window opens a critical finding on every page, each of which then
has to be resolved by hand.

The tool defends against this itself: if **more than 34%** of previously-200
URLs are non-200 at once it returns `verdict: site_wide_failure` and **refuses
to record a regression verdict**, because a site that just lost every page is
mid-deploy or down — a different problem with a different response. Tune with
`--max-fail-share`. A refusal is the correct outcome, not an error to work
around.

**Success criteria**: `check` returned a verdict other than `site_wide_failure`. If it refused, the deploy had not settled — wait and re-run; never widen `--max-fail-share` to force a verdict.

---

## 3. Read the lifecycle, not the diff

Findings are keyed `(path, rule)` and carry state:

- **`opened`** — new since the last check. This is the deploy's damage.
- **`still_open`** — seen before and still true. Not new, still broken.
- **`resolved`** — auto-closed because the page stopped tripping the rule.

So `check` answers *what is broken now*, not *what changed since I last happened
to run this*. Nothing needs closing by hand in the normal case; `resolve` exists
for a finding you have consciously accepted.

Severity is fixed, not advisory:

| Critical | Warning |
|---|---|
| `page_now_unavailable`, `page_now_redirects`, `noindex_added`, `canonical_removed`, `canonical_changed` (pointing away), `title_removed`, `h1_removed`, `schema_removed`, `hreflang_removed` | `title_changed`, `h1_changed`, `content_shrank`, `og_tag_removed`, `internal_links_dropped`, `description_removed`, `hreflang_count_changed` |

**Success criteria**: Every `opened` finding is classified as intentional or a regression, and every `opened` critical has a named cause. `still_open` counts are known, not newly discovered.

---

## 4. When the change was intentional

Re-baseline. That is the whole mechanism:

```bash
python3 $SEO/contract.py baseline --name prod --sitemap …   # the new markup IS the contract now
```

**Never resolve a finding by loosening a rule.** If a title legitimately
changed, the new title is the contract — record it. A manual `resolve` closes
the finding but does **not** re-baseline, so the next check re-opens it; that
is deliberate.

**Success criteria**: The baseline was re-recorded, and a re-run of `check` reports the previously-opened finding as `resolved` rather than `still_open`.

---

## 5. Report

- verdict, and the counts by severity
- every `opened` critical, with path and rule
- what `resolved` since last time (this is the part that shows fixes landing)
- if the run refused: say it refused and why, and **do not** report a pass

```bash
python3 $SEO/seostate.py log-run --workflow contract --summary "<N urls, M opened>"
```

**Success criteria**: The report states the verdict, the counts by severity, every `opened` critical with path and rule, and what resolved. A refused run is reported as refused — never as a pass. The run is written to the run log.

---

## What it reads that a markup-only checker misses

- **`X-Robots-Tag` from the response header.** A `noindex` delivered by header
  is invisible to every checker that only parses HTML, and it deindexes exactly
  as hard. `contract.py` reads both and treats either as `noindex_added`.
- **Redirects, unfollowed.** A canonical that quietly starts 301-ing is the most
  common silent regression there is, and following the redirect makes it look
  like a healthy 200. Everything here uses `follow=False`.

## Where it fits with everything else

Run it **immediately after a deploy** and before any of the slower work. If the
contract broke, nothing downstream — rank checks, decay analysis, drift — is
measuring what you think it is: the pages changed underneath the measurement.
