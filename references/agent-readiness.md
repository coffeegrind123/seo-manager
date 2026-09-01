# Agent readiness — what an AI agent is allowed to read, and what it gets

Read this before running `agentcheck.py`, and before writing anything into a
report about `llms.txt`, AI crawlers, or "GEO". The myths in this area are
confident, widespread, and mostly wrong, and repeating one costs the whole
report its credibility.

Companion measurement: `crawllog.py` (which AI crawlers actually *came*) and
`references/workflow-geo-scan.md` (whether assistants actually *cite* you).
`agentcheck.py` answers the third question — whether they are permitted to, and
what they receive when they arrive.

---

## 1. The three AI crawler classes are not interchangeable

This is the single most load-bearing distinction in the whole area, and almost
every piece of "block the AI scrapers" advice collapses it into one thing.

| Class | Examples | Can it ever cite you? |
|---|---|---|
| `ai_search` | OAI-SearchBot, PerplexityBot, Claude-SearchBot, DuckAssistBot, YouBot | **Yes.** This is the index assistants cite from. |
| `ai_user` | ChatGPT-User, Claude-User, Perplexity-User, MistralAI-User, Meta-ExternalFetcher | **It already is.** A live fetch means a real person asked and the assistant came to your page. |
| `ai_training` | GPTBot, ClaudeBot, anthropic-ai, Google-Extended, CCBot, Amazonbot, Bytespider, meta-externalagent | **No.** Trains a model. Never cites, never sends traffic. |

The taxonomy is declared once, in `crawllog.py`'s `BOTS` table, and
`agentcheck.py` imports it. Do not maintain a second copy.

**The configuration that matters: `ai_search` blocked while `ai_training` is
allowed.** The site feeds model training and can never be cited or sent
traffic — the worst available outcome, arrived at by accident, because the
popular advice says "block AI bots" and the popular robots.txt snippets list
GPTBot but not OAI-SearchBot. `agentcheck.py policy` reports it as
`farmed_not_read`.

**Blocking everything is a coherent position and is NOT flagged as that.**
Measured while this was built: `nytimes.com` trips `farmed_not_read` (blocks
every citing crawler, still allows a trainer); `reddit.com` does not (it blocks
all three classes, which is a policy, not an accident). The rule exists to catch
incoherence, not to argue for openness.

**Google AI Overviews ride normal Googlebot.** `Google-Extended` governs Gemini
training and grounding, not AI Overviews. Blocking `Google-Extended` does not
remove you from AI Overviews, and allowing it does not get you in.

---

## 2. `/llms.txt` — report it, never score it

**Google Search ignores `llms.txt`.** That is Google's own documented position
in its AI-optimization guide (2026-06-29): publishing one "won't harm (nor
help) your visibility or rankings in Google Search, as Google Search ignores
them."

Supporting evidence, all from before that statement and all pointing the same
way:

| Source | Date | Finding |
|---|---|---|
| John Mueller (Google) | 2025 | "No AI system currently uses llms.txt"; later called the discovery use case "a dead end" and compared the file to meta keywords |
| Gary Illyes (Google), Search Central Live | Jul 2025 | Google has no plans to support it |
| SE Ranking, 300k-domain study | Nov 2025 | Of the 50 most AI-cited domains, **one** had an `/llms.txt` |
| OtterlyAI, server-log audit | 2025 | **0.1%** of AI-bot requests targeted `/llms.txt` (84 of 62,100) |
| Anthropic, Stripe, Cloudflare, NVIDIA | 2024–25 | All publish one. **None** has stated its crawlers consume third-party ones. |

**Where it genuinely is consumed: AI coding agents** (Claude Code, Cursor,
Cline, Continue) reading per-library documentation, and Mintlify auto-generates
it for thousands of developer-docs sites. For a developer-tooling site that is
a real win. For everything else it is zero-cost optionality.

So: `agentcheck.py llms` checks presence and well-formedness, and its output
carries a `framing` field stating the above. **Never present `llms.txt` as a
ranking or citation lever in a report**, and never propose building one as a
GEO action item. Lighthouse's `agentic-browsing` category does include an
llms.txt presence check — that is a Lighthouse opinion, not a Google Search
ranking signal, and the two get conflated constantly.

---

## 3. Agent-UX: the accessibility tree is the highest-leverage surface

Agents interpret a page through three channels — screenshots + a vision model,
raw DOM, and the browser's accessibility tree. Modern agents combine all three,
and the accessibility tree is the cleanest signal of the three. If it is broken,
visual polish does not rescue it.

Statically decidable (and therefore checked by `agentcheck.py page`):

- Real interactive elements — `<button>` / `<a href>` / `<input>`, not
  `<div onclick>`. A div with a click handler and no `role`/`tabindex` appears
  in the tree with no role at all, and agents skip it.
