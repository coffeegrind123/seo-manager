# Data sources — what is free, what actually works, and what it costs

Most SEO pipelines run on SerpApi or DataForSEO. Neither is required here.
This page is the measured free-first ladder that replaces them.

**Everything below marked "verified" was tested from inside a container on a
datacenter IP on 2026-07-30.** That matters: half the "free SEO API" advice on the
internet fails from exactly that position, and finding out mid-run costs a whole
research cycle. Where something failed, the failure is recorded too — a negative
result you can trust is worth as much as a positive one.

---

## SERP — "what is on page 1?"

The authority gate is the single most important measurement in the whole system,
so this is the ladder that matters most.

| Provider | Cost | Key? | Status | Notes |
|---|---|---|---|---|
| **`serpd`** (local SERP daemon) | free | no | ✅ **verified - the fast path** | The same headed Chrome, held open behind a small HTTP server. One curl returns a fully scored result; `/batch` did **25 queries in 37s**. Compact verdicts are **1.4KB where the full payload is 164KB**. Start with `serpd.py --start`. See below. |
| **`browser`** (real Google via browser MCP) | free | no | ✅ **verified working** | Real Google page 1 with positions, titles, URLs, the AI-Overview flag and People-Also-Ask. Verified returning 8 clean results at the exact moment every HTTP provider was refusing. Cannot run unattended from a script — `serp.py --provider browser` prints a hardened recipe, and you pipe the result back through `serp.py --score-json -`. |
| **`ddg`** (DuckDuckGo HTML) | free | no | ⚠️ **works, then refuses for a while** | The scripted default. Real, relevant results, ~10/page. Not Google, so treat ordering as *directional*. Goes into blanket HTTP 202 refusal under load — **see below, this is the fact that will bite you.** |
| **`serper`** | **2500 free credits** (one-off, no monthly reset) | yes | ✅ **verified live** | Real Google, **1 credit per search**, organic + PAA + related searches. The cheapest real-Google query available here — reach for this first among keyed providers. `SERPER_API_KEY` or `~/.serper_key`. |
| **`serpapi`** | free tier 250/mo | yes | ✅ **verified live** | Real Google, **top-100 in one credit**, **AI Overview inline** (confirmed present in a live response). Spend its 250 on checks that need DEPTH or the AI-Overview read; use `serper` for volume. `SERPAPI_KEY` or `~/.serpapi_key`. |
| **`brave`** | ⚠️ **card required** | yes | ⚠️ **not free-without-a-card any more** | Usage-billed at $5/1k requests with $5/month in credits; a key needs an active subscription. See "The keyed SERP providers" below. `BRAVE_SEARCH_API_KEY`. |
| **`marginalia`** | free | no | ✅ **verified keyless** | `api.marginalia.nu/public/search/<q>` answers 200 JSON with no key at all. A genuinely independent index that deliberately favours non-commercial pages — **useless for the authority gate** (it down-ranks exactly the commercial results the gate counts) but a real seam for information-gain research. |
| **`dataforseo`** | paid | yes | untested here | Real Google, live, per-request billing. |
| **`searxng`** | free (self-host) | no | ⚠️ **public instances blocked** | A *self-hosted* SearXNG with `format: json` is an excellent free aggregator. Public ones are not: `searx.be` answered with an antibot challenge, `priv.au` with 429. |
| ~~Bing~~ | — | — | ❌ **actively hostile — never add it** | See the wrong-query warning below. |

### ❌ Bing returns clean results for a DIFFERENT query

**The most dangerous thing measured in this whole exercise, and the reason the
relevance guard exists.**

Asked for `self hosted rank tracker` through residential exits, Bing returned:

- via a French exit — 7 well-formed `<li class="b_algo">` blocks about
  *"Accueil - Société d'ergonomie"*, *"Self - Wikipedia"*, *"Self. - Zalando"*;
- via a German exit — 10 blocks about *"SelfService - Universität Münster"*,
  *"Self Möbel: Wohnen"*, *"Self. online bei ZALANDO"*.

HTTP 200. No captcha. ~122 KB. Ten rows that parse perfectly. It had latched onto
the single token **"self"** and thrown away "hosted rank tracker".

Every naive guard passes this: the status is 200, the result count is 10, and
*"at least one query token appears in the results"* is **true for every row**.
Feed it to the authority gate and you score a stranger's SERP.

So the guard measures two things, and both fixtures are kept as regression tests:

| | coverage | hit_rate |
|---|---|---|
| Bing (fr) — wrong query | 0.25 | 0.00 |
| Bing (de) — wrong query | 0.25 | 0.00 |
| DuckDuckGo — same query, good read | 1.00 | 0.90 |
| Browser/Google — same query, good read | 1.00 | 0.75 |

- **coverage** — how many *distinct* query tokens appear anywhere in the top 10.
  This is the one that catches single-token latching.
- **hit_rate** — what fraction of results carry at least half the query tokens.

Thresholds are coverage ≥ 0.6 **and** hit_rate ≥ 0.3, sitting in the middle of a
very wide gap. A failing read returns `ok: false` with the observed titles
attached and **exits 3**.

### `serpd` - the SERP daemon

`scripts/serpd.py` puts a long-lived headed Chrome behind `127.0.0.1:8791`.
Measured on this box:

| | |
|---|---|
| single `/serp` | ~1.4-2 s, fully scored |
| `/batch` of 25 | **37 s, 25/25** |
| full payload for 25 | 164 KB |
| **compact verdicts for 25** | **1.4 KB** |

`/batch` therefore defaults to `view=verdict` - authority count, weakness
signals, relevance, AI-Overview flag and the top 3 domains, which is everything
the quality bar reads. Ask for `"view":"full"` only when you actually need
titles and snippets.

```bash
python3 scripts/serpd.py --start                      # idempotent
curl -s 'http://127.0.0.1:8791/serp?q=rank+tracker&depth=20&view=verdict'
curl -s -X POST localhost:8791/batch -d '{"queries":["a","b"],"depth":20}'
curl -s -X POST localhost:8791/reset                  # unwedge (see below)
python3 scripts/serpd.py --status | --stop            # --status now reports orphan chrome
python3 scripts/serpd.py --stop --force               # also SIGKILL chrome + clear the portfile
```

`serp.py --provider serpd` uses it too, and `--fallback` prefers it over every
keyed provider when it is up, because it is real Google.

#### ⛔ Never start it with a trailing `&`, and never poll it with `pgrep -f`

Two shell mistakes, both measured on 2026-08-01, that between them cost ~15
minutes and a whole research run:

- **`serpd.py --start &` inside a tool call is silently lost.** `--start` already
  detaches the server itself (`Popen(start_new_session=True)`), so the `&` buys
  nothing and the *poller* — the part that reports whether it actually came up —
  dies with the shell. You get no error and no daemon. Run `--start` in the
  foreground and read its JSON; it returns in ~2s on a warm profile.
