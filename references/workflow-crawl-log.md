# Workflow: crawl-log (server-log crawl analysis)

**Cadence:** monthly, and whenever indexing, traffic or AI visibility does
something unexplained. **Job:** find out what search and AI crawlers actually
did on this site, from the site's own access log.

This is the only **first-party** measurement in the skill. Every other workflow
asks a third party what it thinks; this one reads the server's own record. It is
free, it is complete, it cannot be rate-limited, and no competitor can see it.

It answers questions nothing else here can:

- Where does Googlebot spend its crawl budget? (Search Console's Crawl Stats
  gives totals, never the breakdown by section.)
- What status codes were bots actually served?
- Which AI crawlers read the site, and are they the ones that can cite it?
- Which "Googlebot" hits were not Googlebot?

It is now first-party **twice over**, because the crawler will also tell you its
own side of the story. See §1b: `bing.py crawlstats` is Bingbot's record of the
same events, and on a site where Bing carries the traffic the two readings check
each other. Where they disagree, one of them is wrong and you have learned
something either way.

---

## 0. Preflight

```bash
SEO=~/.claude/skills/seo-manager/scripts
python3 $SEO/seodoctor.py
python3 $SEO/seostate.py overview        # confirm the domain
```

You need read access to the web server's access log. It is usually on the
server, not here — `--remote` ships the script over ssh and aggregates **there**,
so a gigabyte of decompressed log never crosses the wire.

**Success criteria**: The preflight is green and the domain in `overview` is confirmed to be the site you mean to measure. Read access to the access log is established.

---

## 1. Scan

```bash
python3 $SEO/crawllog.py scan \
  --remote root@<host> --ssh-key ~/.ssh/<key> \
  --glob '/var/log/caddy/access*.log*' \
  --silo-depth 1 > /tmp/scan.json
```

`--format auto` detects Caddy JSON vs Apache/nginx `combined`/`common`.
`.gz` archives are read directly. Anything else: `--format regex` with named
groups.

**Quote the `--glob`.** ssh joins its arguments into one string and hands them to
the *remote* shell, which re-expands them — an unquoted glob arrives as twenty
positional arguments and argparse rejects the lot. (Measured; the script quotes
correctly on its side, but your shell must not expand it locally first.)

**Check `unparsed_share` before reading anything else.** Above ~0.02 the format
guess is wrong and every number below it is drawn from a subset.

**Success criteria**: A scan completed with `unparsed_share` below ~0.02. Above that the format guess is wrong and every downstream number is drawn from a subset — fix the format before reading anything.

---

## 1b. The crawler's own record — `bing.py`

The access log is what arrived. This is what Bing believes it did, and what it
did with the result. No user-agent to verify, no log to parse, no spoofing to
strip. It is a different instrument reading the same events, which is why it is
worth running both.

```bash
SEO=~/.claude/skills/seo-manager/scripts
python3 $SEO/bing.py sites                # the auth control - run it FIRST
python3 $SEO/bing.py crawlstats           # fetched per day, codes, pages in index
python3 $SEO/bing.py crawlissues          # per-URL problems, empty answer guarded
python3 $SEO/bing.py feeds --verify       # the sitemap AS BING HOLDS IT
python3 $SEO/bing.py blocked              # account-level blocks. Invisible to any crawl
python3 $SEO/bing.py crawlsettings        # a self-imposed crawl-rate cap
```

**Read them in that order, and read `blocked` even when everything looks fine.**
It is the only one of the six that describes state you set rather than state the
crawler observed: an account-level block survives every deploy, appears in no
crawl of the site, and is invisible to every other instrument here.

⚠ **`crawlstats` mixes two kinds of column in one row and labels neither.**
`CrawledPages` is a count for that day; `Code2xx`, `InIndex`, `InLinks` and
`BlockedByRobotsTxt` are running totals or stocks. Summing the second kind
produced `Code2xx: 96,000` for a site Bing had crawled 7,408 pages of, and
`InIndex: 82,767` for an index holding 4,809. The tool re-derives each column's
kind from the series every run and **refuses to sum** the cumulative ones —
read `latest_stock.<col>.latest`, and `.change_over_window` for movement.

