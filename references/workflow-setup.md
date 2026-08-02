# Workflow: setup (one-time)

**Job:** find or create the site's content home and tools home, write
`.seo/conventions.md` — the site-facts file every other workflow adapts from —
and fill the directory profile the backlink playbook personalizes from.

Runs once when a project first connects, and again whenever the stack or
positioning changes.

Six parts, in order. **Do all six.**

---

## Part 0 — create the project

```bash
python3 $SEO/seostate.py init \
  --name "<Site name>" --domain example.com \
  --repo owner/repo --mode semi --serp-provider ddg
```

`--mode`:
- **`semi`** — the agent proposes, the owner approves. Pending IS the product.
  The safe default.
- **`auto`** — hands-off. The agent decides every idea and the owner reviews
  finished PRs. Nothing is ever parked.

Then measure the site's authority, because the entire quality bar scales off it:

```bash
python3 $SEO/authority.py --domain example.com --save
```

Read `references/data-sources.md` if you want a real link-graph number instead of
the keyless estimate (Open PageRank is free and takes two minutes).

**Success criteria**: `seostate.py init` succeeded and `authority.py --save` recorded a `dr` that is not null. The quality bar scales off that number, so a null one blocks every later gate.

---

## Part 1 — find or create the content home

Published guides need somewhere to live. Ask the owner: *"does the site have a
blog or content section?"* — and treat the answer as **a hint, not truth**. Owners
misremember their own repos.

1. **Detect.** Inspect the repo for ANY existing content surface, whatever its
   name: markdown/MDX content directories, CMS configs, route folders matching
   `/blog`, `/articles`, `/guides`, `/resources`, `/learn`, `/news`, `/posts`,
   sitemap entries, RSS feeds.
2. **Reconcile — the repo wins:**
   - Found a content surface (even if the owner said there is none): **use it.**
     Say what you found and where you will publish.
   - The owner named a path but nothing is there: check the rest of the repo
     before concluding; if the repo genuinely has none, say so and scaffold.
   - Nothing found: **scaffold a minimal content section in the site's OWN
     stack**, as its own PR, separate from any other change: a content directory,
     a list page, a detail template, and sitemap coverage — reusing the site's
     existing layout, design tokens, and metadata patterns. Minimal and native: a
     folder and two templates, not a themed blog that fights the site's design.

     The detail template MUST include the **reading rail** the guide builder
     counts on (build-guide forbids in-article CTAs precisely because
     "rails/end-CTAs render automatically" from the template): a desktop sidebar —
     sticky, hidden on mobile — with an "On this page" ToC built from the
     article's H2s, plus a small product promo card (one-line pitch + link)
     beneath it, all in the site's own tokens.

     **Scroll-test the built page before opening the PR:** the rail must stay
     pinned while the article scrolls past it. A rail that scrolls away with the
     page is a broken implementation, not a style choice (classic cause: an
     `items-start` grid collapsing the aside to content height, leaving its sticky
     child no room to pin).

     The list page must be **presentable, not a bare link list**: post cards with
     title, description, date, and reading time, a clear hover state, and a header
     that ties back to the product — it will carry 20+ posts within months and is
     many visitors' first page.

     On an EXISTING content surface, leave its chrome alone — the owner's design
     wins; just record in conventions.md whether a ToC/CTA surface exists.
