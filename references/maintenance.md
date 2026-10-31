# Maintaining SKILL.md, and upgrading the skill

Two decisions in the frontmatter look like mistakes and are not. Moved here from
SKILL.md on 2026-09-20 so they stop loading on every trigger; the rules are
unchanged.

---

## Why `allowed-tools` grants bare `Bash`

Deliberate. A narrow pattern cannot cover this skill: the build workflows run
**the site's own build command** — whatever the conventions file says — and
`crawl-log`/`backlinks` shell out over `ssh` to a host named at runtime. The
allowlist would need rewriting per project, and a miss presents as a permission
prompt mid-run. The real constraints are the Non-negotiables above, not the tool
grant.

---

## Frontmatter is spec-exact — do not add `when_to_use`

The agentskills spec allows exactly six keys: `name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools`. **`when_to_use` is not one of
them**, and Anthropic's own `skill-creator/scripts/quick_validate.py` rejects it
outright ("Unexpected key(s) in SKILL.md frontmatter") rather than ignoring it.
This skill carried one until 2026-08-02; its trigger phrases and its do-NOT-use
boundaries now live in `description`, which is the field every client reads.

So `description` is doing two jobs and sits near its 1024-char ceiling. When
editing it, keep the trigger list and the `NOT for…` boundary — it is what
stops this skill firing on an ads task that belongs to `adsense`. (There used
to be three: `search-console` until 2026-09-01, when `gsc.py` brought Search
Console inside, and `seo-audit` until 2026-10-25, when the one-page audit was
merged in as the `audit` workflow.) Re-check with:

```bash
python3 ~/.claude/skills/skill-refiner/skills/anthropic-skills/skills/skill-creator/scripts/quick_validate.py \
  ~/.claude/skills/seo-manager     # must print: Skill is valid!
```

---


---

## Upgrade protocol — the prior-art pass

Run when asked to "upgrade", "run the upgrade protocol", or "search GitHub for
SEO repos and implement". There is nothing vendored to bump: the script layer
is stdlib cleanroom (see prior-art.md, "The rule to keep"), so an upgrade means
re-surveying the ecosystem, finding what this skill lacks or gets wrong, and
building it. Previous passes: 2026-08-31, 09-01, 09-20, 10-03, 10-25 — each is a
section of `references/prior-art.md`, and the fourth (commits `130a3c0` ..
`cfcb6b8`) is the model for the commit shape.

**Cadence.** A one-day re-survey found nothing (09-01); thirteen to nineteen
days found real movement every time. Under ~10 days, say so and ask before
spending the pass.

### 1. Baseline — prove the instruments before changing them

```bash
python3 scripts/run_tests.py       # every suite must pass; record the count
python3 scripts/controls.py audit  # record proven/declared; `unreachable` is not a pass
git status --short                 # start clean
```

A red baseline is fixed (or reported) first; otherwise the pass cannot tell
its own regressions from inherited ones.

### 2a. Survey — measure the known siblings, never recall them

- Re-read every repo already named in prior-art.md with `gh api repos/<r>`:
  stars, `pushed_at`, licence, latest release. A repo whose numbers went
  *down* or whose licence changed is a finding (rename, reset, relicence).
- Diff each moved repo since the last pass date: `gh api
  repos/<r>/commits?since=<date>` and its releases' notes. Changelogs that
  say "Google-currency", "crawler", "robots", "schema" or name an API are the
  ones that matter.
### 2b. Discover — new sources of features, not just updates to known ones

The known siblings are where the pass starts, not where it ends. Every pass
so far found its biggest win in a repo the previous pass had never heard of
(marketingskills on 09-20, rankme.fast and searchmirror on 10-03, Canonry on
10-25). A pass that only diffs the known list is half a pass.

- **Search every term the ecosystem uses**, not just "seo": `seo`, `geo
  seo`, `aeo`, `ai seo`, `generative engine optimization`, `llm
  visibility`, `ai search visibility`, `llms.txt`, `crawler`, `search
  console`, `bing webmaster`, `indexnow`, `rank tracker`, `serp`, `seo
  skill`, `seo agent`, `seo mcp`, plus `topic:claude-skill` /
  `topic:agent-skills` / `topic:mcp-server` with an SEO term. The 08-31
  pass missed a 50k★ tier because it searched one term. Add any new term
  the found repos describe themselves with, and record it here.

  ```bash
  gh search repos "<term>" --sort stars --updated ">=<last pass date>" \
    --limit 30 --json fullName,stargazersCount,createdAt,description
  ```