⚠ **`all_other_codes` is not an error rate and Bing documents no breakdown of
it.** On the site measured it ran at 0.88× the pages crawled, which is far too
large to be a per-day count of responses to those crawls. The tool reports it
under `caution` for that reason. Do not build a finding on it.

⚠ **An empty `crawlissues` is not "no issues".** It is cross-examined against
`crawlstats`: errors there plus nothing here is `verdict: unknown`, with the
contradiction named. Only a zero corroborated by an independent reading is
reported as `none`.

### The sitemap gap is a DATE, not a defect

`feeds` reports `url_count_per_bing` from Bing's last crawl **of the feed**, so a
sitemap you changed yesterday still reads with the old total. `--verify` counts
the live file and prints the delta. Measured on combatskirmish.net 2026-09-01:
live 5,388, Bing 6,127, `last_crawled` 2026-08-31 — a 739-URL gap that is
either a change bingbot has not re-read or a change that never deployed.
`last_crawled` against your deploy date is what separates the two. Neither is a
defect on its own, and reporting the delta as one is the mistake to avoid.

---

## 1c. Getting crawled — the two channels, and they are not the same

There are exactly two ways to ask for a crawl, they have separate quotas, and a
ping to one does nothing for the other:

| | `indexnow.py ping` | `bing.py submit` |
|---|---|---|
| reaches | Bing, Yandex, Seznam, Naver, DuckDuckGo | Bing only |
| costs | keyless, no quota worth counting | a real per-site allowance (100/day, 3,000/month on the account measured) |
| needs | a key file on the site | a verified Bing Webmaster account |

**Neither reaches Google**, which has never joined IndexNow and restricts its
Indexing API to `JobPosting`/`BroadcastEvent`. `indexnow.py google-steps` batches
the manual follow-up instead of pretending otherwise.

`submit` is the only mutating call in `bing.py`, so it is a **dry run until
`--yes`**. It drops off-site URLs before spending the call (Bing rejects the
whole batch for one), refuses a batch larger than the remaining quota rather
than truncating it, and its receipt says explicitly that it is a receipt for the
REQUEST — not a crawl confirmation. Verify a few days later with
`bing.py urlinfo --url <u>`, always alongside a known-crawled control.

⚠ **Spending the quota on a silo that is the subject of an open re-measure
hypothesis contaminates it.** Submission is a second intervention landing inside
someone else's experiment; check `remeasure.py due` before you submit, and if it
collides, say so and let the owner choose.

---

## 2. Read it in this order

### a. `by_category` — the shape of the whole picture

| category | means |
|---|---|
| `search` | indexes you and can send organic traffic |
| `ai_search` | feeds an assistant that **cites** sources |
| `ai_user` | a **live** fetch because a person asked a question |
| `ai_training` | trains a model. Never cites, never sends traffic. |
| `social` | link unfurls |
| `seo_tool` | third-party crawlers reselling your content as competitor data |
| `user_fetch` | user-**triggered** but not an assistant — Google-Read-Aloud is a person pressing a button. Deliberately not folded into `ai_user`, which is the GEO signal |
| `self` | this skill's own fetches (`sitegraph`, `vitals`, `agentcheck`). Visible so nobody reads our crawler as third-party interest |
| `unknown` | matched no name in the table. **Not a synonym for "an unknown crawler"** — see below |

### a2. `unnamed_bots` — read this before treating `unknown` as crawl demand

The `unknown` bucket is routinely one of the largest rows in the report, and it
is a mixture of three different things: real crawlers not yet in the table,
network scanners, and outright attack probes. On combatskirmish.net it held
vulnerability scanning (`l9scan`/leakix), a UA field containing
`http://<site>/wp-admin/install.php`, and an academic scanner — alongside
genuine crawlers. A high `error_rate` on the bucket points at the scanners.

