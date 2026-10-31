# Workflow: build-tool (tools ONLY)

**Cadence:** weekly, or on approval. **Job:** build the approved tool at the top
of the queue into a merge-ready PR.

The pipeline is **PICK → SURFACE → TEMPLATE → GATE → THEME → PLAN → BUILD →
HUMANIZER → VERIFY → PR.**

This workflow only ever builds a suggestion that is **already APPROVED** — by the
owner, or auto-approved on projects with `auto_approve_tools`. **It must never
build guides.**

Two ideas govern every step:

- **The PAGE SHELL is invariant**: large centered title, one value line, the tool
  itself, CTA, description copy, FAQ — every tool page reads the same way.
- **The TOOL INSIDE is bespoke**: designed from this keyword's search intent with
  its own execution plan and real domain logic, styled in the host site's own
  theme.

**Copying the reference widget and swapping words and buttons is a build failure,
not a shortcut.**

---

## Owner content preferences — read them FIRST

```bash
python3 $SEO/seostate.py prefs
```

The `tool_note` it returns is a set of instructions this workflow **OBEYS**:
shapes the owner removed from the rotation, skeleton blocks they turned off, and
free-text house rules to follow verbatim.

They are preferences, not a licence: **they never override the quality bar.** An
owner can tell you to skip the FAQ; they cannot tell you to ship a page that
fails the thin-content gate. If a preference and a gate genuinely collide, the
gate wins and you say so in the run report.

If nothing has been customised the note says so, and the defaults below apply
unchanged.

---

## 1. PICK

```bash
python3 $SEO/seostate.py suggestions --status approved --type tool
```

The list comes back in BUILD ORDER, so **take the FIRST item**. None → say "queue
empty" and stop cleanly.

An idea with `source: manual` was typed by the owner and may be just a title —
derive the search intent and functionality from the keyword and the PLAN step as
usual; a thin brief is not a reason to skip.

```bash
python3 $SEO/seostate.py update <id> --status in_progress
```

**Success criteria**: Exactly one idea — the FIRST in build order — is claimed as `in_progress`, or the run reported "queue empty" and stopped cleanly. A thin `source: manual` brief is built, not skipped.

---

## 2. SURFACE CHECK — build the tools home if the site has none

Read the conventions file's **Tools** section first. If it names no public tool
surface, points at a route that turns out to be behind the product's login, or
says tools are "not wired yet" / "do not build tool suggestions": **that is a repo
that finished setup before it had a tools section, NOT a stop order.**

Do not exit and do not park the suggestion — scaffold the surface as the first
commits of THIS PR, then build the tool into it:

- a **registry** module (one entry per tool: slug, title, h1, value line, meta
  description, description copy, FAQ items, widget reference), an **index page**
  listing entries as cards, and a **detail template** rendering the locked funnel
  below — all in the site's own stack and design tokens, discovered per the THEME
  step;
- served from a **PUBLIC path** with sitemap coverage and excluded from any auth
  gate. If the obvious name is taken by the product's own app (a `/tools`
  dashboard screen is the classic case), publish at a sibling public path —
  `/free-tools/<slug>` is the safe default — and **never move or rename the
  owner's app routes**;
- verify like a logged-out stranger: build, serve, request the new tool page with
  no cookies, expect **200 with the widget rendered**;
- update `.seo/conventions.md`'s Tools section in the same PR with the real base
  path, registry path, wiring steps, and this tool as the reference
  implementation. **Leaving the stale "not wired" text behind means the next build
  scaffolds a second tools system.**

Say plainly in the run report and PR body that this PR also created the site's
tools section. Every later build skips this step.

**Success criteria**: A PUBLIC tools surface exists, verified logged-out (200 with the widget rendered, no cookies). If it was scaffolded here, `.seo/conventions.md`'s Tools section is updated IN THE SAME PR with the real base path, wiring steps and this tool as the reference — leaving the stale "not wired" text makes the next build scaffold a second tools system. No app route was moved or renamed.

---

## 3. TEMPLATE

The living template is the reference tool named in the conventions file. Before
writing anything, read COMPLETELY: its registry entry (field by field), its widget
component, and the page template that renders the shell.

**The funnel is LOCKED:**

> hero (tool name as a LARGE CENTERED title + one value line stating what the user
> walks away with) → the widget itself (the product, immediately usable, no scroll
> hunting) → CTA to the paid product → description copy → FAQ

The build's job is a registry entry and a widget that match the reference's
quality bar. On a site whose tools home was just scaffolded in step 2 there is no
reference tool to read — the locked funnel IS the spec, and this build becomes the
reference every later one studies, so hold it to that bar.

### The widget's interaction pattern is chosen BY ARCHETYPE

Classify first, then apply that archetype's locked pattern. The researcher stamps
an `archetype` in the spec — **confirm it, do not blindly trust it.**

