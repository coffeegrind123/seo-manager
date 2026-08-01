# Workflow: build-guide (guides ONLY)

**Cadence:** daily. **Job:** build the guide at the top of the owner's queue into
a merge-ready PR.

The pipeline is **PACE → PICK → TEMPLATE → GATE → INFO-GAIN → DRAFT → VISUALS →
HUMANIZER → VERIFY → BACK-LINKS → PR**. Every step is mandatory. The output must
be merge-ready in the site owner's voice so their job stays "approve → merge",
never "rewrite by hand".

This workflow builds **GUIDES exclusively**; tool suggestions belong to
`references/workflow-build-tool.md` and must never be picked up here.

Read `references/quality-bar.md` and `.seo/conventions.md` before starting.

---

## Owner content preferences — read them FIRST

```bash
python3 scripts/seostate.py prefs
```

The `guide_note` it returns is a set of instructions this workflow **OBEYS**:
shapes the owner removed from the rotation, skeleton blocks they turned off, and
free-text house rules to follow verbatim.

They are preferences, not a licence: **they never override the quality bar.** An
owner can tell you to skip the FAQ; they cannot tell you to ship a page that
fails the thin-content gate. If a preference and a gate genuinely collide, the
gate wins and you say so in the run report.

If nothing has been customised the note says so, and the defaults below apply
unchanged.

---

## 0. PACE GATE — before anything else

```bash
python3 scripts/seostate.py pacing
```

The pace is flat and simple: **at most ONE guide ships per UTC day, whoever ships
it** — a guide the owner merged by hand uses the day's slot too. Search engines
do not punish publishing speed; they punish thin sameness at scale, and the
sameness / thin-content / SERP gates below carry that risk. The daily slot exists
to keep the cadence steady instead of bursty.

If `build_allowed` is false, **stop cleanly**: report "paused by pacing: a guide
already shipped today", change no statuses, exit without edits in headless runs.
No exceptions — a trend-sourced idea at the front of the queue waits for
tomorrow's slot like everything else; at one day maximum, the wait never outlives
a hype window.

---

## 1. PICK

```bash
python3 scripts/seostate.py suggestions --status approved --type guide
```

The list comes back in **BUILD ORDER**: the owner's queue exactly as they see it
(front-placed ideas first — trend approvals and "do this one next" adds land
there — then oldest first). **Take the FIRST item.** Never re-rank or
second-guess the order; it is the owner's explicit call.

### None approved → the LOW-TANK BACKSTOP, before giving up

The daily cadence is a promise; a builder idling while vetted ideas sit pending
is a starved queue, not an empty one.

```bash
python3 scripts/seostate.py suggestions --status pending --type guide --source research
```

Look for pending-ZONE ideas whose rationale carries "FLAGGED FOR YOUR CALL" —
they passed the full quality bar and only the auto-approve KD line held them
back. Take the FIRST such idea and:

```bash
python3 scripts/seostate.py update <id> --status approved \
  --note "auto-promoted by the low-tank backstop - approved queue was empty"
```

then build it this run. **On semi projects the state layer records that approval
as pending instead** — the response says so; in that case do NOT build: report
loudly that the queue is empty while N vetted ideas await the owner's call (name
them, best first), and exit cleanly.

Never promote `source: manual` ideas (the owner's own drafts await THEIR
decision), never anything rejected, and never promote more than the one being
built now. No promotable ideas either → say "queue empty" and stop cleanly.

### Thin manual ideas

An idea with `source: manual` was typed by the owner and may be just a title and
a note — no keyword, no spec. Treat the missing research as step-0 work: use
`keywords.py expand` to pick the primary keyword and `seostate.py track` it
before moving on. **For manual ideas the step-4 gate is ADVISORY, not a veto**:
the owner asked for this one by name, so build it either way and note in the run
report if page 1 looks hard to beat.

---

## 2. CLAIM

```bash
python3 scripts/seostate.py update <id> --status in_progress
```

---

## 3. TEMPLATE

Before drafting, read the living template: **2 recent exemplar posts** from the
guides directory (pick ones closest in shape to this topic) plus whatever content
playbook the conventions file names.