`unnamed_bots` surfaces its top user-agent strings so the bucket is diagnosable
instead of opaque. Where a string is a genuine crawler, add it to `BOTS` in
`crawllog.py` — **leaving the rDNS list empty unless the operator documents a
suffix.** A guessed suffix does not fail quietly: it reports every legitimate
hit from that crawler as SPOOFED, which is a confident finding about someone
else's infrastructure manufactured entirely by our own table.

⚠ **The UA sample used to cut at 120 characters, and a bot token is appended at
the END of a spoofed browser string.** `YandexMobileBot` identifies itself at
character 155 of an otherwise ordinary iPhone Safari UA, so 482 hits — the
largest unnamed crawler on the site — were filed under a key that read as a
mobile visitor, with several distinct crawlers collapsed onto it. The key now
keeps both ends. Naming the bots that fix revealed moved **57% of the unknown
bucket** into correctly categorised rows in one pass, including an `ai_search`
engine (`GrokBot`) the GEO report had never counted.

**The three OpenAI agents are not interchangeable and conflating them is the
most common mistake in this area.** `GPTBot` trains. `OAI-SearchBot` builds the
index ChatGPT search cites from. `ChatGPT-User` is a real person's question
arriving at your page. A robots.txt that blocks "AI bots" as one group opts you
out of AI *search* while doing nothing about training.

### b. Googlebot's `top_silos` — the crawl-budget answer

The budget question is not "how much" but "on what". A silo taking a third of
the crawl while producing none of the organic traffic is the finding.

### c. `status` and `top_errors`

- **4xx on pages you expect to be indexed** — dead internal links, or URLs in
  the sitemap that do not exist.
- **A spike of 404s in a narrow time window** across everything is a **deploy
  outage**, not a content problem. Check `daily` against your deploy times.
- **403 to bots only** is a WAF or firewall rule.

### d. `daily` — the crawl-rate trend

A crawl rate falling steadily while page count is flat means Google is losing
interest. This leads impressions by weeks, which is exactly why it is worth
reading.

**Success criteria**: `by_category`, Googlebot's `top_silos`, `status`/`top_errors` and `daily` have each been read, with the three AI-crawler classes kept distinct. A 404 spike is checked against deploy times before being called a content problem.

---

## 3. Verify before you conclude

**User agents are claims.** Anything can send `Googlebot`.

```bash
python3 $SEO/crawllog.py verify --scan /tmp/scan.json --bot googlebot --bot bingbot
```

Reverse DNS to the operator's domain, then a **forward** lookup back to the same
IP. Reverse alone proves nothing — the PTR for an IP is set by whoever holds the
IP.

**Read `resolver_control` first.** It resolves a PTR that must exist
(`8.8.8.8 → dns.google`). If it fails, every verdict is `unknown` and none of
them is a finding.

> This is not hypothetical. On the first real run of this tool **every**
> Googlebot IP came back "spoofed" — because this container's Docker DNS silently
> refuses all reverse lookups, and `socket.gethostbyaddr` raises the same
> exception for "does not exist" as for "cannot ask". The control is the fix, and
> resolution now goes over DoH.

Three distinct verdicts, and they are not the same thing:

| `verified` | meaning |
|---|---|
| `true` | PTR on the operator's domain **and** forward-confirms |
| `false` | contradicted — the forward lookup returned different addresses. **Spoofed.** |
| `null` | no verdict available: the operator publishes IP ranges instead of rDNS (OpenAI, Anthropic, Meta), or the PTR has no forward record (measured on AhrefsBot), or the resolver failed |

**`null` is never evidence of spoofing.** Subtract only `spoofed_hits` from a
crawl total.

### 3b. `ua_spoofing` — forged identities, with no network call

`scan` now returns a `ua_spoofing` block automatically. It looks for one thing:
an IP presenting as crawlers belonging to **two or more different companies**.
No address is legitimately Anthropic's crawler *and* OpenAI's *and* Perplexity's.