3. **HARD RULE — never create a second content system.** If any content surface
   exists under any name, extend it. Scaffold only when the repo genuinely has
   none, and state in the PR description which case applied ("found /resources,
   publishing there" or "no content surface found, scaffolded /blog").
4. **HARD RULE — the content home must be PUBLIC, never behind a login.**

   Many apps guard every route with an auth gate (a `middleware.ts` that redirects
   to `/login`, route-group guards, host rules). A blog inside that wall **fails
   silently**: the build passes, guides publish, and every page bounces visitors
   AND Googlebot to the login screen — zero indexing, zero traffic, and nobody
   notices until Search Console shows nothing weeks later.

   So, whether you found the content home or scaffolded it:
   - If the repo has any auth gate, **explicitly exclude the content routes from
     it** (middleware matcher, public route list — whatever the repo's mechanism
     is).
   - **Verify like a logged-out stranger**: build and serve the site, request a
     content page with NO cookies (`curl` is enough) — it must answer **200 with
     the article**, not a redirect — and confirm the sitemap lists it.
   - A product that is entirely a login-walled dashboard is fine: the content home
     simply becomes the site's first public surface. Say so in the PR description.
5. If you scaffolded, downstream workflows can only publish once that PR merges —
   **tell the owner merging it is what makes the site publishable.**

**Success criteria**: A content home exists, is PUBLIC (verified with a cookie-less request returning 200 with the article, not a redirect), and is listed in the sitemap. If it was scaffolded, the detail template carries a ToC rail that stays pinned under a scroll test, and the owner has been told that merging that PR is what makes the site publishable.

**Human checkpoint**: ask the owner whether a content section exists — then treat the answer as a hint and let the repo win.

---

## Part 2 — find or create the tools home

Free interactive tools are the other half of what this pipeline publishes, and
they convert better than guides. The weekly research run queues tool ideas for
EVERY project, so **every project needs somewhere for them to land.**

1. **Detect a PUBLIC tool surface**, whatever it is called: routes like `/tools`,
   `/free-tools`, `/calculators`, `/generators`, `/utilities`, a tools registry or
   config, existing widget/calculator pages.
2. **The login trap — check who the surface serves.** A `/tools` route that
   belongs to the product's own logged-in app or dashboard is **NOT a tools
   home**: it is a product screen behind auth, and treating it as one silently
   blocks tool publishing forever. Same test as the content home — would a
   logged-out stranger get the page? If the obvious name is taken by an app route,
   publish under a sibling public path (`/free-tools` is the safe default) and say
   so; **never rename or move the owner's app routes.**
3. **Scaffold when none exists**, in the site's OWN stack and tokens, as its own
   PR (or folded into the content-home PR — state which):
   - a **registry** module — one entry per tool carrying at minimum slug, title,
     h1, one-line value statement, meta description, description copy, FAQ items,
     and a reference to its widget component. **It ships EMPTY** (no placeholder or
     demo tool — a fake tool is worse than none);
   - an **index page** listing the registry's tools as cards (name, one line,
     link), presentable with zero entries ("first tools coming");
   - a **detail template** rendering the locked funnel the tool builder writes
     against, in this order: large centered title → one value line → the widget
     itself → CTA to the product → description copy → FAQ;
   - **sitemap coverage** for the index and every registry entry, and the same
     public-route rule as Part 1 (excluded from any auth gate, verified logged-out
     with no cookies: 200 with the page, not a redirect).

   Keep it minimal: a registry, a list, a template. Not a themed showcase.
4. **Never create a second tools system.** If any public tool surface exists,
   extend it and record its wiring instead.
5. Record the result for Part 3: the public base path (e.g.
   `/free-tools/<slug>`), the registry path, the widget directory, and the exact
   steps to ship one tool. If you scaffolded, tell the owner that PR is what makes
   tools publishable.

**Success criteria**: A PUBLIC tools home exists with an EMPTY registry (no placeholder tool), an index page presentable at zero entries, a detail template rendering the locked funnel, and sitemap coverage — all verified logged-out. No app route was renamed or moved. The base path, registry path, widget directory and ship-one-tool steps are recorded for Part 3.

---

## Part 3 — write `.seo/conventions.md`

Inspect THIS repo and write the site-facts file every other workflow depends on.
**Discover, never assume**: read the actual files, run the actual build command
once to confirm it. Keep it factual and terse — it is a reference card, not prose.
**Every claim must come from a file you actually read (cite the path inline).**

Write it with:

```bash
python3 $SEO/seostate.py conventions --write conventions-draft.md

**Success criteria**: All seven sections are written, every claim cites a path actually read, and the build command was RUN once to confirm it. The product surface lists BOTH positioning and capability files — a list of only architecture/source files is a failed Part 3. Facets are 3-6 things the product DOES. The Tools section contains no stand-down line.
# or pipe it:  cat draft.md | python3 $SEO/seostate.py conventions --write -
```

The file must contain these sections:

### 1. Product

What the site is, who it serves, and above all **the PROBLEM it sells the fix
to**, in the owner's own words. Then the product-surface files a researcher should
read fresh each run, split into two groups and **BOTH required**:

- **Positioning** — the marketing surface: landing/home page copy, README,
  pricing page, docs introduction. This is what the site is ABOUT, and the
  research run derives its topic remit from it.
- **Capability** — the docs/config/content files describing what the product does,
  feature by feature.

List concrete paths under each.

> **A product-surface list containing only architecture and source files is a
> FAILED Part 3.** Research that reads only those derives keywords about how the
> product is BUILT instead of what it is FOR, and those pages rank for engineering
> questions and sell nothing. If the repo genuinely has no marketing surface, say
> so and point at the closest thing (README intro, docs landing) rather than
> omitting the group.

Then, from the positioning surface, write the site's **FACETS**: 3–6 honest
descriptions of the job this product does, most direct first, each one a subject
people search. A worked example for an SEO-automation product: *"SEO automation"
/ "agents that do real work unattended" / "content publishing pipelines" /
"rank tracking and Search Console without a subscription"*.

The research run measures these against the site's current authority every week
and spends the week on whichever is actually winnable, so a product whose most
obvious market is saturated is not trapped in it.

Two rules keep this from becoming a licence to write about anything:

- A facet is what the product **does** — never what it is built with, and never a
  tool its audience happens to use alongside it. *"Runs on Vercel"* is not a
  facet. *"Works with Claude Code"* is not a facet.
