# Workflow: geo-scan (AI visibility / GEO)

**Cadence:** weekly. **Job:** measure whether AI assistants cite this site when
asked the questions its customers actually ask.

Search is splitting: a growing share of the queries this site targets get answered
by an AI assistant instead of ten blue links. Ranking #3 is worth much less if the
answer above the results cites somebody else.

Three sides, measured differently:

- **Google's AI Overview and AI Mode** — on the SerpApi key this skill already
  holds. `geo.py` asks both (`google_ai_overview`, `google_ai_mode`); `serp.py`
  also records `ai_overview.present` on every ordinary check. AI Mode was found
  reachable on 2026-09-20, nineteen days after the previous version of this file
  said the LLM side needed keys the install did not have — check `geo.py
  engines` before assuming what cannot be asked.
- **ChatGPT, Gemini, Perplexity, Copilot** — through `geo.py`'s `searchapi_*`
  engines when a SearchApi.io key exists, or `perplexity`/`openai` on their own
  keys. `no_key` is cannot-ask; the sweep refuses rather than reporting a zero.
- **Chat assistants you can reach by hand** — the fallback. **You are the
  instrument**: you sample the questions with your own web search, on the
  owner's own subscription, so the check costs nothing.

> **Do not fabricate answers from memory.** Every recorded result must come from a
> real web-search-backed answer produced in this run. A remembered answer is not a
> measurement, and this metric is worthless the moment it stops being one.

> **One answer is an anecdote.** Answer engines are non-deterministic: the same
> prompt cites different sources run to run. Every automated question runs
> `--runs 3` or more and is reported as a RATE with its n ("cited 2/3"); a
> hand-sampled question that could only be asked once is recorded as a single
> observation and never enters a trend line on its own. Compare rates over
> weeks, not runs: 4/5 → 3/5 is noise, 4/5 → 0/5 held for a month is signal.

### The ladder, and which instrument measures each rung

| rung | meaning | instrument |
|---|---|---|
| **retrieved** | the engine's crawler fetched the page | `crawllog.py` (`ai_search` rows, `hits_net`), §0 |
| **cited** | a page on the site is among the answer's sources | `geo.py` → `cited`, `runs.rate` |
| **mentioned** | the answer text names the site or brand | `geo.py` → `mentioned`, `sentences_naming_us` |
| **recommended** | the site is on the shortlist the buyer acts on | a HUMAN reading of `sentences_naming_us` — recommended / neutral / hedged / **recommended-against** |

The rungs are governed by different things. Citation follows content usefulness
(structure, statistics, freshness); recommendation follows web-wide consensus
(reviews, forums, analysts, press) and is largely independent of the site's own
pages. Lily Ray's 100-query B2B study (spring 2026): self-promotional "best
[category]" listicles earned 323 AI Overview citations, and in **69% of them the
answer recommended a competitor** — the publisher's own research supplied the
model's competitor list. A rising citation rate with a flat mention rate is a
specific, diagnosable gap, and this report names the rung, never a single
"AI visibility" number.

---

## 0. First check whether they can read you at all

Added 2026-08-01. Citation has a precondition: an assistant cannot cite a page
its crawler never fetched. Sampling answers without checking ingestion measures
the *symptom* and leaves the cause invisible, so run these two first — both are
cheap and both are hard numbers rather than samples.

```bash
python3 $SEO/crawllog.py scan --remote root@<host> --ssh-key ~/.ssh/<k> \
  --glob '/var/log/<server>/access*.log*'          # -> by_category
python3 $SEO/backlinks.py footprint --domain <domain>
```

Read `by_category` and note that the AI crawlers split three ways, which is the
distinction this workflow lives or dies on:

| category | can it cite you? |
|---|---|
| `ai_search` (OAI-SearchBot, PerplexityBot, Claude-SearchBot, DuckAssistBot) | **yes** — this is the index assistants cite from |
| `ai_user` (ChatGPT-User, Claude-User, Perplexity-User) | **it already is** — a live fetch means a real person asked and the assistant came to your page |
| `ai_training` (GPTBot, ClaudeBot, CCBot, Google-Extended, Amazonbot, meta-externalagent) | **no.** Trains a model. Never cites, never sends traffic. |

Then interpret the scan below against it:

- **`ai_search` crawlers present, but you are not cited** → a content and
  authority problem. This workflow's sampling is the right instrument, and its
  findings are actionable.
- **No `ai_search` crawlers at all** → you are not in the index they cite from.
  No amount of answer-shaping fixes that; being crawlable and being linked does.
  Report it as the finding, and do not read the zero-citation result as a
  content verdict.
- **`ai_training` ≫ `ai_search`** → you are being farmed, not read. Worth
  knowing before anyone argues about robots.txt.
- **Common Crawl `absent`** → you are missing from the corpus a large share of
  pretraining and AI retrieval draws on. Upstream of everything below.

> Measured on a real site: `ai_training` 28% of bot traffic against `ai_search`
> 0.4% — farmed ~68× more than read — while Common Crawl held **zero** captures
> of the domain. The near-zero citation result that this workflow had been
> reporting was explained entirely by ingestion, not by the answers.

**Success criteria**: `by_category` ingestion numbers and Common Crawl presence are in hand BEFORE any answer is sampled, and the three AI-crawler classes are kept distinct. If no `ai_search` crawler reaches the site, that is reported as the finding and the citation result is NOT read as a content verdict.

---

## 1. Build the question set — ~15, hard cap 20

```bash
python3 $SEO/seostate.py keywords          # what is tracked
python3 $SEO/seostate.py conventions       # what the site is and who it serves
python3 $SEO/seostate.py ai-visibility     # prior_queries - reuse them
```

Convert keywords into **the questions a real customer would ask an assistant** —
"best time tracker for freelancers", not the raw keyword string.

**Generate them from six prompt classes, not from the keyword list alone.**
Assistants are asked in shapes a search box never sees, and a bank built only
from tracked keywords measures the site's own vocabulary back at it. For each
facet in the conventions file, draft 2–4 prompts per class, then cut to the cap:

| class | shape | why it is on the list |
|---|---|---|
| direct recommendation | "best X for Y", "recommend a X that Z" | the highest-intent prompt an assistant answers with names |
| comparison | "A vs B for Y", "alternatives to A" | where a competitor's shortlist is decided |
| feature-specific | "X that supports Z", "which X has Z" | where a product's real capability is the answer |
| use-case | "X for [scenario/industry]" | the long tail an assistant fans out to |
| pricing / value | "free X", "is A worth it", "cheapest X" | the extractable pricing page is the citation |
| migration / switching | "switch from A to B", "A replacement" | the intent that converts fastest |

Cross with the audience and intent modifiers the conventions file names
(beginner / team / self-hosted / free / fastest). Two rules from the format
evidence: **keep the comparison and recommendation classes even though ChatGPT
5.6 (Aug 2026) demoted listicle and comparison PAGES** — the demotion is about
what gets cited, not what gets asked; and never write a page per prompt, which
is the scaled-content pattern that shift was aimed at.

If NO keywords are tracked yet (fresh project), that is a configuration state,
**not a failure**: derive the question set from the conventions file's product
facts alone and say so in the scan report.

**Prefer commercial/comparison questions** (where being cited converts) over
definitional ones.

**Reuse roughly the same set week to week so the trend line means something** —
`ai-visibility` returns `prior_queries`; keep them unless one was retired for a
reason.

**Success criteria**: 15-20 customer-shaped questions exist, weighted to commercial/comparison intent, and `prior_queries` were reused so the trend line stays comparable. A fresh project with no tracked keywords derives the set from conventions and says so.

---

## 2. Sample each question

**Automated first**, for every engine `geo.py engines` reports usable:

```bash
python3 $SEO/geo.py sweep --domain <domain> --bank questions.txt --max 20 --runs 3
```

Read `per_question[].rates` (per engine, over answered runs), `mentioned_by`,
and each result's `sentences_naming_us` — verbatim, for the framing call. A
question whose engines all returned `no_answer_surface` is a fact about the
question, not a miss; one that `could_not_ask` on every engine is unknown and
is NOT in the rate.

**By hand for the rest.** For each question no reachable engine could take, run
a **real web search** and compose the answer an assistant would give from those
results, noting every source you would cite. Mark it `runs: 1` when recorded.

Judge citation **honestly**: the site counts as cited only when a page on it is
among the sources that **actually support the answer** — not when it merely
appeared somewhere in search results.

Sources you can use for the search leg:

```bash
python3 $SEO/serp.py "<the question>" --count 10
```

…or the agent's own web search, or the browser MCP for a real Google read
including the AI Overview block. Any of them is fine; what matters is that the
answer is composed from results fetched **in this run**.

**Success criteria**: Every question has an answer composed from results FETCHED IN THIS RUN. Nothing came from memory. Citation is counted only where a page on the site actually supports the answer.

---

## 3. Record everything in one call

```bash
python3 $SEO/seostate.py record-ai --json '[
  {
    "engine": "claude",
    "query": "best rank tracker for a solo founder",
    "has_ai_answer": true,
    "cited": false,
    "runs": 3,
    "cited_runs": 0,
    "mentioned": false,
    "cited_url": null,
    "answer_excerpt": "<1-2 sentences, VERBATIM from the answer you composed>",
    "citations": [
      {"domain":"zapier.com","url":"https://zapier.com/blog/...","title":"The 12 best rank trackers"},
      {"domain":"backlinko.com","url":"https://backlinko.com/...","title":"5 rank tracking tools"}
    ]
  }
]'
```

- `engine` — `claude` today; `chatgpt` / `perplexity` / `gemini` / `google_ai_overview`
  are reserved and read back separately.
