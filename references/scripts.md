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
| `sameness.py` | the corpus sameness gate + a pairwise drift audit |
| `sitegraph.py` | **the internal link graph** — build it from a LOCAL generated tree (no network, works on an undeployed build) or by live BFS, then ask it for orphans, click depth, broken links and `silos`. That last one is the point: it ranks silos by EXTERNAL-silo inbound links and flags **islands** — a silo whose pages cross-link each other so every naive inlink count looks healthy while nothing outside points in. Counts `<link rel=alternate hreflang>` as a discovery edge, ignores self-loops, and detects site furniture by FREQUENCY rather than by tag, because a nav rendered as `<p class="silonav">` defeats a tag-based rule. `orphans --contextual` splits out pages that are THEMSELVES global-nav furniture into `nav_hub_urls` — a hub with 12,070 inbound links is the most reachable page on the site, and counting it as an orphan pads the number until the real orphan hides in the list |
| `authority.py` | DR-equivalent, and the KD zones / volume band that follow from it. Also `--bulk` competitor scoring, free referring-domain counts, a 12-month authority trend, and two independent popularity reads (Cloudflare Radar + **Tranco**, which is keyless and carries 40 days of rank history) |
| `bing.py` | **the only free source of real search volume AND backlinks** — Bing Webmaster Tools for a site you own. Impressions per query, related-keyword expansion with numbers, inbound links, query stats, and `pages`/`pagequeries` for the PAGE dimension — which URL actually earns the clicks, and for which searches. `queries` alone cannot tell a locale page ranking in its own language from the homepage ranking for that language, and those two have opposite fixes |
| `trendfeeds.py` | **keyless demand signals**: Google Trends' RSS feed (works where the 429-ing JSON API does not), Wikimedia pageviews as absolute topic demand, HN + StackExchange chatter, **GDELT's per-topic news-volume timeline**, and Google News (who is covering the topic now) |
| `rankcheck.py` | batch rank checks for every tracked keyword |
| `serpd.py` | **the fast path for SERP-heavy runs**: a long-lived headed Chrome behind `localhost:8791`. One curl per check, real Google, no DOM in your context. 25 checks in ~37s and 1.4KB of verdicts. |
| `indexnow.py` | get published pages crawled: IndexNow ping (free, keyless, Bing/Yandex) + the batched Google "Request indexing" follow-up |
| `test_guards.py` | regression tests for the SERP guards, against real captured responses |
| `test_providers.py` | regression tests for the free-provider integrations — chiefly that Open PageRank's `found: false` can never become a DR of 0 |
| `seodoctor.py` | **self-healing preflight** - idempotent check+repair of the daemon, its Chrome, deps and project state. Run it first, every run. `--providers` adds a live sweep of every data source |
| `providers.py` | **the provider registry**: every data source declared once, each with a live probe and its own control. `providers.py status` answers "what can I use right now?" by measuring, not by reading a table |
| `factcheck.py` | **information gain, sourced**: OpenAlex + Crossref papers with citation counts and DOIs, Wikidata entities, the Wikipedia neighbourhood of a topic, and a draft-vs-neighbourhood coverage gap |
| `pagecheck.py` | keyless technical checks for ANY url: W3C HTML validity, Google's own structured-data extractor, Wayback change history (yours or a competitor's), and Core Web Vitals |
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
| `test_bing.py` | the controls for the Bing page dimension: `GetPageStats` returning the page URL in a field literally named `Query`, per-date rows that only total after aggregation, click-sorting vs impression-sorting (which invert on real data), CTR on a zero-impression page, and an empty result that must name BOTH "no data yet" and "you typed the URL wrong" rather than implying the page is dead |
| `test_sitegraph.py` | the controls for the link graph: the `<p class="silonav">` case that defeats tag-based boilerplate detection, self-referential hreflang counted as an inbound link, the island silo whose inlink counts look healthy, a `--start` that was never crawled reporting mass unreachability, a zero-edge graph refusing a verdict instead of calling every page an orphan, a global-nav hub being kept out of the orphan count while staying visible for review, section-scoped furniture (a locale nav on 100% of its locale and 1.5% of the site) being caught WITHOUT swallowing the island silo whose share is almost identical, and a canonical naming a URL the tree does not serve being told apart from one naming a different real page |
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

# what data sources actually work right now (measured, not assumed)
python3 $SEO/providers.py status

# real numbers (Bing Webmaster - needs a verified property)
python3 $SEO/bing.py sites                          # auth control; run first if anything looks odd
python3 $SEO/bing.py traffic --days 90              # IS Bing bigger than Google here? check, do not assume
python3 $SEO/bing.py queries --limit 400 > bq.json  # real impressions AND real positions
python3 $SEO/bing.py pages --limit 40               # WHICH URL earns - sorted by CLICKS, not impressions
python3 $SEO/bing.py pagequeries --page 'https://example.com/zh/'   # attribution for one page
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

# does the draft read as machine-written (advisory, unlike the sameness gate)
python3 $SEO/slop.py scan draft.md
python3 $SEO/slop.py diff before.md after.md      # read `introduced`, not just `removed`
```