- **`pkill -f seo-serpd-profile` kills the shell running it.** The pattern
  appears in that shell's own command line, so `-f` matches it. Same reason
  `pgrep -f gen-seo-pages` waits on itself forever. If you must match on a
  pattern, use the bracket trick (`pgrep -f '[s]eo-serpd-profile'`) or let
  `--stop --force` do it — it scans `/proc` and filters `--type=` children, so it
  cannot match itself.

#### ⛔ "The daemon won't start" is almost always an orphan Chrome (fixed, but know the shape)

The failure looked like `chrome did not bind CDP port <N> within 60s`, and it
recurred **on every retry, permanently**. Cause: the portfile is written only
*after* a successful CDP bind, so any crash between launch and bind leaves a
live Chrome on the profile whose port nothing recorded. Chrome's singleton then
hands each new launch off to that instance and exits, so the freshly-chosen port
is never bound. Deleting the `Singleton*` lock files (which the old code did)
cannot help — it removes the lock while leaving the process.

`ensure_chrome()` now reconciles this first: it scans `/proc` for browser
processes on our profile, **adopts** one whose CDP is live and whose proxy state
matches (reading `--proxy-server` off its own argv, which cannot disagree with
reality the way a stamp can), and **kills** any other. Measured: the wedged state
went from "fails after 60s, forever" to recovering in **1 second**.

If you ever see it again, `--status` now shows the wedge directly rather than a
bare `not running`:

```json
{"ok": false, "error": "not running",
 "chrome_on_profile": [{"pid": 1149391, "port": 49237}],
 "hint": "…the next --start will adopt or clear it automatically…"}
```

**Four things that cost real debugging here, all worth knowing:**

- **`/dev/shm` is 64 MB in this container.** Chrome's renderers use it, three
  tabs rendering Google exhausted it, and the browser died with
  `ConnectionRefusedError` on the CDP port and *no crash line in the log*.
  `--disable-dev-shm-usage` fixes it outright: the batch went from **1m46 with
  2 failures to 11s with 0**. That flag is NOT a fingerprinting signal - unlike
  `--disable-gpu`, which sits in the same "low memory" preset and DOES matter.
  Take one, leave the other.
- **`websockets` sends keepalive pings and closes on a missed pong.** A tab
  idling behind the batch stagger missed one, and the symptom was a mid-flow
  `ConnectionClosedError` that looked like Chrome dying. CDP needs no
  application keepalive: `ping_interval=None`.
- **`/json/version` is single-threaded and stalls under a burst.** Creating the
  tab pool all at once made 4 of 6 batch items fail with "cannot read
  /json/version" while Chrome was perfectly healthy. The browser websocket URL
  is fixed for the process lifetime - fetch it once, cache it, and pre-warm the
  pool serially.
- **An abandoned request does not stop.** A client that disconnects mid-batch
  leaves its worker threads running and holding tab locks, and because
  `BaseHTTPRequestHandler` only logs on *response*, the wedged requests never
  appear in the log at all - `/health` answers fine while every `/serp` hangs.
  `POST /reset` rebuilds the pool; the lock timeout is 75 s so a wedge surfaces
  before the caller's own timeout.

### What the headed browser actually launches (verified by `ps`)

Same trick the `veikkaus-browser` skill uses, and for the same reason: a headed
browser loads Cloudflare/Turnstile-protected pages that `--headless=new` cannot
clear at all.

Inspected on the live process (2026-07-30):

```
/bin/google-chrome                       # real Chrome 150, not chromium/headless-shell
--user-data-dir=/tmp/seo-serp-profile    # persistent: cookies + challenge clearance survive
--window-size=1440,900
--load-extension=/opt/zendriver-mcp/extensions/ublock
--no-sandbox --remote-debugging-port=...
# NO --headless, and NO --disable-gpu
```

Two things worth knowing:

- **uBlock Origin is already side-loaded by the MCP itself.** No setup needed —
  fewer trackers and ad frames to render, and a real extension is more
  human-looking than a bare profile.
- **The MCP is zendriver-based**, the same library veikkaus drives directly, so
  the anti-detection patches come along for free.
- The profile path keeps this browser's cookies separate from any other
  session's; `/tmp/veikkaus-headed` and `/tmp/seo-serp-profile` run side by side
  without touching each other.

`low_memory=false` matters here: that flag set adds `--disable-gpu` and software
WebGL, which are themselves automation signals.

### The DuckDuckGo 202 — and what does *not* fix it

Exhaustion returns **HTTP 202 with a ~14 KB anomaly page** — not a 429, and not
an empty result set. Everything else about it contradicts the obvious guesses:

- **It is NOT per-IP.** Six fresh residential exits across six countries
  (`nl se it pl ie cz`) all returned 202 in the same minute a direct request did.
  **A proxy does not rescue DuckDuckGo.**
- **It is NOT the request shape.** POST and GET, the full browser header set and a
  bare User-Agent, `html.duckduckgo.com` / `lite.duckduckgo.com` /
  `duckduckgo.com/html` — all 202 together.
- **It IS time-based.** The same client got 10 clean results ~20 minutes earlier
  and again ~40 minutes later.

**What to do:** `serp.py` retries with backoff (4/10/25s), tries one proxy
rotation in case a given block happens to be exit-scoped, then falls through
`--fallback` to any keyed provider, and finally returns the **browser handoff**.
Failing over to `--provider browser` is the reliable answer, not a workaround.

### Measured failures — do not waste a cycle rediscovering these

- **Google over HTTP is not "blocked" — it is JS-only.** HTTP 200 and ~90 KB with
  **zero `<h3>`, zero `/url?q=` links**, no consent page and no captcha. Identical
  direct, through a residential proxy, with a Googlebot UA, and with or without
  `num=`. **No proxy or header trick changes this**; the browser is the only way.
- **Brave / Startpage / Mojeek / Ecosia over HTTP**: JS-rendered or blocked.
  Brave's 311 KB response was CSS, not results.
- **`searx.be` with `&format=json`**: HTTP 200, browser-verification challenge.
- **urllib cannot tunnel HTTPS through this proxy.** Every request died with
  `IncompleteRead(~7900 of ~29000 bytes)` or `RemoteDisconnected`, across
  `Connection: close`, identity encoding and gzip. The identical request through
  **curl** and the same proxy returned the full body — which is why `serp.py`
  uses curl as its transport.

**If you write a new provider: log the raw bytes, a shape verdict, AND a
relevance verdict before trusting a parse.** A wrong guess about a SERP does not
fail loudly — and the worst case is not an empty page, it is a full page of
somebody else's results.

---

## Proxies — what they are actually for

If you have a rotating-residential proxy, its real value here is narrower than it
first looks. Measured against one commercial provider; the shapes generalise:

| Use | Verdict |
|---|---|
| **Sustained browser/serpd SERP volume** | ✅ **the main reason to have one** — see below |
| **Geo-pinned SERPs** — how does this page rank *from Germany* | ✅ real feature |
| Getting around **DuckDuckGo's 202** | ❌ measured not to work |
| Making **Google HTML** scrapeable | ❌ it is JS-only, not IP-gated |

