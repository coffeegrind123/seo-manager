# Workflow: international (the hreflang mesh + content parity)

**Cadence:** quarterly, and **after any change to the locale set, the URL scheme
or the page template**. **Job:** prove the site's own internationalisation still
holds up, and that the translations behind it are real translations.

Only run this on a site that actually serves more than one language. On a
single-locale site the correct output is "not applicable", not a clean pass.

Why it needs measuring rather than reviewing: **hreflang fails silently and
bidirectionally.** A missing return tag invalidates the annotation for *both*
pages, Search Console has reported nothing about it since the International
Targeting report was removed in **2022**, and every page involved keeps serving
a healthy 200. There is no symptom until the wrong locale starts ranking in the
wrong country, or none of them rank at all.

---

## 0. Prove the instrument first

```bash
SEO=~/.claude/skills/seo-manager/scripts
python3 $SEO/hreflang.py control
```

Must print `ok: true`. It parses a built-in fixture with a known answer and
checks four things that have each silently broken a real parser: head-vs-body
scoping, `<link>` vs `<a>`, canonical extraction, x-default recognition.

**If the control fails, stop.** `audit` refuses a verdict in that state on
purpose — "this page has no hreflang" and "this parser is broken" are the same
output, and shipping the first when the second is true is how a whole finding
gets invented.

**Success criteria**: `hreflang.py control` printed `ok: true`. If it did not, the run STOPS here and reports a broken instrument — never a finding about the site.

---

## 1. Audit the mesh

Seed from the sitemap where there is one, or from a handful of representative
pages. The audit **expands automatically**: it fetches every alternate each seed
declares, which is what makes the return-tag check possible.

```bash
python3 $SEO/hreflang.py audit --url https://example.com/some/page --max-urls 120
python3 $SEO/hreflang.py audit --sitemap https://example.com/sitemap.xml --locale-only
```

Read the findings in severity order. The ones that matter most:

| Rule | Why it is critical |
|---|---|
| `missing_self_reference` | Google discards the **entire set**, not just the missing tag |
| `missing_return_tag` | Invalidates the relationship for **both** pages |
| `alternate_dead` | The site advertises a 404 as a language alternate. Survives every on-page check, because the page carrying the tag is a healthy 200. |
| `hreflang_on_non_canonical` | hreflang is only honoured on canonical URLs; here it is ignored wholesale |

`alternate_dead` is the one to look for first on a site with **generated**
pages. A template that emits the full locale list for every page will advertise
`/xx/<slug>` for slugs that only exist in one language — thousands of dead
annotations from a single template line, all of them invisible locally.

**Sanity-check the locale list in the output** against what the site actually
publishes. A code appearing that nobody ships, or a shipped locale missing, is
a template bug worth more than any individual finding.

**Success criteria**: Every seed expanded to its declared alternates and the findings are read in severity order. The locale list in the output has been reconciled against what the site actually publishes, and any `alternate_dead` cluster is traced to the template line that emits it.

---

## 2. Check the content behind the mesh

A correct mesh pointing at untranslated pages is worse than no mesh: it claims
a locale for a near-duplicate of the English page on a different URL.

```bash
python3 $SEO/hreflang.py parity https://example.com/some/page
```

Read `systematic` **before** `findings`. When most locales trip the same rule
it is one fact about the source page or the template, not N per-locale defects,
and the tool collapses it accordingly — measured on a real 22-locale site,
21 of 21 locales tripped `length_ratio_outlier` because the *English* page
carried sections the translations did not. Reported per-locale that is 21 rows
burying the single finding that mattered (one locale whose `h1` was still
English).

The load-bearing checks:

- **`title_not_localised` / `h1_not_localised`** — byte-identical to the
  reference locale. This is a real defect and almost always a **single missing
  translation key** falling back to English, which is invisible in review
  because the page renders fine.
- **`schema_missing_on_locale`** — structured data present on the reference and
  absent on a translation.
- **`length_ratio_outlier`** — **advisory**. The expansion bands are editorial
  localisation conventions, *not* measurements, and the tool says so in the
  finding text. Use them as a prompt to look, never as a defect on their own.

**Success criteria**: `systematic` was read before `findings`, and every systematic row is stated as one fact about the template or source page rather than N per-locale defects. `length_ratio_outlier` was treated as advisory, not as a defect.

---

## 3. Fix upstream, not per page