- Every form field with a `<label for>`, `aria-label` or `aria-labelledby`.
  Without one the field is a void an agent cannot fill correctly.
- Semantic landmarks — `<main>` above all, so the primary content is findable
  without guessing between nav, sidebar and footer.
- Content present in the raw HTML. **Most AI crawlers do not execute
  JavaScript**, so a client-rendered page is an empty page to every `ai_search`
  bot.

**Not** statically decidable, and reported as `unmeasurable_statically` rather
than quietly passed:

- interactive target size (vision pipelines discard targets below ~8 px² of
  unobscured area; WCAG's 24×24 and Apple's 44×44 both clear that comfortably)
- transparent overlays covering interactive nodes (full-card click handlers,
  cookie layers left mounted, modal portals with `pointer-events` still on)
- computed `cursor: pointer` — a legitimate actionability hint that should be
  on interactive elements and off everything else
- the real accessibility tree

For those, run the browser:

```
npx lighthouse@latest <url> --only-categories=agentic-browsing
```

Google created the **Agentic Browsing** Lighthouse category in 13.2.0
(2026-05-01), on by default since 13.3.0 (2026-05-07), Chrome 150+. Two things
to get right when reporting it: it returns a **fractional pass-ratio (X of N),
not a 0–100 weighted score** — do not compute one — and the **PageSpeed
Insights REST API does not return this category**, so `pagecheck.py vitals`
will never contain it. DevTools, the CLI, and the PSI web UI are the run paths.

---

## 4. WebMCP — an opportunity, never a finding

WebMCP lets a site declare structured tools for agents instead of making them
infer intent from the DOM. Status: a *proposed* standard from the Web Machine
Learning community group, flag-gated Early Preview from Chrome 146 Canary
(2026-02-10), with origin-trial availability from Chrome 149 — not W3C-final.

Lighthouse 13.2+ ships three audits under `agentic-browsing`:
`webmcp-form-coverage` (forms lacking `toolname`/`tooldescription`),
`webmcp-registered-tools`, `webmcp-schema-validity`. They require Chrome 150+
and origin-trial registration to fire.

`agentcheck.py page` reports `webmcp.forms_with_tools` and whether the JS API is
referenced. **Absence is never a defect** — flag it as an opportunity only for a
site that wants first-class agent actions.

---

## 5. Currency

The dated claims above were compiled on 2026-08-01 from the primary sources
named in each table. They have **not** all been independently re-fetched since.
Treat every date as "as recorded", and re-check the source link before quoting
one as current — the whole area moves, which is exactly why every row carries a
source and a date rather than a bare assertion.

**Re-check this file when:** any major AI search system documents `llms.txt`
consumption; Google renames or adds `agentic-browsing` audits; WebMCP leaves
origin trial; or a follow-up log study shows `/llms.txt` request rates moving.

## robots.txt groups do NOT inherit — the exclusion bypass

**The trap.** A crawler obeys only the **most specific group that names it**. So a
named `User-agent: GPTBot` group whose whole body is `Allow: /` grants that agent
every path the `*` group closes. Nothing in the file looks wrong: the exclusions
are right there, a few lines above, in a group that does not apply to it.

**Measured on combatskirmish.net, 2026-09-01.** The file allowed AI crawlers
deliberately and correctly — and 18 named agent groups reached up to 11 disallowed
paths each, including `/g/` (tokenised game binaries), `/api/` and `/dl/`. Over
the six days 2026-08-26..09-01 Amazonbot made 5,035 requests (839/day), the
largest crawler on the site by a factor of two. The file's own header stated the
intent — *"keep them out of the API and
tokenized/binary/internal paths that have no content value"* — and it had simply
never been applied to the named groups.

`agentcheck.py policy` now reports this as `named_group_escapes_default_exclusions`
(high). ⚠ **It cannot be seen on a `/` check**, because the paths involved are never
the homepage — which is why `policy` returned a confident `pass` for as long as it
did.

**The fix is repetition, not restructuring:** copy the `Disallow` lines into each
named group. It costs no content access — every page, image and text surface stays
open — and it is the only mechanism, since there is no inheritance to rely on.

**Two adjacent robots.txt facts that bit the same file on the same day**, both
worth checking whenever you touch one:

- **`Allow: /` placed FIRST in a group kills every `Disallow` below it** for
  first-match parsers. RFC 9309, Google and Bing resolve by most-specific match and
  are unaffected, but the ordering costs nothing to get right. Put it last.
- **A blank line ends a record** in legacy parsers (Python's `urllib.robotparser`
  among them), orphaning every rule after it. Keep a group contiguous — a bare `#`
  gives the same visual separation without the break.