- A facet must be narrow enough that the product is a plausible **ANSWER** to
  searches inside it. *"Agents that do SEO"* qualifies; bare *"agents"* does not.

### 2. Stack & build

Framework and content system (e.g. Next.js + MDX, Astro, Hugo), package manager,
the exact **build/verify command**, and any CI validators that gate merges.

### 3. Guides

Where article files live, the metadata/frontmatter contract (required + optional
fields, length limits), slug conventions, what the platform renders automatically
(sitemap, RSS, OG images, related posts, JSON-LD), which structured data the stack
emits (e.g. FAQPage from frontmatter), internal-link style, and **2–3 exemplar
posts** to read before drafting.

### 4. Tools

The public base path tool pages are served at (e.g. `/free-tools/<slug>`), where
the registry and widget components live, the registry/wiring steps to ship one,
the reference implementation to read completely (write *"none yet — first build
sets the reference"* when the registry is empty), and what the page template
renders automatically.

> **HARD RULE — this section may never tell downstream workflows to stand down.**
> Never write "tools are not wired", "do not queue tool ideas", or "do not approve
> or build tool suggestions". The research run queues tool ideas every week for
> every project and the tool builder scaffolds whatever is still missing on its
> first PR, so a stand-down line here does not pause tools — **it silently kills
> them**, and the owner just sees an empty tool queue for months. If Part 2 could
> not finish (an unfamiliar stack, an ambiguous repo), write what IS true — the
> intended base path and the concrete wiring steps the first tool build must
> perform — and name the open question.

### 5. Design system

Where the theme tokens live (e.g. the `globals.css` `@theme` block), the token
names, card/button/label idioms, the icon language, where brand logomarks live,
and **2–3 exemplar visual components**.

### 6. Voice & writing rules

Author attribution, first-person or not, punctuation rules (e.g. dash bans), the
humanizer skill path if the repo carries one, and anything else that makes copy
read like the owner wrote it.

### 7. Analytics

The tracking helper and event-naming convention, if any.

---

## Part 4 — personalize the site profile

Fills the profile the backlink playbook personalizes from — every directory
submission's prefilled copy uses it.

1. **Read the product surface fresh** (the files you just listed in Part 3).
   Enough to describe the product accurately — do not draft from memory.
2. Write the profile, respecting the length contracts (**directories enforce
   them**):
   - `name`
   - `url`
   - `tagline` ≤ 60 chars
   - `short_description` ≤ 160 chars
   - `long_description` 300–600 chars
   - `categories` (1–5 real directory categories, e.g. "Developer Tools", "AI",
     "Productivity")
   - `tags` (1–10, lowercase-kebab)
3. **Copy quality bar:** plain English, first-person-free, concrete (what the
   buyer gets), **zero hype words** — "revolutionary", "game-changing" get
   listings rejected. Follow the writing rules from Part 3.
4. Save it:
   ```bash
   python3 $SEO/seostate.py profile --json '{
     "name":"...","url":"https://...","tagline":"...",
     "short_description":"...","long_description":"...",
     "categories":["Developer Tools","AI"],"tags":["seo","automation"]
   }'
   ```
   The command **rejects** copy that breaks a length contract rather than saving
   something a directory will bounce.
5. Show the saved profile back as a table, and tell the owner the backlink
   playbook (`references/backlink-playbook.md`) is now personalized — work it top
   to bottom.

**Success criteria**: The profile saved without a length rejection, was drafted from files read fresh in this run, and carries no hype words. The saved profile was shown back to the owner as a table.

---

## Part 5 - write `.seo/publish-paths` (needed before any auto-merge)

One path prefix per line, `#` comments allowed: the directories a content PR is
allowed to touch. The auto-merge workflow refuses to run without it, and refuses
to merge any PR that changes a file outside it.

```

**Success criteria**: The file exists and lists only the directories Parts 1-3 actually found, plus `.seo/`. Anything broader defeats the gate it exists to enforce.
# .seo/publish-paths - what a content PR may touch
content/blog/
public/blog/covers/
src/components/guides/
.seo/
```

Derive it from what Parts 1-3 actually found: the guides directory, the cover
output directory if the repo has a generator, the visual-component directory,
and `.seo/` itself (the builder records state there). **Nothing else.** If a
build legitimately needs to touch something outside these, that PR should be
reviewed by a human - which is exactly what the gate enforces.

---

## Finish

```bash
python3 $SEO/seostate.py overview
python3 $SEO/seostate.py log-run --workflow setup --summary "<what was found/scaffolded>"
```

Verify before declaring success:

- `overview` reports the right domain and `conventions: true`;
- the content home and tools home are both PUBLIC (verified logged-out);
- the profile saved without a length rejection;
- the authority score is recorded (`dr` is not null).

Then run `research` so the queue fills day one.

**Success criteria**: `overview` reports the right domain and `conventions: true`; both homes verified public; the profile saved cleanly; `dr` is not null. The run is logged and `research` has been run so the queue fills day one.