Exemplars teach the CONVENTIONS and voice — **they are NOT skeletons to clone**:
the new draft must not mirror an exemplar's section order, intro pattern, or
transitions. The generated article must be indistinguishable in structure and
voice from a hand-written post.

### Choose the article SHAPE (archetype) — and rotate it

Guides come in distinct shapes, each with its own geometry:

| Archetype | Geometry |
|---|---|
| **tutorial** | ordered how-to steps |
| **comparison** | X vs Y / alternatives, table-led |
| **data-study** | built around a measured finding |
| **opinionated take** | a defended position |
| **reference/checklist** | a scannable spec or list |

Note the shape of the last 2–3 published guides (`seostate.py pages --type guide`
records `archetype`). **THIS one must not repeat them** unless the keyword's
intent truly forces it — three tutorials in a row is the template-sameness that
gets a corpus discounted. The live SERP read in the next step confirms which
shape the query actually rewards (if page 1 is all comparisons, a tutorial will
not rank). State the chosen archetype and why — the recent mix plus the SERP — in
the run report and the PR body.

---

## 4. THIN-CONTENT GATE + INTENT CONTRACT — before writing a word

Re-pull the live SERP top 5 for the primary keyword (research may be days old):

```bash
python3 scripts/serp.py "<primary keyword>" --count 10
```

> **Free mode — no SERP source connected** (`serp.py` errors and there is no
> paid provider): do NOT stall or refuse here. Build the intent contract from the
> keyword's plain reading, this site's own Search Console queries, and product
> knowledge instead; write **"SERP gate: skipped (free mode — no SERP source)"**
> in the run report AND the PR body; every other bar in this step still applies
> in full. **Never fabricate SERP claims you did not fetch.**

List concretely what each page-1 result covers. **That list is not just a bar to
clear — it is the INTENT CONTRACT**: the subtopics every top result shares are
what the searcher actually came to do, and the draft must let them finish that
job start to finish without opening a second tab.

- A "how to build X" query gets a complete build path (setup → working code →
  test → connect).
- An "X examples" query gets actual named examples and runnable code.
- An "X setup/integration" query gets the real setup steps.

Write the contract (dominant format + must-cover subtopics) into the run report.

The planned draft must beat the current page 1 on **at least 2 of**:
completeness, tested accuracy, freshness, actionability — **by covering the
contract AND adding more, never by skipping the contract to be different.**

If it cannot beat page 1, **DO NOT BUILD**: `update <id> --status pending`, state
the reason in the run report, and stop. No filler, ever.

---

## 5. INFORMATION GAIN — required, the one thing no competitor page has

Comprehensive coverage is no longer a differentiator — an AI can compile it from
ten articles in seconds, and Google discounts restated consensus. What earns the
ranking now, and what AI answer engines actually cite, is **ORIGINAL
information**: a fact, number, or artifact that exists on none of the page-1
results.

Before drafting, decide and produce at least one such asset, in rough order of
strength:

1. **(a)** a command or config you actually RAN for this article, with its real
   output pasted in;
2. **(b)** an original measurement or benchmark — time it, count it, compare two
   approaches on the same task and report the numbers;
3. **(c)** the site's OWN data as a citable stat — `seostate.py rankings` and the
   `search-console` skill return this project's real traffic and rank history;
4. **(d)** an original worked example or end-to-end config no docs page shows;
5. **(e)** a clear, defended stance where every page-1 result hedges.

6. **(f)** a **primary source page 1 never opened** — a peer-reviewed finding, a
   real citation count, a dated figure from the literature. `factcheck.py`
   surfaces these keylessly:

   ```bash
   python3 scripts/factcheck.py sources --query "<the underlying question>" --since-year 2020
   ```

   It returns titles, years, citation counts, DOIs and open-access links from
   OpenAlex and Crossref. On a topic where every page-1 result is restating the
   same blog post, one properly-cited primary source *is* the information gain.

   ⚠ **These are candidates to READ.** The tool has not verified that a paper's
   finding is sound, current, or applicable to your niche — only that it exists.
   Citing one you did not open is a fabricated fact with a DOI attached, which is
   worse than an unsourced claim because it looks checked. Open it, confirm the
   number says what you think it says, then cite it.