- `has_ai_answer` — false only if the question produced no meaningful answer.
- `runs` / `cited_runs` — the sample size and how many runs cited us (from
  `geo.py`'s `runs` block). Omit them for a hand-sampled question and the row is
  stored as ONE observation; `ai-visibility` reports how many rows rest on one.
- `mentioned` — did the answer text name the site or brand (`geo.py` reports
  it). Leave it out when unknown; never guess it.
- **`answer_excerpt` is what makes the dashboard number trustworthy — never skip
  it.** A citation rate with no verbatim evidence behind it is a number nobody can
  audit, including you next week.

**Success criteria**: Every sampled question is recorded with a VERBATIM `answer_excerpt` and its citation list. A record without an excerpt is not acceptable.

---

## 4. Read the gap — and split the cause before proposing anything

```bash
python3 $SEO/seostate.py ai-visibility --days 90
```

`gap_domains` is the list of sites getting cited on questions where this site is
**not**. That list is the content backlog, ranked by how often each domain beat
you.

**Before a content idea is queued, name which of three causes the gap is** — they
have different fixes and only one of them is content:

| cause | evidence | fix lives in |
|---|---|---|
| **technical** | no `ai_search` hits in `crawllog`, `agentcheck.py policy` blocks the citing class, `agentcheck.py page` finds JS-only content, Common Crawl `absent` | §0 — crawlability, robots, rendering. No page fixes this. |
| **comprehension** | `mentioned` but `sentences_naming_us` describe the product wrongly or vaguely | the extractable fact block — `geo.py extractable`, answer-first openings, the definition sentence |
| **trust** | crawled, described correctly, still not cited or not recommended | consensus off-site — the presence step below. A new guide changes little here. |

## 4.5 Presence — turn the recurring gap domains into prospects

`gap_domains` is not only a content backlog. A domain that beats you on three or
more questions is a **surface the engines already trust**, and being on it is
worth more than out-writing it — the `catalogue` deferral rule, applied to AI
answers. For each recurring gap domain, decide which it is:

- **a directory, review site, marketplace or listicle host** → a listing
  prospect. Record it; the backlinks workflow works the queue:

  ```bash
  python3 $SEO/seostate.py prospect-add --domain alternativeto.net \
    --url https://alternativeto.net/manage-item/ --link-type nofollow \
    --reason "cited on 4 of 12 geo-scan questions where we are not (2026-09-20)" \
    --angle "listing under the facet the questions belong to"
  ```

- **a community thread** (Reddit, HN, a forum) → owner-voice work, not agent
  work. Name the thread in the report; do not draft the comment. The security
  rule keeps unattended runs off third-party posting surfaces, and a comment
  that reads as a brand account is worse than none.
- **a competitor's own page** → the content backlog, as before.

Citation mixes are volatile — ChatGPT's Aug 2026 retrieval change nearly removed
Reddit as a source within days — so a presence list is a portfolio, never one
surface. Say in the report which gap domains became prospects and which were
left as content.

For each gap that maps to a content opportunity the site could plausibly win, note
it in the report. If one is a clear, queue-worthy idea:

```bash
python3 $SEO/seostate.py propose --type guide --title "..." --keyword "..." \
  --source geo-scan --rationale "..." 
```

**Pending, never auto-approved from this workflow.**

**Success criteria**: `gap_domains` is read, each gap carries a cause (technical / comprehension / trust), and each recurring gap domain is either a recorded prospect, a named owner-voice thread, or a content idea. Anything queued is recorded as `pending` — never auto-approved from this workflow.

---

## 5. Report

- Questions asked, runs per question, and per engine: the citation RATE with
  its n, the mention rate, and how many rows are single observations.
- The ladder, per engine: retrieved (from §0) / cited / mentioned / the framing
  of each mention as a human reading (recommended, neutral, hedged,
  recommended-against) with the verbatim sentence beside it.
- The 2–3 most interesting **verbatim** answers (cited and not).
- The gap domains, each with its cause, and which became prospects.
- Any ideas queued.

**If nothing cites the site yet, say so plainly** — a zero baseline is the point
of measuring. The number only means something as a trend, and the trend needs a
first point.

```bash
python3 $SEO/seostate.py log-run --workflow geo-scan --summary "<N questions, M cited>"
```

**Success criteria**: The report gives questions asked, rates WITH n per engine, the ladder per engine with mention framing read from verbatim sentences, 2-3 verbatim answers, the gap domains with causes and prospects, and anything queued. A zero-citation baseline is stated plainly as the first point of a trend, and a single-observation row is never presented as a rate. The run is logged.

---

## What actually moves this number

Worth stating because it is not the same as classic SEO, and the research and
build workflows already encode most of it:

- **Information gain** (build-guide step 5) — answer engines cite the page that
  has the fact no other page has. Restated consensus is exactly what they compress
  away.
- **Answer-first openings** — the first paragraph fully answering the query in
  2–4 sentences is the extractable unit.
- **Real DOM text for every number** (build-guide step 7) — a value that only
  exists as the height of an SVG bar is invisible to the thing deciding whom to
  cite.
- **FAQ blocks that mirror their structured data word for word.**
- **Being on the domains that get cited** — the backlink playbook's directories
  and community placements show up in AI answers far out of proportion to their
  link value. Step 4.5 is where those get recorded.
- **Owned, "official" pages** — product, docs, pricing, original data. ChatGPT
  5.6 (Aug 2026, Peec AI data) cut listicle citations by half and comparison
  pages by a third while `site:` and "official" fan-outs surged; Gemini already
  draws ~60% of citations from business-owned sites. The pages only this site
  can publish are the rising citable class, and a self-ranked "best X" page is
  a human-conversion asset, not a citation play.
