# Prior art, and the roadmap that follows from it

Researched **2026-08-31** with `gh`, every repo inspected live (trees, manifests,
dependency lists, and source where it mattered). Numbers are from the GitHub API
that day, not from memory.

This file exists because the research is easy to lose and expensive to redo, and
because one finding in it decides how every future integration has to work.

---

## The finding that decides everything

**Every serious SEO library in this space carries dependencies incompatible with
this skill's design.**

| Project | Stars / forks | Created | Dependencies |
|---|---|---|---|
| `eliasdabbas/advertools` | 1,449 / 249 | 2017 | pandas, scrapy, pyarrow, twython |
| `sethblack/python-seo-analyzer` | 1,476 | — | langchain, lxml, bs4, trafilatura |
| `PhialsBasement/LibreCrawl` | 893 | — | Flask stack |

This skill is **stdlib only, zero installs** — verified by resolving every
non-stdlib import across all scripts to one of its own modules. That is not
incidental. It is why `seodoctor.py` can self-heal, why a run works on a fresh
container, and why there is no install step to go wrong at 3am.

So the integration mode splits by LAYER, and it is **not** a licensing question
(claude-seo, open-seo, geolook and LibreCrawl are all MIT):

- **Skill layer (markdown) → FUSE.** Markdown skills compose for free.
- **Script layer (Python) → CLEANROOM.** Not for licence reasons — vendoring
  scrapy+pandas would destroy the property that makes this deployable. Their
  value is proving *what* to build and *what shape the output should be*.

---

## The three siblings (real projects, not vanity repos)

Healthy fork ratios and live issue counts, checked rather than assumed.

- **`AgriciDaniel/claude-seo`** — 15,935★ / 2,331 forks, MIT, pushed 2026-08-26.
  31 sub-skills, 18 agents. Covers ground we do not: `seo-schema`, `seo-images`,
  `seo-local`, `seo-maps`, `seo-ecommerce`, `seo-sxo`, `seo-unlighthouse`,
  `seo-content-brief`, `seo-competitor-pages`, PDF/Excel reporting.
- **`every-app/open-seo`** — 15,664★ / 1,890 forks, MIT. "Open source alternative
  to Semrush and Ahrefs." Ships a `deslop` skill with
  `references/{phrases,structures,tropes}.md` — **the same lineage as our
  `slop.py` + `references/deslop.md`.** Also `keyword-clustering`,
  `competitor-analysis`, `competitive-landscape`, `link-prospecting`, `seo-coach`.
- **`aigclink/geolook`** — 644★, MIT. A full GEO pipeline (Chinese; covers both
  CN engines — GLM/Doubao/DeepSeek/Kimi/MiniMax/Baidu — and Western ones —
  Gemini/ChatGPT/Claude/Grok/Perplexity).

  Its thesis is the sharpest thing in this research and is quoted here because it
  reframes what GEO measurement even is:

  > GEO's endpoint is not ranking. It is whether the sentence in the AI answer is
  > phrased the way you framed it. So the minimal unit is not a page — it is an
  > **extractable fact block**.

  Its pipeline: crawl → audit → **AI answer sampling** → tickets → assets →
  report → **automated acceptance verification**. It also shares our
  no-fabrication discipline: anything not extractable from the site is marked
  "to be confirmed", never filled in from common sense.