### Semantic completeness — a cheap second pass, not a gate

Once a draft exists, check what a thorough page on the subject would be expected
to touch and you did not:

```bash
python3 scripts/factcheck.py coverage --draft <draft>.md --topic "<Wikipedia article title>"
```

⚠ It matches **words, not meaning** — a draft covering a concept in different
vocabulary scores as a gap, and one that name-drops a term without explaining it
scores as covered. Read every gap and decide. **Never close a gap by inserting
the phrase**: that is precisely the template convergence the sameness gate
exists to catch, and it will fail you there instead.

### Only what THIS run can honestly do

You hold the repo, the current official docs, the local scripts, and the agent
itself — and nothing else. **NEVER install or run a third-party or competitor
tool** (a rival CLI, a paid API you have no key for, anything needing a browser
login) to manufacture an asset: that is what breaks the morning build, and faking
the number instead of running it is worse.

So (a) and (b) mean the stack you ARE and the commands you CAN run here. For an
X-vs-Y guide where you cannot run Y, the honest asset is deep, **SOURCED**
specifics pulled from BOTH tools' current official docs and changelogs that the
thin page-1 posts get wrong or omit (cite them), plus the side you CAN run for
real, plus a defended verdict — never a benchmark of a tool you never executed.

**A small real asset always beats an impressive fake.** If a topic honestly
supports no original asset this run, SAY SO in the report and lean the page on
tested accuracy and current-docs freshness; do not invent one to fill the step.

### The asset is a section, never the spine

Information gain sits INSIDE an article that fulfils the step-4 intent contract —
it must never become the article's organising principle. Two failure shapes to
refuse:

- an article whose spine is the site's own product as the running case study
  ("here's what OUR server does" stretched across every section);
- an article whose spine is meta-commentary on the SERP itself ("most results for
  this query are toys; here's the real kind").

Both read as ads wearing a keyword, and both lose to the mediocre-but-on-task
page that actually does the searcher's job. **Cap them: the product case study
and any SERP critique each get at most ONE clearly-bounded section, placed after
the contract is already fulfilled.**

The asset must be REAL. Fabricating it is the worst possible failure on the page:
worse than shipping nothing, because a wrong "measured" number destroys trust the
moment a reader checks it. Name the asset in the run report and PR body
("information gain: measured cold-start of X vs Y, table in section 3").

---

## 6. DRAFT

For any factual topic (a feature, API, version, command), **FETCH THE CURRENT
OFFICIAL DOCS FIRST and draft from that** — never from memory, which may be
stale. Respect the trusted-sources security rule.

Test every command you include **that this run can run** (the site's own stack,
standard shell) and use its real output. For a command belonging to a tool NOT
installed here, do not try to install it and never invent its output: verify the
exact syntax against that tool's current official docs and present it as
*documented*, not as *run*.

### SEEDED GUIDES

When the suggestion's spec carries `seed_url`, this idea grew from one specific
viral video/thread, and the draft is an **ENRICHMENT** of that source, never a
disguised copy. The seed rode the owner's approval, so fetching it is within the
trusted-sources rule.

Read it (transcript for a video where available), pull the real substance — the
argument, the steps, exact quotes with attribution — and **credit it with a
visible link early in the piece**; embed the video where the stack supports
embeds. Then EARN the page: cover what the original missed, correct what it got
wrong against current docs, and still produce this run's own step-5 information
gain. The seed is raw material, not a substitute for original work. Its numbers
(`spec.seed_stats`) may be cited as why-now evidence. **Never republish the
source wholesale**: a transformed, credited enrichment ranks; a paraphrase gets
discounted as scraped content.

### Universal body rules

- **Answer-first**: the first paragraph fully answers the core query in 2–4
  sentences (for Google AND AI engines). No preamble. Then a TL;DR blockquote.
- **Question H2s** matching real queries; at least 3 H2s; at least one comparison
  table where a comparison exists.
- **FAQ mirror rule**: end with a FAQ section, and if the stack emits FAQ
  structured data from metadata, the metadata must mirror the body FAQ **WORD FOR
  WORD**.
- **Internal links**: 2–3 root-relative links to sibling guides plus one free tool
  where natural. Never absolute URLs to the site's own domain, never UTM params
  in body links.
- **No mid-article CTAs.** One closing in-prose product mention in the final
  paragraph at most; rails/end-CTAs render automatically if the stack provides
  them.
- **Honest limits section** ("when NOT to use this").

### Structural variation (anti-sameness)

The format elements above are a fixed skeleton; **the prose must never be.**
Search engines discount repeated LAYOUT across a corpus — every post sharing
intro patterns, transition phrases, and section geometry reads as one template
wearing N keywords, the scaled-content-abuse profile.

Per post:
- let the SERP and topic set the geometry (section count, H2 wording, where
  tables and visuals fall, FAQ length 3–7, word count);
- run the **ANTI-RHYME check** — read the 2–3 newest published guides and rewrite
  anything in this draft that echoes them (intro sentence pattern, transitions,
  conclusion shape);
- surface the step-5 information-gain asset where it does the most work — never
  bury your one original thing in a footnote;
- vary the visual TYPE from the previous few posts.

---

## 7. VISUALS — mandatory, 2 to 3 bespoke components

Every guide ships with two or three custom visual components that are **ABOUT
this guide's content** — never stock decoration. Before designing anything, read
the exemplar visual components named in the conventions file COMPLETELY, then
follow the site's component conventions (file location, naming, server vs client,
theme tokens, card idiom).

