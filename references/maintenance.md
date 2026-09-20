# Maintaining SKILL.md

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
editing it, keep the trigger list and the three `NOT for…` boundaries — those
are what stop this skill firing on work that belongs to `seo-audit` or an ads
task. (`search-console` was a third boundary until 2026-09-01, when `gsc.py`
brought Search Console inside — see references/data-sources.md.) Re-check with:

```bash
python3 ~/.claude/skills/skill-refiner/skills/anthropic-skills/skills/skill-creator/scripts/quick_validate.py \
  ~/.claude/skills/seo-manager     # must print: Skill is valid!
```

---