- **Filter for novelty against prior-art.md**, not against memory: grep
  each candidate's name there; anything with zero hits is new. Keep a floor
  (~150★) for the star-sorted sweep, but also take low-star repos created
  since the last pass whose description names a measurement we do — small
  repos with real test fixtures (rankme.fast, searchmirror) paid more than
  big prose-only ones.
- **Follow the graph from known siblings**: repos they cite in READMEs or
  credit in code comments, awesome-lists (`awesome-seo`, `awesome-geo`,
  RankSpotAI lists), and `gh search code` for a distinctive identifier from
  a sibling (a function name, a fixture filename) to find who else
  implements the same check. Mind the 10/min `code_search` quota.
- **Search for API maps** (wrappers, MCP servers, OpenAPI files) of
  surfaces this skill calls or could call: Search Console, Bing Webmaster,
  IndexNow, CrUX, SerpApi/SearchApi, AI-vendor search APIs. A wrapper
  useless as a dependency is often the best documentation of an endpoint
  (the second pass's lesson).
- **Read vendor changelogs directly**, since they are the sources the
  siblings are copying from: Google Search Central's `/search/updates` and
  `/crawling/docs/changelog`, Bing Webmaster blog, the AI vendors' crawler
  and search-API pages. A sibling's "Google-currency patch" is a pointer to
  these, never the fact.
- **Treat a star count that dropped as a finding**, not noise; check
  whether it was a rename, a reset, or a purge of fake accounts before
  weighing that repo (geo-score, 619 → 32★ on 10-25: forker accounts 404).

Every new repo found goes into prior-art.md's pass section, taken or not,
so the next pass's novelty filter sees it.

### 3. Teardown — parallel, read-only, claims are leads

Pick the repos that moved most or are new and relevant; run teardowns in
parallel (subagents, read-only, `gh` only). Each returns: what the repo checks
or measures that we do not, what it checks that we ALSO check, and its
fixtures.

- **Run OUR equivalent on THEIR fixture before reading their code** (the
  fourth pass's rule). That is where the bugs-at-home come from, and in that
  pass they outnumbered the features.
- **A field name is read off the LIVE response**, not the reference page:
  on 10-25 the docs said `first_incomplete_date` and the wire sent
  `firstIncompleteDate`.
- **Every claim acted on is re-read on its primary source** — the spec, the
  vendor's docs page, the RFC — never on the teardown's or the sibling's
  word. Three of the fourth pass's claims failed that check. Record the ones
  that fail under "did not survive the primary source".

### 4. Rank and confirm

Write the ranked list — bugs found at home first, then gaps, each with its
evidence and the stdlib route — and **show it to the owner before building**.
Composite scores, model-labelled sentiment, and anything needing an install
step are "Not taken" by default (prior-art.md says why).

### 5. Build — one instrument per commit, test first

- A defect: a failing test that reproduces it, watched failing, then the fix.
- A new instrument: stdlib only, its own `test_*.py` (registered with
  `run_tests.py`), and a control in `controls.py` if it can ever report
  "absent" — a probe without a control that must come back "present" is not
  done.
- Commit subject is the finding, not the activity: `geo: a mention is a name,
  and a rate carries its interval`, not `geo: improve mention matching`.

### 6. Gate

Both baseline commands again: every suite passes, the suite count went up by
the suites added, and `controls.py audit` reports `broken: 0` with `declared`
up by the controls added. Re-run `quick_validate.py` if the frontmatter moved.

### 7. Record

- Append a "Nth pass, <date>" section to prior-art.md in the established
  shape: what moved (with numbers), the found-at-home table, what was built
  (with instrument / control / suite counts after), claims that did not
  survive, Not taken, and the rule this pass adds (if any).
- Mark ranked-gap items ✅ BUILT with the date and script.
- Update every reference the change touches (`scripts.md`, the workflow
  files, `data-sources.md`, `agent-readiness.md`, README's suite count) in
  one closing `docs:` commit, as `cfcb6b8` did.
- Ask before pushing.