Other repos worth knowing: `AminForou/mcp-gsc` (1,465★, GSC MCP),
`JustinBeckwith/linkinator` (1,255★, broken links), `crawlseo/crawlseo` (567★,
GSC + crawler + CWV), `StanGirard/seo-audits-toolkit` (815★),
`searchsolved/search-solved-public-seo` (412★, Lee Foot's clustering scripts),
`serpapi/awesome-seo-tools` (1,083★, discovery list).

---

## Where THIS skill is genuinely ahead

Checked, not assumed — worth knowing before copying anything.

- **UA-spoofing detection.** advertools' `reverse_dns_lookup` does bulk rDNS and
  has **no multi-operator spoofing detection**. `crawllog.py` caught two IPs on
  2026-08-31 forging **10 and 8 different companies** — 1,068 hits that would
  otherwise have inflated every AI-crawler figure on the report. Nothing else
  found in this research does that.
- **`seodoctor.py`** — self-healing preflight. No sibling has one.
- **`providers.py`** — control-based probing, where "cannot ask" and "the answer
  is no" are structurally different states.
- **`contract.py`** — post-deploy markup guard with an open/resolve lifecycle.
- **The fail-closed control discipline throughout.** It earned itself twice in a
  single run: it caught the `serpd` `google.com` artefact (which would have
  rejected every research candidate on a parser bug) and the fake "100% page-1
  churn" in drift (which was two extraction regimes being diffed, not volatility).

---

## Second pass, 2026-09-01 — what a re-survey was and was not worth

Re-run with `gh` the day after the survey above. **The sibling landscape had not
moved**: claude-seo 16,033★ (pushed 08-26), open-seo 16,048★ (08-24), geolook
648★ (08-10) — all unchanged since the teardown. A general re-survey at this
cadence yields nothing, and that is worth knowing so nobody repeats it weekly.

**What DID pay was searching for an API map rather than for a project.**
`merj/bing-webmaster-tools` (21★, a pydantic wrapper) is useless as a dependency
and excellent as documentation: it enumerates every Bing Webmaster endpoint with
its response model, including a whole service area this skill never called.
`isiahw1/mcp-server-bing-webmaster` covers the same ground as an MCP server.

That reframes what "integrating other projects" means here. The unusable half of
a project is its code; the usable half is its **map of somebody else's API**, and
that transfers cleanly into a stdlib caller. Read the models, write the client.

### ✅ #8 — The Bing CRAWLER surface — **BUILT 2026-09-01 (`bing.py`)**

`bing.py` asked Bing what SEARCHERS did and never asked what BINGBOT did, on a
site where Bing carries the traffic and "is the new silo being crawled at all"
was an open question answered only from access logs. Six subcommands now close
it — `crawlstats`, `crawlissues`, `feeds`, `blocked`, `crawlsettings`, and the
`quota`/`submit` pair that is the first crawl-acceleration lever in the skill.

Every endpoint was probed live before a line was written, and the probes
produced findings the moment they ran:

- **`GetCrawlStats` mixes daily counts and running totals in one row and labels
  neither.** Summing it gave `Code2xx: 96,000` for a site with 7,408 pages
  crawled and `InIndex: 82,767` for an index of 4,809 — the exact arithmetic
  error the quality bar already warns about, reproduced from scratch on a new
  source. The tool re-derives each column's kind from the series **every run**
  rather than trusting a hardcoded table, and reports a disagreement, because a
  remembered constraint nobody re-checks is how the `--days` bug shipped.
- **A SECOND "never" sentinel, and this one decodes to a real date.**
  `_dotnet_date` guarded `DateTime.MinValue` (`-62135568000000` → year 0001).
  `GetFeeds` returns `-11644473600000` — the Windows FILETIME epoch — for a
  sitemap Bing discovered itself, and it shipped in the first run as
  `"submitted": "1601-01-01"`. The guard is now a **sanity floor**, not a list
  of magic numbers: a third sentinel from a fourth epoch would slip past an
  enumeration, and no date before the web is a submission date.
- **`crawlissues` returned an empty list while `crawlstats` logged 91 crawl
  errors over the same window.** Those cannot both be complete, so the empty
  answer is `verdict: unknown` with the contradiction named — never "no crawl
  issues", which would be a finding about the site made from a gap in the
  instrument.
- **`feeds --verify` dated a deploy from the crawler's side**: live sitemap
  5,388 URLs, Bing holding 6,127 from a feed crawl on 2026-08-31.

`submit` is the only mutating call in the script and is a dry run until `--yes`.
It also carries a rule that is about the PROGRAM rather than the API: submitting
into a silo that is the subject of an open `remeasure.py` hypothesis is a second
intervention landing inside someone else's experiment, so it is an owner
decision, not an agent one.

### ✅ #9 — The unnamed-crawler bucket — **FIXED 2026-09-01 (`crawllog.py`)**

Not on any roadmap, found by using the tool rather than reading about it. The
`other-bot` bucket was the fifth-largest crawler row on the site — 1,442 hits
over seven days — and the field that exists to make it diagnosable was blinded
by its own truncation.

**The UA display key cut at 120 characters, and a bot token is appended at the
END of a spoofed browser string.** `YandexMobileBot` identifies itself at
character 155 of an otherwise ordinary iPhone Safari UA. So the largest unnamed
crawler on the site — 482 hits — was filed under a key that read as a mobile
visitor, with several distinct crawlers collapsed onto it. Widening the key to
keep both ends surfaced, in one pass: `YandexMobileBot` (482),
`YandexRenderResourcesBot` (106, which does not contain the substring
"yandexbot"), `Google-CloudVertexBot` (55), `GrokBot` (45), `YisouSpider` (60),
`Google-Read-Aloud` (20), `360Spider` (14), `coccocbot` (4).

Naming them moved **57% of the unknown bucket** into correctly categorised rows.

⚠ **This entry originally claimed one of them — "GrokBot, an answer engine the
GEO report had never counted" — as a discovery. It was not a discovery, it was
the scanner.** All 45 hits came from a single already-flagged address at a 100%
404 rate against `/.env.vault`, `/@fs/..%252f../.aws/credentials` and
`/proc/self/environ`. `Google-CloudVertexBot` (55), which this entry discusses
at length as a taxonomy decision, was 55/55 forged too. The spoof detector had
named both addresses in the same report; nothing subtracted them, so a warning
printed one screen above was read past. See #10 below — the correction, and the
structural fix that makes it impossible to repeat.

Three decisions in that fix are the reusable part:

- **Every new bot's rDNS list is EMPTY unless the operator documents a suffix.**
  A guessed suffix does not fail quietly — it reports every legitimate hit from
  that crawler as spoofed, a confident finding about someone else's
  infrastructure manufactured entirely by our own table.
- **`Google-CloudVertexBot` went to `ai_training`, the bucket that does NOT
  imply a citation**, precisely because its class is uncertain. A wrong guess
  must not inflate the `ai_search` number the program reads as AI visibility.
  Two new categories keep the same line: `user_fetch` (a person pressing Read
  Aloud is not an assistant citation) and `self`.
- **`self` exists because the instrument was in its own data.** `seo-manager/1.0`
  had 44 hits in the report it produced, counted as an unidentified bot.
- **The fix broke `test_agentcheck.py`, correctly.** Its fixture enumerated the
  `ai_search` roster by hand, so ADDING an answer engine read as
  "`ai_search_fully_blocked` does not fire". The fixture now derives the roster
  from `BOTS` and checks the rule rather than the snapshot — a taxonomy meant to
  grow must not have its growth reported as a regression.

**And the measurement that got there was itself broken twice**, both caught by
controls: a first sweep used `bc`, which is not installed on the central VPS, so
every count came back as a shell error reading as `0` — the binutils trap, on a
different binary; and a coarse `zgrep` over whole log lines "found" Yeti and
Sogou that a UA-field-scoped grep showed were not there at all. The reliable
method was neither: classify every distinct UA **through `classify_ua()` itself**
and read what the function could not name.

### ✅ #10 — The spoof detector found the forgery and nothing subtracted it — **FIXED 2026-09-01 (`crawllog.py`)**

Third pass, and the pattern from the second one held: **a general re-survey was
again worth nothing, and reading somebody else's API map was worth a lot.** The
sibling landscape had not moved — claude-seo 16,046★ and open-seo 16,097★ are
both still on the push dates recorded a day earlier, geolook unchanged at 648★
since 08-10. What paid was `gh search repos "yandex webmaster api"` /
`"seznam webmaster"` — MCP servers whose CODE is unusable here and whose
**endpoint enumeration is exactly the deliverable**, the same trick that produced
`bing.py` from `merj/bing-webmaster-tools`.

But the survey never got as far as building anything, because running the
existing instrument to justify the new one found the existing one lying.

**The defect.** `detect_ua_spoofing()` names the forging addresses, counts their
hits, and then prints, in prose: *"Treat every hit from these addresses as forged
and subtract it before reading any per-bot or per-category total."* Nothing
subtracted it. The per-bot rows in the same JSON were the contaminated ones, and
the warning sat one screen above them.

**A warning that has to be applied by hand is not a control**, and this file is
the proof: §9 above recorded GrokBot as an answer engine newly discovered on the
site, from a row that was 45/45 forged.

Measured over 7 days on combatskirmish.net, 1,485,860 log lines:

| category | claimed | real | forged |
|---|---|---|---|
| `ai_search` | 365 | **128** | 65% |
| `ai_user` | 293 | **112** | 62% |
| `social` | 179 | **15** | 92% |
| `search` | 6,296 | 5,929 | 6% |

Twelve bots were **entirely** forged — `GrokBot`, `Google-CloudVertexBot`,
`CCBot`, `Claude-User`, `Claude-SearchBot`, `ClaudeBot`, `Perplexity-User`,
`Google-Extended`, `meta-externalagent`, `TelegramBot`, `Slackbot`,
`LinkedInBot`. Two of those twelve are the ones §9 wrote up as taxonomy
findings. And one is a finding in its own right: **every Anthropic crawler row
is forged, so Claude's crawlers have not fetched this site at all** — which is a
better explanation for absent citations than anything in the content.

The two categories worst hit are precisely the ones the reading block calls the
GEO signal, exactly as `detect_ua_spoofing`'s own docstring predicted they would
be. It predicted it, printed it, and then published the inflated number anyway.

**The fix is structural, and deliberately keeps both numbers.** `hits` stays as
CLAIMED so no existing reading silently changes meaning; `hits_net` is what the
program reads; `forged_share` and `all_hits_forged` sit alongside, because "this
crawler visited less than claimed" and "this crawler never came" are different
findings and only the second invalidates a conclusion. Rows now sort by
`hits_net`, so a scanner cannot outrank real crawl demand. Categories net on
both sides of the division — a share of a contaminated whole is not a share.

Three things in the fix are the reusable part:

- **The subtraction reads the FULL flagged set, not the printed one.** The
  display list truncates at 25; subtracting only what is displayed would
  understate forgery on precisely the log that matters most, a farm rotating
  many addresses. Controlled with a 119-address fixture.
- **Controls in both directions, again.** A bot seen only from a flagged address
  must net to zero; a bot from clean addresses must be **untouched**. Without
  the second, a subtraction that shrank everything would pass.
- **The taxonomy control was rewritten to derive from `BOTS`** rather than list
  the Yandex agents by hand — the `test_agentcheck.py` lesson: a table meant to
  grow must not have its growth reported as a regression. Adding `YandexImages`,
  `YandexFavicons` and `YandexUserproxy` (all sitting unnamed in `other-bot`)
  would otherwise have broken it.

### ✅ #13 — Search Console was a sibling SKILL, and three instruments could not reach it — **BUILT 2026-09-01 (`gsc.py`, `postdeploy.py`)**

Not a survey finding. It came from the owner asking why the post-deploy step was
a per-project shell script plus a different skill, and the answer was that this
file had explicitly ruled the integration out: `references/data-sources.md`
carried a section headed *"Search Console — use the skill, not a new
integration"* ending **"Do not build a second GSC integration here."**

That was right about the second integration and wrong about where the first one
belonged. The cost had been visible for months without being named:

- **`decay.py`** — the instrument that finds the highest-return work on any site
  older than a few months — opened with *"INPUT: rows, from the `search-console`
  skill"* and made the operator export two JSON files by hand and remember which
  was which. That is exactly the shape that produced this skill's fake "100%
  page-1 churn".
- **`indexnow.py`** pinged four engines automatically and printed a manual
  checklist for Google — including the one Google step that IS automatable, the
  sitemap re-submit.
- The **post-deploy sequence** therefore could not run unattended at all.

`gsc.py`: `sites`, `sitemaps`, `sitemap-submit`, `inspect`, `query`,
`decay-export`. Still stdlib-only — the RS256 service-account JWT is signed in
pure Python (PKCS#8 → EMSA-PKCS1-v1_5 → `pow()`), so there is not even an
`openssl` binary to be missing, and the signer proves itself with an offline
sign/verify round-trip rather than failing as an opaque `invalid_grant`.

Three traps, each probed live before a line was written, each returning
confident nonsense rather than an error:

- **`contents[].indexed` is a stuck counter.** It reads `0` on a property with
  thousands of demonstrably indexed URLs. Reported as `null` with a note —
  "0 of 5,388 indexed" is the most alarming and most wrong thing the endpoint
  can say.
- **Every count is a STRING.** `"9" > "5388"` is `True`, so any unconverted
  threshold silently inverts. Controlled in both directions, including a check
  that the raw comparison really is wrong, so the coercion is not cargo cult.
- **The data lags 2-3 days.** A window ending today comes back thin, and that is
  the lag, not a collapse. A window entirely inside it is REFUSED with the last
  complete day named, rather than answered with zero rows.

`postdeploy.py` is the sequence: health gate → contract → IndexNow → Google,
dry-run until `--yes`. Two things it taught while being built:

- ⚠ **Retiring the project's own IndexNow client would have been a silent
  regression, and the skill's tests would all still have passed.** The local
  script submitted from `public/sitemap.xml`; the skill's `ping --pending` reads
  the CONTENT QUEUE, which on a generated site is empty by construction. Measured
  on the 5,388-URL silo: `--pending` submitted **0 and reported `ok`**. Caught by
  running it before deleting anything. `indexnow.py` now takes `--sitemap`
  (follows an index one level, dedupes, and treats an empty or unreadable feed as
  an ERROR — an empty list would ping nothing and call it success), and
  `postdeploy.py` uses that source. Parity confirmed at 5,388 URLs.
- **The receipt exists because of a question with no answer.** "Was IndexNow
  pinged for that deploy?" was unanswerable: Google records `lastSubmitted` so
  its side was provable, Bing's URL-submission quota is a different channel whose
  own tool says so, and IndexNow returns 200 and keeps no queryable history. An
  action with no receipt cannot be audited, so every run appends to
  `.seo/postdeploy.jsonl`.

**The rule that survives, and is now written where the old one was:** do not
build a SECOND Search Console client. `keywords.py gsc`, `decay.py`,
`authority.py` and `rankcheck.py` all read what `gsc.py` emits, and it
deliberately emits Google's OWN row shape so a helpfully-renamed field here
cannot break them.

### #11 — Yandex Webmaster API v4 — **MAPPED, DEFERRED BY THE OWNER 2026-09-01**

The measurement that came out of #10 is the argument for it. Net of forgery, over
7 days:

| engine | real hits | distinct URLs |
|---|---|---|
| **Yandex** (5 agents) | **1,995** | 1,409 |
| Baidu | 1,318 | 1,192 |
| Bing | 1,596 | 884 |
| **Google** | **395** | **313** |

Yandex is the largest search crawler on this site and Googlebot is the smallest
of the four, on a 3,568-page silo. The program measures Google (GSC) and Bing
(`bing.py`) and has **never once queried the engine that crawls it most**.

`weselow/Yandex-webmaster-mcp-server` (MIT, TypeScript, unusable as a dependency)
enumerates the whole v4 surface, base `https://api.webmaster.yandex.net/v4`:

- `/hosts`, `/hosts/{id}/summary`, `/owner-verification`, `/diagnostics`
- `/indexing/history`, `/indexing/samples`
- `/search-urls/in-search/{samples,history}`, `/search-urls/events/{samples,history}`
  — appearance and **exclusion events with reasons**, which nothing else here has
- `/search-queries/{all/history,popular}`, `/query-analytics/list`
- `/links/external/samples`, `/links/internal/broken/samples` — free backlink data
- `/sqi-history`, `/important-urls`, `/original-texts`
- **`/recrawl/quota` and `/recrawl/queue`** — a direct, quota-metered recrawl
  request. Google has no such lever for ordinary pages, Bing's arrived with
  `bing.py submit`, and IndexNow is fire-and-forget with no feedback at all.

`PavelUngr/seznam-webmaster-mcp` maps a much smaller Czech equivalent (index
counts, per-document detail, `reindex_url` at 500/day, plain API-key auth).
SeznamBot is 77 real hits here — cheap, and worth it only after Yandex.

**Blocked on an owner action, not on design**: an OAuth token from
`oauth.yandex.ru` with `webmaster:hostinfo` + `webmaster:verify`. Everything
after that automates, verification included — `/owner-verification` returns a DNS
TXT value and this project already drives Cloudflare DNS through the API.

⚠ Do not build it before the token exists. A tool whose only reachable state is
`no_key` is the stub #2 warned about, and this API cannot be probed live without
one — and `bing.py` was good *because* every endpoint was probed before a line
was written.

**Owner's call, 2026-09-01: deferred**, with the Google side taken first on the
grounds that Googlebot fetching 313 distinct URLs a week out of 3,568 is its own
problem. Baidu (`Baiduspider`, 1,318 real hits and 290 MB/7d — the heaviest
search crawler here by bandwidth, also unmeasured) deferred with it; its Ziyuan
push API needs a verified site in Baidu's webmaster platform and the payoff for a
`.net` with no Chinese hosting is genuinely uncertain. Both stay here with their
measured numbers so the case does not have to be rebuilt.

### ✅ #12 — Crawl budget, measured per engine — **the Google side, 2026-09-01**

Taken instead of #11. `crawllog.py urls` + `gap` against the live sitemap, over
the whole retained log window:

| engine | sitemap URLs crawled | coverage of 5,388 | never crawled |
|---|---|---|---|
| YandexBot | 2,672 | **49.6%** | 2,716 |
| Googlebot | 2,390 | **44.4%** | 2,998 |
| bingbot | 792 | **14.7%** | 4,596 |

Bing is last by coverage and first by traffic. The reason was one row of `gap`'s
output — and reading it correctly took three attempts, each of which would have
shipped a different wrong fix:

1. **`/play 506` on bingbot, not in the sitemap** → "a bare 302 is eating 25% of
   Bing's budget; block it." Wrong.
2. `urls --keep-query`: **503 of the 506 are `/play?connect=` across 408 distinct
   URLs**, and `/play?connect=` is *already* `Disallow`ed → "the rule is being
   ignored; go serve-side." Also wrong.
3. `grep -c` over every release directory on the box: **the rule reached
   production on 2026-09-01, the same day**. Every earlier release has zero
   occurrences. So the 440 August and 61 early-September hits are all *pre-fix*,
   and the 30% figure is the problem the fix was written for, not evidence
   against it.

Nothing is concluded from that yet. It is registered instead:
`remeasure.py record --id bing-play-connect-block`, baseline **502** `/play`-silo
hits in the 14 days to 2026-09-01, expect `decrease` by ≥400, `not_before
2026-09-22` — with the falsification written down in advance ("if it does not
drop, the rule is not being honoured and the next step is a serve-side block,
not another robots.txt edit").

**Two tool defects fell out of the three attempts, and both are the same shape:**
an output that cannot distinguish two states with opposite fixes.

- **`urls` strips the query, so `gap` collapses every parameterised variant onto
  one path.** Correct for the comparison — sitemap `<loc>` entries carry no
  query — and wrong the instant a reader treats the count as a budget figure.
  Fixed with `--keep-query` (off by default, forwarded over `--remote`) and a
  note in `gap`'s own output pointing at it.
- **`--bot` makes the spoof detector blind, and blind read as clean.** The signal
  is one address claiming several operators; filter to one operator and nothing
  can ever be flagged. Unfiltered, bingbot was 1,655 claimed / 1,596 net; the
  same window with `--bot bingbot` said 1,658 / 1,658. A `--bot` run now returns
  `null` for every net field plus `spoof_subtraction_available: false` — the
  `no_key` rule, applied to an instrument instead of a credential.

**And one in `remeasure.py`, found by trying to use it**: `--metric` refused
`bots.bingbot.top_silos./play` with "no index 'bingbot'", because `bots` is a
list and only positional indices were implemented — while the script's own
`--help` had documented the identity form since it was written. Worse than a
missing feature: the workaround, `bots.0`, is a positional index into a list
**sorted by value**, so it silently re-points itself between runs and the verdict
compares two different bots. Now resolved by identity (`key`/`id`/`name`/`bot`/
`slug`), with a name matching zero or several elements refused rather than
guessed — and refused even under `--missing-is-zero`, which is for a sparse map,
not for a row that does not exist.


## Third pass, 2026-09-20 — the seomachine teardown, and a landscape that HAD moved

Re-run with `gh` nineteen days after the second pass, prompted by the owner
asking for `TheCraigHewitt/seomachine` specifically. Every repo below was
inspected live (trees, manifests, source where it mattered); star counts are the
API's that day. Two of the three prior lessons held, and the third did not:

- **The sibling landscape moved this time.** claude-seo 16,046 → 17,258★ (v2.3.1,
  pushed 09-11), open-seo 16,097 → **19,560★** (pushed 09-20 — a `deslop` skill
  with the same lineage as ours, MCP cleanup tools, a rewritten audit skill),
  geolook 648 → 715★ (still nothing pushed since 08-10). A nineteen-day cadence
  finds movement where a one-day one found none; weekly would still be waste.
- **Reading somebody else's API map was again worth more than reading their
  code** — three maps this pass, one of them for a credential this skill
  already holds (below).
- **The survey missed a whole tier last time.** `coreyhaines31/marketingskills`
  — **50,951★ / 7,715 forks**, MIT, the parent of seomachine's 26 vendored
  skills — was never in this file. Neither were `zubair-trabzada/geo-seo-claude`
  (10,728★), `TheCraigHewitt/seomachine` (7,452★), `nowork-studio/notfair-plugin`
  (3,828★), `yaojingang/GEOFlow` (3,664★, **AGPL**, PHP), `AgriciDaniel/claude-blog`
  (2,202★), `Auriti-Labs/geo-optimizer-skill` (849★), `elmohq/elmo` (341★),
  `dannwaneri/seo-agent` (50★). The 08-31 search terms were "seo"; the ecosystem
  had reorganised around "GEO"/"AEO"/"AI SEO" and a search on the old term does
  not surface it.

### seomachine, torn down

**What it is.** A Claude Code *workspace* for long-form blog production: 24
slash commands (`/research`, `/write`, `/optimize`, `/cluster`, `/repurpose`,
`/research-ai-citations`, five `/landing-*`), 11 agents, the 26 marketingskills
skills vendored under `.claude/skills/`, 23 Python modules under
`data_sources/modules/`, a WordPress+Yoast REST publisher, and a `context/`
directory of brand/voice/keyword templates that every command reads. Originally
Castos's internal tool; `examples/castos/` is the filled-in reference.

**Dependencies.** `data_sources/requirements.txt`: scikit-learn, nltk, textstat,
beautifulsoup4, google-analytics-data, google-api-python-client, the DataForSEO
client. Same verdict as every project in §"The finding that decides everything":
the script layer is cleanroom by necessity. Nothing changes there.

**Where it is behind this skill, specifically.**

- **A composite score at every step**, and the scores absorb missing data.
  `opportunity_scorer.py` is an 8-factor weighted 0–100 (volume 25%, position
  20%, intent 20%, competition 15%, cluster 10%, CTR/freshness/trend 5% each)
  and `scores['cluster_score'] = cluster_value or 50` — a missing input becomes
  a mid-value and the total is reported without a mark. That is the substitution
  the Non-negotiables forbid, done inside the arithmetic where a reader cannot
  see it. `seo_quality_rater.py`, `content_scorer.py`, `landing_page_scorer.py`
  and the `/optimize` "SEO score (0–100)" are the same shape. `slop.py` emits no
  score on purpose; this is the counter-example.
- **`/research-ai-citations` is a template, not an instrument.** Step 3 says to
  run 10–15 prompts through ChatGPT/Perplexity *by hand*, and: "If AI tools are
  not available for live testing, note this in the output and proceed with
  steps 1-2 plus the audit template." Cannot-ask and not-cited are one path, on
  paper. Nothing is fail-closed anywhere in the repo; no controls exist.
- **Volume is DataForSEO or nothing.** No free ladder, no Bing Webmaster, no
  Search Console → candidates seam.
- **`content_length_comparator.py` reads page 1 for word counts only** —
  `competitors.py` here reads depth, headings, thin/UGC/stale, subtopics, and
  refuses to call a bot-challenge "weak".

**Where it carries something this skill does not.** Four things, all in the
markdown layer or trivially stdlib:

1. **A question-bank taxonomy for answer engines** (`/research-ai-citations`
   step 1): six prompt classes — direct recommendation, comparison,
   feature-specific, use-case, pricing/value, migration/switching — crossed with
   audience, platform and intent modifiers, then clustered. `workflow-geo-scan.md`
   step 1 says "convert keywords into the questions a real customer would ask";
   this is the generator that sentence was missing.
2. **The action half of GEO.** `context/ai-citation-targets.md` (five tiers of
   citation surfaces with a "Listed?" column), `context/reddit-strategy.md`
   (comment > post, F5Bot monitoring, three comment shapes), and `/repurpose`
   (one article → LinkedIn Article, Medium, 2–3 Reddit comment drafts, a Quora
   answer, each linking back). `geo-scan` measures `gap_domains` and stops;
   nothing here turns a gap domain into a prospect.
3. **Invisible Unicode.** `content_scrubber.py` strips U+200B/200C/200D/FEFF and
   every category-`Cf` code point. `slop.py` has 20 prose rules and no
   character-level one, and an invisible character is the one tell that is
   mechanical *and* a defect (it splits words for tokenisers and search).
4. **`/cluster`** plans a pillar + 8–12 supporting pages with a link matrix and
   a build order. `sitegraph.py` measures the graph after the fact; nothing here
   plans one before it.

The WordPress publisher is not applicable — this skill ships PRs.

### `marketingskills/ai-seo` 2.5.0 — the reference worth reading in full

Pushed 2026-09-05, and unlike most of this space its claims carry sources and
dates. Five things transfer; two must not.

- **The visibility LADDER**: *retrieved → cited → mentioned → recommended*, plus
  a shadow rung, *recommended-against*. Each is governed by a different system
  and only the last changes buying behaviour. Lily Ray's 100-query B2B study
  (spring 2026): self-promotional "best [category]" listicles earned 323 AI
  Overview citations, and in **224 of them (69%) the answer recommended a
  competitor instead** — the publisher's own research handed the model the
  competitor list. `geo.py` measures the *cited* rung and surfaces
  `sentences_naming_us` for the *mentioned* one; it does not label the framing.
- **Format volatility.** ChatGPT 5.6 (Aug 2026, Peec AI data via Tomek Rudzki):
  listicle citations **−50.5%**, comparison-page citations **−32.1%**, with a
  surge in `site:` and "official" fan-out queries — retrieval moved toward
  primary sources. Measured on ChatGPT only; Gemini ~60% owned-site citations.
  This is the `catalogue` deferral class, confirmed from the other side: a
  list query is not a page this site should write, on ANY engine.
- **AI answers are non-deterministic.** Run each prompt 3–5 times per engine,
  report the *rate with its n* ("cited 3/5"), compare rates over time. `geo.py
  ask` runs once and caches the answer for the cache TTL, so today it reports a
  coin-flip as a state.
- **Split the cause before the fix**: *technical* (cannot be crawled or parsed),
  *comprehension* (described wrongly), *trust* (understood, not selected).
  `workflow-geo-scan.md` §0 already does the first split; the other two have no
  home.
- **Markdown content negotiation and RFC 8288 `Link` headers.** Serve Markdown
  at the same canonical URL on `Accept: text/markdown` (Cloudflare "Markdown for
  Agents" does it at the edge — measured live on `www.cloudflare.com` and
  `developers.cloudflare.com` by geo-seo-claude's PR draft), and advertise
  parallel resources in a `Link:` header. `dodopayments/dualmark` (105★) is a
  whole project for the first half. `agentcheck.py page` checks
  `<link rel="alternate" type="text/markdown">` and probes `<url>.md`; it never
  sends the `Accept` header and never reads `Link:`.

Two things in the same file to keep OUT: it lists `ClaudeBot` and `anthropic-ai`
as crawlers to allow "so Claude can cite you" (both are `ai_training`; the citing
crawler is `Claude-SearchBot` — §1 of `agent-readiness.md`), and it recommends
publishing `llms.txt` as an action. The evidence table in `agent-readiness.md` §2
stands; a 50k-star repo repeating the myth does not change the log study.

### Three API maps, one of them for a key this skill already holds

**SerpApi `engine=google_ai_mode` — PROBED LIVE 2026-09-20, with `~/.serpapi_key`.**
HTTP 200, `search_metadata.status: Success`, and unlike the AI Overview it is
**single-stage**: `text_blocks` (6), `references` (5) and a
`reconstructed_markdown` all arrive on the first response. For "how to play
counter strike 1.6 in browser": Instagram, **play-cs.com ×2**, VPN4Games, DOS
Zone — combatskirmish.net absent, the same finding as the AI Overview on
2026-09-01. Google AI Mode is a second answer surface, on the same key, and
`geo.py` could not ask it. That is the #2 lesson repeated verbatim: "we lack the
credential" was a remembered constraint that nobody re-checked against the
credential in hand. (Plan: `free`, 240 of 250 searches left this month.)

**SearchApi.io** (a different company from SerpApi — `dannwaneri/seo-agent`
keeps the two clients apart for that reason): `GET /api/v1/search?engine=chatgpt|
gemini|perplexity|bing_copilot&q=` returns `markdown` + `reference_links[]`
(`chatgpt` needs `web_search=true` or it never cites). One key, four answer
engines, in the shape `geo.py`'s `_llm_engine` already parses. Unprobed — no key
here — so it is wired as `no_key`, which is the correct reachable state and not
a stub, exactly as `perplexity`/`openai` are today.

**Published crawler IP ranges — one JSON shape across five operators.** Every AI
crawler in `BOTS` has an EMPTY rDNS list by rule (§9: a guessed suffix reports
every legitimate hit as spoofed), so `crawllog.py verify` can say nothing about
the crawlers the GEO report is about, and §10's "every Anthropic row is forged"
was inferred from the multi-operator heuristic rather than checked. Probed
2026-09-20, all `{"creationTime": ..., "prefixes": [{"ipv4Prefix"|"ipv6Prefix"}]}`:

| operator | file | note |
|---|---|---|
| OpenAI | `openai.com/gptbot.json`, `searchbot.json`, `chatgpt-user.json` | three files, one per class — the taxonomy, published |
| Anthropic | `claude.com/crawling/bots.json` | `creationTime 2026-08-18`; linked from the support article, not from any docs index |
| Perplexity | `perplexity.ai/perplexitybot.json`, `perplexity-user.json` | `creationTime 2025-02-07` |
| Google | `developers.google.com/static/crawling/ipranges/{common-crawlers,special-crawlers,user-triggered-fetchers,user-triggered-fetchers-google}.json` | the old `/search/apis/ipranges/` path 301s here — a remembered URL would have read as "Google removed the list" |
| Bing | `bing.com/toolbox/bingbot.json` | `creationTime 2024-01-03` |

`ipaddress` is stdlib. This is the verification path for exactly the rows whose
rDNS list must stay empty — and it distinguishes "forged" from "unverifiable"
per address rather than per operator-count.

**`ai-robots-txt/ai.robots.txt`** — the community UA list, 175 entries against
our 79; **135 are unknown to `BOTS`**. Most are scrapers that belong in
`ai_training` by the §9 rule (a wrong guess must not inflate `ai_search`), but
several are documented answer-engine indexers and matter for the GEO reading:
`Bravebot` (Brave Search is Claude's search backend — being in that index is a
precondition for a Claude citation, and it is not in the table),
`meta-webindexer`, `MistralAI-Index`, `Kimi-SearchBot`/`Kimi-User`,
`Amzn-SearchBot`/`Amzn-User`, `ExaSearchBot`, `Andibot`, `PhindBot`,
`GoogleAgent-Mariner`, `Gemini-Deep-Research`, `Google-NotebookLM`,
`ChatGPT Agent`/`Operator`, `kagi-fetcher`, `DeepSeekBot`, `QwenBot`/`TongyiBot`,
`ERNIEBot`/`YiyanBot`, `DoubaoBot`, `Claude-Web` (undocumented — Anthropic's
page does not list it).

**Smaller maps.** `FlorianBruniaux/google-search-console-mcp` (61 tools) is a
list of GSC-derived analyses, and every one is already here except
`ai_overviews_impact` — CTR-at-position on queries with an AI Overview against
those without, which needs `drift.py`'s `ai_overview.present` joined to
`gsc.py query` rows. `claude-seo` (v2.3.1) carries four scripts worth knowing
and not porting: `keyword_planner.py` (Google Ads Keyword Planner — real Google
volume, but a developer token and a manager account, and **bucketed ranges
without ad spend**), `lcp_subparts.py` (CrUX has exposed LCP's four sub-metrics
— TTFB, resource load delay, load duration, render delay — since January 2025;
`vitals.py origin` reads CrUX and does not decompose LCP), `commoncrawl_graph.py`
(host-level PageRank + harmonic centrality from the quarterly web graph,
keyless — a third independent authority read after Open PageRank and Tranco,
at the price of a multi-GB download), and `content_verify.py` (claims in a
draft — `47% of`, `$3.2 billion`, `according to a Stanford study` — with no
citation marker within 200 characters). `goenning/google-indexing-script`
(7,706★) is the Indexing API, which Google restricts to `JobPosting` and
`BroadcastEvent` pages; it is not a lever for ordinary URLs and abuse revokes
access, so `indexnow.py`'s "the Google half is a human clicking a button"
stays true.

**open-seo `deslop` vs `slop.py`.** Its `structures.md`/`tropes.md` catalog and
our 20 rules overlap almost entirely; three regex-able patterns are missing
here: **anaphora** (three or more consecutive sentences opening on the same
words), **false ranges** ("from X to Y to Z" where nothing lies between), and
**invented concept labels** ("the supervision paradox", "the acceleration trap"
— an abstract problem-noun bolted to a domain word and used as if defined).

### Ranked, from this pass — ✅ ALL BUILT 2026-09-20

Everything below is stdlib and keyless unless it says otherwise; nothing needs
an install. Ordered by how much of the program's current reading it changes.
Built the same day, in this order; `controls.py audit` reads 32 of 32
instruments, 623 checks, and `run_tests.py` 19 of 19 suites. What each one
found on contact with live data is under its entry.

- **✅ #14 — `geo.py`: the surfaces it could already reach.** Live: AI Mode
  answered the site's core query with play-cs.com ×2, dos.zone, Instagram,
  VPN4Games — combatskirmish.net absent, the AI Overview finding repeated on a
  second surface. `--runs` forced a rewrite of the sweep arithmetic (a run is
  an answer, so share-of-voice stays ≤ 1.0 by construction), and `seostate.py
  record-ai` / `ai-visibility` now store `runs`/`cited_runs`/`mentioned` and
  compute the rate over answers that existed — the old `cited / queries`
  counted questions with no answer surface in the denominator, the exact
  error `geo.py` had already fixed one layer down. `google_ai_mode`
  engine on the existing SerpApi key (measured working); a `searchapi` provider
  for `chatgpt`/`gemini`/`perplexity`/`bing_copilot` (`no_key` until one exists);
  `--runs N` sampling with the citation *rate and n* reported, cache bypassed
  for repeat runs; ladder fields — `cited`, `mentioned` (from
  `sentences_naming_us`), and the verbatim framing sentence left for a human,
  never auto-labelled "recommended". Controls: a rate with n=1 is reported as a
  single observation, not a rate; a run set where every attempt was `cannot_ask`
  refuses.
- **✅ #15 — `crawllog.py verify` by published CIDR.** All eleven files read
  live (2,488 prefixes). Googlebot `66.249.66.1`: both witnesses agree. A
  known Anthropic address verifies, `8.8.8.8` claiming ClaudeBot is spoofed,
  and the combination is a pure function with its own six-way control. Fetch the operator files
  above (cached with their `creationTime`), match with `ipaddress`, and give
  every AI-crawler row a direct `verified`/`spoofed`/`unverifiable` per address
  instead of the operator-count inference. Controls in both directions: an
  address inside a published prefix verifies, `8.8.8.8` claiming GPTBot does
  not, and an operator with no published list is `unverifiable`, never
  `spoofed`. The `.googlebot.com` rDNS path stays for Google and Bing; CIDR is
  a second witness there, not a replacement.
- **✅ #16 — `BOTS`: the documented answer-engine crawlers.** 79 → 107 rows.
  A new structural control derives from the table: no earlier key may be a
  substring of a later one, because `classify_ua` takes the first match and a
  shadowed row is unreachable forever — nothing had ever checked that. Add the named
  indexers and fetchers above with the class the operator documents, rDNS
  lists empty, and `Bravebot` as `search` with a note that it is the Claude
  index. Everything undocumented from the community list goes to
  `ai_training`, which is the bucket that does not imply a citation. The
  taxonomy control derives from `BOTS`, so growth cannot read as regression.
- **✅ #17 — `agentcheck.py`: three signals it did not send or read.** The
  known-positive control is live: `developers.cloudflare.com` answers
  `Accept: text/markdown` with `text/markdown; charset=utf-8` and `Vary:
  Accept`, and carries `</api/>; rel="service-doc"`.
  `Accept: text/markdown` on the page URL (report `Content-Type`, and `Vary`);
  the response `Link:` header parsed per RFC 8288; and in `policy`, the
  `Content-Signal:` robots.txt directive (Cloudflare's `search`/`ai-input`/
  `ai-train` content-usage declaration) reported as stated policy. All three
  are informational — absence is never a finding, same as WebMCP.
- **✅ #18 — `slop.py`: four rules.** 24 in the catalog. The structural two
  needed their own firing path; the invisible-character rule is `high` because
  it is a defect, and its exclusions are by context (script, neighbours), so a
  format character the rule has never met still fires. `invisible_unicode` (category `Cf`,
  located by code point, with the emoji-ZWJ and Persian/Indic ZWNJ cases
  excluded by script so the rule cannot fire on legitimate text),
  `anaphora`, `false_range`, `invented_label`. Each with its own control in both
  directions, as `binary_contrast` has.
- **✅ #19 — `factcheck.py claims`.** The citation-proximity rule was wrong
  twice while its control was being written: centre-distance attached a
  paragraph's second claim to the first claim's source, and the fallback then
  borrowed the previous sentence's link — both turning an *uncited* claim into
  a `not_in_source` against the wrong page. Now: the nearest link AFTER the
  claim in the same sentence (or an immediate footnote), else a link before it
  in the same sentence, else uncited. Live on a deliberately mis-cited draft:
  `0.1%` cited to Google's AI guide came back `not_in_source` from 24,866
  characters of read page, which is the finding the instrument exists for. The instrument the Non-negotiable "a source
  you cannot cite, you have not verified" has never had: extract the numeric
  and authority claims from a DRAFT, report every one without a citation marker
  nearby, and with `--fetch` open each cited URL and check the claimed number
  actually appears in the source text. A fetch that fails is `unverified`,
  never `false`; a number absent from a page that was read is the finding.
  Control: a known-true claim against a fixture page passes, a fabricated one
  fails, and an unreachable source refuses.
- **✅ #20 — `workflow-geo-scan.md`.** The six-class question-bank generator in
  step 1; rates-with-n in steps 2–3 and 5; the ladder in the report (cited /
  mentioned / framing verbatim); the technical–comprehension–trust split before
  any content is proposed; and a **presence step** that reads `gap_domains`
  and files the recurring ones as `seostate.py prospect-add` rows — the action
  half seomachine has and this skill measures without acting on. Repurposing
  copy (LinkedIn/Medium/Reddit) stays out: it is owner-voice work and the
  quality bar's security rule keeps agents off third-party posting surfaces.
- **✅ #21 — `vitals.py origin --subparts`.** Built and then found
  `no_key` on this install: the CrUX API answers the service-account bearer
  PSI accepts with **HTTP 400 "invalid argument"** on a correct request, and
  only an unauthenticated call says "use an API key". The 400 is the trap —
  it reads as a bad request and is a missing credential — so the script
  reports `no_key` with the measured reason instead of letting the status
  through. `data-sources.md`'s "the CrUX API is moot" was half right and is
  corrected. The four LCP sub-metrics from CrUX,
  so "LCP 4.2s" becomes "TTFB 1.1s, render delay 2.4s". Needs the PSI
  credential `origin` already needs; reports `unavailable` when CrUX has no
  record, as it does now.
- **✅ #22 — `references/data-sources.md`.** Entries for SearchApi.io, SerpApi AI
  Mode, the five IP-range files, Keyword Planner (mapped, not built: bucketed
  without spend), the Common Crawl web graph (mapped, not built: the download),
  and the Indexing API (not a lever; say why).
- **✅ #23 — `quality-bar.md` / `workflow-build-guide.md`.** Plus a CLAIMS
  CHECK step in build-guide §9, before the sameness gate.
  The ChatGPT 5.6 format shift belongs next to the `catalogue` class and next
  to the archetype rotation: a listicle archetype is a human-conversion shape,
  not a citation play, and the report may not claim otherwise.

Not taken, with the reason: `/cluster`-style pillar planning (the research
ladder already works one FACET at a time and `sitegraph.py silos` measures the
result; a planning layer on top would be a second queue), Common Crawl web
graph (a multi-GB quarterly download for a third authority read that Open
PageRank and Tranco already triangulate), Keyword Planner (a manager account
and a developer token for bucketed ranges), and every composite score.


---

## Ranked gaps

### ✅ #0 — A shared control primitive — **BUILT 2026-09-01 (`controls.py`)**

Not on the original list, and it is the one the research itself argued for
without naming. The teardown's own closing claim was "the fail-closed control
discipline throughout" — measured, it was **five scripts of twenty**. The other
fifteen could return a zero that nothing in the code could distinguish from a
broken reader, including `slop.py`, which had failed exactly that way (44 of 44
pages `warn`) the same week.

Seven instruments failed their controls in one run on 2026-09-01. Every one was
caught by a human noticing; nothing in the code required a control to exist.

`controls.py` makes it structural: `Controls` / `refuse()` / `guard_zero()` /
`uniform_verdict()`, plus `controls.py audit`, which runs every instrument's own
control and reports `ok: false` naming any that cannot prove itself. **29 of 29
instruments, 436 checks, no network, 0 broken.**

Three findings came out of the retrofit itself, which is the argument for it:

- **`backlinks.py` counted a hotlinked root asset as a backlink.** `ASSET_PREFIXES`
  is site-tuned and had no rule for `/favicon.ico` or any asset by extension, so
  a hotlink was classified `genuine` — an overcount of the single number that
  instrument exists to produce. Fixed with a deliberately narrow suffix list
  (an extension list that grew to cover `.html` would DISCARD real backlinks,
  the costlier direction) plus its own controls in both directions.
- **`bing.py`'s `--days` refusal set was a local inside `main()`.** A control
  checking a copy of a literal proves nothing, so it was hoisted to module
  scope and the control now reads the real constant.
- **`rankcheck.py`'s domain matcher was inline in `main()`** and therefore
  unprovable. Extracted to `position_of()` and controlled: a lookalike domain
  must not match, a subdomain must, and absent must be `None` rather than 0.

⚠ **Three of the controls written in that pass were themselves wrong**, and each
had to be corrected against the code rather than the other way round: two
asserted a value copied out of the implementation's own docstring (a control
that agrees with the code by construction), and one put an "orphan" robots.txt
directive where it was a legitimate group continuation. Derive the expected
value independently, or the control is a mirror.



### ✅ #1 — Site crawler with a link graph — **BUILT 2026-08-31 (`sitegraph.py`)**

The one that was proven by failure rather than argued for. Finding "the guides
have one inlink each" took a `grep -rl` over 3,977 files that ran for minutes and
hit ENOMEM; the link graph does 3,981 pages / 240,971 edges in 30 seconds, and
surfaces it in the DEFAULT report without anyone thinking to ask.

Prior art: LibreCrawl (`link_manager.py`, `issue_detector.py`, `seo_extractor.py`),
advertools `crawlytics.links()`, linkinator.

**What we do that none of them do: offline mode.** It walks a LOCAL generated
tree, so it runs on a build that has not shipped — catching the problem before
deploy rather than after. See `references/scripts.md` and `workflow-health.md`.

**Follow-up, 2026-09-01.** First real re-run after the guides fix landed confirmed
it (contextual median 1 → 85, 16 entry points, no islands) and exposed a defect in
the tool rather than the site: `orphans --contextual` was reporting 27 orphans, of
which 6 were the global-nav hubs themselves, carrying 3,978-12,070 inbound links
apiece. A count that is 22% artefact is worse than no count, because it teaches you
to skim the list. Furniture targets are now split into `nav_hub_urls` with their
true inbound count, leaving 21 genuine findings. Controlled in `test_sitegraph.py`
case 7.

### ✅ #2 — AI answer sampling — **BUILT 2026-09-01 (`geo.py`)**, reversing the call below

The deprioritisation was half right and the half it got wrong was the important
one. "The sampling half needs API keys this install does not have" was true of
the LLM engines and FALSE of the biggest answer engine in the world: **Google's
AI Overview is reachable today through the SerpApi key this skill already
uses.** The reasoning stopped at "answer engine = LLM API" and never checked.

Measured within minutes of the tool existing, on six of the site's core queries:
5 produced an AI Overview, **zero cite combatskirmish.net, and play-cs.com is
cited in 4 of 5** - an 80% share of voice on our own queries, held by the DR 35
competitor. Nothing in the program was asking this.

**And `serp.py` could never have found it.** SerpApi returns the AI Overview in
TWO STAGES: the first response carries only a `page_token`, and `references`
does not exist until the accompanying link is followed. `serp.py` read
references off the first response, so it reported `present: true,
references: []` for every AI Overview on earth - a permanent silent "never
cited". Fixed, with `references_resolved` so a failed follow-up reads UNKNOWN.

Perplexity and OpenAI are wired and report `no_key`, which is the correct state
and not a stub: the moment a key exists they work, and until then `cannot_ask`
never touches `not_cited`.

**The lesson, which generalises past this entry:** "we lack the credential" is a
claim about a capability, and it deserves the same control discipline as any
other negative. Three roadmap items were deprioritised on measurement in this
file and two of those were right; this one was a remembered constraint that
nobody re-checked against the sources actually configured.

#### The original deprioritisation, kept because its second half still stands

Measured before building, the same way #4 was, and with the same outcome. Two
halves, and neither survived contact:

- **The sampling half needs API keys this install does not have.** Built today it
  would fail closed on every engine — a tool that cannot run. Fail-closed is the
  right behaviour, but a tool whose only reachable state is "cannot ask" is not a
  capability, it is a stub.
- **The extractability half — the part that IS keyless — measured clean.** The
  geolook thesis says the unit is an extractable fact block, so the audit is
  whether a page states its answer in one self-contained, liftable sentence.
  Across 2,694 pages: **zero** with no lead, and the apparent failures were a
  probe artefact — the weapons pages open with a deliberate one-line tagline
  ("The T-side rifle. One-shot headshot, brutal recoil.") and the very next
  paragraph is a proper definition naming the entity.

⚠ **A negative result on the way there, worth keeping.** The first probe was a
cross-page numeric-contradiction detector, aimed at a REAL near-miss in the site's
own history: a mode page reading "15 servers" one paragraph from a table saying
321. It reported zero conflicts across the silo — and then **failed its control**,
unable to find the known instance when handed it directly. Greedy noun-phrase
capture had keyed `active servers worldwide` against `active servers`. The clean
"zero conflicts" reading was worth nothing, and would have shipped as a finding
without the control. Generic numeric-contradiction detection needs entity context
that is site-specific; it is not a stdlib-shaped problem.

**Revisit when** an install has engine keys, or on a site whose prose is
hand-written rather than generated from a template.

### #2b — the ORIGINAL framing, kept because it is still right

`geo-scan` measures who CRAWLED (OAI-SearchBot: 27 verified hits) and who
REFERRED (chatgpt.com: 18 clicks). It never asks the actual question: **do
assistants cite us, and is what they say correct?**

Prior art is thin — `aigclink/geolook` is the reference; `paulacavero/aeo-tracker`
is at 0★. So this is mostly build-it-ourselves.

Design notes carried forward from geolook: the unit is a **fact block**, not a
page; keep a per-facet **question bank**; verify the CLAIM, not just the mention.
Must fail closed — "could not query the engine" is not "we are not cited",
exactly the `providers.py` rule. Costs real API calls, so it needs a budget knob
and a cache.

### ✅ #2c — Bing's PAGE dimension — **BUILT 2026-09-01 (`bing.py pages`)**

Not on the original list, and it turned out to be the item that actually mattered.
`queries` could say the best-converting terms on combatskirmish.net were Chinese
and could not say WHICH PAGE earned them — and "our Chinese locale is working" and
"the English homepage is ranking for Chinese queries" have opposite fixes. Bing
exposes `GetPageStats`/`GetPageQueryStats`; nothing here called them.

One call settled it and reframed the whole account: **`/zh/` takes 8,522
impressions and 2,298 clicks at position 4 — a 27% CTR, three times the
homepage's clicks from a quarter of its impressions, and 68% of every click the
site gets.** The homepage carries 33,403 impressions at 2.1%.

Two traps, both controlled, because each returns confident nonsense rather than an
error: `GetPageStats` puts the page URL in a field named **`Query`**, and sorting
by impressions **inverts** the real ranking — the page earning most of the clicks
comes second.

**The general lesson: measure the DIMENSION you are missing before building the
capability you assumed you needed.** Three roadmap items were measured and found
not to be this site's problem; the thing that was, was a missing column in a
source already wired up.

### #5–#7, and the rest — **BUILT 2026-09-01**

The additive tier turned out to be worth building, and each one found a real
defect the moment it ran against live data:

- **`vitals.py`** (was #5, whole-site CWV). Per TEMPLATE, not per URL, because
  on a generated site 2,234 map pages share one layout and a fix is applied per
  template. Keyless. Its first version reported **5,940ms TTFB** for a server
  that answers in 119ms - the rest was this container's DNS - and flagged it
  "server/CDN work", pointing at the wrong system entirely. Connect is now timed
  separately and every sweep measures a known-fast third-party host first. It
  also samples TTFB twice: `/leaderboard` measured 12,065ms once and 70-94ms on
  repeat (a 5-minute cache the sweep missed), while `/servers/*` measured
  4,188 / 4,186 / 4,307 - reproducible, bimodal `[65, 66, 4071, 4157]` across
  1,404 indexed URLs on the tier Bing crawls most.
- **`brief.py`** (was #6, content briefs). Assembled from measurements, and a
  hard refusal without a readable page 1. Its cannibalisation check used jaccard,
  which is symmetric - a real query and `zzq nonexistent topic 9f2b` both scored
  0 against 17 real guides, i.e. a metric that could not fire.
- **`remeasure.py`** (not on the list). Hypotheses with pre-registered directions
  and stored argv, because the four ways "did it work" gets answered wrongly are
  all silent. The four open questions from this session are registered with
  measured baselines.
- **`controls.py`** — see #0 above.

**MinHash/LSH for `sameness.py` remains genuinely not needed**: shingles handled
2,637 documents fine, and that is a scale concern rather than a correctness one.

### #3 — Schema generation — **DEPRIORITISED 2026-09-01, on measurement**

Measured across 2,696 generated pages: **100% JSON-LD coverage, zero invalid
JSON**, every page carrying `BreadcrumbList` + `VideoGame`, guides carrying
`Article`. There is nothing on this site to generate.

The two retired types present (`FAQPage`, `HowTo`, both on `/how-to-play`) were
checked against `references/schema-gates.md` and are **correct to keep** — the
table's own ruling is "not a defect; keep it if non-SERP consumers read it, do not
add it for search benefit". That is the gate working as designed: it answered the
question without a guess and without a build.

### #3b — the ORIGINAL framing

`pagecheck.py schema` reads Google's extractor. `claude-seo/seo-schema`
generates. On combatskirmish.net the `/ring` and `/leaderboard` JSON-LD was
written by hand.

⚠ Pairs with `references/schema-gates.md`: a retired rich-result type is a reason
not to ADD it, and only sometimes a reason to remove it.

### #4 — Image SEO — **DEPRIORITISED 2026-08-31, on measurement**

advertools `image_spider.py`, `claude-seo/seo-images`. Still a genuine capability
gap in the abstract, but it was measured before building and there was nothing to
fix on the site that motivated it: **562 real `<img>` elements, 562 with alt, 562
DISTINCT alt strings** — 100% coverage, zero duplication. Hero images carry
`width`/`height` + `loading="eager"` + `fetchpriority="high"`; card images are
`loading="lazy"` without dimensions, and that is not a CLS defect because the CSS
reserves the box (`.card img{width:100%;height:120px;object-fit:cover}`).

Two process notes, because both cost a step:

- **The first probe was a regex over raw HTML and reported 415 images with no alt
  — one per page.** Every one was the literal string `<img>` inside an HTML
  COMMENT explaining the aspect-ratio reasoning. This is the identical bug the
  2026-08-02 health run found in `agentcheck` ("every structural regex ran against
  raw HTML including comments and script blocks"), reproduced from scratch. Any
  image audit must use an `HTMLParser`, not a regex — `sitegraph.py`'s parser is
  already immune and is the thing to reuse.
- **Measure before building.** The tool would have been built for a problem that
  does not exist here. Re-check on a site whose images are NOT generated from a
  template before promoting this back up the list.

### #5–#7 — the ORIGINAL framing, superseded above

- **Whole-site CWV** (`unlighthouse`) — "not the bottleneck on any site measured
  so far". It was: `/servers/*` runs 4.2s on 1,404 indexed URLs. The claim was
  made without ever measuring the site's server response per template.
- **Content briefs / competitor pages** (open-seo, claude-seo) — sits on top of
  `competitors.py`, which deliberately returns structure only. Still true, and
  that is exactly how `brief.py` composes it.
- **MinHash/LSH for `sameness.py`** — shingles handled 2,637 docs fine; a scale
  concern, not a correctness one. **Still the right call, still not built.**

---

## The rule to keep

Every item above is worth having, and **none of them is worth an install step.**
If an integration cannot be done in stdlib, it belongs in the markdown layer or
it does not belong here. That constraint is what makes this skill work anywhere,
and it is the first thing that will be traded away by accident.
