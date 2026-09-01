# Workflow: research `<topic?>`

**Cadence:** weekly. **Job:** fill the queue to quota with keywords this site can
actually win, derived from what the product IS.

Read `references/quality-bar.md` first — every gate below is defined there.

---

## Method — product knowledge FIRST (the moat)

Never start from a generic seed list. Start from what the site IS, read fresh
from the repo at research time, so the research evolves with the product.

---

### 0. Read what already happened — the learning step, never skip it

```bash
python3 $SEO/seostate.py overview
```

Look at the `guides` and `tools` arrays: every page this pipeline already
shipped, with its age and latest position. Pull the traffic side from the
`search-console` skill (28-day query + page report). Pages published in the last
~3 weeks are still settling — weigh them lightly — but anything older is a graded
answer to "did our targeting work?", and it is the only honest evidence this run
has.

Bucket the settled pages by the traits recorded on their suggestion
(`seostate.py suggestions --status done` carries `keyword_volume`,
`keyword_difficulty`, `authority_count`, the intent class, and `serp_notes`) and
answer three questions **in writing**:

- Which traits produced pages now sitting in the top 20?
- Which produced pages stuck past position 50, or with impressions but no
  clicks? Those are the patterns to STOP repeating.
- Where the two disagree with the reported KD, **trust the observed positions**.
  Real outcomes on this domain beat any vendor's difficulty score.

