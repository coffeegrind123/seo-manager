# Workflow: geo-scan (AI visibility / GEO)

**Cadence:** weekly. **Job:** measure whether AI assistants cite this site when
asked the questions its customers actually ask.

Search is splitting: a growing share of the queries this site targets get answered
by an AI assistant instead of ten blue links. Ranking #3 is worth much less if the
answer above the results cites somebody else.

Two sides, measured differently:

- **Google's AI Overview** — free, and already covered: `serp.py` records
  `ai_overview.present` on every check that carries it (SerpApi inline, or the
  browser provider's page read). No separate run needed.
- **Chat assistants** — this workflow. **You are the instrument**: you sample the
  questions with your own web search, on the owner's own subscription, so the
  check costs nothing.

> **Do not fabricate answers from memory.** Every recorded result must come from a
> real web-search-backed answer produced in this run. A remembered answer is not a
> measurement, and this metric is worthless the moment it stops being one.

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

---

## 1. Build the question set — ~15, hard cap 20

```bash
python3 scripts/seostate.py keywords          # what is tracked
python3 scripts/seostate.py conventions       # what the site is and who it serves
python3 scripts/seostate.py ai-visibility     # prior_queries - reuse them
```

Convert keywords into **the questions a real customer would ask an assistant** —
"best time tracker for freelancers", not the raw keyword string.

If NO keywords are tracked yet (fresh project), that is a configuration state,
**not a failure**: derive the question set from the conventions file's product
facts alone and say so in the scan report.

**Prefer commercial/comparison questions** (where being cited converts) over
definitional ones.

**Reuse roughly the same set week to week so the trend line means something** —
`ai-visibility` returns `prior_queries`; keep them unless one was retired for a
reason.

---

## 2. Sample each question

For each question, run a **real web search** and compose the answer an assistant
would give from those results, noting every source you would cite.

Judge citation **honestly**: the site counts as cited only when a page on it is
among the sources that **actually support the answer** — not when it merely
appeared somewhere in search results.

Sources you can use for the search leg:

```bash
python3 scripts/serp.py "<the question>" --count 10
```

…or the agent's own web search, or the browser MCP for a real Google read
including the AI Overview block. Any of them is fine; what matters is that the
answer is composed from results fetched **in this run**.

---

## 3. Record everything in one call

```bash
python3 scripts/seostate.py record-ai --json '[
  {
    "engine": "claude",
    "query": "best rank tracker for a solo founder",
    "has_ai_answer": true,
    "cited": false,
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
- **`answer_excerpt` is what makes the dashboard number trustworthy — never skip
  it.** A citation rate with no verbatim evidence behind it is a number nobody can
  audit, including you next week.

---

## 4. Read the gap

```bash
python3 scripts/seostate.py ai-visibility --days 90
```

`gap_domains` is the list of sites getting cited on questions where this site is
**not**. That list is the content backlog, ranked by how often each domain beat
you.

For each gap that maps to a content opportunity the site could plausibly win, note
it in the report. If one is a clear, queue-worthy idea:

```bash
python3 scripts/seostate.py propose --type guide --title "..." --keyword "..." \
  --source geo-scan --rationale "..." 
```

**Pending, never auto-approved from this workflow.**

---

## 5. Report

- Questions asked, citation count per engine, the citation rate.
- The 2–3 most interesting **verbatim** answers (cited and not).
- The gap domains.
- Any ideas queued.

**If nothing cites the site yet, say so plainly** — a zero baseline is the point
of measuring. The number only means something as a trend, and the trend needs a
first point.

```bash
python3 scripts/seostate.py log-run --workflow geo-scan --summary "<N questions, M cited>"
```

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
  link value.