| Archetype | When | Locked pattern |
|---|---|---|
| **Configurator / wizard** | 3+ decisions that build an artifact | Stepped wizard — ONE decision per screen, single-select auto-advances on click, multi-select keeps an explicit Next, Back always preserves answers, a thin progress bar as the only chrome, focus moves to the new screen's heading on step change, an escape hatch per question ("I don't know" / skip still yields a good result), then a results screen as the payoff (output + copy/download actions). **NEVER a long scrolling form of stacked sections.** |
| **Calculator / converter** | input(s) → computed output, no real branching | One focused card, result computed **LIVE as the user types** — zero steps, no submit button, the output pane always visible. |
| **Analyzer / checker** | paste something in, get findings back | One large paste box + a single Analyze action → a findings view (issues listed with severity and a fix each); analysis stays **client-side**. |
| **Library / directory** | a browsable collection | Grid + filter chips + search. |

The conventions file names the shipped reference per archetype where one exists;
once a new archetype's first implementation merges, THAT becomes the living
reference. If a tool genuinely fits no archetype, pick the nearest pattern and
**STATE THE DEVIATION and why in the PR body** — a mismatch must be visible, never
silent.

**Success criteria**: The reference tool's registry entry, widget and page template have been read completely, and the archetype from the spec is CONFIRMED rather than trusted. Any deviation from an archetype's locked pattern is stated in the PR body — never left silent.

---

## 4. THIN-CONTENT GATE

```bash
python3 $SEO/serp.py "<primary keyword>" --count 10
```

The planned tool must be the definitive **INTERACTIVE** answer on page 1 — if a
competitor tool exists, list concretely what ours does better (polish,
completeness, presets, zero-login). If it cannot clearly win, **DO NOT BUILD**:
`update <id> --status pending`, state why, stop.

> **Free mode — no SERP source connected**: do NOT stall or refuse; judge
> winnability from the tool idea itself and product knowledge, and write "SERP
> gate: skipped (free mode — no SERP source)" in the run report AND the PR body.
> **Never fabricate SERP claims you did not fetch.**

**Success criteria**: The tool can be the definitive INTERACTIVE answer on page 1, with a concrete list of what it does better than any existing competitor tool. If it cannot clearly win, the row goes back to `pending` and nothing is built. Free mode records the skipped gate in both the report and the PR body, and fabricates no SERP claims.

---

## 5. THEME — know the host site before styling anything

The tool must look native to the site it ships on, and the site's design system is
read **fresh each build** — never assumed from memory or carried over from another
site:

- Read the theme-token source the conventions file names: every token name,
  colour, font variable, and radius. **These are the ONLY colours and fonts the
  widget may use.**
- Read the site's landing page and one live tool page end to end for the design
  language: heading scale, spacing rhythm, card idiom, button shapes, microtype
  patterns, how accents are used sparingly.
- Skim one existing widget component for the code idiom (state shape, button/copy
  action patterns, focus handling).

Write a short **THEME BRIEF** (5–8 lines: tokens, type scale, card and button
idiom, label style, anything unusual) and keep every visual decision in the widget
traceable to it. Nothing from another site's palette or idiom ever leaks across
sites.

**Success criteria**: The theme-token source was read FRESH this build, and the widget uses only those tokens — no colour, font or radius from memory or from another site.

---

## 6. EXECUTION PLAN — mandatory, design the tool, never re-skin the template

The template gives every tool the same shell; **this step is where the actual tool
gets designed, in writing, BEFORE any code:**

- **Search intent.** Who types this keyword, what job they are trying to finish,
  and what the perfect tool hands them at the end. Specific, never generic.
- **Core transformation.** Input → output in one line. The output must be
  something the user takes away and uses: a file, a number, a verdict with fixes,
  a filtered pick. **If the output restates the input or barely changes across
  inputs, the tool is fake — redesign now.**
- **Domain logic inventory.** The real knowledge the widget encodes: validation
  rules, mappings, presets, reference data, edge-case handling. Source every
  factual item from current official docs or the product surface (fetch them —
  never from memory, respecting the trusted-sources rule), and test every command
  or config the tool emits. **This inventory is what makes the tool worth
  existing.**
- **Interaction design.** Apply the confirmed archetype's locked pattern to THIS
  tool's actual decisions: the exact steps or inputs, what auto-advances, what the
  results screen shows, the escape hatches.
- **States and edge cases.** Empty input, invalid input, extreme values,
  copy/download actions, mobile width.
- **Win statement.** What this tool does that page 1 cannot (ties back to the gate
  verdict).

### Then hold the plan against the VALUE BAR — all four must hold, or do not build

1. the widget does **real work** (computes, generates, validates, analyzes,
   filters) that a paragraph of prose could not replace;
2. meaningfully different inputs produce **meaningfully different outputs**;
3. the domain logic inventory has **sourced, tested substance**;
4. the owner would honestly **bookmark this** if a stranger shipped it.

Failing the bar means a **redesign loop, not a build ticket.** If after rework the
keyword honestly cannot support a real tool, do not fake one: `update <id>
--status pending`, state why, stop.

**Named anti-patterns, all value-bar failures:**
- the reference widget with relabeled steps and buttons;
- a wizard whose every path ends in near-identical canned output;
- a "checker" that trivially pattern-matches and calls it analysis;
- static content wearing an input box as decoration.