Universal rules on top:

- **Theme tokens only** — no ad-hoc hex colours, no external images, no chart
  libraries.
- **Icons are real depictions, never abstractions.** Named products/brands get
  their official logomark (from the site's brand-marks module; extend it in its
  existing style if a mark is missing). Concepts in a list get an icon that
  depicts the concept (a plug for a server connection, a webhook for hooks).
  **NEVER a first-letter chip, NEVER a bare coloured square/dot standing in for a
  brand or concept.** Coloured dots are acceptable only for things with no
  possible pictorial form.
- **EVERY number and fact in a visual is real DOM text** that crawlers and AI
  engines can read — bars, rings, and shapes are decoration UNDER the values,
  never the only carrier. Visuals show the guide's own verified facts;
  fabricating data for a visual is as bad as fabricating it in prose.
- Pick shapes that fit the content: comparison split, scorecard with bars, stat
  callout row, step flow, inventory grid. If the guide has no numeric data,
  visualize its structure (workflow, decision path, before/after) — there is
  always something real to show.
- Named for the concept it shows (never `Visual1`/`GuideChart`), imported only by
  this guide, with a top-of-file comment explaining what it shows and why.

### COVER IMAGE — when the repo supports it

If the repo has a cover generator (e.g. `scripts/generate-cover.mjs`), every
guide also ships with a cover **YOU author as vector art** — no image model is
involved. You know exactly what this post is about; draw that, don't describe it
to a generator.

Write a subject-layer SVG to a temp file (full `<svg>` document,
`viewBox="0 0 1600 900"`, **TRANSPARENT background** — no full-canvas rects),
then run the generator with `--slug <slug> --svg <file> --hue <hue>`; it
composites your layer onto the house base so every cover stays one family.

Subject rules:

- Draw the post's **ACTUAL subject** as a minimal line diagram: the thing a
  reader would sketch on a whiteboard to explain the topic (a client and server
  exchanging labelled calls, a cron clock feeding a PR, a grid of example cards).
  A reader should guess the topic from the image alone. **Never an abstract
  metaphor object** (cube, orb, monolith) — that is the slop this system
  replaced.
- Style: stroke-based line art, 3–5px strokes, rounded caps and joins, generous
  empty space, one clear focal structure — never a busy scene.
- Tiny UPPERCASE mono labels on diagram parts are ENCOURAGED — they carry the
  relevance ("TOOLS/CALL", "CRON", "PR") — but never the post title, and never
  more than a handful of words total.
- Real product marks are composite-only: pass the generator's icon flag so it
  overlays the EXACT official glyph. **NEVER draw a logo by hand.**

**VARIETY IS MANDATORY**: look at the last 2 published posts' covers and pick a
hue AND a composition (centered subject, left-to-right flow, grid, split) that
differ from both — same anti-sameness rule as article shapes.

