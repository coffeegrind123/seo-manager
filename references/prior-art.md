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
