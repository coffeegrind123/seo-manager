# seo-manager

A [Claude Code](https://claude.com/claude-code) skill that runs a complete SEO
program for a git-based website from the terminal — research, build, measure —
with every gate written down and every data source measured rather than assumed.

**No backend, no database, no MCP server.** State is a committed `.seo/`
directory. Scripts are stdlib Python 3 with no installs.

## What it does

**Propose and build**

| Workflow | Job |
|---|---|
| `setup` | write `.seo/conventions.md` — the site facts every other workflow adapts from |
| `research` | keyword research that starts from what the product IS, gated by a page-1 authority count |
| `build-guide` | ship a guide as a PR, through a corpus sameness gate |
| `build-tool` | ship a free interactive tool as a PR |
| `trend-scan` | what is moving in the niche |
| `geo-scan` | whether AI assistants cite the site |
| `backlinks` | prospecting, with an explicit do-not-buy list |

**Measure what already exists**

| Workflow | Job |
|---|---|
| `crawl-log` | real crawl budget from your own access log: budget by silo, statuses served to bots, AI-crawler ingestion, verified-vs-spoofed Googlebot |
| `decay` | pages that lost *rank*, separated from pages whose *demand* fell |
| `drift` | whole-page-1 diffs: new entrants, AI-Overview changes, volatility, algorithm-update correlation |
| `programmatic` | index-bloat scoring across generated silos, decided on index evidence |
| `health` | technical audits triaged into queue items |

## Design rules it holds itself to

- **Never fabricate data.** A failed call is reported, never papered over.
- **A negative result is only as good as its control.** Every probe that can
  report "absent" runs a control that must come back "present" first — because a
  broken instrument and a real finding look identical. Both of the tools that
  needed this caught a false conclusion during development.
- **A refused SERP read is a failed read, never an empty page 1.** The relevance
  guard rejects a well-formed page of results *for a different query*, which is
  the failure mode that silently scores a stranger's SERP.
- **The authority count on page 1 overrules any difficulty score**, in both
  directions.
- **Free-first.** DuckDuckGo, Google Autocomplete, real Google via a headed
  browser, Search Console, access logs, Common Crawl, RDAP, sitemaps. SerpApi /
  Brave / DataForSEO / Open PageRank are upgrades, never prerequisites.

## Install

```bash
git clone https://github.com/<you>/seo-manager ~/.claude/skills/seo-manager
python3 ~/.claude/skills/seo-manager/scripts/seodoctor.py   # preflight
```

Then, from your site's repo root, ask Claude Code to "set up the SEO pipeline".

## Tests

```bash
python3 scripts/test_guards.py    # SERP guards, against real captured responses
python3 scripts/test_measure.py   # bot classification, log parsing, verification, decay
```

Both suites are built from bugs that actually shipped — the fixtures in
`assets/fixtures/` are real responses, including the two that fooled every naive
check.