If no generator exists, skip WITHOUT failing the run and note "no cover —
generator not configured" in the report. Never hotlink an external image or
fabricate a cover path.

---

## 8. HUMANIZER — mandatory, not optional

Apply the humanizer pass the conventions file points at (or its principles if the
repo carries no skill copy): kill AI tells, tighten, match the first-person
practitioner voice of the exemplar posts. **This is about genuinely good
human-quality writing, not evading detectors.**

Then re-run the ANTI-RHYME check, and re-check the FAQ mirror after edits.

---

## 9. VERIFY

The site's build/verify command (from the conventions file) must pass — this also
compiles the visual components. Sanity-check internal links resolve to real
slugs and the FAQ mirror holds.

### First the SLOP SCAN — advisory, and it runs BEFORE the sameness gate

```bash
python3 scripts/slop.py scan <the FINAL guide file>
```

Order matters: a rewrite for slop changes the text, so running the sameness gate
first wastes the check. This one detects **AI writing tells** — the "It's not X,
it's Y" reframe, "Experts say", delve/leverage/robust, throat-clearing openers,
bold-first bullets — mechanically, with line numbers and the fix for each.

It is the one thing here a model genuinely cannot self-assess: these are exactly
the patterns it reaches for without noticing, so a model reviewing its own draft
for them is the same instrument grading itself. A regex is not.

Read `flagged` (over tolerance) and act on it; `within_tolerance` is shown for
awareness, not action — one binary contrast is rhetoric, ten is a fingerprint.
Two rules deserve special attention:

- **`vague_attribution`** ("Experts say…") also **fails the information-gain
  requirement outright** — a citation-shaped phrase with no citation. Name the
  source or cut the claim.
- **`binary_contrast`** is the most-cited AI tell there is. The fix is always
  to state the positive and drop the negation.

**Advisory, not blocking** — unlike the sameness gate. A `fail` on a first draft
is expected. Rewrite, re-scan, and use `slop.py diff before.md after.md` to
confirm the rewrite removed tells rather than trading them for new ones (read
`introduced`). Full catalog and rationale: `references/deslop.md`.

### Then the SAMENESS GATE — mandatory

```bash
python3 scripts/sameness.py check \
  --draft <the FINAL guide file, post-humanizer> \
  --corpus <guides directory from conventions.md> \
  --keyword "<primary keyword>" \
  --pages .seo/pages.json
```

It compares this draft against the guides this site has already published —
opening word-runs, the heading skeleton with the topic stripped out, and stock
phrases shared across the catalogue — and returns pass/fail with the **exact
offending strings**.

A fail means **REWRITE what it flagged**: a genuinely different opening,
different H2 wording and order, kill the named phrases. Then call it again.

**Never argue with a fail, never ship past one, and never "fix" it by rewording
the check** — this is the only check that sees your whole back catalogue at once,
which is exactly what Google sees and what no review of a single draft can ever
catch.

**Bounded at THREE attempts.** If it still fails after three honest rewrites,
stop rewriting — that is no longer a prose problem, it means the TOPIC
substantially duplicates something this site already published, and more polish
cannot fix that. Do what the thin-content gate does: `update <id> --status
pending`, say in the report which guide it collides with and what the gate
flagged, and exit cleanly without a PR. **Never loop past three.**

If it passes open with a note (corpus unreadable), say so in the report.

---

## 10. BACK-LINKS — only when the project opted in

Read `internal_linking` from `seostate.py config`. **When it is not exactly
`true`, SKIP this step entirely and say nothing about it** — that is the default
and it is not a failure. Never infer permission from Auto mode, from
`auto_merge`, or from the owner having been happy with previous PRs: **this is
the only step that edits pages the owner ALREADY PUBLISHED, and it needs its own
yes.**

When it IS true: the new guide already links OUT to siblings (step 6). Now make
the link flow both ways, so the corpus compounds instead of every post standing
alone. **In THIS SAME PR, edit 2–3 already-published posts** so they link TO the
new guide.

- **Pick by topical closeness, not recency.** From `seostate.py pages`, choose the
  posts whose primary keyword and title sit nearest this guide's topic. A reader
  of that post should plausibly want this one next. If only one page is genuinely
  close, edit one. If none is, edit NONE and say so — a forced link between
  unrelated posts is worse than no link.
