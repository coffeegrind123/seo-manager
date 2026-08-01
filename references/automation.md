# Running it on a schedule

The workflows in this skill are designed to be run by a human asking for them, by
a cron, or by CI. Nothing about the methodology changes — only who pulls the
trigger and whether a human is present to answer a question.

**Headless runs must never ask a question.** If a gate says stop, stop cleanly and
report why. Every workflow already specifies its clean-exit behaviour; honour it.

---

## The intended cadence

| Workflow | When | Why that cadence |
|---|---|---|
| `setup` | once, and again when the stack or positioning changes | it writes the facts everything else adapts from |
| `research` | weekly | fills the tank the daily builder drains: 7 approved guides + 1–2 tool ideas |
| `build-guide` | daily | one guide per UTC day, flat. The pace gate enforces it. |
| `build-tool` | weekly, or when the owner approves a tool | tools are approve-first |
| rank check | weekly (daily if you have SerpApi/DataForSEO quota) | positions move slowly; the free provider throttles |
| authority refresh | weekly | DR moves on a monthly scale — a daily call is waste |
| `geo-scan` | weekly | the trend line only means something with a stable question set |
| `trend-scan` | on demand only | it is a radar the owner reads, not a firehose |
| auto-merge | event + hourly backstop | without it the daily builder stalls: it refuses to build while an `seo` PR is open |
| IndexNow ping | after each merge | free and instant for Bing/Yandex; Google does not participate |
| `report` | on demand | |

---

## Option A — GitHub Actions (the unattended path)

Copy the templates into the site's repo:

```bash
mkdir -p .github/workflows
cp ~/.claude/skills/seo-manager/assets/workflows/seo-daily.yml      .github/workflows/
cp ~/.claude/skills/seo-manager/assets/workflows/seo-weekly.yml     .github/workflows/
cp ~/.claude/skills/seo-manager/assets/workflows/seo-auto-merge.yml .github/workflows/
```

Vendor the skill into the repo so the runner can reach it:

```bash
mkdir -p .claude/skills
cp -r ~/.claude/skills/seo-manager .claude/skills/
git add .claude/skills/seo-manager .github/workflows .seo
```

### Secrets

| Secret | Required? | What it unlocks |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | **yes** | the agent itself |
| `SERPAPI_KEY` | no | real Google SERPs, top-100 rank checks in one credit (250/mo free) |
| `BRAVE_SEARCH_API_KEY` | no | an independent SERP index (2000/mo free) |
| `OPENPAGERANK_API_KEY` | no | a real link-graph authority number instead of the capped estimate (free) |
| `DATAFORSEO_LOGIN` / `_PASSWORD` | no | real volume + KD + backlinks (paid) |

`GITHUB_TOKEN` is provided automatically.

### The setting everybody misses

**Settings → Actions → General → "Allow GitHub Actions to create and approve pull
requests"** must be **ON**. It is off by default on new repos, and with it off
`gh pr create` fails *after* the branch is pushed — which is the worst outcome,
because the work exists and nothing surfaces it. Both build workflows exit
non-zero on that failure specifically so it goes red instead of stranding.

### Why three cron times for one daily build

A usage-limit hit at 05:13 should be a quiet deferral, not a lost day. The later
attempts re-run, and the pace gate skips them the moment one of them actually
ships. Offsetting off the top of the hour matters too — GitHub's scheduler queues
and drops on-the-hour triggers far more often than odd minutes.

### Auto-merge is what keeps it moving

`seo-daily` deliberately refuses to build while an `seo` PR is open, so **one
unmerged PR stops the whole pipeline**. `seo-auto-merge.yml` closes that loop,
and it is off unless you turn it on:

```bash
python3 scripts/seostate.py config --set auto_merge=true
```

Two hard safety properties, both worth keeping:

- It merges only PRs whose every changed file sits under a prefix in
  `.seo/publish-paths`. **It refuses to run at all if that file is missing** —
  without a path allowlist it would merge whatever an agent happened to touch.
- **Tool PRs are never auto-merged.** They ship LLM-authored code that runs in
  your visitors' browsers; a human exercises the widget first. They carry the
  `seo-tool` label and are excluded at the query.

### Getting pages crawled

After a merge, `scripts/indexnow.py ping --pending` submits every new page to
IndexNow — free, keyless, instant, and it reaches **Bing, Yandex, Seznam, Naver
and DuckDuckGo. Not Google**, which has never joined IndexNow.

For Google the only legitimate accelerator is a human clicking "Request
indexing" in Search Console, so `indexnow.py google-steps` batches everything
pending into ONE session (the quota is per property per day, so batching costs
nothing). Do not believe any tool that claims to automate this — the Indexing
API is JobPosting/BroadcastEvent only, and URL Inspection is read-only.

### State lives in git

`.seo/` is meant to be committed. Both templates commit it back after a run, which
means the queue, the rank history and the run log are all reviewable in a diff and
survive a runner being thrown away. **Do not add `.seo/` to `.gitignore`** — the
history is the product.

Concurrency groups keep two builds from racing the same queue item.

---

## Option B — a local schedule

If the site is not on GitHub, or you want the runs on a machine you control:

**The `schedule` skill** creates cron-scheduled cloud agents:

```
/schedule  → "every weekday at 6am, use the seo-manager skill to run build-guide"
```

**The `loop` skill** self-paces a repeating task inside a session:

```
/loop 1d Use the seo-manager skill and run the build-guide workflow.
```

**Plain cron**, on any always-on box:

```cron
13 5 * * *  cd /path/to/site && claude -p "Use the seo-manager skill. Run build-guide." --permission-mode acceptEdits
27 4 * * 1  cd /path/to/site && claude -p "Use the seo-manager skill. Run research." --permission-mode acceptEdits
```

**Schedules only run while the machine is awake.** A laptop that sleeps at night
will silently skip the 05:13 build and nothing will tell you. If the cadence
matters, put it on something that stays on — a $5 VPS, a Pi, or GitHub's runners.

---

## What to check when a scheduled run goes quiet

In order of how often it is actually the cause:

1. **The pace gate.** `seostate.py pacing` — a guide already shipped today,
   including one the owner merged by hand. This is the intended behaviour, not a
   fault.
2. **An SEO PR is already open.** The builder deliberately refuses to stack PRs.
   Merge or close the open one.
3. **The queue is empty.** `seostate.py next-actions` will say so. Run research.
4. **The token expired.** `CLAUDE_CODE_OAUTH_TOKEN` rotates; a line-wrapped paste
   is the classic silent failure.
5. **A build died mid-run**, leaving a suggestion stuck at `in_progress`.
   `next-actions` flags any that has sat there over a day — reset it to `approved`.

```bash
python3 scripts/seostate.py runs --limit 20     # the run log
python3 scripts/seostate.py next-actions        # ranked, with the fix
```

---

## Cost discipline for unattended runs

The gates that keep an autonomous pipeline from running up a bill or a mess are
all in the methodology already — this is just where they are listed together:

- **25 SERP checks per research run**, spent best-candidate-first. Hitting the cap
  is a real result, not a failed run — provided every seam was worked first.
- **One guide per UTC day**, site-wide, counting the owner's own merges.
- **1–2 tool ideas queued per week**, never more.
- **5 trend subjects per scan**, hard cap. **5 takes per subject**, hard cap.
- **3 sameness-gate attempts**, then the topic is the problem and the run exits
  without a PR.
- **3 back-linked files per build**, one link each, and only when the project
  opted in.
- **One DataForSEO facet-pricing call per research run** (5 seeds max), never one
  per facet.
