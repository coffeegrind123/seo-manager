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