Nearly every finding here comes from a template or a translation table, so the
fix is one edit and the re-run is the proof. Resist per-page patches: on a
generated silo they do not scale and they hide the systematic cause.

Queue anything that needs real translation work rather than a code change:

```bash
python3 $SEO/seostate.py propose --type fix --title "..." --source international \
  --rationale "..."
```

**Success criteria**: Each finding is traced to the template or translation table that produced it, and anything needing real translation work is in the queue as `type: fix`. No per-page patches were proposed for a generated silo.

---

## 3b. Ask which locale actually EARNS, and what it earns for

The mesh being correct says nothing about whether a locale is worth having, and
the answer is routinely not the one the English-speaking team expects. Do this
before any decision to cut, translate, or expand a locale.

```bash
python3 $SEO/bing.py pages --limit 60          # per-URL clicks/CTR - sorted by CLICKS
python3 $SEO/bing.py pagequeries --page 'https://example.com/zh/'   # what that page earns for
python3 $SEO/bing.py expand --seed '<the locale's own head term>' \
        --country cn --language zh-CN --limit 200                   # the demand around it
```

Then diff the demand against what the page already ranks for. That is the gap,
and it is stated in impressions rather than intuition.

**Measured on combatskirmish.net, 2026-09-01, and every step of it was a
surprise to the people who built the site:**

- `/zh/` earned **2,298 of the site's 3,357 Bing clicks — 68%** — at a 27% CTR
  from position 4, against the English homepage's 2.1% from four times the
  impressions. The site's own access log agreed independently: `cn.bing.com`
  sent 1,988 referrals against google.com's 495.
- The phrase driving it, **网页版** ("web version", 20,770 impressions), appeared
  **nowhere on the page**. It ranked #2 on relevance alone.
- The gap came to ~14,000 impressions of Chinese demand with no presence at all.

⚠ **Two disciplines this needs, both of which changed the answer here.**

**Strip brand and competitor terms before summing a gap.** A competitor's own
brand (`webcs.xyz`, 21,178 impressions) is demand you cannot serve, and leaving
it in inflates the opportunity by more than the opportunity.

**Check that the phrase is TRUE before targeting it.** 中文版 ("Chinese version")
was worth another ~4,700 impressions and was rejected: the page is in Chinese but
the game is not localised — no `game_langs`, no `zh_CN` strings in the client — so
the phrase would promise a build that does not exist. A keyword gap is a reason to
check a claim, never a reason to make one.

And when the winning page is that large a share of the site's traffic, changes to
it are **additive** — keep every token that is already there. There is no A/B here.

---

## 4. Report

- Locales declared vs locales actually reachable.
- Critical/high findings with the rule name — the rule name IS the fix.
- The systematic rows, stated as one sentence each.
- What was **not** measurable: pages the run could not read are listed in
  `unread` and are **never** counted as "no hreflang".

```bash
python3 $SEO/seostate.py log-run --workflow international --summary "<N locales, M findings>"
```

**A clean pass is a real result and worth stating plainly** — but only because
`scripts/test_hreflang.py` fires every rule against synthetic markup, so the
checker is known to discriminate. If that suite is failing, a pass here means
nothing.

**Success criteria**: The report names locales declared vs reachable, every critical/high finding by rule name, the systematic rows, and what was `unread`. Unread pages are never reported as "no hreflang". The run is written to the run log.

---

## The traps, all measured

- **`<a hreflang="es" href=…>` is a language switcher, not an annotation.** Only
  `<link rel="alternate" hreflang=…>` is the hreflang signal. On the site this
  was built against, a naive `grep hreflang` counted 43 carriers where the real
  set was 23 — it would have reported every locale as a duplicate.
- **An annotation after `</head>` is not honoured**, and looks identical in a
  grep.
- **`be`, `uk`, `se`, `ca`, `br`, `ie` are all valid ISO 639-1 languages that
  are also other countries' codes.** `uk` is Ukrainian, not the United Kingdom
  (that is `en-GB`); `be` is Belarusian, not Belgium. These pass every validator
  while targeting the wrong audience, so `validate_code` reports them valid with
  the alternative named.
- **Region-only codes are invalid.** You cannot say "Belgium" without a
  language.
- **`es-LA` is not Latin America** — `LA` is Laos. The correct form is the UN
  M.49 code `es-419`.
- **`en-uk` is not a thing.** The region code is `GB`.
