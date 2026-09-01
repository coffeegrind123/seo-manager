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
control and reports `ok: false` naming any that cannot prove itself. **24 of 24
instruments, 301 checks, no network, 0 broken.**

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

### #2 — AI answer sampling — **DEPRIORITISED 2026-09-01, on measurement**

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

### #5–#7 — additive, not corrective

- **Whole-site CWV** (`unlighthouse`) — real gap; `pagecheck.py vitals` does one
  URL. Not the bottleneck on any site measured so far.
- **Content briefs / competitor pages** (open-seo, claude-seo) — sits on top of
  `competitors.py`, which deliberately returns structure only.
- **MinHash/LSH for `sameness.py`** — shingles handled 2,637 docs fine; this is a
  scale concern, not a correctness one.

---

## The rule to keep

Every item above is worth having, and **none of them is worth an install step.**
If an integration cannot be done in stdlib, it belongs in the markdown layer or
it does not belong here. That constraint is what makes this skill work anywhere,
and it is the first thing that will be traded away by accident.