Carry the answer into step 2 as a concrete steer ("comparison long-tails under
800 volume are landing top-20; the commercial 'alternative' terms are stuck past
80 — hunt the former, drop the latter this run"). State the steer in the run
report so the owner can see the loop is closing.

#### Minimum sample — do not pattern-match on noise

A "settled" page is one published **21+ days** ago (`overview` counts them for
you as `pages_settled` / `outcome_sample_ready`).

- **Fewer than 10 settled pages: REPORT ONLY.** Write down what you see ("3 of 6
  settled pages are past position 50") but derive NO targeting rule from it and
  pass NO steer to step 2. Two pages sharing a trait is a coincidence, and a
  confident rule invented from n=3 is worse than no rule at all — it will bias
  every run that follows while looking like evidence. Say "outcome data too thin
  to steer (N settled pages, need 10)" and move on.
- **10 or more: steer normally.**
- A trait needs at least **3 settled pages** behind it before it can support a
  conclusion, however clean the pattern looks.

On a site with no settled pages at all, say "no outcome data yet — first runs"
and move on.

**Success criteria**: The three questions are answered IN WRITING from settled pages, and where observed positions disagree with reported KD the positions win. A site with no settled pages says "no outcome data yet" and moves on.

---

### 0.5 Clear the held backlog

**An Auto project ends this step with ZERO undecided research ideas.**

```bash
python3 $SEO/seostate.py suggestions --status pending --source research
```

On a project with `mode: auto`, every one of these is a decision somebody
deferred, and deferral is not a state this mode has. Do NOT wait for DR to rise:
on a young site that is months away and may never happen, so "held until the
ceiling moves" is shelving with a kinder name.

Decide each one now, from the **authority count already recorded** in its
rationale and `serp_notes` — that check was paid for when the idea was proposed,
so this step costs nothing:

- count **0–2** → `update <id> --status approved`, **including when the row has
  no KD at all**. Missing KD is a missing estimate of a thing already measured;
  it is not a reason to withhold.
- count **3** → approve if the ICP fit is dead-on, else reject with the reason.
- count **4+** → reject; the gate has not changed.
- **no count recorded** (an older row, or a run that stopped early) → spend a
  SERP check on it now and decide as above. There is no budget to run out of, so
  "unchecked — re-propose later" is not an available verdict: check it, then
  decide.

Report the counts: how many released, how many rejected, and how many remain
undecided (which should be 0 on Auto). **Never touch an idea the owner rejected,
and never one with `source: manual`** — those are the owner's own drafts,
awaiting THEIR decision in both modes.

On a **Semi** project this step does the opposite: leave pending ideas pending,
because there the owner's judgment is the feature. Just report what awaits them,
oldest first.

**Success criteria**: On an `auto` project ZERO research ideas remain undecided — every one is approved or rejected from its recorded authority count, and an unrecorded count was paid for with a SERP check rather than deferred. Owner-rejected and `source: manual` rows were not touched. On `semi`, pending rows are left pending and reported oldest first.

---

### 1. Read the product surface

Skim, do not deep-read: the product-surface files listed in
`.seo/conventions.md`, plus the existing content inventory — published slugs in
the guides directory and the tools registry, cross-checked against
`seostate.py pages` (never propose duplicates).

**Success criteria**: The product-surface files and the existing content inventory have been read this run, cross-checked against `seostate.py pages`, so no duplicate can be proposed.

---

### 1.5 Aim the run: measure the product's facets, then work ONE

A product is not about one subject. Every honest description of the job it does
is a different search market with its own competition, and they are rarely
equally winnable. Read a rank-tracking product as "SEO" and its market has had
Ahrefs and Semrush publishing into it since 2011, so everything simultaneously
relevant and winnable for a young site is a long-tail. Read the same product as
"agents that do work unattended" and it sits in a market two years old where
nobody has published the real answers yet. **Same product, same quality bar,
order-of-magnitude different opportunity. A run that never checks is not choosing
the crowded market — it is failing to notice there was a choice.**

The conventions file lists this site's facets (setup writes them, most direct
first). Measure them:

#### Build MID-TAIL seeds first — free, and this is what makes the measurement worth anything

```bash
python3 $SEO/keywords.py expand \
  --seed "<facet 1>" --seed "<facet 2>" --seed "<facet 3>" \
  --groups commercial comparison audience --limit 120
```

Pick, per facet, the most product-shaped **MULTI-WORD** phrase it returns —
"ai seo tool", "seo automation software" — **NOT the bare head term**.

> **Never price a facet by its head term.** A head term on a young site is always
> KD 15–40, so seeding the paid call with one measures the single band this site
> cannot have and reports the facet as closed when its winnable keywords were one
> level down all along. This is not theoretical: a real run priced "SEO
> automation" and "agents unattended" by their head terms, reported KD 16–21 and
> no clean candidates, and fell back to 70–170-volume long-tails — while the same
> facet held "ai powered seo tool" at 1000/KD 8 and "ai seo services" at 1000/KD
> 0, which nothing in that run ever looked at.

#### Price the facets

**With DataForSEO configured** — one call for the whole measurement:

```bash
python3 $SEO/keywords.py volume \
  --seed "<midtail facet 1>" --seed "<midtail facet 2>" --seed "<midtail facet 3>"
```

The tool expands at most 5 seeds per call — the facet count, by design. Results
are guaranteed to CONTAIN the seed phrase, so a mid-tail seed returns the
mid-tail band directly. Expansion and pricing are the same call; there is no
separate bulk-metrics lookup, do not go looking for one. Attribute each keyword
to the facet whose phrase it contains; unattributed ones are still valid
candidates.

**Cost discipline.** That is ONE metered call for the whole measurement. Do NOT
loop it per facet, and do not re-measure a facet you already priced this run.

**Without DataForSEO (free mode)** — price the facets on what you CAN measure:
the autocomplete `demand_proxy` from `keywords.py expand`, plus **one** SERP
check per facet on its mid-tail phrase (`serp.py "<phrase>"`). Score each facet
on the authority count and weakness signals its representative query returns.
Say in the report that the facet scoring was unpriced — you measured
competition, not demand.

#### Score each facet on two numbers only

How many of its candidates carry volume inside this site's band AND KD under its
auto-approve ceiling, and the median volume of those. That is the opportunity
available to THIS site today — not the facet's total size, which is a vanity
number a young site cannot spend. (Free mode: substitute "candidates whose
representative SERP shows authority count ≤ 2" and the median demand proxy.)

Then work ONE facet, and **print the table** — facet, candidates in range, median
volume, verdict — in the run report so the owner can see the choice and disagree
with it.

#### Stickiness — do not facet-hop

Twenty guides spread across five facets build authority in none; twenty inside
one build a cluster that lifts every page in it. So look at what the last ~10
queued and published guides were about (step 0 already loaded them): **if that
facet still shows candidates in range, STAY IN IT.** A facet is exhausted when
its in-range count hits zero, not when another looks shinier. Switch only when
the current facet is genuinely dry, or when another scores several times higher
AND the current cluster is still too thin to be worth defending. Say which case
applies.

There is deliberately no stored "current facet" field: the queue and the
published pages ARE that record. They cannot drift from reality.

#### A facet never overrides the per-keyword bar

Facets decide where to HUNT; the product-is-the-answer test and the authority
gate decide what may be QUEUED. The failure mode to watch is widening to a
facet's PARENT topic: "agents that do SEO" is a facet, bare "agents" is not.

**Success criteria**: Every facet was priced from a MULTI-WORD mid-tail phrase, never its head term, and scored on candidates-in-band plus median volume. ONE facet is chosen, and the facet table is PRINTED in the report so the owner can disagree. Free mode says explicitly that the scoring was unpriced.

---

### 2. Derive candidate queries

From that product knowledge, and read the question strictly: **what would someone
google right before this product is the ANSWER** — not what your audience googles
in general.

Start from the problem the product sells the fix to, as the positioning surface
states it, and work outward: setup pains, feature-by-feature questions,
comparisons, error messages, "best X for Y", generator/checker intents. The
subject is the job the product does; the stack it happens to be built on, and the
other tools its users happen to run, are NOT the subject.

**Then strike the off-remit candidates before validating any of them.** Run the
product-is-the-answer test over the whole list — it costs nothing, needs no API
call, and every off-remit keyword you carry into step 3 spends volume, KD and
SERP checks on a page you could not honestly write. **Report the count struck.**

Aim for **40–60 candidates** across both content types — the quota ladder's rung
1 requires 40 to carry real numbers before a run may report a miss at all, so
deriving fewer guarantees a failed run. If a topic argument was given, scope this step to that
topic; otherwise cover the whole product.

#### Intent — hunt buying-adjacent FIRST

Traffic that never converts is the #1 way an SEO program wastes a month: easy
informational queries ("what is X", "X meaning") pile up impressions and zero
revenue. So commercial and comparison intent is where you look first and what
breaks a tie: "X vs Y", "best X for <use case>", "X alternatives", "X pricing",
"how to <job the product does>", "X for <audience>". Roughly half the run's ideas
landing there is a healthy week.

**That is a direction, never a quota, and it NEVER outranks the quality bar.**
The bar decides what MAY be queued; intent only decides what you hunt for and
which of two passing candidates wins. Never stretch the KD ceiling, never soften
the volume floor, and never talk yourself into a me-too page because the keyword
smells commercial. Equally, never pad the queue with informational filler to hit
a number.

**It rises on its own.** The KD ceiling scales with DR, and DR is exactly what
puts the harder commercial terms in range — so the mix should climb over months
with nobody touching this rule. If it is NOT climbing while DR grows, hunt WIDER
(autocomplete phrasings, comparison framings, use-case and audience angles,
long-tails of pages already ranking) — expand the search, never the bar.

Tag each candidate with its intent class (`commercial` | `comparison` |
`informational` | `transactional`). `keywords.py expand` stamps a first guess;
correct it where it is wrong.

#### Sweep for tool-shaped queries as its own pass, every run

Guide candidates crowd out tool candidates when both come from one list, and the
tool queue then sits empty for months. So run a separate, explicit sweep:

```bash
python3 $SEO/keywords.py expand --seed "<facet>" --tools --limit 60
```

Plus the jobs the product's own users do by hand today (config files,
boilerplate, sizing decisions, audits). **Aim for 5–10 tool candidates in every
run**; they carry into the same validation and SERP steps as guides.

**Success criteria**: 40-60 candidates exist across both content types, each tagged with an intent class, with the remit test applied to the whole list BEFORE any validation and the struck count reported. A separate tool sweep produced 5-10 tool candidates.

---

### 3. Validate

Volume + KD for the candidates where available (batch where possible — see 1.5).
Apply the quality bar. In free mode the volume floor and KD ceiling are
inapplicable data gates; the SERP-weakness test and the best-answer test carry
the decision instead, and **you never invent numbers to fill the gap.**

**Success criteria**: Every candidate is judged against the quality bar. In free mode the volume floor and KD ceiling are recorded as inapplicable data gates — never as gates the candidate failed — and no number was invented to fill the gap.

---

### 4. Eyeball the SERP

For the survivors, best candidate first. Check every one of them — there is no
cap, and a survivor left unchecked is unfinished work.

**Preferred for a real run — the SERP daemon.** One call for the whole batch,
real Google, and compact verdicts instead of 25 SERPs of prose in your context
(measured: 25 checks in 37s, 1.4KB out):

```bash
python3 $SEO/serpd.py --start          # once; idempotent
curl -s -X POST localhost:8791/batch -H 'Content-Type: application/json' \
  -d '{"queries":["kw one","kw two","..."],"depth":20,"target":"<your domain>"}'
```

Each verdict carries `authority_candidate_count`, `weakness_signals`,
`strong_serp_weakness`, `relevance_coverage`, `ai_overview` and the top 3
domains. Read the top-3 titles to turn the authority *ceiling* into the real
authority count.

**One-off check:**

```bash
python3 $SEO/serp.py "<keyword>" --count 10 --target-domain <your domain>
```

Read the titles and decide the real authority count. Note the weak spots
(forums, thin listicles, outdated posts) for `--serp-notes`.

Highest fidelity, when the free provider disagrees with itself or the call is
close: `--provider browser` and drive the browser MCP — that is real Google,
including the AI Overview flag and related searches.

**Success criteria**: EVERY survivor has a real authority count from a successful read. There is no cap and no budget: a refused read is retried, and a survivor left unchecked means the run is unfinished. Weak spots are captured for `--serp-notes`.

---

### 5. Persist

```bash
python3 $SEO/seostate.py track --json '[{"keyword":"...","volume":480,"kd":8,"intent":"commercial"}]'

python3 $SEO/seostate.py propose \
  --type guide --title "..." --keyword "..." \
  --volume 480 --kd 8 --authority-count 1 --intent commercial \
  --rationale "remit: we are the answer - <how>. ICP: <the problem at query time>. KD 8 under the DR-0 line; measured authority count 1/10." \
  --serp-notes "authority count 1/10 - two reddit threads, one outdated listicle" \
  --spec '{"angle":"...","outline":["..."],"internal_links":["..."]}'

python3 $SEO/seostate.py update <id> --status approved
```

Follow the queue policies (guides build-first, tools approve-first behind the
project's tool gate). **Every rationale opens with its remit verdict in one
clause** (how the product is the answer to this query, not who the searcher is)
and NAMES the query's intent class. An informational idea says in one clause what
it is doing for the site — feeding a commercial cluster through internal links,
or claiming a term in AI answers. Both are real jobs; this is labelling, not a
tribunal.

Tool ideas include a conversion rationale in `--rationale`, the intended widget
functionality in `--spec`, and an `archetype` field in the spec
(`wizard` | `calculator` | `analyzer` | `library` — see
`references/workflow-build-tool.md`) so the builder knows the intended
interaction pattern up front.

**A site with no public tools page yet is NOT a reason to skip tool ideas.** Some
conventions files say tools are "not wired yet" — that describes the repo on the
day setup ran, not a policy. The build-tool workflow scaffolds the tools home
inside its first PR. Queue the ideas; note in the report that the first build
will create the tools section.

**Success criteria**: Every queued idea carries volume/KD where measured, the authority count, its intent class, and a rationale OPENING with the remit verdict. Tool ideas carry a conversion rationale and an `archetype`. A site with no tools page yet still gets tool ideas queued.

---

## Weekly quota — the queue guarantee

The consumers run at the site's own pace: the guide builder ships at most **ONE
guide per day** (up to 7/week) and the tool builder **ONE approved tool per
week**. That cadence is a PROMISE to the owner, and this run is the only thing
that fills the tank — so the run may not end short while ladder rungs remain.

**Target: end the run with 7 approved guides and 1–2 tool ideas in the queue.**
Count what is already queued first (`suggestions --status approved pending`) and
top up the DIFFERENCE — never overfill past ~9 approved guides or ~2 queued
tools.

### The tool slot is not optional — it is half the promise

Every run ends with **1–2 tool ideas queued** (top up to 2; skip only when 2 are
already waiting). A run that queues zero tools while the tool queue is empty is a
FAILED run, however good its guides were. Exactly two excuses are banned outright:

- *"The repo has no tools surface / conventions says tools are not wired"* — the
  first tool build creates it. Queue anyway.
- *"No perfect tool keyword turned up"* — that is rung 1, not a verdict: re-run
  the tool-shaped sweep wider before concluding it. A product whose users
  configure, calculate, or check ANYTHING by hand has a tool in it.

The quality bar still decides WHICH tool ideas pass — a fake widget queued to hit
the number is worse than an honest miss. If after a genuine wider sweep nothing
clears the bar, say so explicitly ("0 tools queued — N tool candidates validated,
none cleared the bar because X"), so the miss is visible instead of silent.

### Auto mode fills the tank; semi mode fills the backlog

Read `mode` from `seostate.py config`.

**Auto** (hands-off publishing): pursue the weekly target HARD but through
**rung 1 only** — hunt wider until 7 keywords clear the bar on their own merits.

Two things are forbidden on an Auto project, and they pull in opposite
directions — hold both lines at once:

- **Never close the gap by handing the owner work.** Ending a run with pending
  ideas the owner is expected to approve is the "why do I still have to add these
  myself?" bug the mode exists to prevent.
- **Never close the gap by approving what the bar rejects.** A queue of seven
  pages that land past position 50 is worse than three that rank.

So when the honest yield is 4, queue 4, hold the rest, and report it plainly:
"4 of 7 queued — ladder worked to rung N, 3 held above the KD ceiling". **On a
young site, fewer winnable guides IS the product.** The gap then closes by
itself, from two directions: step 0.5 releases held ideas as DR lifts the
ceiling, and the facet measurement in step 1.5 moves the hunt to a less crowded
space.

**Semi**: approve only the confident auto-approve-zone winners and leave the rest
pending — the state layer coerces your extra approvals to pending anyway, so just
list them in the run report.

---

## The ladder — work it IN ORDER, exhaust a rung before descending

### Rung 1 — hunt wider. This rung does the work, and it almost never runs dry.

The candidate floor is a rule, not advice: **you may NOT conclude the niche is
exhausted before at least 40 candidates carry real numbers in this run.** If 7
guides are not yet queued at 40, keep going.

A young site's winnable keywords are almost never the ones a head-term sweep
surfaces. Work these seams in order — each reliably yields queries that clear the
authority gate because nobody bothered to target them:

1. **Queries you already appear for.** The single best free seam — proven
   relevant, already half-ranked, free. **Mine it FIRST every run.**

   ⚠ **Mine the engine that actually sends the traffic, and CHECK which one that
   is rather than assuming Google.** On a site with a verified Bing property,
   `bing.py traffic` answers it in one call, and the answer is not always the
   obvious one: measured on combatskirmish.net 2026-09-01, Bing delivered
   **57,596 impressions / 4,300 clicks** in 29 days against Search Console's
   **545 / 43** for the same window — about 100x — after this program had spent
   its entire history reading GSC. Every outcome steer it had derived described
   about 1% of the site's search traffic.

   ```bash
   # Google
   python3 $SEO/keywords.py gsc gsc-queries.json --band striking-distance page3-5
   # Bing - real impressions AND real positions, free, for a verified property
   python3 $SEO/bing.py queries --limit 400 > bing-queries.json
   python3 $SEO/keywords.py bing bing-queries.json --band striking-distance page1
   ```

   **Read `by_script` before anything else.** A blended CTR hides a language
   segment that may be carrying the site: on the same run, Chinese queries were
   15% of queries and **73% of all clicks**, converting at 30.3% against 2.5%
   for everything else — invisible in every aggregate the program had looked at.

   And read `ctr_underperformers` as its own class: a query ranking top-10 with
   real impressions and under 2% CTR is a **title and snippet** problem, not a
   ranking problem, and no amount of new content fixes it.
2. **Error strings and failure modes.** Verbatim messages, "X not working",
   "why does X", "X keeps <failing>". Almost always thin SERPs dominated by forum
   threads, and dead-on intent.
   ```bash
   python3 $SEO/keywords.py expand --seed "<product>" --groups problem
   ```
3. **Version and release deltas.** Anything the product's ecosystem shipped
   recently: new features, renamed flags, changed defaults, deprecations.
   Fast-moving spaces mint winnable queries every week and incumbents are slow.
4. **Integration pairs.** "X with Y", "X + Y", "using X in Y" across every tool
   the product's audience already runs alongside it.
5. **Qualified long-tails of head terms you CANNOT win.** A head term that failed
   the authority gate is still a seed: add a use case, an audience, a constraint,
   a version, a platform ("X for solo devs", "X without Y", "X on Windows"). The
   qualified variant routinely clears the gate the bare term failed.
   ```bash
   python3 $SEO/keywords.py expand --seed "<head term>" --groups audience constraint
   ```
6. **Trend radar.** `seostate.py trends --status new` — fresh topics have the
   thinnest SERPs on the internet.
7. **Cross-engine agreement.** The same sweep against six independent suggestion
   corpora instead of one:
   ```bash
   python3 $SEO/keywords.py expand --seed "<facet>" --engines all --sort agreement
   ```
   A phrase surfaced by Google, Bing AND DuckDuckGo is corroborated by three
   different audiences and three different algorithms — which is a far better
   reason to spend a build slot than "it ranked first in one autocomplete list".
   It also returns `intent_evidence`: `video` when YouTube's corpus surfaced the
   phrase, `product` when Amazon's did. That is **observed** intent, and when it
   disagrees with the guessed `intent` field, believe the observation — a phrase
   Amazon suggests is someone about to buy something, whatever its wording.

   ⚠ `engine_agreement` is still **ordinal corroboration, not a volume**, and it
   never satisfies the volume floor. ⚠ Check `engines_silent` before reading the
   scores: an engine that answered nothing all sweep is a dead instrument, and
   agreement is scored only against the ones that answered.

**Coming back short while any of these seams is unworked is a FAILED run, not an
honest one.** Name in the report which seams you mined.

### Rung 2 — promote pending-zone survivors. CLOSED while DR < 20.

On a site with DR-equivalent under 20, **this rung does not exist**: do NOT
promote pending-zone ideas to hit the quota, ever. A young site has no authority
to spend on a keyword that already needed an exception, and every such page lands
past position 50, burns a build slot, and adds a page the owner has to look at.
Skip straight to rung 3. (The evidence: promoted-to-hit-cadence pages are
reliably the worst performers in step 0's outcome data.)

At **DR 20+** the rung opens: pending-zone ideas from `source: research` passed
the FULL quality bar; only the auto-approve KD line kept them out. Approve the
best — audience fit first, then lowest KD — until the target is met, prefixing the
rationale with "AUTO-PROMOTED to keep the daily cadence". Never promote `manual`
ideas or anything the owner rejected.

### Rung 3 — reach into the volume band's soft edge, dead-on ICP only

Candidates just OUTSIDE the band qualify WHEN the searcher is unmistakably the
product's buyer. **Reach DOWNWARD first and by default**: below the floor is where
a young site's winnable queries live, and a 120-volume query owned at position 3
compounds. Reaching UPWARD is governed by the authority gate, not by this rung.
KD zones and the authority gate never move. Low-volume + perfect fit beats
high-volume + tangential — never the reverse.

### Rung 4 — mine the radar

Trend topics already on the radar are candidate sources too — derive guide angles
and validate normally.

### Rung 5 — a genuinely exhausted niche, and nothing else

You reach this rung only when **every** rung-1 seam has been worked to the point
where it returns nothing new, and every candidate that passed the cheap filters
has an actual authority count against its name. There is no other way out of the
ladder.

**These are NOT reasons to stop, and none of them may appear in a run report:**

- *"SERP budget spent"* — there is no budget. Keep checking.
- *"N candidates passed the cheap filters but never got checked"* — then check
  them. An unchecked survivor is unfinished work, not a finding.
- *"the provider was throttled"* — the daemon self-heals and `serp.py` fails over;
  re-run it, and if it still refuses, restart the daemon
  (`serpd.py --stop --force && serpd.py --start`) and re-run again.
- *"carries to the next run"* — nothing carries. The run finishes its own work.
- *"running low on time/context"* — say so to the owner and continue; do not
  silently convert it into a short queue.

If, after all that, the honest yield is still under quota, state it plainly:
"quota missed — N of 7 guides queued; ladder exhausted at rung 5", and name every
seam you mined **and what each returned**. A miss with fewer than 40 validated
candidates, or with any rung-1 seam unworked, or with any survivor still
unchecked, is a **failed run**, not an honest one.

**The bar still never bends to close the gap.** Queueing something page 1 says you
cannot win is worse than queueing less — see Precedence below. The way to close a
gap is always more hunting, never a lower bar.

A repeated miss is a signal to escalate, not a new normal: if two consecutive
runs come up short with the full ladder worked, say so and name what would
unblock it (a wider topic remit, more product surface, backlinks lifting the DR
ceiling).

---

## Precedence — one rule, no exceptions

**The bar decides what may be queued; the quota decides how many; intent decides
which ones you chase and which of two passers wins.**

The quality bar itself NEVER bends — not for the quota, not for the intent mix,
not for a keyword you like. Rung 3's soft edge is the bar's own written
exception, not a bend. The daily builder idling beats filler shipping — but on a
well-worked run it should never come to that.

---

## Output

Open with the **outcome steer from step 0** in one or two lines — what the
already-published pages show about which targeting is working on this domain, and
how it shaped this run's picks (or "no outcome data yet — first runs").

State how many candidates the **remit test** struck before validation and name
the pattern they shared ("struck 14 off-remit — all of them questions about tools
our readers use alongside us, not about the job we do").

Then the **facet table** from step 1.5 (facet, candidates in range, median
volume, verdict).

Then two markdown tables:

**(a) Keyword opportunities** — keyword, volume, KD, **authority count**, intent,
type, angle.

**(b) Recommended tools / interactive pages** — idea, target keyword, why it
converts, status. **Table (b) is never empty-by-omission**: list the tool
candidates you swept even when none was queued, with the reason each was dropped.

Then a one-line summary of what was queued, the quota status for BOTH queues, and
the HONEST intent mix — never a forced one — e.g. "queue now holds 7 approved
guides and 2 tool ideas; 3 of 7 commercial, the rest were the only keywords under
the KD ceiling this week".

Finally:

```bash
python3 $SEO/seostate.py log-run --workflow research --summary "<one line>"
```
