# The scripts

All stdlib Python 3, no installs. Every one prints JSON. `--help` on any of them
lists its subcommands.

```bash
SEO=~/.claude/skills/seo-manager/scripts    # adjust if vendored into the repo
```

⚠ **`$SEO` does not survive between tool calls** — each Bash invocation starts a
fresh shell. Re-declare the line above at the top of any block you run
separately, or write the path out in full.

---

| Script | Job |
|---|---|
| `seostate.py` | **all state**: queue, keywords, ranks, pages, trends, prospects, profile, conventions, pacing, overview, next-actions, run log — and since 2026-09-14 the **deferred list** (`defer` / `deferred --due` / `deferred-update`): every candidate a gate withheld, with a class (`authority` / `catalogue` / `brand_navigational` / `dev_intent` / `off_remit`) and a revisit condition. `catalogue` files the page-1 aggregators as prospects; `brand_navigational` folds the keyword into a crediting page; `authority` re-opens at the next DR band and `next-actions` says when. `propose` refuses a keyword that is still deferred |
| `serp.py` | live SERPs through the provider ladder, plus the weakness/authority scoring the gate needs. **Failover is the default** (serpd → serper → serpapi → ddg; `--no-fallback` pins one) and `fell_back_from` names the provider that failed; `engine` names whose index answered. `unresolved_wrappers` lists results whose host could not be recovered from a redirect wrapper (Google's `/goto?url=`, Bing's `/ck/a`) — counted nowhere, never as authority. **`--provider bing` is opt-in and never in the failover** (2026-10-12): a residential exit pinned to the market and CONFIRMED (`exit_country`), one page only, and named refusals — `challenge` / `transport` / `unparsed` / `echo_missing` / `exit_mismatch` (cannot ask) kept apart from `wrong_query` / `query_collapse` (the engine answered something else). Every proxied read reports `exit_country`, `exit_requested`, `exit_unconfirmed`; DuckDuckGo's repeated page-2 rows are dropped and counted |
| `keywords.py` | expansion across **six independent suggestion corpora** (Google, Bing, DuckDuckGo, YouTube, Yandex, Amazon) with a cross-engine agreement signal and observed video/product intent, plus tool-intent sweeps, DataForSEO volume/KD, and Search Console → candidates |
| `competitors.py` | **reads page 1 instead of guessing from titles**: fetches each result and returns the depth you are matching (median words/headings), which results are thin/UGC/stale, and the subtopics most of them cover. The HTTP fetcher honours robots.txt (that is what RFC 9309 governs) and treats every fetched byte as inert data; anything it cannot read is listed as `browser_candidates` to be read through the browser-automation MCP - a bounded one-pass read of ten page-1 URLs, which is normal research practice, not crawling . A page that returns a bot challenge is reported as `challenged`, never as `weak`: an interstitial looks exactly like a thin page (a few words, one heading), and calling it weak inverts the finding — it says a competitor is beatable when all you learned is that they block you |
| `brief.py` | **a content brief ASSEMBLED from measurements, never invented**. Composes page 1 (`serp.py`), the intent contract (`competitors.py`), what people actually ask (`keywords.py` suggestion corpora), whether we already have the page (`sameness.py` extractor + containment over slug/headings/opening), and whether we can realistically compete (`authority.py`). Every section is MEASURED or listed in `unavailable` with its reason. **No readable page 1 = a hard refusal**, because a brief that states a depth target and a competitive read it did not measure is the writer's assumptions handed back with a tool's authority. Subtopics are WITHHELD below 4 readable results - the failure there is a confident list, not an empty one |
| `sameness.py` | the corpus sameness gate + a pairwise drift audit |
| `headterms.py` | **head-term ownership** (2026-10-12): `.seo/headterms.json` records which URL owns each head phrase; `check --url --title --h1` gates a head BEFORE it ships, `scan --root <build>` / `--urls` finds every page whose title or H1 carries a phrase another URL owns. Token containment on normalised words, not similarity — `sameness.py` sees bodies and is blind to two different pages with the same head, which is how a site ends up rotating its own URLs at the bottom of page 1. Per-term `allow` globs for designed long-tail families, global `ignore`. Reports `owner_missing_phrase` / `owner_not_scanned` separately. Refuses an empty/missing map and a scan of zero pages. Also owns the shared HTML reader (title, H1, canonical, visible text, JSON-LD prose) |
| `claims.py` | **product claims vs a ledger** (2026-10-12): `.seo/claims.json` holds verified product facts (each with its source file) for writers, a `never` list of regexes for claims known to be false (`except` clears historical/quoted context), and `competitors`. `scan --root <build>` / `--urls` reports deterministic `findings` per sentence of visible text AND JSON-LD prose (a FAQ answer in structured data is published copy), and `review` candidates where a competitor is named next to an English preference cue ("obvious choice", "right answer", "recommend") — a human reads those verbatim; non-English pages report mentions without pretending to read cues. `lint` validates the ledger. Refuses no ledger, an empty ledger, and zero documents |
| `sitegraph.py` | **the internal link graph** (+ `suggest`, 2026-10-03: which existing pages should link to each weak one and with what anchor - `high` needs a shared Search Console query AND a shared title word, `medium` one of them; a target nothing relates to gets an empty list, never padding) — build it from a LOCAL generated tree (no network, works on an undeployed build) or by live BFS, then ask it for orphans, click depth, broken links and `silos`. That last one is the point: it ranks silos by EXTERNAL-silo inbound links and flags **islands** — a silo whose pages cross-link each other so every naive inlink count looks healthy while nothing outside points in. Counts `<link rel=alternate hreflang>` as a discovery edge, ignores self-loops, and detects site furniture by FREQUENCY rather than by tag, because a nav rendered as `<p class="silonav">` defeats a tag-based rule. `orphans --contextual` splits out pages that are THEMSELVES global-nav furniture into `nav_hub_urls` — a hub with 12,070 inbound links is the most reachable page on the site, and counting it as an orphan pads the number until the real orphan hides in the list |
| `authority.py` | DR-equivalent, and the KD zones / volume band that follow from it. Also `--bulk` competitor scoring, free referring-domain counts, a 12-month authority trend, and two independent popularity reads (Cloudflare Radar + **Tranco**, which is keyless and carries 40 days of rank history) |
| `bing.py` | **the only free source of real search volume AND backlinks** — Bing Webmaster Tools for a site you own. Impressions per query, related-keyword expansion with numbers, inbound links, query stats, `urlinfo` for Bing's own per-URL index record (discovered / last crawled / size — its answer to Google's URL Inspection), and `pages`/`pagequeries` for the PAGE dimension. **`queries` and `pagequeries` rows are one per query per WEEK** (Bing's ~10-week fixed window, Friday-dated): every row carries `date`, and `by_query` gives one row per query (total impressions/clicks, ctr, impression-weighted `avg_position`, `weeks`, `first_date`/`last_date`, first/last/best/worst weekly position). `queries --q "<query>"` (case-insensitive exact) or `--contains <substr>` returns that query's weekly series in date order — the only way to tell a stable ranking from the same query listed ten times. ⚠ `avg_click_position` is `null` whenever Bing sends a sentinel; measured -1 on every row including rows with clicks, so it is never a rank. **`cannibal`** runs `pagequeries` over a page set (`--pages a,b` / `--from-file` / the top `--limit` pages by impressions; owner URLs always added) and flags per query `split` (≥2 URLs each ≥ `--min-share`, default 10%), `owner_absent` (the URL named by `--owner "phrase=url"` or `--owners-file` — `{phrase: url}` or headterms.py's `{"terms":[…]}`, relative paths allowed — gets nothing for a query containing that phrase while another URL gets impressions) and `edge_of_page` (best impression-weighted position 7–11), with `weekly_leaders`/`leader_changes` showing rotation. Only measured pages are visible; an owner whose call failed is `unmeasured`, never `absent`; a slash-variant owner URL is refused. Page-level impressions count each URL appearance (measured 1.42× the query-level total), so `site_impressions` and `measured_page_impressions` are reported side by side, never as a ratio. **Also the CRAWLER's side of the same account** — `crawlstats` (fetched per day, code mix, pages in index), `crawlissues` (per-URL, empty answer guarded against `crawlstats`), `feeds --verify` (the sitemap as Bing holds it vs the live file), `blocked` (account-level blocks, invisible to any crawl), `crawlsettings` (a self-imposed rate cap), `quota` + `submit` (the one crawl-acceleration lever here, dry-run until `--yes`, and NOT IndexNow's channel or quota) — which URL actually earns the clicks, and for which searches. `queries` alone cannot tell a locale page ranking in its own language from the homepage ranking for that language, and those two have opposite fixes. **The rest of the API (2026-10-18):** `fetch --url [--live]` has bingbot fetch a URL now and parses what it was served (crawler record, status, X-Robots-Tag, canonical, hreflang, truncation; `--live` diffs it against a normal fetch), `fetched [--url]` reads the history; `submitfeed` / `removefeed` (dry run until `--yes`, live feed must be 200 and parse, read back from GetFeeds; a feed Bing does not hold is refused because a no-op returns the same null) and `feeddetails` (a sitemap index child by child); `submitcontent` (the page's HTTP response, quota drop as proof); `keywordtrend` (weekly exact/broad demand), `querypages` (EVERY url ranking for a query, split/edge flags), `querypagedetail` (position histogram, `page1_share`), `querytraffic` (daily clicks/impressions); `urltraffic` (measured returning 0 for pages with traffic, so cross-checked against GetPageStats) and `children` (Index Explorer, measured empty everywhere, reported `unknown` against `in_index`); `cannibal --query/--top-queries` adds every URL Bing showed for those queries (`pages_found_by_query`). A split is now one of three: `locale_split` (the same page in different languages, which Bing pools across markets: hreflang's job, not cannibalisation), `stacked` (one URL led every week at position 3 or better, so the sibling is an extra page-1 result), or `split` (the rest, the only kind to act on). `crawlstats` calls a column a running total when it dips on at most 25% of days and never by more than 15% of its peak; the old one-dip rule summed `in_index` to 366,850 for an 8,442-page index. `submit` uses SubmitUrl for one URL and reports the quota drop. All calls decode Bing's ErrorCode from the HTTP 400 body, report a 404 as a retired method, and retry ThrottleUser/ThrottleHost with backoff. Writes append receipts to `.seo/bing.jsonl` when `--root` has a `.seo/` |
| `bingadmin.py` | **Bing Webmaster account CONFIGURATION**, the state that lives in Bing and in no file: `state` (every setting in one report, a failing section named rather than dropped, an `attention` list), `sites` (with the meta-tag / BingSiteAuth.xml / CNAME verification proofs), `site-add` / `site-verify` / `site-remove` (needs `--confirm-site`), `roles` / `role-add` / `role-remove` (the last administrator cannot be removed), `blocks` / `block-add` / `block-remove` / `preview-add` / `preview-remove`, `params` / `param-add|remove|enable|disable`, `regions` / `region-add|remove`, `crawlrate-set --rate '0-6=8,7-23=3' --boost on|off` (read-modify-write), `connected` / `connected-add`, `deeplink-blocks` / `deeplink-block-add|remove`. `sitemoves` / `sitemove` / `deeplinks` / `deeplink-algo` / `deeplink-weight` are RETIRED (HTTP 404 on 2026-10-18, same as a made-up method) and say so. Every write: refused when it would be a no-op (Bing answers null for success AND no-op), DRY RUN until `--yes`, verified by read-back (`confirmed` / `not_reflected` / `unverifiable`), receipt to `.seo/bing.jsonl` |
| `trendfeeds.py` | **keyless demand signals**: Google Trends' RSS feed (works where the 429-ing JSON API does not), Wikimedia pageviews as absolute topic demand, HN + StackExchange chatter, **GDELT's per-topic news-volume timeline**, and Google News (who is covering the topic now) |
| `rankcheck.py` | batch rank checks for every tracked keyword. **Since 2026-10-03 every row carries `rank_state` (`ranked` / `out_of_range` at `depth_checked` / `not_measured`) and `exit_country` (`unpinned` when nobody chose one)** - `seostate.py record-rank` had been silently DROPPING `depth_checked`, so every stored null had lost the depth that made it readable. A fall is a drop CANDIDATE (out of the top 10, 5+ places, or gone from a read deep enough to have seen it; previously-unranked never counts) and is only reported after a SECOND read settles it `confirmed` / `volatile` / `unconfirmed` (rankme.fast's rule) |
| `algoupdates.py` | **Google's update calendar, synced from Google's own dashboard** (2026-10-03). The ledger was hand-kept on the belief "there is no API" and had gone three months stale, missing two spam updates - and carried NO end dates, so `decay`/`drift` windowed on a field no row had. `status.search.google.com/incidents.json` (exact timestamps, ~10 recent incidents, all products) and each product's `/history` page (every incident since 2021, start + duration) are merged by `sync`, hand rows matched by incident id → name → kind±3 days and never rewritten. `correlate` is the one span definition both consumers use: known end, `ongoing` (overlaps every later window), or `unknown` → `possibly_in_window`, bounded by the longest MEASURED rollout. `status` names staleness; `seodoctor` re-syncs a stale ledger. `unverified[]` holds refused third-party claims (SAFE, "Authority Signals Update") so they are recorded and never used |
| `sitecheck.py` | **the site-level technical checks the health workflow said to always run** (2026-10-03 - the `*-audit` skills it named were not installed). `hosts` (http/https × www/apex reach ONE origin in one permanent hop), `soft404` (random path), `redirects` (hop-by-hop chains, loops, temporary hops), `sitemap` (an evenly SPREAD sample of listed URLs: redirects, errors, noindex in meta OR `X-Robots-Tag`, canonical elsewhere; `&amp;` in `<loc>` unescaped), `canonicals` (target not 200 = unindexable), `headers` (HSTS + security headers, mixed ACTIVE content, lang/viewport/duplicate titles), `variants` (trailing-slash and tracking-param twins), `links` (from a sitegraph graph: internal links landing on a redirect/error, indexable pages missing from the sitemap), `all`, `diff` (new/fixed/persisting keyed `(rule, url)`). Redirects never followed silently; comments never parsed as markup; refuses when no host answers or no sitemap reads. **2026-10-25:** a challenge or 429 is `*_unverifiable`, never dead; a partly read sitemap claims no coverage; canonical from `<head>` only, `canonical_conflict` / `canonical_cross_host` / `canonical_in_body`; `redirect_to_homepage`; `coverage` = sitemap x crawl x pages with impressions |
| `stats.py` | **the statistics the rate comparisons need, exact** (2026-10-03): Wilson intervals, the Newcombe hybrid interval for a difference (`compare_rates` calls a change ONLY when it excludes zero), exact McNemar, Holm, `stable_band`, a small-sample floor of 20. Pinned in `test_stats.py` to values published by the methods' authors, never to its own output. Used by `geo`, `decay`, `remeasure` |
| `serpd.py` | **the fast path for SERP-heavy runs**: a long-lived headed Chrome behind `localhost:8791`. One curl per check, real Google, no DOM in your context. 25 checks in ~37s and 1.4KB of verdicts. `/batch` spools every result to `/tmp/seo-serpd-batches/<id>.json` before replying and `GET /batch?id=` returns it, so a client timeout no longer loses the batch — pass your own `"id"`. `/health` reports `throttled` when Google served `/sorry` in the last 15 min. **2026-10-25:** refuses an extraction whose headings do not add up (`extract_accounted`); `consent-wall` is its own error |
| `indexnow.py` | get published pages crawled: IndexNow ping (free, keyless, Bing/Yandex/Seznam/Naver) + the batched Google "Request indexing" follow-up. Google's automatable half is `gsc.py sitemap-submit`; only per-URL requests are still a human clicking a button. **2026-10-25:** one batch per host; the key file is checked on every host first (`--skip-keycheck` overrides) |
| `gsc.py` | **Google Search Console, in this skill** — `sites` (also the auth control), `sitemaps` (when Google last DOWNLOADED each feed, which is the field that matters — `lastSubmitted` only records that you asked), `sitemap-submit` (the ONE programmatic nudge Google offers, and it asks for a feed re-read, not for a URL to be indexed; dry run until `--yes`), `inspect` (URL Inspection — `canonical_agrees: false` is the finding to act on), `query` (search-analytics in Google's OWN row shape, so `decay.py`/`keywords.py gsc` consume it unchanged), `decay-export` (both adjacent equal-length windows `decay.py compare` wants, in one command). RS256 signed in pure Python — no `openssl`, no wheel. ⚠ Three guarded traps: `contents[].indexed` is a stuck counter reading 0 on properties with thousands of indexed URLs (returned as `null`), every count arrives as a **string** (`"9" > "5388"`), and the data lags 2-3 days so a fully-lagged window is REFUSED rather than answered with zero rows. **2026-10-25:** the cut-off comes from the API (`metadata.firstIncompleteDate` - camelCase on the wire), `data_edge.source` says measured or assumed; `query --filter dim:op:expr --aggregation --fresh`; `inspect` keeps a 2,000/day quota ledger per property; `sitemap-delete` |
| `directives.py` | **robots directives, read the way Google reads them** (2026-10-25): meta robots + X-Robots-Tag, comma tokens, `none` expanded, crawler-scoped lines (`otherbot: noindex` binds otherbot only). Shared by sitecheck, contract and bing, which each got a different part wrong |
| `ga4.py` | **what AI-assistant visitors do** (2026-10-25): `ai-referrals` / `landing` / `metadata` / `properties` / `realtime` (last 30 minutes - the same-day proof a new tag fires; the standard reports lag hours on a fresh property). Same service account as gsc.py (Analytics scope); reads the property's schema first and refuses on a missing field (`conversions` -> `keyEvents`); `api_disabled` carries the enable link. Needs the Data API (and Admin API for `properties`) enabled and the key added as a Viewer |
| `audit/` | **the one-page audit's scripts** (2026-10-25, merged from the seo-audit skills; see `workflow-audit.md`): `check-site.py`, `check-page.py`, `check-schema.py`, `check-social.py`, `check-pagespeed.py`, `fetch-page.py` for the core report; `run-seo-manager.py` (runs this skill's instruments, a state each) and `render-advanced.py` (fills the full report's measured sections) for the advanced one. Stdlib through `audit/audit_net.py` (redirect chain, every hop checked against private addresses); robots.txt via `agentcheck`, noindex via `directives`; PSI with no key via the service account. `test_audit.py` pins it |
| `postdeploy.py` | **the whole post-deploy sequence, behind a health gate**: `/`, `/sitemap.xml` and `/robots.txt` must all be 200 before anything is announced (a release swap can 404 a site for minutes, and a CDN caches those 404s for hours), then `contract.py check`, then IndexNow, then `bing.py submitfeed` (Bing re-reads the SITEMAP, which IndexNow does not make it do; `--skip-bing`, `--bing-site`, a missing key is `no_key` not a failure), then `gsc.py sitemap-submit`. Submissions are a DRY RUN until `--yes`; the checks always run. Every run appends to `.seo/postdeploy.jsonl`, because IndexNow returns 200 and keeps no queryable history — "did we announce that deploy?" was genuinely unanswerable once, and an action with no receipt cannot be audited. **2026-10-25:** `robots_edge` step - a cached robots.txt that differs from a cache-busted read is `stale_at_edge` |
| `test_serp_bing.py` | the Bing parser and guards on synthetic pages in the measured shape: `/ck/a?u=a1<base64>` unwrapping with the `=` padding stripped, inline JS that says `b_ad` and `Challenge` on a page with neither, ads (incl. the nested `b_adLastChild`) kept out of both the organic count and the modules, and the refusals each under its own name — wrong-query and collapsed pages that both carry a correct echo, a challenge page, a mismatched and a missing echo, a 200 with no rows. The provider runs end to end over a fake network (exit pinned, mismatch refused before the search, unconfirmed exit reported as `null` + `exit_unconfirmed: true`), DuckDuckGo's repeated page-2 results are dropped, and a refused provider's exit never shows up on the payload of the provider that answered |
| `test_headterms.py` / `test_claims.py` | the two copy guards end to end through the CLI: an H1-only intruder found and a body/anchor mention not, the owner allowed its own phrase, missing/empty map and zero pages refused; a stale claim found in visible text and JSON-LD, a recommendation sent to review and a neutral mention not, markdown read, a ledger with no rules refused, a broken pattern caught by `lint`. Each control was mutation-tested: a broken token boundary, cue-reading on non-English pages, a dropped `except` and skipped JSON-LD each fail it |
| `test_guards.py` | regression tests for the SERP guards, against real captured responses |
| `test_providers.py` | regression tests for the free-provider integrations — chiefly that Open PageRank's `found: false` can never become a DR of 0 |
| `controls.py` | **the control primitive, and the audit that proves every instrument carries one**. A negative result is only as good as its control: "nothing found" and "the reader is broken" serialise to the same JSON unless something separately proves the reader can still find a thing that is known to be there. Exports `Controls` (accumulate named checks, emit one verdict), `refuse()` (the standard shape for "cannot ask", which must never share a code path with "the answer is no"), `guard_zero()` (gate a zero on a passing control) and `uniform_verdict()` (a whole population agreeing IS the tell - `slop.py` once returned `warn` for 44 of 44 pages because it was counting `<title>`, JSON-LD and comments as prose). `controls.py audit` runs every instrument's own control and reports which can currently prove they discriminate |
| `seodoctor.py` | **self-healing preflight** - idempotent check+repair of the X display (a dead Xvfb leaves a stale socket that presents as "chrome did not bind CDP"; the doctor restarts it), the daemon (a Google-throttled one is restarted on a fresh proxy session), its Chrome, deps and project state, and re-syncs the algorithm-update ledger from Google's Search Status Dashboard when it is over 30 days old (`update_ledger` in the output - a stale calendar reads as "no update near this date"). Run it first, every run. `--providers` adds a live sweep of every data source |
| `providers.py` | **the provider registry**: every data source declared once, each with a live probe and its own control. `providers.py status` answers "what can I use right now?" by measuring, not by reading a table. **2026-10-25:** `max_bytes` cap (64 MiB) on body AND inflation; error responses keep headers; repeated headers kept; PSL-private hosting suffixes in `registrable` |
| `ipranges.py` | **the CIDR witness** (2026-10-03: + Google's `user-triggered-agents.json`, named on the fetchers page and previously unread; the Google agent rows map to the user-triggered files, and rDNS is deliberately not a witness for them - user-owned fetchers resolve to `*.gae.googleusercontent.com`, which any App Engine tenant can obtain) (2026-09-20): OpenAI, Anthropic, Perplexity, Google and Bing publish their crawler IP ranges in one JSON shape, and this reads them (cached a day, with the operator's `creationTime`) and answers `verified` / `spoofed` / `unverifiable` for an address claiming a bot. **"Spoofed" needs a list that was actually READ**: a fetch failure, a moved URL (Google's did move) or a reshaped document is `unverifiable`, never a forgery finding. `crawllog.py verify` calls it; `status` says which lists can be read now |
| `factcheck.py` | **information gain, sourced**: OpenAlex + Crossref papers with citation counts and DOIs, Wikidata entities, the Wikipedia neighbourhood of a topic, and a draft-vs-neighbourhood coverage gap. **`claims` (2026-09-20)** is the instrument behind "a source you cannot cite, you have not verified": every numeric or named-source claim in a DRAFT, which of them has no citation within 200 characters, and with `--fetch` whether the cited page actually CARRIES the number. Three states per cited claim - `verified`, `not_in_source` (the page was read and the number is not in it: the finding), `unverified` (dead link, paywall, JS-only: unknown, never false). A number can be restated in ways the matcher does not know, so open the page before calling a `not_in_source` a fabrication |
| `pagecheck.py` | keyless technical checks for ANY url: W3C HTML validity, Google's own structured-data extractor, Wayback change history (yours or a competitor's), and Core Web Vitals |
| `vitals.py` | **whole-site Core Web Vitals, by TEMPLATE rather than by URL**. `history` (2026-10-03): the CrUX History API's 25 weekly p75 points per vital, with a trend called only when the last-4 vs first-4 mean beats twice the series' median weekly wobble AND moves 5% (lower is better; CLS p75s arrive as strings; a missing week is a gap). Live-verified 2026-10-03 with the install's `GOOGLE_API_KEY`: 25 weekly periods for www.wikipedia.org; a low-mobile-traffic site's PHONE is `no_record` (too little mobile traffic - a fact about sample size). `origin --subparts` (2026-09-20) decomposes the CrUX LCP into its four sub-metrics - TTFB, resource load delay, load duration, element render delay - so "4.2s" names the layer the fix lives in; image LCP only, and ⚠ the CrUX API is **API-key only**: the service-account bearer PSI accepts is answered with a misleading HTTP 400, so without `GOOGLE_API_KEY` it reports `no_key`. `pagecheck.py vitals` is the right tool for one page and the wrong shape for a site: PSI takes ~20s per URL and is quota-limited, so a 5,388-URL sitemap is not a sweep you can run. This groups URLs by path shape, samples within each, and reports per template - the level at which a fix is applied. Keyless by default (TTFB, transfer size, compression, render-blocking head resources, image dimension/lazy hygiene, DOM size, third-party origins); `--psi N` spends the quota on the N worst. ⚠ It reports PROXIES, never LCP/CLS/INP - those need a browser, and the ranking-relevant versions need real users, which is what `vitals.py origin` reads from CrUX |
| `crawllog.py` | **the access log**: crawl budget by silo, status codes served to bots, AI-crawler ingestion, and bot verification by TWO witnesses - reverse+forward DNS, and since 2026-09-20 the operator's **published IP ranges** (`ipranges.py`), which is the only path that can reach the AI crawlers, whose rDNS lists are empty by rule. `verify` reports both witnesses per address and a combined three-state verdict; `witnesses_disagree` is named rather than resolved. The `BOTS` registry grew to 107 rows the same day from the `ai.robots.txt` community list - documented answer-engine crawlers only (Bravebot is the Claude search index; meta-webindexer, MistralAI-Index, Kimi-SearchBot, Amzn-SearchBot, ExaSearchBot, Google's agent fetchers), undocumented ones filed as `ai_training`. `--remote` runs the aggregation on the server so the log never crosses the wire. `urls --keep-query` when a single path's hit count looks like a budget finding - by default the query is stripped, so 408 parameterised URLs and one page fetched 506 times are the same row. **`--bot` makes the spoof detector blind and now says so (`hits_net: null`) instead of reporting a false clean.** `facets --urls <urls --keep-query output>` (2026-10-03) ranks parameter crawl traps per path (5+ variants, 3+ params, facet keys, tracking keys) with the share of the bot's hits spent on parameter URLs, and `--check N` fetches the top variants: `canonicalised` / `noindex` / `indexable_duplicate`. `BOTS` gained Google-GeminiNotebook (NotebookLM's 2026-07-16 rename; both tokens classify), Google-Agent and OAI-AdsBot (110 rows). **Every row is netted against the UA-spoofing detector — read `hits_net`, never `hits`**, and treat anything in `bots_entirely_forged` as "did not visit" rather than as a small number; measured 2026-09-01 that was 65% of `ai_search`, 62% of `ai_user` and 92% of `social`, including every Anthropic row. **2026-10-25:** every BOTS key has an operator (36 had none); `meta-externalads` |
| `decay.py` | two Search Console periods -> pages that LOST RANK, separated from pages whose demand fell. Plus self-cannibalisation. Every row carries `ctr` as a rate with its Newcombe interval, and a fourth class, **`ctr_loss`** (2026-10-03): impressions held, position held, CTR fell beyond noise - the click went to an AI Overview or a new SERP feature, invisible to both decay and demand. Algorithm-update correlation now uses real rollout spans (`algoupdates.py`) and adds `algorithm_updates_possibly_in_window` for rollouts whose end is unknown |
| `drift.py` | whole-page-1 snapshots and their diff: new entrants, AI-Overview changes, site-wide volatility, algorithm-update correlation (real rollout spans via `algoupdates.py`; a rollout still running overlaps every later window) |
| `backlinks.py` | **measurement**, not prospecting: real traffic-sending backlinks from your own referrer log, and Common Crawl corpus presence. `referrers` classifies every referring domain and reports only the genuine ones — a referring domain is NOT a backlink, and on a live run 40 of 52 were the site's own second domain (`--own`), an attack probe whose forged `Referer` made `wordpress.org → /wp-login.php` look editorial, a hotlinked favicon, or referrer spam on a cPanel port. The rejects stay in `excluded` with their reason, because a misclassification has to be visible to be fixable. **2026-10-03:** `referrers` also emits `reclaim_candidates` (genuine referrals SERVED a 4xx or a redirect) and `reclaim --scan` re-checks each live and proposes a 301 target by slug; `mentions --brand X --site Y` searches the brand, READS every result, and files pages that name the site without linking (one row per publisher - a substack, a medium author, a subreddit). IP/WHOIS/scanner pages are `machine_listings`, never prospects: the first live run's only "prospect" was an IP-lookup page |
| `remeasure.py` | **hypotheses with baselines, re-checked by re-running the SAME command**. `--denominator <path>` (2026-10-03) makes it a RATE hypothesis: the baseline stores x/n and `confirmed` needs the Newcombe interval to exclude zero in the predicted direction as well as `--min-change` on the rate - 3/10 → 5/10 is `no_detectable_change`. A change ships and the honest question is 'did it work'. Four ways that gets answered wrongly, and this refuses each: re-measuring with a DIFFERENT command (the argv is stored with the baseline and a mismatch is refused, not compared - this skill has already produced a fake '100% page-1 churn' from two extraction regimes); deciding the expected direction afterwards (direction and threshold are pre-registered); checking too early (`not_before`, because a crawl hypothesis at day three reads noise as an answer); and reading 'cannot measure' as 'no change' (a throttled provider and a flat metric produce the same empty screen — the first is `unmeasured` and leaves the hypothesis open). `record` REFUSES a duplicate - an experiment's identity is what it runs and what it reads, not the label somebody gave it, and two baselines for one intervention can disagree with nothing to say which to believe (this fired on its author within the hour). `close --id X --reason ...` retires one; the reason is required, because closing without one is indistinguishable from closing because the answer was inconvenient. `--metric` addresses a list element by **identity** (`bots.bingbot.hits`), not position: `bots.0` into a value-sorted list silently re-points itself between runs, so the verdict would compare two different bots and call it a change. **Row selectors** (2026-10-12): `rows[field=value]` names a row by CONTENT - `by_query[query=best hiking boots].avg_position` - for instruments that re-sort rows every run (`bing.py pagequeries`), where `queries.3` today is a different row next month. Exact, case-sensitive string match; numbers and bools by their JSON text (`[rank=3]` hits `3`, not `3.0`; `[code=007]` hits the string `"007"`, not the number 7). Zero matching rows is `unmeasured`, never zero - `--missing-is-zero` does NOT apply to a selector, because a missing row in a top-N list means "below the cut", a renamed field or a typo, and a zero position would read as better than #1 (it still covers a sparse map BELOW the selected row). Two or more matches are REFUSED with the count (`kind: ambiguous`, hypothesis stays open) rather than picking the first; stack selectors on one segment to disambiguate (`queries[query=tent][date=2026-09-28]`). A malformed path is refused at `record`. The duplicate check counts the selectors: two queries on one page are two experiments, and `[q=a b]` / `[q="a b"]` are one |
| `contract.py` | **the deploy guard**: baseline a URL set's on-page SEO contract, then diff it. Reads `X-Robots-Tag` from the header as well as the meta tag, never follows redirects, and keys findings `(path, rule)` with an open/auto-resolve lifecycle. Refuses a verdict during a site-wide outage. |
| `hreflang.py` | **the international mesh**: self-reference, RETURN TAGS, x-default, ISO 639-1/15924/3166-1 validity, canonical alignment, and the HTTP status of every URL advertised as an alternate. Plus `parity` — is the content behind the mesh actually translated.. **2026-10-25:** `redirects URL` probes every alternate plain / foreign Accept-Language / Googlebot; same-language canonical inside the cluster is info (Google recommends it) |
| `geo.py` | **do answer engines CITE us, and is what they say right?** `crawllog` measures who CRAWLED and `backlinks referrers` who REFERRED; neither asks whether an assistant answering a question this site exists to answer names this site. The unit is an ANSWER, not a ranking - a page can sit at position 3 and be absent from the overview above it. Google's AI Overview **and AI Mode** (`google_ai_mode`, found reachable on the existing SerpApi key 2026-09-20 - single-stage, references on the first response) are askable today; the LLM engines and the four SearchApi.io fronts (`searchapi_chatgpt` / `_gemini` / `_perplexity` / `_bing_copilot`, one key) report `no_key` until one exists. **`--runs N`** asks each engine N times and reports the citation RATE with its n - answers are non-deterministic, and a single run is labelled `single_observation` so it cannot be read as a rate. Rows carry the LADDER: `cited`, `mentioned` (a sentence names us), and the verbatim sentences for the framing call, which is never auto-labelled. `extractable` is the keyless half: an assistant lifts a SENTENCE, so a page can rank and still never be the sentence quoted. **2026-10-03:** a mention is a NAME, not a substring (word boundaries; link targets and URLs blanked first, so a citation no longer double-counts as a mention; `brand` takes comma-separated aliases); `_registrable` stopped turning `news.bbc.co.uk` into `co.uk` (shared `providers.registrable`); an empty AI Overview block is `no_answer_surface`, not an uncited answer; citations are cleaned (`utm_*`, `#:~:text=`), unwrapped (`/url?q=`) and Gemini's `vertexaisearch` redirects resolved (capped); `retrieved_not_cited` is its own rung; every rate carries a Wilson `ci95`, a `band` that is `stable` only when both ends agree, and `small_sample`; a sweep marks an engine that answered 5+ questions with ZERO references as `parser_suspect` and drops its answers from the rate; gap domains carry a source `kind` (forum/video/pr/reviews/ecommerce/social/developer/reference/institutional/listicle/comparison). `diff` pairs two sweeps question-by-question with an exact test and Holm, refusing below 6 changed questions; `gap` joins Search Console queries to the overview (B: ranks 1-4 and a competitor is quoted; A: ranks 5-20; C: cited but CTR below this site's own no-overview median at that position; D: no overview) plus CTR with vs without an overview per position bucket. **2026-10-25:** citation markup (chips, footnotes, 【】, cite spans, alt text) blanked before mention matching; Unicode edges + folding; `openai` = Responses API web_search (forced, located), `perplexity` = Agent API; SearchApi gets its per-engine location; every answer records `method` and `diff` refuses a changed one; `--domain a,b`; `sentences_backed_by_us`; `fan_out` returned/missing/not_offered; branded vs unbranded rates (`by_query_type`) |
| `agentcheck.py` | **can an AI agent read, understand and act on this site, and is it allowed to**: robots.txt resolved per AI crawler in the ai_search/ai_user/ai_training taxonomy, agent-UX semantics, token budget, JS-dependence, WebMCP, and `llms.txt` well-formedness. Since 2026-09-20 three more signals, all informational: **Markdown content negotiation** (`Accept: text/markdown` on the canonical URL - Cloudflare answers it at the edge; `served_markdown` without `Vary: Accept` is the one defect), **RFC 8288 `Link:` headers** (service discovery without parsing HTML), and the robots.txt **`Content-Signal:`** directive (a stated search / ai-input / ai-train policy that crawlers may ignore - reported as intent, never scored). **2026-10-03:** every group matching a crawler is COMBINED (RFC 9309 2.2.1 - Cloudflare's managed robots.txt prepends a second `*` group and the old reader dropped the origin's Disallows); lines split on CR/LF only (U+2028 in a comment had become a live rule) and a leading BOM no longer hides the first group; an empty `User-agent:` names nobody. New: Cloudflare's managed block is detected and the agents it closes named; named groups missing the `*` group's Content-Signal; robots.txt fetched per crawler UA and diffed; `reach` (what each AI-crawler UA is SERVED, against a browser control and a forged-Googlebot control - an edge that refuses forged Googlebot verifies by IP, and then the AI rows answer `cannot_ask`); `discovery` (Agentmap / `ai-catalog.json` validated with the ARD conformance suite's two tiers, `/.well-known` api-catalog / OAuth / A2A / UCP with UCP's real profile shape, soft-200 detection); `llms` 403/406 is `blocked`, never `absent`; WebMCP `document.modelContext` vs legacy navigator. **2026-10-25:** groups match by exact product token (RFC 9309; `Applebot` no longer binds Applebot-Extended), paths compare percent-encoded; `page` reports `content_use` - Google AI-features eligibility (noindex/nosnippet/max-snippet/data-nosnippet), Bing Chat NOARCHIVE/NOCACHE, TDMRep; AWS WAF / SiteGround challenges; deep JSON named `nested_too_deep` |
| `slop.py` | **AI writing tells, detected mechanically** — 22 regex patterns plus two structural rules (2026-09-20: `anaphora`, three consecutive sentences opening on the same two words; `invisible_unicode`, a category-Cf code point in prose - zero-width space, BOM, direction mark - `high` severity because it is a defect as well as a tell, with emoji ZWJ, Persian/Indic ZWNJ and mid-word soft hyphens excluded by context; `false_range`, "from X to Y to Z"; `invented_label`, "the supervision paradox"), each with a tolerance, located hits and line numbers. Code fences, inline code and link targets excluded. No score, on purpose. |
| `test_measure.py` | the controls for the **measurement** scripts (`crawllog` / `backlinks` / `decay`). Every case is a bug that shipped and was caught against live data, or a distinction that silently produces a confident wrong answer — UA registry ordering (`Googlebot-Image` contains "googlebot"; `ChatGPT-User` does not contain "gptbot"), and the three-state verification that keeps "cannot ask" out of the same code path as "the answer is no" |
| `test_keywords.py` | the controls for the band analysers, chiefly `keywords.py bing`: Bing returns one row PER MARKET, so a plain mean over rows lets a 3-impression market move the headline position as much as a 3,000-impression one (the position must be impression-weighted); script segmentation, because a blended CTR hid a segment earning 73% of clicks at 12x the average rate; and `ctr_underperformer`, which must fire on a top-10 page with <2% CTR but NOT on a deep ranking, where low CTR is expected |
| `test_backlinks.py` | the controls for referrer classification: attack probes vs real links from the same host (`wordpress.org` → `/wp-login.php` vs → `/`), a second owned domain at any subdomain/port, bare-IP and cPanel-port referrer spam, hotlinked assets, and a structural check that **every `referrers` flag survives the `--remote` argv reconstruction** — a flag added to the parser and forgotten there is silently dropped, and only on remote runs |
| `test_bing.py` | the controls for the Bing page dimension AND the crawler side: a running total summed over days (`Code2xx` = 96,000 for 7,408 pages crawled), a reference/measurement disagreement on a column's kind that must SHOW rather than be overridden, an empty `crawlissues` that must read `unknown` when `crawlstats` logged errors, the SECOND never-sentinel (`/Date(-11644473600000)/` = the Windows FILETIME epoch, which decodes to a well-formed `1601-01-01` and shipped that way), and `submit`'s guards firing BEFORE the network — off-site dropped, over-quota refused whole, nothing sent on a dry run. Also the original page-dimension set: `GetPageStats` returning the page URL in a field literally named `Query`, per-date rows that only total after aggregation, click-sorting vs impression-sorting (which invert on real data), CTR on a zero-impression page, and an empty result that must name BOTH "no data yet" and "you typed the URL wrong" rather than implying the page is dead. Also the `urlinfo` decoder — .NET `DateTime.MinValue` (`/Date(-62135568000000-0800)/`) is "never", not year 0001 — and the refusal of `--days` on the four subcommands whose endpoints have no date range, with controls that `keyword`/`expand` still accept it. Also the weekly query shape: dated rows, impression-weighted `by_query` (7.4, not the plain mean 7.0), date-ordered `--q` series, exact vs `--contains`, -1 click positions never surfacing; and `cannibal`: a known split + owner_absent + edge_of_page, `--min-share` honoured, a failed owner call `unmeasured` not `absent`, a slash-variant owner refused, all-pages-failed refused, the head-term map's shape read, and a control site with no shared queries reporting none. Since 2026-10-18 also: the error envelope in a 400 body, throttle retry and exhaustion, an empty 404 as retired; keywordtrend ordering and short-series trends; querypages URL-in-`Query` and split flags; querypagedetail histogram and page1_share; urltraffic's zero cross-examined; children `unknown` while Bing holds pages; feeddetails stale and failing children; submitfeed/removefeed guards and read-back; fetch retry and slash-folded history; cannibal query-side discovery without double-counting |
| `test_bingadmin.py` | the account-configuration writes against a fake account that mutates like Bing (or not at all, like Bing's null-for-a-no-op): no-op removes/adds refused before sending, dry run sends nothing, `confirmed` / `not_reflected` / `unverifiable`, a blocked row removed exactly as Bing returned it, last-administrator and `--confirm-site` guards, crawl-rate read-modify-write and boost refusal, retired endpoints reported as retired, `state` naming a failed section |
| `test_sitegraph.py` | the controls for the link graph: the `<p class="silonav">` case that defeats tag-based boilerplate detection, self-referential hreflang counted as an inbound link, the island silo whose inlink counts look healthy, a `--start` that was never crawled reporting mass unreachability, a zero-edge graph refusing a verdict instead of calling every page an orphan, a global-nav hub being kept out of the orphan count while staying visible for review, section-scoped furniture (a locale nav on 100% of its locale and 1.5% of the site) being caught WITHOUT swallowing the island silo whose share is almost identical, and a canonical naming a URL the tree does not serve being told apart from one naming a different real page |
| `test_geo.py` | the controls for answer sampling: that an unaskable engine REFUSES rather than reporting `not_cited`, that a SERP with no overview is `no_answer_surface` and excluded from the rate rather than counted as a miss, that share of voice is one vote per answer (it read 4.2 on real data), and that the engine's own plumbing is excluded but visibly so |
| `test_remeasure.py` | the controls for the runner: that a missing metric is `unmeasured` and never a zero (the failure reports a collapse that never happened), that a changed command is refused rather than compared, that checking before the window refuses, and that `--cmd` survives argparse — a subparser `dest="cmd"` silently overwrote it and the dispatch died looking up the user's whole crawllog invocation as a subcommand name. A hand-built namespace in a control cannot catch that, because it never runs argparse, and that a row selector compares the SAME row after it moves from index 0 to index 3 (the positional index, run as a control in the same pass, compares a different row and reports `refuted`), that zero matches is `unmeasured` even with `--missing-is-zero`, and that two matches is refused, not resolved to the first |
| `test_brief.py` | the controls for the assembler: that no readable page 1 REFUSES rather than emitting a brief with the competitive sections quietly missing, that a 2-page contract withholds its subtopics while keeping them visible, and that the cannibalisation metric discriminates in both directions - jaccard scored a real hit and a nonsense query BOTH at zero, and a 'clear' from a metric that cannot fire green-lights a duplicate page for a query we already own |
| `test_vitals.py` | the controls for the sweep: the commented-out `<img>`/`<script>` that a regex counts (the same bug found twice before in this skill), `media=print` and `defer`/`async` not being render-blocking, template grouping that is neither so coarse the site is one row nor so fine it degenerates into the per-URL run it replaces, and that a sweep reading NOTHING refuses rather than reporting a fast site |
| `test_algoupdates.py` | the ledger and the span bug: a rollout that began before a window and ended inside it, a window wholly inside a rollout, an open rollout overlapping a later window, staleness named, every row on a Google-owned host, no `unverified[]` claim leaking into `updates[]` |
| `test_sitecheck.py` | host duplication counted once not twice, a refusal when no variant answers, soft 404 both ways, sampling that spreads across a file rather than reading its head |
| `test_stats.py` | every function pinned to a value PUBLISHED by the method's author (Newcombe 1998 tables, Holm 1979, the exact binomial), and the sign convention of a difference stated - a mirrored interval passed every magnitude check |
| `test_rankcheck.py` | `depth_checked` / `exit_country` / `rank_state` surviving `record-rank` (they did not), the four rank states, drop candidates (previously unranked never; gone from a too-shallow read is `unknown_depth`), and drop settlement on a second read |
| `test_controls.py` | the controls for the control audit itself: that it recognises BOTH invocation shapes, and - fired at a real fixture tree containing one controlled and one uncontrolled script - that an uncontrolled instrument makes the verdict not-ok and is NAMED rather than merely counted. An audit whose only failure mode is untestable is the instrument it exists to catch |
| `run_tests.py` | **the suite gate** - runs every `test_*.py` in its own process and reads its EXIT CODE, which is the signal these suites actually emit. 17 of the 18 define no `def test_` function, so `python3 -m pytest -q` collected 6 tests from ONE file and printed "6 passed" - a clean green over an eighteenth of the suite, with nothing skipped and no warning. It reports `invisible_to_pytest` on every run so that number is measured rather than remembered, and an empty discovery REFUSES rather than reading as a clean sweep. `test_all_suites.py` is a two-function shim so that `pytest` cannot lie either |
| `test_gsc.py` | the controls for the Search Console client: that the pure-Python RS256 signer verifies against its own public key and REFUSES a signature over other bytes, that a truncated key raises rather than returning a tiny modulus (a PKCS#1 parser pointed at PKCS#8 reads the version integer as the modulus), that string counts coerce and a missing one is `None` not 0, that a fully-lagged window is refused with the last complete day named, and that `sitemap-submit` cannot fire without `--yes` |
| `test_directives.py` | crawler-scoped X-Robots-Tag, `max-image-preview:none` vs `none`, `<meta name=googlebot>`, repeated headers kept, and the three consumers agreeing |
| `test_http.py` | providers.http against a local server: a gzip bomb refused under `max_bytes`, a 403 keeps its headers, a gzipped error body still decoded |
| `test_indexnow.py` | per-host batches and the five key-file states |
| `test_serp_browser.py` | the extraction accounting guard and the consent-wall marker (the JS itself was verified in headless Chrome on openserp's fixture) |
| `test_ga4.py` | which 403 a 403 is, the schema guard, AI hosts as hosts, offset paging |
| `test_hreflang.py` / `test_contract.py` / `test_agentcheck.py` / `test_slop.py` / `test_crawllog.py` / `test_competitors.py` | the controls for the above: every rule is fired against synthetic input, so a clean pass on a real site means something. `test_crawllog` covers the UA-spoofing detector (including the control that a single operator's many crawlers are NOT flagged) and the no-input refusal. `test_competitors` fires every guarantee of the page-1 profiler against synthetic input - robots.txt obedience by the HTTP fetcher, injection-shape defanging, that every unread result is offered for browser escalation WITH its reason, and that platform chrome never becomes a 'subtopic page 1 covers' |

Also `assets/google-updates.json` — Google's published algorithm-update calendar,
every entry carrying a Google-owned source URL. Consumed by `decay.py --updates`
and `drift.py --updates`. **`algoupdates.py sync` keeps the rollout half current
from Google's Search Status Dashboard** (`incidents.json` + each product's
`/history`); documentation, product and policy rows are still added by hand,
with a Google-owned source. "No API exists" was the belief this file carried
until 2026-10-03 — it was wrong, and the calendar was three months stale under
it. Its silence about a window is still not evidence that nothing happened.

`--help` on any of them lists the subcommands. Common ones:

```bash
python3 $SEO/seostate.py suggestions --status approved --type guide   # the build queue, in order
python3 $SEO/seostate.py pacing                                       # can a guide ship today?
python3 $SEO/seostate.py deferred --due                               # withheld targets whose re-check condition is met
python3 $SEO/seostate.py defer --keyword "kw" --class catalogue --authority-count 6 --page1 a.com,b.com
python3 $SEO/serp.py "keyword" --count 10 --target-domain example.com
python3 $SEO/serp.py "best hiking boots" --provider bing --count 10   # Bing page 1; opt-in, needs SEO_PROXY_URL
# ⚠ Read `refusals.bing.kind` first. `class: cannot_ask` (challenge, exit_mismatch,
# echo_missing, unparsed, transport) = Bing never answered from here. `class: wrong_answer`
# = it answered something else: `wrong_query` (an unrelated SERP - a request without the
# proxy returned speed tests with the right query still in the search box) or
# `query_collapse` (the query's first word alone: verb-led queries returned the bare verb's
# results 15 of 15 times). Neither is ever an empty page 1.
# ⚠ ONE page: Bing ignored first=11 and count=30. Same query, different session can be
# clean vs collapsed - one refused read is a refused read, not a property of the query.
python3 $SEO/headterms.py check --url /guides/new --title "<title>" --h1 "<h1>"   # before shipping a head
python3 $SEO/headterms.py scan --root <build output>      # every page that took an owned phrase
python3 $SEO/claims.py scan --root <build output>         # copy the ledger says is false + competitor framing
python3 $SEO/remeasure.py record --id h1 --question "did the owner take the query?" \
  --cmd "bing.py pagequeries --page https://example.com/" \
  --metric 'by_query[query=best hiking boots].avg_position' --expect decrease --min-change 2 --after 4w
python3 $SEO/keywords.py expand --seed "<facet>" --groups commercial comparison
python3 $SEO/keywords.py expand --seed "<facet>" --engines all --sort agreement  # 6 corpora
python3 $SEO/sameness.py check --draft new.md --corpus content/blog --keyword "kw" --pages .seo/pages.json
python3 $SEO/authority.py --domain example.com --save
python3 $SEO/authority.py --domain example.com --bulk rival1.com,rival2.com   # who actually outranks you
python3 $SEO/rankcheck.py --all --depth 20                     # drops confirmed by a second read
python3 $SEO/rankcheck.py --all --proxy-country us              # name the exit, or it is "unpinned"

# keyless demand signals (no key, no browser - see data-sources.md)
python3 $SEO/trendfeeds.py trending --geo US            # Trends RSS: the API 429s, this does not
python3 $SEO/trendfeeds.py wiki --topic "<topic>"       # resolve the article title FIRST
python3 $SEO/trendfeeds.py pageviews --article "<Title>" --days 90
python3 $SEO/trendfeeds.py discussions --query "<problem>" --site webmasters

# per-topic trend + who is covering it (keyless)
python3 $SEO/trendfeeds.py newsvolume --query "<phrase>" --months 3   # COVERAGE, not demand
python3 $SEO/trendfeeds.py news --query "<phrase>"                    # + the publishers

# information gain, with real sources behind it (keyless)
python3 $SEO/factcheck.py sources --query "<topic>" --since-year 2020  # papers, citations, DOIs
python3 $SEO/factcheck.py related --topic "<Article Title>"            # the topic neighbourhood
python3 $SEO/factcheck.py coverage --draft new.md --topic "<Article Title>"
python3 $SEO/factcheck.py claims --draft new.md --fetch    # is every number in the page it cites?

# any-URL technical checks, incl. a COMPETITOR's change history (keyless)
python3 $SEO/pagecheck.py schema https://rival.example/page
python3 $SEO/pagecheck.py history https://rival.example/page --since 2026-05-01
python3 $SEO/pagecheck.py vitals https://oursite.example/page         # lab + real-user CWV

# do answer engines cite us? (Google AI Overview works today via the SERP key)
python3 $SEO/geo.py engines                              # what can be asked RIGHT NOW
python3 $SEO/geo.py ask --query "..." --domain example.com --runs 3   # a RATE with n, not a coin toss
python3 $SEO/geo.py sweep --domain example.com --bank questions.txt --max 10 --runs 3
python3 $SEO/geo.py extractable --root ./public/seo/guides
python3 $SEO/geo.py diff --before .seo/geo/sweep-a.json --after .seo/geo/sweep-b.json  # paired, exact
python3 $SEO/gsc.py query --dimensions query --days 28 > .seo/gsc-q.json
python3 $SEO/geo.py gap --domain example.com --gsc .seo/gsc-q.json --top 30          # who ranks, unquoted
# ⚠ Read `runs.ci95` and `stable` before a rate. "Cited 3/3" has a Wilson low of 0.44 and
# is NOT stably "usually cited". `small_sample` is true below 20 answers.
# ⚠ `parser_suspect_engines` names an engine that answered 5+ questions and cited NOTHING
# in any of them - its answers are dropped from the rate, because a renamed payload field
# looks exactly like an engine that cites nobody.
# ⚠ `no_key` and `failing` are CANNOT ASK. Only an engine that ANSWERED can say
# `not_cited`, and a sweep with no reachable engine REFUSES rather than reporting that
# nobody cites you. This is the same rule as providers.py, and it is the whole instrument.
# ⚠ A SERP with no AI Overview is `no_answer_surface`, NOT a miss - counting it computes
# a citation rate against answers that never existed.
# ⚠ `share` is one vote per ANSWER, not per citation link, or a single answer linking a
# domain four times outweighs four answers linking it once (measured: share 4.2).
# ⚠ The engine's own domains are excluded and listed in `engine_furniture_excluded`.
# google.com appeared 21 times across 5 real answers and dominated the table.
# ⚠ SerpApi returns the AI Overview in TWO STAGES - the first response carries only a
# page_token, so reading `references` off it yields [] for every overview on earth. serp.py
# had exactly that bug: `present: true, references: []` always, i.e. "never cited", forever.

# did the change actually work? Register the question BEFORE you know the answer.
python3 $SEO/remeasure.py --root . record --id app-robots-block \
  --question "did the robots.txt block stop bingbot crawling /app?" \
  --cmd "crawllog.py scan --remote root@HOST --ssh-key K --glob '/var/log/caddy/access*.log*' --days 14 --bot bingbot --top 40" \
  --metric 'bots.0.top_silos./app' --missing-is-zero \
  --expect decrease --min-change 400 --after 21d
python3 $SEO/remeasure.py --root . due            # ready vs still waiting, with days left
python3 $SEO/remeasure.py --root . check --id app-robots-block
# ⚠ `--missing-is-zero` is OPT-IN and belongs only on SPARSE maps where absence really
# means zero (crawllog's top_silos omits a silo the bot never touched). As a default it
# turns every renamed key and every failed read into a zero.
# ⚠ `unmeasured` is not `no_change`. The hypothesis stays open and nothing was learned.
# ⚠ The command is stored WITH the baseline and re-run verbatim. A `--cmd` that differs is
# REFUSED. Quoting round-trips through shlex, so `serp.py 'best hiking boots'` survives.
# metric paths: keys, list indices, and ROW SELECTORS (2026-10-12)
#   segment  := name? selector*            several selectors on one segment = AND on ONE row
#   selector := [field=value]              dots/spaces inside [] are literal; "quoted" for ] or "
#   --metric 'by_query[query=best hiking boots].avg_position'
#   --metric 'queries[query=tent][page=https://example.com/b].avg_position'
# ⚠ Prefer a selector over an index for any list the instrument sorts by value: an index
# re-points itself between runs and the check compares two different rows.
# ⚠ zero matches = `unmeasured` (even with --missing-is-zero); two+ = refused `ambiguous`.

# a build brief for one query - assembled from measurements, refuses without page 1
python3 $SEO/brief.py build --query "how to wax a snowboard" --our-domain example.com \
    --corpus ./content
python3 $SEO/brief.py build --query "..." --contract-json profile.json  # reuse a run
# ⚠ Read `completeness` and `unavailable` before the brief itself. A missing section is a
# question that was not answered, NEVER a finding of "nothing there".
# ⚠ `differentiators` are WITHHELD below 4 readable page-1 results and the raw list is
# kept in `differentiators_withheld.raw_gaps` for review. Measured on a niche how-to
# query: 2 of 10 results were readable and the gaps came back `ratings, submission,
# score, favorite` - platform furniture from two community upload sites, because the UGC
# registry is a fixed domain list and one of them was not on it. Printed as differentiators that is an
# instruction to write a section about somebody's ratings widget.
# ⚠ The cannibalisation check reads the SLUG as well as headings and opening, and
# `matched_in` says which fired. A well-written page avoids its own keyword in H2s -
# the real guide leads with "what the engine is actually doing" and scored 0.0
# until the slug counted. It also uses CONTAINMENT, not jaccard: jaccard is symmetric, so
# a 4-token query against a 100-token page scores 0.04 and every query reads "clear".

# whole-site Core Web Vitals, sampled per template (keyless, no browser)
python3 $SEO/vitals.py sweep --sitemap https://example.com/sitemap.xml --per-template 4
python3 $SEO/vitals.py page https://example.com/some-page     # one URL
python3 $SEO/vitals.py origin https://example.com             # CrUX field data (credential)
python3 $SEO/vitals.py origin https://example.com --subparts  # + LCP decomposed (API key only)
python3 $SEO/vitals.py history https://example.com            # 25 weekly field p75s + a noise-aware trend
# ⚠ READ `network_baseline` IN THE OUTPUT FIRST. It times a known-fast third-party host
# from THIS machine, because the first version of this tool timed a plain urlopen and
# reported 5,940ms TTFB for a server that answers in 119ms - the rest was the container's
# DNS (1,446ms) and TLS. It flagged `slow_ttfb`, severity high, "server/CDN work", and
# pointed at entirely the wrong system. connect_ms is now measured and EXCLUDED.
# ⚠ TTFB is sampled TWICE and the finding uses the FASTER run. One sample cannot tell a
# cold cache from a slow page: /rankings measured 12,065ms once and 70-94ms on three
# repeats (a 5-minute server cache the sweep happened to miss), while /listings/<one-item>
# measured 4,188 / 4,186 / 4,307ms - reproducible, and a real finding on 1,404 URLs.
# `ttfb_cold_ms` keeps the cold number, reported as its own `cold_cache_cost` finding.
# ⚠ A row carries `ttfb_spread` when its slowest sample fired the rule, because a median
# next to a `fail` reads as a tool bug. /listings/* came back [65, 66, 4071, 4157] - a
# bimodal template, which a median would have hidden either way.

# run the test suites - NOT `pytest`, which collects from a handful of files and exits 0
python3 $SEO/run_tests.py                # 33 suites, JSON verdict, exit 1 on any failure
# ⏱ ~1m35s on a 9p mount (~10s user, ~43s sys - it is process churn, not network).
# `controls.py audit` is ~39s for the same reason. Neither has hung; do not run them
# inside a 2-minute tool timeout together.
python3 $SEO/run_tests.py --list         # what would run + what pytest would collect
python3 $SEO/run_tests.py -k sitegraph   # one suite by substring
python3 $SEO/run_tests.py control        # prove the runner can fail
# ⚠ `pytest` is honest ONLY because `test_all_suites.py` shims it. Delete that file
# and `pytest -q` goes back to reporting a green it did not earn.

# is every instrument still able to tell a finding from a reader bug?
# Run this BEFORE trusting any zero. 35 instruments, 697 checks (2026-10-03).
# Most are offline; trendfeeds/algoupdates-sync/check-sources probe live sources, and a
# live source that REFUSES (429/5xx) is reported `unreachable`, never `broken`.
python3 $SEO/controls.py audit          # ok:false names the ones that cannot
python3 $SEO/controls.py audit --static # detect declarations only, run nothing
# ⚠ `--static` matches a REGEX against each source. It cannot tell a working control
# from one that raises on import or returns garbage, so it reports `proven: 0` and
# `mode: static`. Read `summary.proven`, never `summary.declared`, as the count of
# instruments that can currently prove they discriminate.
python3 $SEO/<any>.py control           # one instrument (serp/serpd/seodoctor/
python3 $SEO/serp.py --control          # rankcheck/authority take --control)
# ⚠ Two invocation shapes, because two argument styles exist. Scripts with
# subparsers take `control`; flag-style ones take `--control`. A detector that
# knew only the first reported SIX controlled instruments as uncontrolled - the
# same class of error as the tools it audits.
# ⚠ An `absent` row is not a bug report about that script's ANSWERS. It means
# nothing in it can distinguish "found nothing" from "the reader is broken", so
# its zeros are not evidence. That is why `absent` counts against the verdict.

# what data sources actually work right now (measured, not assumed)
python3 $SEO/providers.py status

# real numbers (Bing Webmaster - needs a verified property)
python3 $SEO/bing.py sites                          # auth control; run first if anything looks odd
python3 $SEO/bing.py traffic --days 90              # IS Bing bigger than Google here? check, do not assume
python3 $SEO/bing.py queries --limit 400 > bq.json  # weekly rows (`queries`, dated) + `by_query`
python3 $SEO/bing.py queries --q "<query>"          # ONE query's weekly series, date order: trend vs flat
python3 $SEO/bing.py queries --contains "<word>"    # every query containing it, each with its series
python3 $SEO/bing.py pages --limit 40               # WHICH URL earns - sorted by CLICKS, not impressions
python3 $SEO/bing.py pagequeries --page 'https://example.com/zh/'   # attribution for one page
python3 $SEO/bing.py cannibal --limit 20            # top 20 pages: split / edge_of_page per query
python3 $SEO/bing.py cannibal --owners-file .seo/headterms.json   # + owner_absent per owned phrase
# ⚠ `queries` lists a query once per WEEK. Read `by_query` for totals, never count rows.
# ⚠ cannibal sees only the measured pages; `owner_absent` needs the owner's call to have
#   succeeded (`unmeasured` otherwise), and an owner URL must match Bing's exact string.

# THE CRAWLER'S SIDE of the same account - what bingbot did, not what searchers did.
# First-party, no UA to verify, no log to parse. Pairs with crawllog.py: two
# instruments reading the same events, and a disagreement teaches you something.
python3 $SEO/bing.py crawlstats     # per day: pages fetched, code mix, pages in index
python3 $SEO/bing.py crawlissues    # per URL. An empty list is cross-checked, not trusted
python3 $SEO/bing.py feeds --verify # the sitemap AS BING HOLDS IT vs the live file
python3 $SEO/bing.py blocked        # account-level blocks - invisible to any crawl of the site
python3 $SEO/bing.py crawlsettings  # a self-imposed per-hour crawl-rate cap
# ⚠ crawlstats mixes DAILY counts with RUNNING TOTALS in one row and labels neither.
# Summing the wrong column gave Code2xx = 96,000 for a site with 7,408 pages crawled.
# The tool re-derives each column's kind per run and REFUSES to sum the cumulative
# ones; read latest_stock.<col>.latest, and .change_over_window for movement.
# ⚠ `all_other_codes` has no documented breakdown and ran at 0.88x pages crawled.
# It is reported under `caution` and is NOT an error rate.

# GETTING CRAWLED - two channels, separate quotas, and neither reaches Google.
python3 $SEO/bing.py quota                                    # read it immediately before a batch
python3 $SEO/bing.py submit --url https://example.com/a        # DRY RUN - shows, spends nothing
python3 $SEO/bing.py submit --from-file urls.txt --yes         # actually submits
python3 $SEO/indexnow.py ping --pending                        # the OTHER channel: keyless, also
                                                               # Yandex/Seznam/Naver/DuckDuckGo
# ⚠ `submit` is the only mutating call in bing.py. Off-site URLs are dropped before the
# call (Bing rejects the whole batch for one) and an over-quota batch is refused whole
# rather than truncated. Its receipt is for the REQUEST, not a crawl - verify later with
# `urlinfo`, with a control. ⚠ Check `remeasure.py due` first: submitting into a silo
# that is the subject of an open hypothesis is a second intervention inside someone
# else's experiment.

# Bing's OWN index record for one URL - discovered, last crawled, size. The Bing
# counterpart to Google's URL Inspection, free and with no quota worth counting, and
# the one that matters wherever Bing carries the traffic (measure that with `traffic`,
# do not assume it). ⚠ ALWAYS pair it with a known-crawled control: a URL that DOES NOT
# EXIST returns exactly the same empty record as a real page Bing has never seen -
# measured, byte-identical - so `known_to_bing: false` means "no record of this string",
# never "this page was excluded".
python3 $SEO/bing.py urlinfo --url https://example.com/guides/x     # the page in question
python3 $SEO/bing.py urlinfo --url https://example.com/             # CONTROL: known crawled

# ⚠ --days is REFUSED on queries/pages/pagequeries/traffic and that is the guard working:
# those endpoints take no date range, so a --days they accepted would return the same rows
# for every value. `keyword` and `expand` are the two that honour it.
# ⚠ `known_to_bing: false` from `keyword` is NOT a demand verdict either - coverage is
# patchy, measured: a niche long-tail query reports exact=2 while plainly larger queries report
# nothing at all. An unknown query is UNMEASURED.
python3 $SEO/keywords.py bing bq.json               # band them; READ by_script FIRST
# ⚠ Chinese/Japanese/Arabic seeds return NOTHING on the default us/en-US market.
#   That is the wrong question, not absent demand:
python3 $SEO/bing.py expand --seed "<the locale's head term>" --country cn --language zh-CN
python3 $SEO/bing.py keyword --q "<kw>" --days 90   # impressions, NOT Google volume
python3 $SEO/bing.py expand  --seed "<kw>" --limit 25
python3 $SEO/bing.py backlinks

# SERP-heavy run (research): start the daemon once, then one call for the lot
# NEVER append `&` - --start already detaches, and the `&` only kills the poller
# that tells you whether it came up. Run it in the foreground and read the JSON.
python3 $SEO/serpd.py --start
curl -s -m 900 -X POST localhost:8791/batch -H 'Content-Type: application/json' \
  -d '{"id":"my-run","queries":["kw one","kw two"],"depth":20}'   # compact verdicts, spooled under the id
curl -s 'localhost:8791/batch?id=my-run'                           # recover it if the POST's client timed out

# Google's update calendar - kept current from Google's own dashboard
python3 $SEO/algoupdates.py status                    # stale? open rollouts? refused rumours
python3 $SEO/algoupdates.py sync --dry-run            # what the dashboard would add/correct
python3 $SEO/algoupdates.py sync
python3 $SEO/algoupdates.py window --start 2026-09-01 --end 2026-09-30
python3 $SEO/algoupdates.py check-sources             # do the cited Google URLs still answer
# ⚠ `possibly_in_window` is a rollout whose END is unknown, bounded by the longest rollout
# the ledger has measured. It is not "in the window" and must not be reported as such.

# site-level technical checks (the health workflow's three non-negotiables)
python3 $SEO/sitecheck.py all https://example.com --sample 150 --save
python3 $SEO/sitecheck.py sitemap https://example.com --sample 0     # every listed URL
python3 $SEO/sitecheck.py links --graph .seo/graph/site.json --origin https://example.com \
  --sitemap https://example.com/sitemap.xml
python3 $SEO/sitecheck.py diff --before .seo/sitecheck/a.json --after .seo/sitecheck/b.json

# measurement of what already exists
python3 $SEO/crawllog.py scan --remote root@<host> --ssh-key ~/.ssh/<k> \
  --glob '/var/log/caddy/access*.log*'                 # QUOTE the glob
python3 $SEO/crawllog.py verify --scan scan.json --bot googlebot
python3 $SEO/crawllog.py verify --scan scan.json --bot oai-searchbot   # AI crawlers: the CIDR witness
python3 $SEO/ipranges.py status                                        # which operators' lists read now
python3 $SEO/decay.py compare --previous prev.json --current cur.json --pages .seo/pages.json
python3 $SEO/drift.py snapshot --keywords-from .seo/keywords.json --out .seo/drift/$(date -u +%F).json
python3 $SEO/backlinks.py referrers --remote root@<host> --site example.com
python3 $SEO/backlinks.py footprint --domain example.com
python3 $SEO/backlinks.py referrers --remote root@<host> --site example.com > .seo/ref.json
python3 $SEO/backlinks.py reclaim --scan .seo/ref.json --site https://example.com   # dead landings -> 301s
python3 $SEO/backlinks.py mentions --brand "Example" --site example.com --add-prospects
python3 $SEO/crawllog.py urls --remote root@<host> --glob '...' --bot googlebot --keep-query > gb.tsv
python3 $SEO/crawllog.py facets --urls gb.tsv --site https://example.com --check 10
python3 $SEO/sameness.py tiers --corpus public/seo/listings    # O(n) index-bloat

# the internal link graph - offline against a generated tree, no network at all
python3 $SEO/sitegraph.py crawl --root public/seo=/ --root public/legal=/ \
  --rewrite '/landers/=/' --out .seo/graph/site.json     # 3,981 pages in ~30s
python3 $SEO/sitegraph.py silos   --graph .seo/graph/site.json   # READ THIS FIRST
python3 $SEO/sitegraph.py inlinks --graph .seo/graph/site.json /guides/getting-started
python3 $SEO/sitegraph.py orphans --graph .seo/graph/site.json --contextual
python3 $SEO/sitegraph.py depth   --graph .seo/graph/site.json --start /listings
python3 $SEO/sitegraph.py broken  --graph .seo/graph/site.json --ignore '^/live/'
python3 $SEO/sitegraph.py canonicals --graph .seo/graph/site.json   # canonical -> 404 = unindexable
python3 $SEO/sitegraph.py suggest --graph .seo/graph/site.json --gsc .seo/gsc-page-query.json
# live instead, obeying robots.txt per origin:
python3 $SEO/sitegraph.py crawl --url https://example.com/ --max-pages 500 --out g.json
python3 $SEO/keywords.py cluster --file kws.txt                # one page or five?

# guards on your OWN markup (run contract after every deploy - see the note above)
python3 $SEO/contract.py baseline --name prod --sitemap https://example.com/sitemap.xml
python3 $SEO/contract.py check --name prod        # opened / still_open / resolved
python3 $SEO/hreflang.py control                  # ALWAYS first - refuses a verdict if it fails
python3 $SEO/hreflang.py audit --url https://example.com/page   # expands to every alternate
python3 $SEO/hreflang.py parity https://example.com/page        # read `systematic` FIRST
python3 $SEO/hreflang.py codes en-uk eng jp be    # no network at all

# AI agents: permitted? readable? (pairs with crawllog.py, which measures who CAME)
python3 $SEO/agentcheck.py policy https://example.com    # per-crawler, by class
python3 $SEO/agentcheck.py page https://example.com/page # agent-UX + token budget + JS-dependence
python3 $SEO/agentcheck.py reach https://example.com/page   # what each crawler UA is SERVED
python3 $SEO/agentcheck.py discovery https://example.com     # ai-catalog / .well-known / UCP
python3 $SEO/agentcheck.py all https://example.com
# ⚠ `reach` answers `cannot_ask` when a forged Googlebot is refused too: that edge verifies
# crawlers by IP, so a forged AI UA's 403 says nothing about the real crawler (measured on
# reddit.com 2026-10-03). Only the access log answers that.

# ⚠ Do NOT hand-verify a robots.txt with `urllib.robotparser`.read() behind a CDN.
# RobotFileParser turns a 401/403 into disallow_all, so a Cloudflare block of the
# default Python User-Agent reports EVERY path as forbidden - measured 2026-09-01
# on a CDN-fronted site, where it said a /listings/ page was blocked while that page
# was indexed and had 4,106 Googlebot hits in the logs. The refusal and a real
# site-wide Disallow are the same value with no way to tell them apart. Fetch the
# bytes yourself and hand them to .parse(), and always carry a KNOWN-CRAWLABLE
# control path so a blanket deny is visible as the artefact it is:
#   curl -s https://example.com/robots.txt -o /tmp/rb.txt
#   python3 -c "import urllib.robotparser as r;p=r.RobotFileParser();\
#     p.parse(open('/tmp/rb.txt').read().splitlines());\
#     print(p.can_fetch('Googlebot','/a-page-you-KNOW-is-indexed'))"   # must be True
# `agentcheck.py policy` is not affected: it reads robots.txt itself and reports an
# HTTP error as an error rather than as a verdict.

# does the draft read as machine-written (advisory, unlike the sameness gate)
python3 $SEO/slop.py scan draft.md
python3 $SEO/slop.py diff before.md after.md      # read `introduced`, not just `removed`
```