**Read it BEFORE the `by_category` totals**, because that is precisely what it
corrupts. Measured on a live site 2026-08-01:

```
2a09:bac5:…::3e3:e   12 operators   190 hits
31.59.x.x            10 operators   454 hits
34.85.x.x            11 operators    84 hits
```

(Host portions masked on purpose. Addresses get reassigned, and a permanent
public note calling a specific one a scanner outlives the tenancy that earned
it. The shape — a handful of addresses each claiming ten-plus operators — is
the whole finding; the exact octets add nothing.)

Those three accounted for **100%** of the traffic attributed to Claude-SearchBot,
anthropic-ai, cohere-ai, Google-Extended, Perplexity-User, MistralAI-User,
Diffbot and Applebot-Extended — every one of them a vulnerability scanner, ~100%
404s against `/.env`, `/.ssh/*`, `/secrets.json`. The raw log said "assistants
are reading us". Stripped of the forgery, genuine `ai_search` and `ai_user`
crawling was **zero**, and the whole GEO conclusion inverted.

It complements `verify` rather than duplicating it, and covers its blind spot:

| | catches | blind to |
|---|---|---|
| `verify` | a forged IP for an operator that publishes **rDNS** (Google, Bing) | operators that publish IP RANGES — returns `null`, no verdict |
| `ua_spoofing` | a forged **UA**, for any operator, with no DNS at all | a scanner disciplined enough to forge only ONE identity |

**The control is built in**: Googlebot + GoogleOther + Googlebot-Image from a
single Google address collapse to one operator and are never flagged. An empty
result therefore says "no IP claimed two operators" — a **ceiling on honesty,
not a clean bill of health**, and the tool's own `reading` says so.

⚠ Check whether a flagged address is **yours**. A live run flagged the
operator's own workstation IP, because a session of manual `curl`/browser
testing had presented several different agent strings. That is a true positive
about the log and a false alarm about the internet.

**Success criteria**: `resolver_control` passed. Every bot verdict is one of the THREE states, and `null` is never counted as spoofed — only `spoofed_hits` is subtracted from a total.

**Success criteria**: `ua_spoofing` was read BEFORE the `by_category` totals were believed, any flagged address was checked against the operator's own IPs, and an empty result is reported as a ceiling on honesty rather than a clean bill of health.

---

## 4. Crawl gap — the sitemap versus reality

```bash
python3 $SEO/crawllog.py urls --remote root@<host> --ssh-key ~/.ssh/<key> \
  --glob '/var/log/caddy/access*.log*' --bot googlebot > /tmp/crawled.txt

python3 $SEO/crawllog.py gap --crawled /tmp/crawled.txt \
  --sitemap https://<domain>/sitemap.xml
```

- **`never_crawled`** — the sitemap promises a page Googlebot has not fetched in
  the window. At scale this is a crawl-budget problem, not an indexing one, and
  "request indexing" does not fix it. Internal links and a smaller, honest
  sitemap do.
- **`crawled_but_not_in_sitemap`** — budget spent on URLs you never advertised.
  Check they should be indexable at all.

**Success criteria**: `never_crawled` and `crawled_but_not_in_sitemap` are both read, and a large `never_crawled` set is treated as a crawl-budget problem rather than something "request indexing" fixes.

---

## 5. What to do with it

Findings become queue items like anything else, and the same bar applies.

| Finding | Response |
|---|---|
| A silo eats the budget and earns nothing | Cut it from the sitemap, or `noindex` it. Do NOT `Disallow` a page you want de-indexed — a blocked page cannot be re-crawled to see the noindex. |
| 404s on sitemap URLs | Fix the generator or drop the URLs. A sitemap full of 404s costs trust on every other URL in it. |
| Deploy 404 window | An infrastructure fix. Verify no cached error survived it. |
| `ai_training` ≫ `ai_search` | You are being farmed, not cited. A robots.txt decision, not a content one. |
| No `ai_search` crawlers at all | You cannot be cited by an assistant that has never fetched you. This is upstream of everything `geo-scan` measures. |
| `seo_tool` out-crawling Googlebot | Costs real bandwidth and returns nothing. Blocking them is safe — they are not search engines. |