**Google rate-limits the headed browser too, after enough queries.** Around 45
checks into a testing session, consecutive queries started returning `/sorry`
from the datacenter IP. Pointing the daemon at the residential proxy took the
exact same batch from **0/2 to 4/4 immediately.** So: occasional checks are fine
direct; a real research run's sustained checking wants the proxy.

Chrome's `--proxy-server` cannot carry credentials and has no headless way to
answer an auth prompt, so `serpd.py` starts a **local unauthenticated CONNECT
forwarder** that injects `Proxy-Authorization` on the way out, and points Chrome
at that. It also sets `--webrtc-ip-handling-policy=disable_non_proxied_udp` so
the real IP cannot leak over WebRTC. Just set `SEO_PROXY_URL` (or `SERPD_PROXY`)
before `serpd.py --start`.

**Adoption is proxy-aware.** A Chrome already running *without* a proxy is not
reused when a proxy is now configured — it is relaunched. Silently adopting it
would mean believing you were on a residential exit while every request went out
on the datacenter IP.

### Setup

Env first, so the skill stays portable:

```bash
export SEO_PROXY_URL='http://USER:PASS@proxy.example-provider.com:8080'  # HTTP CONNECT
```

…or a `~/.seo-proxy` file (**chmod 600**) with the same `KEY=VALUE` lines. Keep
credentials in one of those two places — never in a committed file, and never in
this skill.

Prefer an **HTTP CONNECT** endpoint (commonly :8080) over SOCKS5 (commonly
:1080): SOCKS5 endpoints have been measured intermittently dropping streamed
responses, and HTTP CONNECT is what curl and the browser want natively.

### Sticky sessions, not per-request rotation

Many residential providers carry session selectors on the **password**,
`_`-joined. A common shape:

```
<pass>[_country-<cc>]_session-<6char>_lifetime-<min>
```

Same token → same exit IP; no token → a new exit **every request**. Rotation
sounds better and is not: it breaks any engine that does a redirect + cookie hop,
because each hop lands in a different country. `serp.py` is sticky per run and
rotates only on demand.

⛔ **Do not hand-roll the selector to test it — build the URL with `serp.Proxy`.**
Measured 2026-08-01: a hand-written probe that appended
`_country-de_session-abc123_lifetime-10` to the password returned a **US** exit
for every country asked for, which reads exactly like "this account has no
geo-targeting". It was the probe that was wrong. The same countries through
`serp.Proxy(url, country=cc).url()` resolved correctly on the first try —
`de → DE`, `nl → NL`, `gr → GR`, and no selector at all rotating across RU and
KR as designed. A **reused session token pins the exit that token already has**,
and it outranks a country selector added afterwards, so a fixed literal token in
a test silently defeats the thing the test is checking. `Proxy._token()` mints a
fresh one per run for exactly this reason.

```python
import serp
p = serp.Proxy(serp._read_proxy_file()["SEO_PROXY_URL"], country="de")
# curl -x p.url() https://ipinfo.io/json   -> {"country": "DE", ...}
```

**Country pool** (`--proxy-country`): whatever your provider actually serves —
verify it rather than trusting the marketing list. On the provider this was
measured against, `gr se nl it de es pl ro pt be at cz dk ie ch` all resolved and
**`us` did not**: asking for it silently returned a random non-US exit, so a
US-pinned request would have quietly lied about its geo. `serp.py` refuses `us`
for that reason. Re-check this for your own provider before trusting a geo-pinned
result.

**Credentials never appear in output.** Payloads log
`proxy: "proxy.example-provider.com (session ab12cd, de)"` and nothing more; curl's stderr
is swallowed for the same reason.

---

## Keyword expansion — "what do people actually type?"

| Source | Cost | Key? | Status | Notes |
|---|---|---|---|---|
| **Google Autocomplete** (`suggestqueries`) | free | no | ✅ **verified working** | The free-mode research primitive, and what every "free keyword tool" is built on. Real queries, ordered roughly by popularity. **No volume numbers.** `keywords.py expand` sweeps it with modifier sets, question forms, comparison forms, audience/constraint qualifiers, problem strings, tool-intent verbs, and optional a–z alphabet soup. |
| **YouTube autocomplete** | free | no | ✅ available | `keywords.py expand --source youtube`. A different demand shape — useful for video-led niches and for spotting how people phrase a problem out loud. |
| **Search Console queries** | free | service account | ✅ (via `search-console` skill) | **The single best free seam.** Queries this domain *already* earns impressions for, at position 11–50, are proven relevant and half-ranked. `keywords.py gsc <export.json> --band striking-distance`. |
| **DataForSEO keyword ideas** | paid | yes | — | The only source here with real monthly volume + KD. `keywords.py volume`, max 5 seeds per call — seed it with **mid-tail phrases, never head terms** (see the research workflow, step 1.5). |

### The demand proxy — what it is and what it is not

`keywords.py expand` returns `demand_proxy` (0–100), computed from prefix depth,
autocomplete rank, and how many separate sweeps surfaced the phrase.

**It is an ORDINAL signal. It is not a monthly search volume, and it must never be
reported as one.** Autocomplete surfaces a query because enough people type it and
Google orders the list roughly by popularity — that is real information, and it is
enough to rank candidates against each other. It cannot tell you whether the top
one gets 200 searches or 20,000.

When there is no volume data at all, the quality bar's volume floor and KD ceiling
are **data gates that are simply inapplicable** — not gates the candidate failed.
The SERP-weakness test and the best-answer test carry the decision instead.
**Never invent a number to fill the gap.**

---

## Domain authority — "how strong is this site?"

The whole quality bar scales off this one number.