- **Wrap words that are already there. Never rewrite, never add prose.** Find a
  sentence in the old post that already discusses the thing, and turn the phrase
  ALREADY IN IT into a root-relative link. You may not reword the sentence, fix
  its grammar, extend it, or write a new one. **The published text must read
  identically with the link markup stripped out** — if it would not, you have
  changed the owner's writing, which is never yours to do. This constraint is what
  makes the edit safe enough to ship unattended: an added link cannot mangle a
  sentence, a rewrite can. If no existing sentence carries a phrase that genuinely
  fits, that post is not a candidate — move on rather than authoring a home for
  the link. And **NEVER add a "Related posts" list, a "See also" footer, or a new
  paragraph whose only job is to hold a link** — those are ignorable boilerplate
  and they make the page worse.
- **Vary the anchor text.** Every anchor pointing at this guide across the corpus
  must read differently, and none may be the bare primary keyword repeated.
  Repetition of one exact-match anchor across many pages is a recognised spam
  signal, and the single most likely way this step does harm.
- **Saturation cap — skip any post already carrying 5 links to sibling guides.**
  This is the rule that stops the whole scheme rotting over time. A genuinely
  hub-ish post is topically close to everything, so without a ceiling it gets
  picked build after build and slowly fills with internal links until it reads
  like a link farm. Count the existing root-relative links to other guides in each
  candidate file BEFORE choosing it; at 5 or more the post is full, and full is a
  permanent state, not a queue.
- **Never double-link.** Skip any post that already links to this guide. One link
  per post per target, ever.
- **Hard cap: 3 files, one link each.** Not "at least" — at most. A run that wants
  to edit a fourth is wrong about relevance.
- **Touch nothing else in those files.** No typo fixes, no reformatting, no
  frontmatter edits, no reordering. The diff for each edited post must be one line
  changed.
- **Concurrency: rebase before you edit.** Another guide build may have a PR open
  against the same posts. `git fetch origin`, rebase onto the latest default
  branch, then re-read each file you are about to touch from that state. If a file
  you want is already modified on an open `seo/` PR, skip it and pick the
  next-closest post — a merge conflict in someone's published content is a far
  worse outcome than one fewer back-link.
- **Verify again after editing.** Re-run the build: the edited posts must still
  build and every link must resolve.

---

## 11. PR — never main

```bash
git checkout -b seo/<slug>
git add -A && git commit -m "..."
git push -u origin seo/<slug>
gh pr create --label seo --title "..." --body "..."
```

Create the `seo` label if missing. The body includes: target keyword, volume/KD,
the suggestion rationale, gate verdict (what page 1 lacks that this draft has),
the archetype and why, the information-gain asset, and a note that the deploy
preview link is the review surface.

**If step 10 edited any already-published post, the body lists them** — one bullet
per edited file with the path, the anchor text used, and the sentence it now sits
in. This is disclosure, not a gate: back-link edits ride in THIS PR and merge with
it under the project's normal merge rules.

**If `gh pr create` fails, that is a FAILED run — never exit green.** The usual
cause is the repo setting *"Allow GitHub Actions to create and approve pull
requests"* being off (the default on new repos). Revert the suggestion with
`update <id> --status approved` so the next run retries it, print the exact error
plus that setting name in the run report, and **exit non-zero** so the workflow
goes red and the owner gets GitHub's failure email. A pushed branch with no PR and
a green run is the worst outcome — it strands silently.

---

## 12. RECORD

```bash
python3 scripts/seostate.py update <id> --status done --pr-url <pr url>
python3 scripts/seostate.py log-page --url "https://<domain>/<path>" \
  --title "..." --type guide --keyword "<primary keyword>" \
  --pr-url <pr url> --archetype <archetype> \
  --information-gain "<one line naming the asset>"
python3 scripts/seostate.py log-run --workflow build-guide --summary "<one line>"
```

---

## 13. REPORT

What was built, the PR link, the gate verdict, the archetype and information-gain
asset chosen, which published posts (if any) were edited to link back, and what
to check on the preview.