**Do not act on a single window.** Crawl rate is spiky — measured on a real site,
Googlebot swung between 39 and 782 hits/day inside two weeks with no change to
the site. Reading the low tail of that series as "the crawl rate collapsed" is
the easiest wrong conclusion available here. Two scans a month apart beat one
scan interpreted confidently.

### ⛔ Do NOT validate robots.txt with Python's `urllib.robotparser`

It does not implement RFC 9309's **longest-match** rule — it returns the first
matching rule in file order. So a perfectly correct file like

```
User-agent: *
Allow: /
Disallow: /api/
```

is reported by the stdlib as **allowing** `/api/`, because `Allow: /` appears
first. Google and Bing both apply the longest match (`/api/` is 5 characters,
`/` is 1), so the path is really blocked.

Measured 2026-08-01: this produced three confident false failures against a file
that was correct, and only a second parser settled it. Use **`protego`** (what
Scrapy uses, implements Google's semantics) or Google's own open-source
`robotstxt` parser, and **always assert a control that must go the other way** —
a UA you expect to be allowed, checked in the same run. A robots test that only
checks blocks passes trivially on a file that blocks everything.

**Success criteria**: Findings are queued against the same bar as any other work, nothing meant for de-indexing was `Disallow`ed, and no conclusion rests on a SINGLE window — crawl rate swings wildly without the site changing.

---

## 6. Report

Log it:

```bash
python3 $SEO/seostate.py log-run --workflow crawl-log --ok --summary "..."
```

State the window, the parse rate, the verified-bot position, and the finding.
"Googlebot crawled 3,666 URLs in 19 days, 33% of its budget on one non-indexed
section, daily rate swinging 39–782 with a ~3% error rate" is a report.
"Crawling looks healthy" is not.

**Success criteria**: The report states the window, the parse rate, the verified-bot position and the finding, with numbers. "Crawling looks healthy" is not a report. The run is logged.

---

## Measured on a real site (2026-08-01)

A ~3,500-page game site behind Cloudflare, two weeks of Caddy logs. Kept because
these are the **shapes** you will meet, not because the numbers transfer:

- **1,105,065 log lines, 0.0% unparsed.** Caddy JSON auto-detected.
- **`seo_tool` was the LARGEST bot category at 39.9%** of all bot traffic —
  28,596 requests and 2.3 GB, roughly 5× Googlebot's volume, from crawlers that
  index nothing and send nothing. One `robots.txt` group fixed it.
- **`ai_training` 28% versus `ai_search` 0.4%.** The site was being farmed for
  training ~68× more than it was being read for citation, while `geo-scan` was
  measuring citation downstream and finding little. The logs explained why.
- **Googlebot: 5,227 hits, 3,666 unique URLs, 19 days** — roughly one pass over
  the surface with almost no re-crawl.
- **A dynamic section took more budget than the entire SEO silo** (1,708 vs
  1,650 hits) — the kind of split no other tool will show you.
- **Crawl rate looked like a collapse and was not.** Daily hits ran 8, 21, 42,
  613, 286, 740, 39, 40, 396, 766, 782, 155, 87, 135, 592, 217, 139, 73, 95.
  Reading the low tail as a trend produced a confident wrong diagnosis; the
  error rate held ~3% throughout. **This is why step 5 says two scans.**
- **One retired URL 404'd 25 times** — long after it left the sitemap and disk.
  Google re-checks dead URLs for a long time; if nothing links to it, a 404 is
  the correct answer and needs no fix. Check what references it before acting.
- **`/env.js`, `/secrets.json`, `/.env.backup` requested by "Googlebot"** —
  vulnerability scanning wearing Googlebot's name, and precisely why step 3 is
  not optional.