| Source | Cost | Key? | Status | Notes |
|---|---|---|---|---|
| **DataForSEO backlinks summary** | paid | yes | — | What the original uses. `rank / 10` → DR. Also gives referring domains, backlinks, spam score. |
| **Open PageRank** | **free** | yes (free) | ✅ **verified end-to-end 2026-08-01** | **The recommended free substitute, and the only free referring-domain count in this skill.** Built from the Common Crawl link graph. Free plan **30,000 domains/month, 100 domains per call**, monthly history back to 2018. Set `OPENPAGERANK_API_KEY` or drop the key in `~/.openpagerank_key` (chmod 600). |
| **Cloudflare Radar** | free | yes (any CF token) | ✅ **verified working** | `radar/ranking/domain/<d>` returns a popularity **bucket** (`200`, `>200000`) from 1.1.1.1 resolver traffic. A completely independent second opinion on authority, and it answers for domains Common Crawl has never seen. |
| **Keyless estimate** | free | no | ✅ **verified working** | `authority.py` composite from domain age (RDAP), live page count (the site's own sitemap), and Search Console footprint. **Capped at 25 on purpose.** |

### ⚠️ Open PageRank MOVED — the old signup is closed (measured 2026-08-01)

`domcop.com/openpagerank/` **no longer accepts new signups at all**. OPR was
acquired by Keywords Everywhere and the live service is now at
<https://openpagerank.keywordseverywhere.com/>. Existing legacy keys keep
working on the old endpoint **until 2026-09-30**, then stop.

Anyone following the old instructions gets a dead end, so:

| | legacy | current |
|---|---|---|
| host | `openpagerank.com/api/v1.0/getPageRank` | `openpagerank.keywordseverywhere.com/v1/domains/bulk` |
| auth | `API-OPR: <40-hex>` | `Authorization: Bearer opr_live_...` |
| free tier | 1000 req/day | **30,000 domains/month** |
| per call | 1 domain | **100 domains** |
| returns | score + rank | score + rank + **referring_domains** + **monthly history since 2018** |

`authority.py` **auto-detects which key you have** from its prefix and speaks
the matching protocol, so a legacy key needs no config change. Registration is
two steps and takes about five minutes: get a free Keywords Everywhere API key
at <https://keywordseverywhere.com/first-install-addon.html> (emailed link),
then sign in with it at the OPR site and create an `opr_live_` key.

⚠️ **A free KE key does NOT buy KE keyword volume.** Measured: their
`get_keyword_data` endpoint returns **`402 Insufficient Credits`**. The key is
only a login for the OPR free tier. Do not add KE as a volume provider.

### ⛔ `found: false` is NOT authority zero

The single most important detail in this integration. OPR returns an explicit
`found` flag, and a domain absent from the Common Crawl link graph gets **no
score at all** — which is a different thing from a score of zero.

Measured on the real API: `google.com` 10.0 (2,242,263 referring domains),
`github.com` 9.5 (269,173), `tildes.net` 3.93 (128) — and a small, real, live
site of our own came back **`found: false`**: genuinely absent from the link
graph, not weak.

So `from_openpagerank()` treats `found: false` as a **failed read** and falls
through to the keyless estimate, exactly like an HTTP error would. Mapping it
to `dr: 0` would hand the quality bar a fabricated measurement, and because
`dr` drives the KD ceiling and the volume band, that one substitution would
silently re-scope every keyword decision that follows. In `--bulk` competitor
tables it stays `null` for the same reason: rendering an unmeasured competitor
as 0 sorts it below you and reads as "weaker than us", which is the opposite
of "unknown".

### Why the estimate is capped

Being wrong **downward** costs a few keywords a stronger site could have won — the
next run picks them up. Being wrong **upward** queues keywords the site loses on,
burns build slots, and buries the evidence under three weeks of settling time. So
a keyless estimate never opens the DR-35 ceiling. Get a real number if you want
the high band.

### RDAP gotcha (measured)

`rdap.org` redirects to the TLD registry, and the .com registry **403s a browser
User-Agent**. A plain tool UA (`seo-manager/1.0`, `curl/8.x`) gets a 200. `curl`
also needs `-L` — without it you get the bare redirect and a JSON parse error that
looks like "RDAP is broken".

### Deliberately not used: the `site:` operator

Page-count estimates from `site:example.com` are unreliable by Google's own
statement, and blocked from datacenter IPs anyway. `authority.py` reads the site's
own sitemap instead — it is the site's own claim, it is free, and it does not lie
about its provenance.

---

## Google Trends — the JSON API is 429, the RSS feed is NOT

❌ The **Trends JSON API** answers **HTTP 429 from this container**, both by curl
and through the browser MCP — it rate-limits by IP reputation and a datacenter
address starts throttled.

✅ **The `trending/rss` feed is a different surface and it works.** Measured
2026-08-01, in the same container, in the same minute the JSON API was still
refusing: **HTTP 200, ~21 KB, 10 items**, each carrying an `approx_traffic`
band. That is the whole reason `trendfeeds.py` exists — the radar had no
quantitative signal at all, and it turned out one was reachable the entire time
behind a different URL.

```bash
python3 $SEO/trendfeeds.py trending --geo US --limit 25
```

⚠️ **It is NATIONAL trending-now, not your niche.** A typical read is football,
politics and celebrity names. Filter it against the site's facets before
treating anything in it as a signal, and remember `approx_traffic` is a floor
band (`"200+"`), not a keyword's search volume.

**A 429 is an unavailable signal, not a dry niche.** Never report "no trends
found" when what happened is "Trends refused to answer" — `trendfeeds.py`
returns `ok: false` with an explicit `REFUSED, not empty` note precisely so the
two cannot be confused.

## Topic demand — Wikimedia pageviews (keyless, and *absolute*)

✅ **verified working.** Real daily counts with years of history, no key:

```bash
python3 $SEO/trendfeeds.py wiki --topic "<your topic>"          # resolve the title first
python3 $SEO/trendfeeds.py pageviews --article "<Exact_Article_Title>" --days 90
```

Measured: a mid-size topic returned 78,820 views over 91 days, 866/day
average, a peak of 1,948 on one day, and a first-half vs second-half change of
−2.0% → `flat`.

Two things make this genuinely better than Trends for the radar: the numbers
are **absolute counts, not a 0–100 index**, so two topics are directly
comparable; and there is no rate limiting to work around.

**It measures interest in a TOPIC, not queries typed at Google.** It cannot
satisfy the quality bar's volume floor — that floor stays a data gate that does
not apply until you have real volume data. Never report a pageview count as a
search volume.

⚠️ Resolve the article title before asking for views. A wrong title returns a
clean **404**, which the script surfaces as `ok: false` with a hint rather than
as zeros — verified against a deliberately bogus article.

## Where a niche argues — HN + StackExchange

✅ Both keyless, both verified. `trendfeeds.py discussions --query "..."`
returns Hacker News (Algolia) stories with points/comments and StackExchange
questions with score/views/answered, from any SE site (`--site gaming`,
`webmasters`, `stackoverflow`, …).

❌ **Reddit is blocked** — `403` on `/r/<sub>/top.json` from this container,
direct and via `old.reddit.com`, with a browser UA and with a tool UA. Reading
it needs an OAuth app or the browser MCP. HN + StackExchange cover the same
"what is this niche actually stuck on" seam without an account.

---

## Search Console — use the skill, not a new integration

The `search-console` skill already does this properly: service-account JWT + curl,
no MCP server, no dependencies. It covers sitemaps, index status, and the
search-analytics queries that matter (striking distance, cannibalisation, CTR
underperformance, lost traffic).

**Do not build a second GSC integration here.** Export its JSON and pipe it into
`keywords.py gsc`.

This is also the **only free source of real, absolute demand data for this
domain** — impressions are actual counts, not estimates. On a site with any
history at all, it beats every keyword database for deciding what to write next.

---

## Your own server log — the best source here, and the only first-party one

Added 2026-08-01. Everything else on this page asks a third party what it
thinks. The access log is the site's own record of what happened: free,
complete, unrateable-limited, and invisible to competitors.

| Question | Answered by |
|---|---|
| Where does Googlebot spend crawl budget? | `crawllog.py scan` → `top_silos` |
| What status codes were bots served? | `crawllog.py scan` → `status`, `top_errors` |
| Do AI crawlers read us, and which kind? | `crawllog.py scan` → `by_category` |
| Was that really Googlebot? | `crawllog.py verify` |
| Which sitemap URLs are never fetched? | `crawllog.py urls` + `gap` |
| Which backlinks actually send people? | `backlinks.py referrers` |

**`--remote user@host` ships the script over ssh and aggregates on the server**,
so a gigabyte of decompressed log never crosses the wire — only the JSON verdict
comes back. Measured: 1,105,065 lines, 0.0% unparsed, ~2 minutes.

⛔ **Quote the `--glob`.** ssh joins everything after the host into ONE string and
hands it to the *remote* shell, which re-expands it. An unquoted glob arrives as
twenty positional arguments and argparse rejects the lot. Same class of mistake
as the `serpd.py --start &` trap above.

⛔ **`socket.gethostbyaddr` does not work in this container and fails as a
plausible "no".** Docker's resolver answers A records fine and drops PTR
entirely, raising the same exception for "does not exist" as for "cannot ask".
The first run of `crawllog.py verify` therefore reported **every** Googlebot IP
as spoofed — including `192.178.6.102`, which reverses perfectly to
`crawl-192-178-6-102.googlebot.com` over DoH. Verification now goes over
**DNS-over-HTTPS** (Cloudflare, then Google) and runs a mandatory control
(`8.8.8.8 → dns.google`); a failed control downgrades every verdict to `unknown`
rather than `spoofed`.

**Three verdicts, not two.** `verified: null` means no verdict was available —
the operator publishes IP ranges instead of rDNS (OpenAI, Anthropic, Meta), or
the PTR has no forward record (measured on AhrefsBot's `*.ahrefs.net`). That is
**not** evidence of spoofing, and only a *contradicting* forward answer is.

---

## Common Crawl — presence, not backlinks

| Endpoint | Status | Notes |
|---|---|---|
| `index.commoncrawl.org/collinfo.json` | ✅ verified | lists current index ids. **Resolve from here; never hardcode an index.** |
| `<index>-index?url=…&matchType=domain` | ✅ verified | control `example.com` answered in 4.2s with real captures |
| an aged-out index | ⚠️ `504 Gateway Time-out` | unavailable index, **not** an absent site |
| a bad index id | ⚠️ `404 No index found for collection` | a DIFFERENT 404 from the one below |
| a genuinely absent domain | ✅ `404 No Captures found` in ~4s | clean, fast, and correct |
| `…/domain-ranks.txt.gz` (host graph) | ❌ **2,385,402,702 bytes** | real link edges live here and it is not fetchable on demand. Do not build on it. |

Two different 404s mean opposite things, so `backlinks.py footprint` runs a
control lookup and reports `unknown` rather than `absent` when it fails.

**Why it is worth measuring at all:** Common Crawl is the corpus a large share of
LLM pretraining and AI retrieval is built on. A site absent from CC is invisible
to every tool that reads CC — which is *upstream* of everything `geo-scan`
measures downstream. You cannot be cited from a corpus you are not in.

**There is still no free backlink index.** Ahrefs, Majestic, Semrush and
DataForSEO are all paid, and nothing here pretends otherwise. The free
substitute is `backlinks.py referrers` — weaker in coverage (a link nobody clicks
is invisible) and **stronger in quality**, because every row is a link a real
person actually followed.

---

## The keyed SERP providers — what registration actually costs (measured 2026-08-01)

The table at the top lists `serpapi`, `brave` and `dataforseo` as upgrades. All
four were taken as far as they go from a container; here is where each one
actually stops, so nobody budgets ten minutes for a signup that cannot be
finished unattended.

| Provider | Free tier | Signup blocker | Outcome |
|---|---|---|---|
| **Serper.dev** | 2,500 one-off credits | **visible reCAPTCHA v2 image challenge** on submit (400×580 panel), on top of a Turnstile that solves itself | ✅ **landed** (owner solved the CAPTCHA) |
| **SerpApi** | 250 searches/month | same — reCAPTCHA v2 challenge renders on submit | ✅ **landed** (owner solved the CAPTCHA) |
| **Bing Webmaster Tools** | genuinely free; real volume + the only free backlink data | requires **verified ownership** of the domain | ✅ **landed** (owner imported from GSC) |
| **Brave** | ⚠️ **no longer a flat free tier** — see below | key minting requires an *active subscription*, and the only plans are usage-billed | ❌ **needs a card** — account created + verified, stops there |

Three of the four are in. The two CAPTCHA gates each cost the owner about
twenty seconds; everything either side of them — form fill, verification mail,
emailed OTP — ran unattended.

### ⚠️ Brave's free tier changed — "2000 queries/month" is stale

Measured on the live dashboard: the Search plan is now **$5.00 per 1,000
requests** with **"free $5 in credits every month, automatically applied"** —
i.e. ~1,000 free requests/month, not 2,000, and structured as credit against a
usage-billed plan rather than as a free plan.

The consequence matters more than the number: `/app/keys` says *"Go to the
Available plans page to subscribe to a plan before generating API keys"*, and
every plan is usage-billed, so **a payment method is required before any key
exists**. An account can be created and verified for free (this was done end to
end — register → email verify → login → emailed OTP → dashboard), and it stops
there. Under a "free only, no card" rule Brave is **out**, despite the
effective cost being zero.

### Bing Webmaster Tools is worth it, but only the owner can do it

It is the one genuinely free source of **backlink data**, plus keyword research
with real volume — for sites you have **verified ownership of**. That
verification is the whole point and the whole blocker: it needs the site
owner's own Microsoft or Google identity, not a throwaway. The fastest route
for an existing site is *Import from Google Search Console*, which reuses a GSC
property that is already verified.

**This is an owner action. Do not attempt it with a disposable identity** — a
throwaway account cannot verify a domain it does not own, and would produce an
account with no data in it.

### Signing up for the ones that need a human

Two of the four just need someone to click a CAPTCHA. Everything else can be
automated, including reading the confirmation mail — that is what
[tempmail-cli](https://github.com/coffeegrind123/tempmail-cli) is for:

```bash
ADDR=$(tempmail new)
SINCE=$(date +%s)                       # BEFORE triggering the signup
# ... fill the form, human solves the CAPTCHA, submit ...
tempmail wait "$ADDR" --match 'verif|confirm' --since "$SINCE"
tempmail code <id>          # emailed OTP
tempmail links <id> --match verif --first
```

Verified against the real thing: the Brave flow needed an email verification
link *and* a 6-digit login OTP, and both came out of that loop without a human
touching an inbox.

## Bing Webmaster Tools — real volume and real backlinks, free

**The most valuable addition to this skill, and the only source here that
answers two questions nothing else could.** Landed 2026-08-01 against a
verified property. `scripts/bing.py`.

| Question | Command | Status |
|---|---|---|
| How much demand does this query have? | `bing.py keyword --q "..."` | ✅ **real impression counts** |
| What related queries exist, and how big? | `bing.py expand --seed "..."` | ✅ **expansion WITH numbers** |
| Who links to us? | `bing.py backlinks` | ✅ works (empty on a new property) |
| What do we appear for on Bing? | `bing.py queries` | ✅ works |
| Clicks/impressions over time | `bing.py traffic` | ✅ works |

Measured live (90d, us/en-US): `running shoes` → **10,681 impressions /
87,198 broad**; `chess openings` → 3,188 / 6,466. And
`expand --seed "chess openings"` → **27 related queries** ordered by real
counts: `chess.com` 357,780 · `chess reps` 2,259 · `chessreps` 1,755 ·
`great openings` 1,203 · `best chess openings` 1,056 · `chess moves` 788.

That expand result also shows what the ordering is worth and where it stops: it
surfaces a navigational giant (`chess.com`), a genuine long-tail phrase
(`best chess openings`), and a misspelling cluster (`chess reps`/`chessreps`)
in one call — but it will not tell you which of those your product can answer.
The remit test still runs first.

### ⛔ These are BING impressions. They are not Google search volume.

The single rule that governs this whole integration. Bing is a minority engine
— single-digit share in most markets, and skewed by demographic and device in
ways that vary per niche. A Bing number **cannot** be converted into a Google
number without a multiplier this skill does not have and must not invent.

So `bing.py` reports `bing_impressions` and **never** `volume`, and **the
quality bar's volume floor is NOT applied to it.** Writing a Bing impression
count into a keyword's `volume` field would silently re-scope every gate that
reads it — the same class of error as mapping Open PageRank's `found:false` to
DR 0.

What it is legitimately good for, and this is a real upgrade on what came
before:

- **Relative ranking of candidates.** If A gets 10× the Bing impressions of B,
  that ordering is real information — and it is the first demand signal in this
  skill backed by *counts* rather than by autocomplete position. The
  `demand_proxy` was always explicitly ordinal; this is ordinal too, but with a
  far better-grounded ordering.
- **A floor on absolute demand.** A query with real Bing impressions has real
  demand somewhere. **The converse does not hold** — near-zero on Bing does not
  prove near-zero on Google.

### Three things measured that the docs get wrong

- **Dates are plain ISO `YYYY-MM-DD`.** The SOAP-era `/Date(1780000000000)/`
  form is rejected with `String was not recognized as a valid DateTime`, which
  reads like a parameter-name problem and is not.
- **A query Bing has never seen returns `Query: null` with zeroes**, so
  `bing.py` reports `known_to_bing: false` and `bing_impressions: null` rather
  than a measured zero. Verified with a nonsense-term control.
- **Legacy SOAP and POX APIs retire 2026-08-31** (Microsoft's own banner). The
  JSON endpoint used here, `ssl.bing.com/webmaster/api.svc/json/<Method>`, is
  the surviving one.

### ⚠️ Empty is only trustworthy because of the control

`backlinks` on a freshly imported property returns empty arrays. That is a
**real** answer and not a broken call — proven by asking for a site we do NOT
own, which returns `NotAuthorized` (ErrorCode 14) instead of empty. Any future
endpoint added here must keep that property, and `bing.py` attaches an
explicit `empty_means` field saying *"authorised and genuinely empty — Bing has
no link data for this site YET"*, because on a new property that is the normal
state and **not** a finding about the site's backlink profile.

Getting a key is an **owner action** (it requires verified domain ownership):
sign in at <https://www.bing.com/webmasters>, import the property from Google
Search Console, then **Settings → API Access → Generate API Key**. The key is
**per user, not per site**, and only one can exist at a time — regenerating
breaks everything using the old one.

## The 2026-08-01 expansion — eleven keyless sources, all measured live

Everything in this section was probed from this container on 2026-08-01 and is
registered in **`scripts/providers.py`**, which is now the single place a data
source is declared. `python3 scripts/providers.py status` probes the whole
ladder for real and prints what is usable *right now* — measurement, not a
table that went stale. `seodoctor.py` reports credential presence cheaply and
`seodoctor.py --providers` runs the live sweep.

Live result at the time of writing: **23–24 usable of 24.**

⚠ **Two members are genuinely intermittent, and a red mark on either is usually
the service, not you.** Both retry internally; neither is worth chasing.

- **crt.sh** — measured 404, 404, 502 inside one 10-second window on a query
  that returns ~1,100 rows when healthy, then four straight failures on a later
  sweep. Its 404 is **ambiguous** on a real domain (no certificates vs unhappy
  server), which is why crt.sh is only ever used to enumerate subdomains it
  does return, and **never to assert that a domain has none**.
- **GDELT** — 429s under load, then answers seconds later. The parallel status
  sweep aggravates this; a single real call rarely trips it.

### Keyword expansion is now SIX independent corpora, not one

| Engine | Endpoint | Why it is not redundant |
|---|---|---|
| Google | `suggestqueries` | the base corpus; everything is compared to it |
| **Bing** | `api.bing.com/osjson.aspx` | independent index *and* audience, keyless |
| **DuckDuckGo** | `duckduckgo.com/ac/` | third web corpus, no personalisation |
| **YouTube** | `suggestqueries…&ds=yt` | **video intent** |
| **Yandex** | `suggest.yandex.com/suggest-ff.cgi` | fourth engine; dominant RU/TR |
| **Amazon** | `completion.amazon.com` | **product/buying intent** |

`keywords.py expand --engines all` sweeps them together and adds two fields:

- **`engine_agreement`** — how many independent corpora surfaced this exact
  phrase. Four engines out of five is corroboration in a way that "rank 1 on
  Google autocomplete" is not, because the engines share neither audience nor
  algorithm. Still **ordinal, still not a volume.**
- **`intent_evidence`** — `video` if YouTube's corpus surfaced it, `product` if
  Amazon's did. Intent is normally *guessed* from the wording of a phrase; this
  is **observed**, and it beats the guess when the two disagree.

⚠ **A silent engine is never counted against a phrase.** Agreement is scored
against `engines_answering`, not against the engines requested — otherwise one
dead endpoint quietly re-scores every candidate as weakly corroborated. The
response lists `engines_silent` explicitly. There is a regression test for it.

### Authority gained a keyless second opinion: Tranco

`tranco-list.eu/api/ranks/domain/<d>` — no key, no account, and it carries
**~40 days of daily history**, so a competitor's rank becomes a *trajectory*.
It is the only authority signal that still answers on a machine with no
credentials at all.

⚠ It is a **popularity** rank, not a link-graph score and **not a DR**. It sits
in `payload.popularity` beside Cloudflare Radar and is never folded into
`dr_equivalent`. A mismatch between the two is itself information: heavy
traffic + thin links is a brand searching for itself, not a site that will
outrank you on a new query.

**Absence is safe to report here, and only because of the control**: a domain
that is certainly not in the top 1M answers **HTTP 200 with an empty `ranks`
array**, while an outage does not answer 200 at all. So `in_list: false` is a
measurement; a non-200 is an error and says so.

### Trends: a per-topic timeline at last (GDELT), plus Google News

The trend radar had no way to ask "how has interest in THIS phrase moved" —
Trends RSS only answers "what is spiking nationally right now" and Wikimedia
pageviews only work for topics with an article.

- **`trendfeeds.py newsvolume`** — GDELT's daily news-coverage timeline for any
  phrase, keyless, months of history, with a rising/falling/flat read.
  ⚠ It measures **press coverage, not search demand.** A rise is a reason to go
  *check* demand, never demand itself.
  ⚠ It **429s under shared-IP load and then answers fine seconds later**
  (measured: first call 429, retry 200 with 86 datapoints). It retries by
  default; a single-shot call reports a permanent failure that is transient.
- **`trendfeeds.py news`** — Google News RSS. Doubles as research: it names the
  publishers currently ranking for the phrase, i.e. who owns the topic.

### Information gain got a starting point: `factcheck.py`

The hardest rule in the quality bar to satisfy honestly, and the easiest to
fake. Four keyless subcommands:

| Command | Source | What it gives |
|---|---|---|
| `sources` | OpenAlex + Crossref | peer-reviewed work with citation counts, years, DOIs, OA links |
| `entities` | Wikidata | what a topic formally *is*, with types and descriptions |
| `related` | Wikipedia `morelike` | the article neighbourhood — what a thorough page would touch |
| `coverage` | the above, vs a draft | which strongly-related concepts the draft never mentions |

⚠ **These are candidates to READ, not citations to paste.** Citing a paper this
returned without opening it is the same fabrication the quality bar forbids —
with a DOI attached, which makes it worse, not better.

⚠ **`coverage` matches WORDS, not meaning.** A draft covering a concept in
different vocabulary scores as a gap; one that name-drops a term without
explaining it scores as covered. Every gap is a prompt to think, never a verdict
— and "fixing" a gap by inserting the phrase is precisely the template
convergence the sameness gate exists to catch. Verified with both controls: an
unrelated draft scores 0%, a draft naming the concepts scores 100%.

### Technical checks for any URL, keyless: `pagecheck.py`

| Command | Source | Notes |
|---|---|---|
| `html` | W3C Nu validator | validity only matters where it breaks something that *is* a ranking input |
| `schema` | **validator.schema.org** | Google's own extractor, keyless, any public URL |
| `history` | Wayback CDX | when a page actually changed — including a competitor's |
| `vitals` | PageSpeed Insights | the one that needs a credential; see below |

⚠ **The schema validator's node shape is `typeGroup` + `types`, NOT `type`.**
The obvious parser reads `node["type"]`, gets `None` for every node, and reports
a page full of perfect structured data as having **none at all**. Caught only by
running it against `nytimes.com`, which certainly has some. Nested objects live
under `nodeProperties`, so the walk must recurse or it under-counts. Both are
pinned by tests.

⚠ **Wayback CDX: a positive `limit` returns the OLDEST N rows.** On a long-lived
URL that answers every recency question with the year 2003 — `example.com`
capped at 20,000 reported `last_capture: 2022` and "0 changes since 2026-01-01"
while the page had been captured that morning. A **negative** limit gives the
most recent N, which is the window anyone actually wants. When the window is
truncated, `versions_since.reliable` says so rather than quietly answering.

⚠ **`distinct_versions_in_window` is byte-level variation, not editorial
rewrites.** `collapse=digest` only merges *adjacent* identical digests, so a
rotating ad slot or a footer timestamp mints a new row on every capture —
`example.com` shows 63,263 of them across 24 years. Compare pages against each
other; never quote the raw count as "times updated".

### PageSpeed Insights: ✅ LIVE — and it needed no new key at all

The account already has a Google service account (the Search Console one). PSI
accepts an OAuth bearer, so no API key is needed — but **the scope you mint with
decides whether you see the real problem**:

| Scope | What PSI says |
|---|---|
| `cloud-platform` | 403 *"Request had insufficient authentication scopes"* |
| `webmasters.readonly` | 403 *"Request had insufficient authentication scopes"* |
| **`openid`** | 403 ***"PageSpeed Insights API has not been used in project &lt;PROJECT_NUMBER&gt; before or it is disabled"*** |

Only the third is the truth. The first two read like "you need a different
credential" and send you looking for a key that was never the blocker. So
`psi_token()` mints with `openid` **deliberately** — a worse-looking error that
is a far more useful one.

**Enabled 2026-08-01 and verified working** — `pagecheck.py vitals` returns lab
metrics and a performance score against the existing GSC service account, with
no API key anywhere.

⚠ **ALWAYS use the project-PINNED enable link.** The bare console URL applies to
whatever project the browser last had selected, so the first enable silently
landed on a different project and PSI kept 403ing with an identical message.
Google puts the correct link in the error itself — use that one verbatim:

```
https://console.developers.google.com/apis/api/pagespeedonline.googleapis.com/overview?project=<PROJECT_NUMBER>
```

⚠ **Do not diagnose that state by retrying.** Six attempts over 2.5 minutes
looked exactly like slow propagation and was not — it was the wrong project.
The distinguishing check is to confirm the project NUMBER named in the 403 is
really the service account's own project, rather than assuming it:

```bash
# the number in the error vs the project the service account belongs to
python3 -c "import json,pathlib; print(json.loads(
  pathlib.Path('~/.gsc_service_account.json').expanduser().read_text())['project_id'])"
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://cloudresourcemanager.googleapis.com/v1/projects/<PROJECT_ID>" | grep projectNumber
```

⚠ **The service account cannot read its own API enablement state** — it lacks
`serviceusage.services.get` and answers *"Permission denied to get service"*.
That is a permissions gap, **not** evidence the API is disabled, and reading it
as one sends you to fix the wrong thing.

`GOOGLE_API_KEY` (or `~/.google_api_key`) is honoured as an alternative and
takes precedence — quicker than the console dance if a key already exists.

### ✅ CrUX field data arrives INSIDE the PSI response — the CrUX API is moot

Confirmed live: `pagecheck.py vitals` returns `field_crux` with 75th-percentile
**LCP, CLS and INP from real users**, plus a FAST/AVERAGE/SLOW category, out of
PSI's `loadingExperience` block. So the separately-rejected CrUX API (which
would not take a service-account bearer) buys nothing — one call now yields both
lab and field data.

⚠ **Empty `field_crux` is not a score of zero.** Measured on a real low-traffic
site: every field metric came back `null` while the lab metrics were fully
populated. CrUX only reports origins with enough real-user traffic to be
statistically meaningful, so `null` means *"too little traffic to measure"* —
which is a fact about sample size, never a fact about the page's speed. The lab
numbers are still valid; they are one synthetic run, and the field numbers are
the ranking-relevant ones when they exist.

⚠ Keyless PSI stays **429** (*"Quota exceeded … for consumer project_number…"*)
— the anonymous quota is exhausted at the shared level, and **the proxy does not
fix it** (measured below).

### ⛔ The proxy fixes none of these — measured, so nobody re-tries it

Every failure above was re-probed through the residential proxy, with the exit
IP confirmed different from the direct one *before* the run — otherwise the
whole comparison proves nothing. The result was unambiguous:

| Source | Direct | Proxied | Verdict |
|---|---|---|---|
| Reddit (search / sub / api) | 403 | 403 | not IP-shaped |
| Qwant | 403 | 403 | not IP-shaped |
| yep.com | 403 | 403 | not IP-shaped |
| PSI keyless | 429 | 429 | quota is per-*project*, not per-IP |
| ConceptNet | 502 | 502 | server-side |
| DBpedia Spotlight | conn fail | conn fail | service down |
| crt.sh | 200 | 200 | works either way |

The proxy's value remains exactly what the section above says — **sustained
SERP volume and geo-pinning** — and nothing else here. Do not spend a session
re-testing these behind it.

---

## Evaluated and REJECTED — do not re-add these

Each was probed from this container on 2026-08-01. Recording the negatives so
the same "free SEO API" shortlist does not get re-litigated every few months.

| Candidate | Why not |
|---|---|
| **Datamuse** | Looks perfect for keyword expansion and is not. It is a **word-level thesaurus**, not a query expander: `ml=browser+game` returned `elk`, `eland`, `smap`; `rel_trg` on a hyphenated phrase returned `[]`; `sug` returned `[]`. Google Autocomplete already does this job properly. |
| **Keywords Everywhere volume API** | `402 Insufficient Credits` on a free key. Volume/CPC is strictly paid. Free key is a login for OPR only. |
| **PageSpeed Insights (keyless)** | **429 from this container** — the keyless quota is exhausted at the shared-IP level. Needs a Google API key, which needs a GCP project + console access. |
| **Google Custom Search JSON** | Genuinely 100 free Google queries/day, but needs a GCP API key **and** a CSE id. The GSC service account **cannot** mint one (`apikeys.googleapis.com` → 403), so it is an owner action, not something this skill can self-provision. |
| **Reddit JSON** | `403` from this container by every route and UA tried. |
| **Wayback CDX** | Timed out at 25s and 30s on repeated attempts. |
| **Mojeek / Bing Search API** | Mojeek's JSON needs a requested key; Microsoft retired the Bing Search APIs in 2025. |
| **Moz / Ahrefs / Majestic / Semrush** | No free API tier that returns link data. Unchanged. **Moz's Links API free tier (2,500 rows/mo) is real but requires a credit card to register**, so it fails the same free-without-a-card rule that ruled out Brave. |
| **Common Crawl host-level web graph** | Rejected on **measured cost, not availability**. The domain-ranks file for one release is **3.5 GB gzipped** and is not indexed by domain, so a single lookup streams a large fraction of it. Open PageRank is built from the *same* link graph and returns the referring-domain count in one call — paying 3.5 GB for a second opinion on data you already have is not a trade worth making. |
| **Reddit JSON** | Still `403` from every route tried — `www/search.json`, `old.reddit.com`, `api.reddit.com`, with a compliant UA. **Re-tested through the residential proxy: still 403**, so it is not IP-shaped and a proxy will not rescue it. The official OAuth API (free, script app) is the only route left, and it is an owner action. |
| **Qwant / yep.com** | `403` behind an antibot page, direct and proxied alike. |
| **ConceptNet** | `502` on repeated attempts, direct and proxied. Also word-level, like Datamuse — the same reason that one was rejected. |
| **DBpedia Spotlight** | Both `api.` and `demo.` hosts failed to connect. Entity extraction now goes through Wikidata + Wikipedia `morelike` instead (`factcheck.py`). |
| **searchmysite.net** | `404` on every documented API path tried. |
| **CrUX API** | `400 "Request contains an invalid argument"` for every documented body shape, with a service-account bearer. Unlike PSI it never names a fixable cause, so there is nothing to act on. **Moot now**: real-user LCP/CLS/INP arrive inside the PSI response (`loadingExperience`) — verified live — so one call gives both lab and field data. |
| **Google Knowledge Graph Search API** | `403 "Method doesn't allow unregistered callers"` — it requires an actual API key and will not take an OAuth bearer, so the existing service account cannot reach it. |
| **Majestic Million / Tranco / Umbrella top-1M CSVs** | All three download fine keylessly (80 MB / ~10 MB / ~10 MB). Not integrated as bulk files because **Tranco's per-domain API answers the same question in one keyless call with history attached**. Revisit only if bulk scoring of thousands of domains becomes a real need. |

## Related skills to reach for

| Skill | Use it for |
|---|---|
| `search-console` | index status, sitemaps, real impressions/clicks/position, striking distance |
| `browser-automation` | real Google SERPs, AI Overview reads, filling directory submission forms |
| `seo-audit` / `seo-audit-full` | on-page technical audit of a single URL — this skill's `build-guide` handles new pages, those two handle existing ones |
| `adsense` | monetizing the content the pipeline publishes (placement, CLS, consent, ads.txt, and reading the earnings back) |
| `claude-design` | when a guide's visual components or a tool's UI need real design work |

---

## Environment variables, all optional

```bash
# SERP
export SERPAPI_KEY=...              # 250 free searches/month, real Google, top-100
export BRAVE_SEARCH_API_KEY=...     # 2000 free queries/month
export SEARXNG_URL=https://...      # your own instance, json format enabled
export SEO_SERP_PROVIDER=ddg        # default provider for serp.py

# Residential proxy - geo-pinned SERPs + a non-datacenter exit for the browser
export SEO_PROXY_URL=http://USER:PASS@proxy.example-provider.com:8080

# Volume / KD / backlinks (paid)
export DATAFORSEO_LOGIN=...
export DATAFORSEO_PASSWORD=...

# SERP - real Google (dotfile fallback shown; env wins)
export SERPER_API_KEY=...            # ~/.serper_key   - 2500 free credits, 1/search
export SERPAPI_KEY=...               # ~/.serpapi_key  - 250/month, top-100 + AI Overview

# Bing Webmaster - real volume + backlinks for a VERIFIED property
export BING_WEBMASTER_API_KEY=...    # ~/.bing_webmaster_key

# Domain authority (free key, 30k domains/month)
#   also read from ~/.openpagerank_key (chmod 600) if the env var is unset
export OPENPAGERANK_API_KEY=opr_live_...

# Cloudflare Radar domain popularity - ANY Cloudflare API token works
export CLOUDFLARE_API_TOKEN=...        # or ~/.cloudflare_token

# PageSpeed Insights / Core Web Vitals. NOT required if a Google service
# account exists AND the PSI API is enabled on its project - see above.
export GOOGLE_API_KEY=...              # or ~/.google_api_key
export GSC_SERVICE_ACCOUNT=...         # default ~/.gsc_service_account.json

# Where the provider layer caches (Tranco, archive reads, etc.)
export SEO_CACHE_DIR=~/.cache/seo-manager

# State root override (defaults to the nearest .seo/ or .git/)
export SEO_ROOT=/path/to/site/repo
```

With **none** of these set, the pipeline still runs end to end: DuckDuckGo for
SERPs, autocomplete for expansion, the keyless composite for authority, Search
Console for real demand. That is the configuration this skill is designed around —
everything above it is an upgrade, not a prerequisite.
