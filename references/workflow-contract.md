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

---

## 5. Report

- verdict, and the counts by severity
- every `opened` critical, with path and rule
- what `resolved` since last time (this is the part that shows fixes landing)
- if the run refused: say it refused and why, and **do not** report a pass

```bash
python3 $SEO/seostate.py log-run --workflow contract --summary "<N urls, M opened>"
```

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
