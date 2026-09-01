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
| `seostate.py` | **all state**: queue, keywords, ranks, pages, trends, prospects, profile, conventions, pacing, overview, next-actions, run log |
| `serp.py` | live SERPs through the provider ladder, plus the weakness/authority scoring the gate needs |
| `keywords.py` | expansion across **six independent suggestion corpora** (Google, Bing, DuckDuckGo, YouTube, Yandex, Amazon) with a cross-engine agreement signal and observed video/product intent, plus tool-intent sweeps, DataForSEO volume/KD, and Search Console → candidates |
| `competitors.py` | **reads page 1 instead of guessing from titles**: fetches each result and returns the depth you are matching (median words/headings), which results are thin/UGC/stale, and the subtopics most of them cover. The HTTP fetcher honours robots.txt (that is what RFC 9309 governs) and treats every fetched byte as inert data; anything it cannot read is listed as `browser_candidates` to be read through the browser-automation MCP - a bounded one-pass read of ten page-1 URLs, which is normal research practice, not crawling . A page that returns a bot challenge is reported as `challenged`, never as `weak`: an interstitial looks exactly like a thin page (a few words, one heading), and calling it weak inverts the finding — it says a competitor is beatable when all you learned is that they block you |
| `brief.py` | **a content brief ASSEMBLED from measurements, never invented**. Composes page 1 (`serp.py`), the intent contract (`competitors.py`), what people actually ask (`keywords.py` suggestion corpora), whether we already have the page (`sameness.py` extractor + containment over slug/headings/opening), and whether we can realistically compete (`authority.py`). Every section is MEASURED or listed in `unavailable` with its reason. **No readable page 1 = a hard refusal**, because a brief that states a depth target and a competitive read it did not measure is the writer's assumptions handed back with a tool's authority. Subtopics are WITHHELD below 4 readable results - the failure there is a confident list, not an empty one |
| `sameness.py` | the corpus sameness gate + a pairwise drift audit |
| `sitegraph.py` | **the internal link graph** — build it from a LOCAL generated tree (no network, works on an undeployed build) or by live BFS, then ask it for orphans, click depth, broken links and `silos`. That last one is the point: it ranks silos by EXTERNAL-silo inbound links and flags **islands** — a silo whose pages cross-link each other so every naive inlink count looks healthy while nothing outside points in. Counts `<link rel=alternate hreflang>` as a discovery edge, ignores self-loops, and detects site furniture by FREQUENCY rather than by tag, because a nav rendered as `<p class="silonav">` defeats a tag-based rule. `orphans --contextual` splits out pages that are THEMSELVES global-nav furniture into `nav_hub_urls` — a hub with 12,070 inbound links is the most reachable page on the site, and counting it as an orphan pads the number until the real orphan hides in the list |
| `authority.py` | DR-equivalent, and the KD zones / volume band that follow from it. Also `--bulk` competitor scoring, free referring-domain counts, a 12-month authority trend, and two independent popularity reads (Cloudflare Radar + **Tranco**, which is keyless and carries 40 days of rank history) |
| `bing.py` | **the only free source of real search volume AND backlinks** — Bing Webmaster Tools for a site you own. Impressions per query, related-keyword expansion with numbers, inbound links, query stats, `urlinfo` for Bing's own per-URL index record (discovered / last crawled / size — its answer to Google's URL Inspection), and `pages`/`pagequeries` for the PAGE dimension — which URL actually earns the clicks, and for which searches. `queries` alone cannot tell a locale page ranking in its own language from the homepage ranking for that language, and those two have opposite fixes |
| `trendfeeds.py` | **keyless demand signals**: Google Trends' RSS feed (works where the 429-ing JSON API does not), Wikimedia pageviews as absolute topic demand, HN + StackExchange chatter, **GDELT's per-topic news-volume timeline**, and Google News (who is covering the topic now) |
| `rankcheck.py` | batch rank checks for every tracked keyword |
| `serpd.py` | **the fast path for SERP-heavy runs**: a long-lived headed Chrome behind `localhost:8791`. One curl per check, real Google, no DOM in your context. 25 checks in ~37s and 1.4KB of verdicts. |
| `indexnow.py` | get published pages crawled: IndexNow ping (free, keyless, Bing/Yandex) + the batched Google "Request indexing" follow-up |
| `test_guards.py` | regression tests for the SERP guards, against real captured responses |
| `test_providers.py` | regression tests for the free-provider integrations — chiefly that Open PageRank's `found: false` can never become a DR of 0 |
| `controls.py` | **the control primitive, and the audit that proves every instrument carries one**. A negative result is only as good as its control: "nothing found" and "the reader is broken" serialise to the same JSON unless something separately proves the reader can still find a thing that is known to be there. Exports `Controls` (accumulate named checks, emit one verdict), `refuse()` (the standard shape for "cannot ask", which must never share a code path with "the answer is no"), `guard_zero()` (gate a zero on a passing control) and `uniform_verdict()` (a whole population agreeing IS the tell - `slop.py` once returned `warn` for 44 of 44 pages because it was counting `<title>`, JSON-LD and comments as prose). `controls.py audit` runs every instrument's own control and reports which can currently prove they discriminate |
| `seodoctor.py` | **self-healing preflight** - idempotent check+repair of the daemon, its Chrome, deps and project state. Run it first, every run. `--providers` adds a live sweep of every data source |
| `providers.py` | **the provider registry**: every data source declared once, each with a live probe and its own control. `providers.py status` answers "what can I use right now?" by measuring, not by reading a table |
| `factcheck.py` | **information gain, sourced**: OpenAlex + Crossref papers with citation counts and DOIs, Wikidata entities, the Wikipedia neighbourhood of a topic, and a draft-vs-neighbourhood coverage gap |
| `pagecheck.py` | keyless technical checks for ANY url: W3C HTML validity, Google's own structured-data extractor, Wayback change history (yours or a competitor's), and Core Web Vitals |
| `vitals.py` | **whole-site Core Web Vitals, by TEMPLATE rather than by URL**. `pagecheck.py vitals` is the right tool for one page and the wrong shape for a site: PSI takes ~20s per URL and is quota-limited, so a 5,388-URL sitemap is not a sweep you can run. This groups URLs by path shape, samples within each, and reports per template - the level at which a fix is applied. Keyless by default (TTFB, transfer size, compression, render-blocking head resources, image dimension/lazy hygiene, DOM size, third-party origins); `--psi N` spends the quota on the N worst. ⚠ It reports PROXIES, never LCP/CLS/INP - those need a browser, and the ranking-relevant versions need real users, which is what `vitals.py origin` reads from CrUX |
| `crawllog.py` | **the access log**: crawl budget by silo, status codes served to bots, AI-crawler ingestion, and reverse+forward DNS bot verification. `--remote` runs the aggregation on the server so the log never crosses the wire. |
| `decay.py` | two Search Console periods -> pages that LOST RANK, separated from pages whose demand fell. Plus self-cannibalisation. |
| `drift.py` | whole-page-1 snapshots and their diff: new entrants, AI-Overview changes, site-wide volatility, algorithm-update correlation |
| `backlinks.py` | **measurement**, not prospecting: real traffic-sending backlinks from your own referrer log, and Common Crawl corpus presence. `referrers` classifies every referring domain and reports only the genuine ones — a referring domain is NOT a backlink, and on a live run 40 of 52 were the site's own second domain (`--own`), an attack probe whose forged `Referer` made `wordpress.org → /wp-login.php` look editorial, a hotlinked favicon, or referrer spam on a cPanel port. The rejects stay in `excluded` with their reason, because a misclassification has to be visible to be fixable |
| `contract.py` | **the deploy guard**: baseline a URL set's on-page SEO contract, then diff it. Reads `X-Robots-Tag` from the header as well as the meta tag, never follows redirects, and keys findings `(path, rule)` with an open/auto-resolve lifecycle. Refuses a verdict during a site-wide outage. |
| `hreflang.py` | **the international mesh**: self-reference, RETURN TAGS, x-default, ISO 639-1/15924/3166-1 validity, canonical alignment, and the HTTP status of every URL advertised as an alternate. Plus `parity` — is the content behind the mesh actually translated. |
| `agentcheck.py` | **can an AI agent read, understand and act on this site, and is it allowed to**: robots.txt resolved per AI crawler in the ai_search/ai_user/ai_training taxonomy, agent-UX semantics, token budget, JS-dependence, WebMCP, and `llms.txt` well-formedness |
| `slop.py` | **AI writing tells, detected mechanically** — 20 patterns with per-rule tolerances, located hits and line numbers. Code fences, inline code and link targets excluded. No score, on purpose. |
| `test_measure.py` | the controls for the **measurement** scripts (`crawllog` / `backlinks` / `decay`). Every case is a bug that shipped and was caught against live data, or a distinction that silently produces a confident wrong answer — UA registry ordering (`Googlebot-Image` contains "googlebot"; `ChatGPT-User` does not contain "gptbot"), and the three-state verification that keeps "cannot ask" out of the same code path as "the answer is no" |
| `test_keywords.py` | the controls for the band analysers, chiefly `keywords.py bing`: Bing returns one row PER MARKET, so a plain mean over rows lets a 3-impression market move the headline position as much as a 3,000-impression one (the position must be impression-weighted); script segmentation, because a blended CTR hid a segment earning 73% of clicks at 12x the average rate; and `ctr_underperformer`, which must fire on a top-10 page with <2% CTR but NOT on a deep ranking, where low CTR is expected |
| `test_backlinks.py` | the controls for referrer classification: attack probes vs real links from the same host (`wordpress.org` → `/wp-login.php` vs → `/`), a second owned domain at any subdomain/port, bare-IP and cPanel-port referrer spam, hotlinked assets, and a structural check that **every `referrers` flag survives the `--remote` argv reconstruction** — a flag added to the parser and forgotten there is silently dropped, and only on remote runs |
| `test_bing.py` | the controls for the Bing page dimension: `GetPageStats` returning the page URL in a field literally named `Query`, per-date rows that only total after aggregation, click-sorting vs impression-sorting (which invert on real data), CTR on a zero-impression page, and an empty result that must name BOTH "no data yet" and "you typed the URL wrong" rather than implying the page is dead. Also the `urlinfo` decoder — .NET `DateTime.MinValue` (`/Date(-62135568000000-0800)/`) is "never", not year 0001 — and the refusal of `--days` on the four subcommands whose endpoints have no date range, with controls that `keyword`/`expand` still accept it |
| `test_sitegraph.py` | the controls for the link graph: the `<p class="silonav">` case that defeats tag-based boilerplate detection, self-referential hreflang counted as an inbound link, the island silo whose inlink counts look healthy, a `--start` that was never crawled reporting mass unreachability, a zero-edge graph refusing a verdict instead of calling every page an orphan, a global-nav hub being kept out of the orphan count while staying visible for review, section-scoped furniture (a locale nav on 100% of its locale and 1.5% of the site) being caught WITHOUT swallowing the island silo whose share is almost identical, and a canonical naming a URL the tree does not serve being told apart from one naming a different real page |
| `test_brief.py` | the controls for the assembler: that no readable page 1 REFUSES rather than emitting a brief with the competitive sections quietly missing, that a 2-page contract withholds its subtopics while keeping them visible, and that the cannibalisation metric discriminates in both directions - jaccard scored a real hit and a nonsense query BOTH at zero, and a 'clear' from a metric that cannot fire green-lights a duplicate page for a query we already own |
| `test_vitals.py` | the controls for the sweep: the commented-out `<img>`/`<script>` that a regex counts (the same bug found twice before in this skill), `media=print` and `defer`/`async` not being render-blocking, template grouping that is neither so coarse the site is one row nor so fine it degenerates into the per-URL run it replaces, and that a sweep reading NOTHING refuses rather than reporting a fast site |
| `test_controls.py` | the controls for the control audit itself: that it recognises BOTH invocation shapes, and - fired at a real fixture tree containing one controlled and one uncontrolled script - that an uncontrolled instrument makes the verdict not-ok and is NAMED rather than merely counted. An audit whose only failure mode is untestable is the instrument it exists to catch |
| `test_hreflang.py` / `test_contract.py` / `test_agentcheck.py` / `test_slop.py` / `test_crawllog.py` / `test_competitors.py` | the controls for the above: every rule is fired against synthetic input, so a clean pass on a real site means something. `test_crawllog` covers the UA-spoofing detector (including the control that a single operator's many crawlers are NOT flagged) and the no-input refusal. `test_competitors` fires every guarantee of the page-1 profiler against synthetic input - robots.txt obedience by the HTTP fetcher, injection-shape defanging, that every unread result is offered for browser escalation WITH its reason, and that platform chrome never becomes a 'subtopic page 1 covers' |

Also `assets/google-updates.json` — Google's published algorithm-update calendar,
every entry carrying a Google-owned source URL. Consumed by `decay.py --updates`
and `drift.py --updates`. **No API exists — it needs manual top-up**, and its
silence about a window is not evidence that nothing happened.

`--help` on any of them lists the subcommands. Common ones:

```bash
python3 $SEO/seostate.py suggestions --status approved --type guide   # the build queue, in order
python3 $SEO/seostate.py pacing                                       # can a guide ship today?
python3 $SEO/serp.py "keyword" --count 10 --target-domain example.com
python3 $SEO/keywords.py expand --seed "<facet>" --groups commercial comparison
python3 $SEO/keywords.py expand --seed "<facet>" --engines all --sort agreement  # 6 corpora
python3 $SEO/sameness.py check --draft new.md --corpus content/blog --keyword "kw" --pages .seo/pages.json
python3 $SEO/authority.py --domain example.com --save
python3 $SEO/authority.py --domain example.com --bulk rival1.com,rival2.com   # who actually outranks you
python3 $SEO/rankcheck.py --all --depth 20

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

# any-URL technical checks, incl. a COMPETITOR's change history (keyless)
python3 $SEO/pagecheck.py schema https://rival.example/page
python3 $SEO/pagecheck.py history https://rival.example/page --since 2026-05-01
python3 $SEO/pagecheck.py vitals https://oursite.example/page         # lab + real-user CWV

# a build brief for one query - assembled from measurements, refuses without page 1
python3 $SEO/brief.py build --query "how to bunny hop" --our-domain example.com \
    --corpus ./content
python3 $SEO/brief.py build --query "..." --contract-json profile.json  # reuse a run
# ⚠ Read `completeness` and `unavailable` before the brief itself. A missing section is a
# question that was not answered, NEVER a finding of "nothing there".
# ⚠ `differentiators` are WITHHELD below 4 readable page-1 results and the raw list is
# kept in `differentiators_withheld.raw_gaps` for review. Measured on "how to bunny hop in
# cs 1.6": 2 of 10 results were readable and the gaps came back `ratings, submission,
# score, favorite` - GameBanana and mods.vg platform furniture, because the UGC registry
# is a fixed domain list and mods.vg is not on it. Printed as differentiators that is an
# instruction to write a section about somebody's ratings widget.
# ⚠ The cannibalisation check reads the SLUG as well as headings and opening, and
# `matched_in` says which fired. A well-written page avoids its own keyword in H2s -
# the real bunny-hop.html leads with "what the engine is actually doing" and scored 0.0
# until the slug counted. It also uses CONTAINMENT, not jaccard: jaccard is symmetric, so
# a 4-token query against a 100-token page scores 0.04 and every query reads "clear".

# whole-site Core Web Vitals, sampled per template (keyless, no browser)
python3 $SEO/vitals.py sweep --sitemap https://example.com/sitemap.xml --per-template 4
python3 $SEO/vitals.py page https://example.com/some-page     # one URL
python3 $SEO/vitals.py origin https://example.com             # CrUX field data (credential)
# ⚠ READ `network_baseline` IN THE OUTPUT FIRST. It times a known-fast third-party host
# from THIS machine, because the first version of this tool timed a plain urlopen and
# reported 5,940ms TTFB for a server that answers in 119ms - the rest was the container's
# DNS (1,446ms) and TLS. It flagged `slow_ttfb`, severity high, "server/CDN work", and
# pointed at entirely the wrong system. connect_ms is now measured and EXCLUDED.
# ⚠ TTFB is sampled TWICE and the finding uses the FASTER run. One sample cannot tell a
# cold cache from a slow page: /leaderboard measured 12,065ms once and 70-94ms on three
# repeats (a 5-minute server cache the sweep happened to miss), while /servers/de_dust2
# measured 4,188 / 4,186 / 4,307ms - reproducible, and a real finding on 1,404 URLs.
# `ttfb_cold_ms` keeps the cold number, reported as its own `cold_cache_cost` finding.
# ⚠ A row carries `ttfb_spread` when its slowest sample fired the rule, because a median
# next to a `fail` reads as a tool bug. /servers/* came back [65, 66, 4071, 4157] - a
# bimodal template, which a median would have hidden either way.

# is every instrument still able to tell a finding from a reader bug?
# Run this BEFORE trusting any zero. 24 instruments, 301 checks, no network.
python3 $SEO/controls.py audit          # ok:false names the ones that cannot
python3 $SEO/controls.py audit --static # detect declarations only, run nothing
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
python3 $SEO/bing.py queries --limit 400 > bq.json  # real impressions AND real positions
python3 $SEO/bing.py pages --limit 40               # WHICH URL earns - sorted by CLICKS, not impressions
python3 $SEO/bing.py pagequeries --page 'https://example.com/zh/'   # attribution for one page

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
# patchy, measured: `cs 1.6 non steam` reports exact=2 while plainly larger queries report
# nothing at all. An unknown query is UNMEASURED.
python3 $SEO/keywords.py bing bq.json               # band them; READ by_script FIRST
# ⚠ Chinese/Japanese/Arabic seeds return NOTHING on the default us/en-US market.
#   That is the wrong question, not absent demand:
python3 $SEO/bing.py expand --seed "cs1.6网页版" --country cn --language zh-CN
python3 $SEO/bing.py keyword --q "<kw>" --days 90   # impressions, NOT Google volume
python3 $SEO/bing.py expand  --seed "<kw>" --limit 25
python3 $SEO/bing.py backlinks

# SERP-heavy run (research): start the daemon once, then one call for the lot
# NEVER append `&` - --start already detaches, and the `&` only kills the poller
# that tells you whether it came up. Run it in the foreground and read the JSON.
python3 $SEO/serpd.py --start
curl -s -X POST localhost:8791/batch -H 'Content-Type: application/json' \
  -d '{"queries":["kw one","kw two"],"depth":20}'      # compact verdicts

# measurement of what already exists
python3 $SEO/crawllog.py scan --remote root@<host> --ssh-key ~/.ssh/<k> \
  --glob '/var/log/caddy/access*.log*'                 # QUOTE the glob
python3 $SEO/crawllog.py verify --scan scan.json --bot googlebot
python3 $SEO/decay.py compare --previous prev.json --current cur.json --pages .seo/pages.json
python3 $SEO/drift.py snapshot --keywords-from .seo/keywords.json --out .seo/drift/$(date -u +%F).json
python3 $SEO/backlinks.py referrers --remote root@<host> --site example.com
python3 $SEO/backlinks.py footprint --domain example.com
python3 $SEO/sameness.py tiers --corpus public/seo/maps        # O(n) index-bloat

# the internal link graph - offline against a generated tree, no network at all
python3 $SEO/sitegraph.py crawl --root public/seo=/ --root public/legal=/ \
  --rewrite '/landers/=/' --out .seo/graph/site.json     # 3,981 pages in ~30s
python3 $SEO/sitegraph.py silos   --graph .seo/graph/site.json   # READ THIS FIRST
python3 $SEO/sitegraph.py inlinks --graph .seo/graph/site.json /guides/bunny-hop
python3 $SEO/sitegraph.py orphans --graph .seo/graph/site.json --contextual
python3 $SEO/sitegraph.py depth   --graph .seo/graph/site.json --start /maps
python3 $SEO/sitegraph.py broken  --graph .seo/graph/site.json --ignore '^/servers/'
python3 $SEO/sitegraph.py canonicals --graph .seo/graph/site.json   # canonical -> 404 = unindexable
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
python3 $SEO/agentcheck.py all https://example.com

# ⚠ Do NOT hand-verify a robots.txt with `urllib.robotparser`.read() behind a CDN.
# RobotFileParser turns a 401/403 into disallow_all, so a Cloudflare block of the
# default Python User-Agent reports EVERY path as forbidden - measured 2026-09-01
# on combatskirmish.net, where it said /maps/de_dust2 was blocked while that page
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