**Success criteria**: A written plan exists covering search intent, core transformation, a SOURCED and TESTED domain-logic inventory, interaction design, states/edge cases and a win statement — before any code. All four value-bar conditions hold. Failing the bar triggers a redesign loop, never a build; if the keyword cannot support a real tool the row goes back to `pending`.

---

## 7. BUILD

Implement the plan per the site's tool-shipping steps in the conventions file
(registry entry, widget component, wiring). The widget must be:

- **purely client-side** (no backend calls),
- **resilient** (no interaction may throw),
- its **primary action obvious**.

**Universal microcopy rule: plain English, meaning first** — every option label
leads with what it does FOR the user; the exact command or mechanism goes in
parentheses or the hint. The bar: a beginner understands every option without
googling, AND a power user still sees exactly what gets written.

Analytics only for real interactive milestones, following the conventions file's
event-naming rule.

**Success criteria**: The widget is purely client-side, no interaction path throws, the primary action is obvious, and every option label leads with what it does for the user.

---

## 8. HUMANIZER — mandatory

Apply the humanizer pass to **ALL registry copy**: title, h1, meta description,
summary, every description paragraph, every FAQ answer. The copy is the ranking
surface — it must read like the owner wrote it.

**Information gain:** the description and FAQ must carry at least one concrete,
tool-specific fact or worked example — a real value the tool computes on a named
input, a specific supported case, a tested edge — **never generic "this free tool
helps you…" filler that would fit any tool.** That specificity is what ranks the
page and earns AI-answer citations.

Registry description copy weaves in 2–3 contextual internal links (at least one
sibling tool, one related guide) with natural keyword-bearing anchors,
root-relative only — and **never inside FAQ answers if those mirror into
structured data as plain text.**

**Success criteria**: All registry copy has had the humanizer pass, and the description and FAQ carry at least one concrete tool-specific fact or worked example — never generic filler that would fit any tool. Internal links are root-relative and stay out of FAQ answers that mirror into structured data.

---

## 9. VERIFY

The site's build command must pass. **Trace every widget interaction path in the
code once more** — a broken interaction blocks the merge.

Then check the composed page against the locked funnel: large centered title,
value line, widget, CTA, description, FAQ, all present **in that order**, and no
class or colour in the widget outside the THEME brief.

**Success criteria**: The build passes, every widget interaction path was traced in code, and the composed page matches the locked funnel in ORDER with no class or colour outside the theme brief.

---

## 10. PR — never main

```bash
git checkout -b seo/<slug> && git add -A && git commit -m "..." && git push -u origin seo/<slug>
gh pr create --label seo --label seo-tool --title "..." --body "..."
```

Create missing labels. Body includes: target keyword, volume/KD, the conversion
rationale, gate verdict, the execution plan (search intent, core transformation,
domain logic inventory, win statement), and what a validator should exercise (the
widget's primary flow).

**If `gh pr create` fails, that is a FAILED run — never exit green.** Same rule
and same usual cause as the guide builder: revert the suggestion to `approved`,
print the exact error plus the *"Allow GitHub Actions to create and approve pull
requests"* setting name, and exit non-zero.

**Success criteria**: A PR exists with both `seo` and `seo-tool` labels and a body carrying the keyword, volume/KD, conversion rationale, gate verdict, the execution plan and what a validator should exercise. **A failed `gh pr create` is a FAILED run**: revert to `approved`, print the error and the repo setting, exit NON-ZERO.

---

## 11. RECORD

```bash
python3 $SEO/seostate.py update <id> --status done --pr-url <pr url>
python3 $SEO/seostate.py log-page --url "https://<domain>/<path>" \
  --title "..." --type tool --keyword "<primary keyword>" --pr-url <pr url>
python3 $SEO/seostate.py log-run --workflow build-tool --summary "<one line>"
```

**Success criteria**: The suggestion is `done` with its PR url, the page is logged as `type: tool`, and the run is logged.

---

## 12. VALIDATION — why tool PRs are never auto-merged [human]

A tool PR ships **LLM-authored interactive code that will run in your visitors'
browsers**. Guides are prose; a widget is a program. So:

- Tool PRs carry the `seo-tool` label and the auto-merge workflow skips them by
  design, whatever the publish-paths say.
- Before merging, a human (or a separate validation run) should exercise the
  widget's primary flow end to end: the happy path, empty input, invalid input,
  an extreme value, the copy/download action, and mobile width.
- The PR body already names what to exercise (step 10) - that list exists for
  this step.

If you want this automated, run the validation in a job that holds **no
secrets**: it executes PR-authored code, so a token in its environment is a
token handed to whatever that code decides to do.

**Success criteria**: The `seo-tool` label is present so auto-merge skips the PR, and the body names the flows a human must exercise. Any automated validation runs in a job holding NO secrets — it executes PR-authored code.

---

## 13. REPORT

What was built, the PR link, the gate verdict, the one-line core transformation,
and what to check on the preview.

**Success criteria**: The report names what was built, the PR link, the gate verdict, the one-line core transformation, and what to check on the preview.
