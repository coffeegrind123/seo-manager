# Workflow: audit (one page, basic or full)

A single-page SEO audit rendered as an HTML report. Merged into seo-manager on
2026-10-25 from the the basic audit and full audits (JeffLi1993's
seo-audit-skill, github.com/JeffLi1993/seo-audit-skill - its report design and
check rules are kept, credited in each report's footer). Its scripts now run on
seo-manager's stdlib layer: no `pip install`, robots.txt read by `agentcheck`,
noindex by `directives.py`, fetches through `scripts/audit/audit_net.py`.

| Level | When | Report | Checks |
|---|---|---|---|
| **basic** (default) | "audit this page", "SEO check", "quick review", a bare URL | `assets/audit/report-basic.html` -> `reports/<host>-<slug>-audit.html` | site + page + E-E-A-T + schema - the STRICT SCOPE below |
| **full** | "full / deep / technical / comprehensive audit", "audit everything" | `assets/audit/report-full.html` -> `reports/<host>-<slug>-full-audit.html` | basic + PageSpeed, social tags, and seo-manager's measured modules |

This is the one-page snapshot. For anything that should be tracked over time -
did a deploy break the markup, what is losing rank, what Googlebot crawls - run
the matching workflow (`contract`, `decay`, `crawl-log`, `health`) instead; the
full audit's advanced sections are those same instruments, run once.

All commands below assume `SEO=~/.claude/skills/seo-manager/scripts` and
`SKILL=~/.claude/skills/seo-manager`.

---

# Basic level

Fill `$SKILL/assets/audit/report-basic.html`. Placeholders:
`{{summary_verdict}}`, `{{summary_critical_html}}`, `{{summary_warnings_html}}`,
`{{summary_passing_html}}`, `{{site_checks_html}}`, `{{eeat_checks_html}}`,
`{{page_checks_html}}`, `{{priority_actions_html}}`, plus the header fields.

```bash
python3 $SEO/audit/check-site.py https://example.com
python3 $SEO/audit/check-page.py https://example.com --keyword "running shoes"
python3 $SEO/audit/fetch-page.py https://example.com --output page.html
python3 $SEO/audit/check-schema.py --file page.html https://example.com
```

Each script exits `0` (all pass/warn) or `1` (any fail/error).

**STRICT SCOPE — do not add any check not listed below. No exceptions.**

Allowed site-level checks (in `{{site_checks_html}}`):
- robots.txt · sitemap.xml · 404 Handling · URL Canonicalization · i18n / hreflang

Allowed E-E-A-T checks (in `{{eeat_checks_html}}`):
- About Us · Contact · Privacy Policy · Terms of Service · Media/Partners (only if present)

Contact logic (Contact row only):
- A dedicated `/contact` page is **not required**
- **Pass** if contact is reachable via any of: dedicated contact page (HTTP 200) · About page with contact details · footer/nav mailto, email, social links, or contact form
- **Fail** only when no contact pathway exists anywhere on the site
- Missing `/contact` alone is **not** a fail when About or footer/nav already expose contact info

Allowed page-level checks (in `{{page_checks_html}}`), output in this exact order:
URL Slug · Title Tag · Meta Description · H1 Tag · Canonical Tag · Image Alt Text · Word Count · Keyword Placement · Heading Structure · Internal Links · Schema (JSON-LD)

  Image Alt Text logic:
  - Parse <img> tags from static HTML
  - Pass: all images have non-empty alt (decorative images with alt="" are OK)
  - Warn: any content image missing alt attribute
  - Unverified (status-info): 0 images found in static HTML → likely JS-rendered, cannot verify

⛔ HARD RULE — Output ONLY the check rows defined in report-basic.html.
If a check is not in the allowed lists above, do NOT output it — not even if you find issues.
No exceptions. No "bonus" checks. No improvisation.
The template is the single source of truth. Treat it as a strict whitelist.

Still BANNED (belong to the full audit): OG tags · Twitter Card · Social tags · Page Weight · Core Web Vitals · Robots Meta

**How to use the JSON output:**
- Map each field's `status` → `pass` / `warn` / `fail` / `error` directly to the report check table
- Use each field's `detail` string as the starting point for the Evidence line in findings
- Do not contradict the script output unless you have additional observable evidence
- Separate check groups with `<div class="subsection-label">Label</div>` inside `{{site_checks_html}}`:
  `Crawlability` · `URL Canonicalization` · `i18n / hreflang` · `Schema (JSON-LD)`
  and `<div class="subsection-label">E-E-A-T Trust Pages</div>` before `{{eeat_checks_html}}`

**LLM review — mandatory when `llm_review_required: true`:**

The script flags fields that require semantic or quality judgment it cannot perform.
Never leave `llm_review_required: true` unresolved — always make an explicit judgment call.

**H1 — triggered when `keyword_match == "partial"`:**
```
h1_text : (from h1.values[0])
keyword : (the --keyword passed to the script)

Judge: Does this H1 semantically cover the keyword's search intent?
  - Consider synonyms, natural variants, topic coverage
  - yes → downgrade to "pass", note the variant
  - no  → keep "warn" or upgrade to "fail", explain the gap
```

**Title — triggered when `keyword_match == "partial"` OR `keyword_position != "start"`:**
```
title   : (from title.value)
keyword : (the --keyword passed)

Judge:
  1. Does the title semantically cover the keyword's search intent?
  2. Is the title grammatically correct and naturally readable?
  3. Keyword position — apply different standards by page type:
     - Homepage   : Brand + core keyword is correct (e.g. "Acme | AI Workflow Automation")
                    Do NOT flag brand-first as a problem.
     - Inner pages: Core keyword should lead (e.g. "AI Workflow Automation for Teams — Acme")
                    Flag if keyword is buried mid-title without good reason.

IMPORTANT — do NOT flag these as negatives:
  - Years (e.g. "2026") → signal freshness, increase CTR — treat as positive unless
    the page is explicitly evergreen content where dating would hurt longevity.
  - Numbers (e.g. "5 best", "Top 10", "3 steps") → set clear expectations,
    consistently outperform non-numeric titles in CTR — always treat as a plus.
  - Specific qualifiers ("Open-Source", "Self-Hosted", "Free") → narrow intent
    and attract higher-quality clicks — do not penalize.
```

**URL Slug — triggered when `keyword_match != "full"` or `is_homepage == false`:**
```
slug    : (from url_slug.slug)
keyword : (the --keyword passed)

Judge:
  1. Does the slug contain the primary keyword or a natural variant?
  2. Is the path hierarchy logical? (/category/keyword is ideal)
  3. Is it concise and human-readable?
  Homepage (is_homepage: true): skip — no judgment needed.
```

**Meta Description — always triggered when content is present:**
```
meta_description : (from meta_description.value)
keyword          : (the --keyword passed)

Judge all four:
  1. Complete sentence(s)? (1-2 sentences, no fragments)
  2. Mentions a concrete result — not vague fluff?
     Good: "Cut design time by 60% with AI-powered templates"
     Bad:  "The best tool for all your design needs"
  3. Keyword or natural synonym used once — not stuffed?
  4. More specific than what a typical competitor would write?

IMPORTANT — do NOT flag these as negatives:
  - Years (e.g. "2026") → signal freshness, improve CTR for time-sensitive queries.
    Only note the year if the page is explicitly evergreen content where dating hurts.
  - Numbers (e.g. "5 best", "3 steps") → concrete specificity, strong CTR signal.
  - Trailing "and more." → minor style note at most, never a Warning or Fail.
```

---


## Recommended Workflow

Follow these steps in order:

1. **Acknowledge scope** — confirm this is a basic audit; note any missing data

2. **Infer primary keyword** — fetch the page with `fetch-page.py`, then determine the primary keyword:
   - If the user explicitly provided a keyword → use it directly
   - If not → read the page H1, title, and first paragraph, then infer the single most likely
     target keyword phrase (what would a searcher type to find this page?)
   - State the inferred keyword explicitly before running checks:
     > "Inferred primary keyword: **open source claude alternatives**"

3. **Run `check-site.py`** — parse the JSON output for robots, sitemap, 404 handling, and URL canonicalization

   **404 check:** fetch `<origin>/this-page-definitely-does-not-exist-seo-audit-check`
   - Returns 404 → Pass · Returns 200 (soft 404) → Fail · Returns 301 to homepage → Warn

   **URL Canonicalization checks** (each is a separate sub-check):
   - **HTTP→HTTPS:** fetch `http://<host>` — must 301 to `https://`. Returns 200 → Fail.
   - **www consistency:** fetch both `https://www.<host>` and `https://<host>` — one must 301 to the other. Both return 200 → Warn.
   - **Trailing slash:** compare the URL actually served vs the canonical tag on the page. Mismatch → Warn.
   - **Canonical match:** canonical tag href must exactly match the final URL after all redirects. Mismatch → Warn.

4. **E-E-A-T infrastructure check** — for each trust page below, check two layers:
   - **Layer 1 — Exists:** fetch the URL, check HTTP status (200 = exists, 404/redirect = missing)
   - **Layer 2 — Reachable:** fetch homepage HTML, check if footer or nav contains a link to this page

   | Page | Required |
   |---|---|
   | About Us | Yes |
   | Contact | Yes — see Contact-specific rules below |
   | Privacy Policy | Yes |
   | Terms of Service | Yes |
   | Media / Partners | No — include only if present |

   Status rules (About, Privacy, Terms, Media/Partners):
   - Page missing (non-200) → **Fail**
   - Page exists but not linked in footer/nav → **Warn**
   - Page exists and linked in footer/nav → **Pass**
   - Optional page missing → skip, do not include row

   **Contact-specific rules** — a dedicated `/contact` page is optional:
   1. Try common paths (`/contact`, `/contact-us`) — HTTP 200 counts as Exists
   2. If no contact page, check the About page body for email, social, or contact details
   3. Also scan homepage footer and nav for `mailto:`, visible email, social links, or a contact form
   4. **Exists Pass** if any contact pathway is found (dedicated page, About, or footer/nav contact details)
   5. **Exists Fail** only when no contact information is found anywhere
   6. **Reachable Pass** if a contact page link or contact details appear in footer/nav
   7. **Reachable Warn** if contact is only reachable inside About page content, not directly in footer/nav
   8. Do not recommend creating `/contact` when About or footer already expose contact info — note the existing pathway instead

5. **Run `check-page.py --keyword "<inferred_keyword>"`** — parse the JSON output for H1, title,
   meta description, canonical, and URL slug

6. **i18n / hreflang check** — only run if the page contains hreflang tags or `<html lang>` suggests multi-language:
   - **Skip entirely (N/A)** if no hreflang tags found and site appears single-language
   - If hreflang tags present, check:
     - **Reciprocal symmetry**: every URL referenced must link back to all other variants — any broken link = Fail
     - **Language codes**: must be valid BCP 47 (e.g. `zh-CN` not `zh`, `en-US` not `en-us`) — wrong code = Warn
     - **x-default**: should be present for language-selector or fallback pages — missing = Warn
     - **html[lang] attribute**: must match the primary hreflang of the page — mismatch = Warn
     - **URL structure**: recommended pattern — default language (usually `en`) at root with no prefix,
       other languages under subpaths (`/zh/`, `/es/`).
       - `/page` (en) + `/zh/page` + `/es/page` → Pass
       - `/en/page` + `/zh/page` → Warn (en prefix is redundant, wastes crawl depth)
       - Only flag if the pattern is clearly inconsistent or en is unnecessarily prefixed

7. **Run `check-schema.py`** — parse the JSON output for schema types and field validation

   ```bash
   python3 $SEO/audit/check-schema.py https://example.com
   # Or from previously fetched HTML:
   python3 $SEO/audit/check-schema.py --file page.html
   ```

   The script extracts JSON-LD blocks, validates `@type` and required fields per Schema.org spec.
   `llm_review_required: true` is always set — confirm `inferred_page_type` matches actual page content.

   Page type → expected `@type` reference:

   | Page Type | Expected @type | Min. required fields |
   |---|---|---|
   | Homepage | WebSite + Organization | name, url, logo |
   | Blog / Article | Article or BlogPosting | headline, datePublished, author, image |
   | Product | Product | name, image, offers (price, priceCurrency) |
   | FAQ | FAQPage | mainEntity[].name, acceptedAnswer.text |
   | How-to | HowTo | name, step[].text |
   | Local business | LocalBusiness | name, address, telephone |
   | Generic landing | — | N/A — skip, no widely-supported type |

   - Pass: correct @type present, all required fields valid, no conflicts
   - Warn: @type present but missing recommended fields
   - Fail: expected @type missing entirely
   - N/A: generic landing page — do not penalize

8. **Summarize findings** — each finding must follow the Evidence / Impact / Fix format

9. **Priority actions** — list the top 3 highest-impact fixes

10. **Render report** — save to `reports/<hostname>-<slug>-audit.html`, then ask user to open

11. **Upgrade prompt** — if issues beyond basic scope are found, suggest `the full audit`

---


**Upgrade line** - end every basic report with: "This was a basic audit. Ask
for a **full audit** for PageSpeed, social tags, field Core Web Vitals,
AI-crawler access, internal links, Search Console and AI-answer citation."

---

# Full level

## Input Expected

| Input | Required | Notes |
|-------|----------|-------|
| Page URL | Yes | The primary page to audit |
| Primary keyword | Recommended | Improves content relevance scoring |
| PageSpeed API key | No | `check-pagespeed.py` uses `--api-key` / `PAGESPEED_API_KEY` / `GOOGLE_PAGESPEED_API_KEY` when set, and otherwise this skill's Search Console service account (`~/.gsc_service_account.json`, the same one `gsc.py` uses) - the full Lighthouse panel either way. `credential` in its output says which |
| Raw HTML or page content | Optional | Enables more accurate content checks when supplied |
| GSC / crawl / analytics data | Optional | The advanced modules fetch Search Console, Bing and GA4 themselves when credentials exist |
| Competitor benchmark data | Optional | Include when supplied |

Only when there is neither a key nor a usable service account does
`check-pagespeed.py` return its credential error; then render Page Speed from
`pagespeed_fallback_html` if `render-advanced.py` produced one, else as
`error` with the get-a-key link. Never stop the audit for it.

---

## Architecture: Full = Core + Performance + Advanced

```
┌─────────────────────────────────────────────────────────────┐
│  the full audit Workflow                                    │
│                                                             │
│  Phase 1: Run core scripts (./scripts/)                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  check-site.py      → robots.txt, sitemap, 404, URL  │   │
│  │  check-page.py      → title, H1, meta desc, slug     │   │
│  │  check-schema.py    → JSON-LD validation              │   │
│  │  fetch-page.py      → raw HTML for analysis           │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  Phase 2: Run performance + full-only scripts (./scripts/)  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  check-pagespeed.py → Lighthouse scores + metrics     │   │
│  │  check-social.py    → OG Tags + Twitter Card          │   │
│  │  (more scripts added here as modules grow)            │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  Phase 2b: seo-manager instruments (always)                │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  run-seo-manager.py → one JSON, a STATE per check     │   │
│  │  render-advanced.py → advanced section HTML + summary │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                  │
│  Phase 3: LLM-only advanced checks                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  E-E-A-T content quality scoring                      │   │
│  │  Duplicate content signals                            │   │
│  │  Anchor text quality assessment                       │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Output

Produce an **Advanced Full SEO Audit Report** by filling the template at
[assets/audit/report-full.html]($SKILL/assets/audit/report-full.html),
then **save it to a file — never print raw HTML to the terminal**.

**File naming:** `reports/<hostname>-<slug>-full-audit.html`
```
https://example.com/blog/best-tools → reports/example-com-blog-best-tools-full-audit.html
https://example.com/                → reports/example-com-full-audit.html
```

**After saving, tell the user:**
```
✅ Full Report saved → reports/example-com-full-audit.html
   Open it now? (yes / no)
```
If yes → run: `open reports/example-com-full-audit.html`

**Template placeholders** — fill each independently:

| Placeholder | Content |
|---|---|
| `{{summary_verdict}}` | One sentence: total checks run, how many failed/warned/passed |
| `{{summary_critical_html}}` | `<li>` per critical item, or `<li class="summary-empty">None</li>` |
| `{{summary_warnings_html}}` | `<li>` per warning item, or `<li class="summary-empty">None</li>` |
| `{{summary_passing_html}}` | `<li>` per passing check, or `<li class="summary-empty">None</li>` |
| `{{pagespeed_checks_html}}` | Full PageSpeed module using `check-pagespeed.py` output |
| `{{site_checks_html}}` | Site-level check tables |
| `{{eeat_checks_html}}` | E-E-A-T trust page table |
| `{{page_checks_html}}` | Page-level check tables, including full-only additions |
| `{{priority_actions_html}}` | Ordered priority action list |
| `{{insights_html}}` | Optional finding walkthrough cards |
| `{{scorecard_html}}` `{{field_vitals_html}}` `{{indexability_html}}` `{{structured_data_html}}` `{{international_html}}` `{{link_graph_html}}` `{{content_quality_html}}` `{{ai_readiness_html}}` `{{search_performance_html}}` `{{ai_visibility_html}}` `{{coverage_html}}` | **Filled by `render-advanced.py --fill`, never by hand.** See "Advanced modules" below |

---

## Scripts

Run full scripts from this directory. All output is structured JSON — use it
directly as evidence.


### Phase 1: Core scripts

```bash
# 1. site-level checks (robots.txt + sitemap.xml + 404 + URL canonicalization)
python3 $SEO/audit/check-site.py https://example.com

# 2. page-level checks (H1, title, meta description, canonical, URL slug)
python3 $SEO/audit/check-page.py https://example.com --keyword "primary keyword"

# 3. fetch raw HTML for downstream scripts
python3 $SEO/audit/fetch-page.py https://example.com --output /tmp/page.html

# 4. JSON-LD schema validation
python3 $SEO/audit/check-schema.py --file /tmp/page.html
```

### Phase 2: Full-only scripts

```bash
# 5. PageSpeed / Lighthouse checks
python3 $SEO/audit/check-pagespeed.py https://example.com --strategy mobile --timeout 180
# key optional: --api-key, PAGESPEED_API_KEY, or the service account (see Input Expected)

# 6. Social tags: OG + Twitter Card validation
python3 $SEO/audit/check-social.py --file /tmp/page.html
# Or directly from URL:
python3 $SEO/audit/check-social.py https://example.com
```

Each script exits with code `0` (all pass/warn) or `1` (any fail/error).

PageSpeed can take 200 seconds. Use a 180-second timeout by default. If it
still times out, mark the Page Speed module as `error` and state that the
PageSpeed API timed out; do not treat that as confirmed page performance failure.
If PageSpeed fails because Google returns a quota/API-key error after a key was
provided, keep the audit running and render the PageSpeed module as `error` with
this instruction:
`Get a PageSpeed API key at https://developers.google.com/speed/docs/insights/v5/get-started → "Acquiring and using an API key" → "Get a Key".`

---

## Scope — Full Audit Check Whitelist

Full runs its own core checks plus the full-only items marked ★ below.

### Site-Level Checks (in `{{site_checks_html}}`)

Core checks:
- Sitemap URL Inventory · Staging Subdomain Indexation · robots.txt · sitemap.xml · 404 Handling · URL Canonicalization · i18n / hreflang

**Staging Subdomain Indexation rules:**
- Check common staging/test hosts before robots.txt: `test.`, `staging.`, `dev.`, `preview.`, `beta.`, `uat.`
- **Fail** when a staging/test subdomain is publicly accessible, closely mirrors the production site, and is not protected by authentication, `noindex`, or a blocking `robots.txt`.
- **Warn** when a staging/test subdomain is publicly accessible but similarity or index protection cannot be confirmed.
- **Pass** when no public staging/test subdomain is detected, or detected staging hosts are protected by authentication, non-200 access, `noindex`, or `Disallow: /`.
- Explain the impact as duplicate indexable pages: Google may treat `test.example.com` and `www.example.com` as separate but near-identical URLs, splitting ranking signals and competing with the production site.
- Recommended fixes: add Basic Auth/password protection first; also block crawlers on the staging host with `User-agent: *` + `Disallow: /`; add page-level `noindex` if pages can still be accessed.

**Sitemap URL Inventory rules:**
- Place this as the first module in `{{site_checks_html}}`, before the Crawlability table.
- Render it as its own table with columns: Directory · URL Count · Page Type · Example Page.
- Use `check-site.py` `sitemap_inventory` output to summarize first-level directories, URL counts, inferred page types, and one representative example URL.
- Treat this as a site-level map, not a pass/fail single-page SEO audit.
- Use status `info` unless sitemap URLs cannot be parsed; do not penalize a site for having many or few URLs in a directory without deeper evidence.
- Always include a next-step note: the user can continue with deeper full audits by selecting representative sample URLs from major directories such as `/blog/`, `/tools/`, `/alternatives/`, `/templates/`, or `/use-cases/`.

### Page Speed Checks (in `{{pagespeed_checks_html}}`)

Full-only:
- Lighthouse category scores: Performance · Accessibility · Best Practices · SEO
- Lab metrics: FCP · LCP · TBT · CLS · Speed Index
- Final URL and screenshot availability

### E-E-A-T Checks (in `{{eeat_checks_html}}`)

Core checks:
- About Us · Contact · Privacy Policy · Terms of Service · Media/Partners (only if present)

Contact logic (Contact row only):
- A dedicated `/contact` page is **not required**
- **Pass** if contact is reachable via any of: dedicated contact page (HTTP 200) · About page with contact details · footer/nav mailto, email, social links, or contact form
- **Fail** only when no contact pathway exists anywhere on the site
- Missing `/contact` alone is **not** a fail when About or footer/nav already expose contact info

E-E-A-T infrastructure rules — two layers per trust page:
- **Layer 1 — Exists:** HTTP 200 for the trust page URL (Contact uses Contact-specific rules above)
- **Layer 2 — Reachable:** linked from footer or main nav

| Page | Required |
|---|---|
| About Us | Yes |
| Contact | Yes — dedicated page optional; About or footer/nav contact details satisfy this |
| Privacy Policy | Yes |
| Terms of Service | Yes |
| Media / Partners | No — include only if present |

Status rules (About, Privacy, Terms, Media/Partners):
- Page missing (non-200) → **Fail**
- Page exists but not linked in footer/nav → **Warn**
- Page exists and linked in footer/nav → **Pass**
- Optional page missing → skip, do not include row

**Contact-specific rules:**
1. Try common paths (`/contact`, `/contact-us`) — HTTP 200 counts as Exists
2. If no contact page, check the About page body for email, social, or contact details
3. Also scan homepage footer and nav for `mailto:`, visible email, social links, or a contact form
4. **Exists Pass** if any contact pathway is found
5. **Exists Fail** only when no contact information is found anywhere
6. **Reachable Pass** if a contact page link or contact details appear in footer/nav
7. **Reachable Warn** if contact is only reachable inside About page content, not directly in footer/nav
8. Do not recommend creating `/contact` when About or footer already expose contact info

### Page-Level Checks (in `{{page_checks_html}}`), output in this exact order:

Core checks:
URL Slug · Title Tag · Meta Description · H1 Tag · Canonical Tag · Image Alt Text ·
Word Count · Keyword Placement · Heading Structure · Internal Links · Schema (JSON-LD)

**Schema (JSON-LD) rules:**
- Treat Schema as a quality check, not only a presence check.
- Validate JSON-LD parseability, expected `@type`, required fields, recommended rich-result fields, nested fields, and primary-type conflicts.
- **Fail** when JSON-LD is invalid, the expected schema type is missing, required fields are missing, or localized schema clearly points to the wrong language/URL.
- **Warn** when recommended fields are missing, nested fields are incomplete, multilingual pages lack `inLanguage`, or localized schema cannot be fully confirmed.
- **Pass** only when the expected schema type is present, required fields are present, no conflicts are found, and localized schema matches the current page language/URL when applicable.
- For multilingual pages, each language version should have its own schema with matching `inLanguage`, language-specific headline/description where present, and `url` / `mainEntityOfPage` pointing to the current localized canonical URL.

★ Full-only additions:
- **OG Tags** — og:title, og:description, og:image, og:type, og:url presence and validity
- **Twitter Card** — twitter:card type, title/description/image (with OG fallback detection)

---

## Advanced modules

The advanced sections are measured by this skill's own instruments in
`$SEO/` - the same ones the program workflows use - not re-implemented for the
audit. Two scripts are the whole bridge:

```bash
# 1. measure - one JSON, a state per instrument (ok / not_applicable / no_key /
#    cannot_ask / error / timeout / skipped). ~1 minute.
python3 $SEO/audit/run-seo-manager.py <url> --keyword "<primary keyword>" \
    --out reports/<name>-seo-manager.json
#    --no-live-apis   skip Search Console, Bing and answer engines (quota / paid keys)
#    --gsc-property   force a property; by default the one covering the host is used
#    --brand "A, B"   brand aliases for AI-answer mention matching

# 2. render - fills the eleven advanced placeholders in the report IN PLACE
cp $SKILL/assets/audit/report-full.html reports/<name>-full-audit.html
python3 $SEO/audit/render-advanced.py reports/<name>-seo-manager.json \
    --fill reports/<name>-full-audit.html --out reports/<name>-fragments.json
```

Rules:
- **Never hand-write an advanced section** and never edit a filled one to
  soften it. The fragments are the evidence; if one looks wrong, re-run the
  instrument or fix the instrument.
- **Not measured is not pass.** A module whose instrument did not run renders
  `Not measured` with the reason, and it is left out of the pass counts.
  Never describe it as passing in the summary or the verdict.
- **Merge the summary.** `fragments.json` -> `summary.critical / warnings /
  passing` lists the advanced rows; add them to `{{summary_*_html}}` beside
  the core findings, and count them in `{{summary_verdict}}`.
- **Priority actions** draw from Fail rows of both core and advanced
  sections. Field Core Web Vitals outranks lab PageSpeed when they disagree:
  CrUX is what real users got.
- `python3 $SEO/test_audit.py` pins these rules offline (with the fetch
  layer, robots/noindex reading and the length rules); `run_tests.py` runs it.
- The core `i18n / hreflang` row stays in Site Checks; the International
  section is the deeper check (return tags across the cluster, automatic
  redirects). Do not repeat its rows in Site Checks.

---

## How to Use Script JSON Output

Same rules across full audit modules — map each field's `status` directly to the report check table:
- `status` → `pass` / `warn` / `fail` / `error` → badge in report
- `detail` → starting point for Evidence line
- Do not contradict script output unless you have additional observable evidence

**For `check-site.py` output:**
- `staging_subdomains.status` → Staging Subdomain Indexation row status
- `staging_subdomains.detail` → Staging Subdomain Indexation row detail
- `staging_subdomains.public_hosts` → evidence for public staging/test hosts
- `staging_subdomains.similar_hosts` → fail evidence for production-like staging duplicates
- `robots.status` → robots.txt row status
- `sitemap.status` → sitemap.xml row status
- `sitemap_inventory.status` → Sitemap URL Inventory module status
- `sitemap_inventory.directories[]` → table rows with `path`, `url_count`, `page_type`, and `example_url`
- `sitemap_inventory.detail` → short explanatory note below the inventory table

**For `check-schema.py` output:**
- `status` → Schema (JSON-LD) row status
- `detail` → Schema (JSON-LD) row detail
- `parse_errors` → fail evidence for malformed JSON-LD
- `schemas[].fields_missing` → fail evidence for missing required schema fields
- `schemas[].recommended_missing` and `schemas[].nested_issues` → warning evidence
- `localized_schema.status` → language/URL alignment status for multilingual schema
- `localized_schema.issues` → evidence for schema language or URL mismatch

**For `check-social.py` output:**
- `og.status` → OG Tags row status
- `twitter_card.status` → Twitter Card row status
- `og.fields.*` → individual field details for the detail cell
- `twitter_card.fields.*` → individual field details, note fallback fields

**For `check-pagespeed.py` output:**
- `status` → overall Page Speed status
- `category_status` → Lighthouse Scores badge status
- `metric_status` → Core Lab Metrics badge status
- `final_url` → Final URL line
- `categories.performance.score` → Performance score
- `categories.accessibility.score` → Accessibility score
- `categories["best-practices"].score` → Best Practices score
- `categories.seo.score` → SEO score
- `compact_metrics.fcp.display_value` → FCP
- `compact_metrics.lcp.display_value` → LCP
- `compact_metrics.tbt.display_value` → TBT
- `compact_metrics.cls.display_value` → CLS
- `compact_metrics.si.display_value` → Speed Index
- `screenshot` truthy → Screenshot available

Do not use overall `status` for the Lighthouse Scores badge. Category scores use
Lighthouse thresholds: 90–100 pass, 50–89 warn, 0–49 fail. Example: Performance
60, Accessibility 84, Best Practices 100, SEO 100 means `category_status` is
`warn`. If LCP or Speed Index fails, `metric_status` and overall `status` may be
`fail` while the Lighthouse Scores panel remains `warn`.

Inside `{{pagespeed_checks_html}}`, include a short `Priority Actions` list after
the score/metric cards. Keep it to 2–4 concise items based on failing or warning
PageSpeed fields:
- LCP slow → optimize hero media, preload critical image, reduce render-blocking CSS
- Speed Index slow → defer non-critical scripts and reduce above-the-fold JS/CSS
- TBT high → split long tasks and delay third-party tags
- Performance score warning/fail → prioritize the biggest Lighthouse opportunities

Always include the official diagnostic link:
`https://pagespeed.web.dev/`

---

## LLM Review Instructions

### Core LLM reviews

Resolve every `llm_review_required: true` field before writing the report:
H1 semantic judgment, Title keyword position, URL Slug evaluation, and Meta
Description quality must all receive an explicit judgment.

### Full-only LLM checks

**OG Tags quality (always review):**
```
og:title   : Does it differ meaningfully from <title>? It should be optimized for social sharing.
og:description : Is it compelling for social feeds? Different focus than meta description is OK.
og:image   : Is the URL an actual image path (not a page URL)?
```

**Twitter Card completeness:**
```
If twitter:card is "summary_large_image", twitter:image (or og:image fallback) must be
at least 300x157px. Flag if the image URL looks like a small icon or favicon.
```

---

## Recommended Workflow

1. **Acknowledge full scope** — confirm this is a full audit
2. **Infer primary keyword** — read the page H1, title, and first paragraph unless the user provided one
3. **Phase 1: Run core scripts** — check-site → check-page → fetch-page → check-schema
4. **Phase 2: Run full-only scripts** — verify PageSpeed API key exists, then run check-pagespeed → check-social
4b. **Phase 2b: seo-manager modules** — `run-seo-manager.py` with the inferred keyword, then copy the template and `render-advanced.py --fill` (see "Advanced modules")
5. **Core checks** — 404 handling, URL canonicalization, E-E-A-T trust pages, i18n/hreflang
6. **PageSpeed checks** — summarize Lighthouse category scores and lab metrics
7. **LLM-only advanced checks** — E-E-A-T content quality, duplicate content signals, anchor text quality
8. **Summarize findings** — Evidence / Impact / Fix format
9. **Priority actions** — top 5 highest-impact fixes with effort/impact tags
10. **Render report** — the copy already filled by `render-advanced.py --fill`; fill the remaining core placeholders and save to `reports/<hostname>-<slug>-full-audit.html`. Grep the file for `{{` before reporting it done: no placeholder may survive.

---


## Report Detail Writing Rules

Use strict formatting:

**Pass → one short phrase. No lists, no elaboration.**

**Warn → one `<div class="detail-issue">` with ≤2 bullet points. One `<div class="detail-fix">`.**

**Fail → same as Warn. Lead with the exact failure.**

---

## Mandatory Finding Format

```
**Finding: [Finding Title]**

- **Evidence:** [Observable fact, data point, or marked assumption]
- **Impact:** [SEO / UX consequence]
- **Fix:** [Actionable recommendation with example]
```

For Priority Actions, add effort/impact tags:
```
1. [High Impact / Low Effort] Fix og:image — social shares currently show no preview.
```

---

