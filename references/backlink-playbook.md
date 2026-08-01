# The backlink playbook

A curated, opinionated list — not a directory blast. **Five real listings beat
five hundred scraped ones**, and the difference between the two is the whole
skill here.

Researched **2026-07-13** with
every submit URL fetched live at the time. Ordered by value for a developer-tool
/ SaaS product.

> **Re-verify before working the list.** Directories die, paywall, and reprice
> constantly — TAAFT, Futurepedia and Toolify all paywalled their listings, and
> StackShare has been effectively dead since the FOSSA acquisition. Check the
> submit URL still resolves and the stated link type still matches before
> spending an hour on a submission. Say in your report which entries you
> re-verified and which you took on trust.

Personalize the copy from `.seo/profile.json` (`seostate.py profile`) — the setup
workflow writes it, and every field below maps onto a real form field.

Track each one:

```bash
python3 scripts/seostate.py prospect-add --domain uneed.best \
  --url https://www.uneed.best/submit-a-tool --link-type dofollow \
  --reason "free dofollow from DR 75, real founder audience" \
  --angle "submit with the free queue, no payment"
python3 scripts/seostate.py prospect-update <id> --status contacted
```

---

## Free — work these top to bottom

| # | Where | Link | Effort | Why it is worth it |
|---|---|---|---|---|
| 1 | **[Uneed](https://www.uneed.best/submit-a-tool)** | dofollow | 15m | The best free dofollow link available right now — Uneed advertises a dofollow backlink from a DR ~75 site, plus a real launch-day audience of founders and indie hackers. Free queue is a few weeks out; paying $29.99 buys **a date, not a better link**. |
| 2 | **A niche "awesome-*" GitHub list** (PR) | nofollow | 30m | The most audience-exact placement possible. Nofollow, but the referral traffic and AI-crawler citations are real. Read CONTRIBUTING first — curation is genuine. **Lead with your free, genuinely useful surface, not the paywall**; a bare product plug gets closed. |
| 3 | **[Dev Hunt](https://devhunt.org)** | nofollow | 15m | 100% developers, far less competition than Product Hunt, much better conversion fit for a dev tool. The audience is the point. |
| 4 | **[Product Hunt](https://www.producthunt.com/posts/new)** | nofollow | 2h+ | Biggest launch-day traffic on the internet, a permanent DR ~91 listing, strong brand-search presence. Worth **one properly prepared launch**, not a casual post. |
| 5 | **[Peerlist Launchpad](https://peerlist.io/user/projects/add-project)** | unverified | 20m | Developer-and-designer audience, weekly launch leaderboard; project pages are SEO-indexed on a DR ~76 domain with real traffic. |
| 6 | **[AlternativeTo](https://alternativeto.net/manage-item/)** | nofollow | 20m | Ranks hard for **"[competitor] alternative"** searches — an evergreen listing that keeps sending qualified visitors long after launch platforms go quiet. |
| 7 | **[SaaSHub](https://www.saashub.com/services/new)** | dofollow | 20m | Free listing on a DR ~79 domain whose "[product] alternatives" pages rank well; verified listings reportedly carry the dofollow. **Do NOT pay for Featured** — see the do-not-buy list. |
| 8 | **[Crunchbase](https://www.crunchbase.com/add-new)** | nofollow | 25m | DR ~91 profile that owns part of your brand SERP and gets cited constantly by AI answer engines. An **entity/credibility play**, not link equity. |
| 9 | **[G2](https://sell.g2.com/claim-your-profile)** | nofollow | 30m | DR ~91, ranks for "[product] reviews", quoted heavily by ChatGPT and Perplexity. The trust surface for a paid product. |
| 10 | **[Capterra / GetApp / Software Advice](https://app.g2digitalmarkets.com/get-listed/start)** | nofollow | 30m | **One free submission now covers all three** (G2 acquired the family from Gartner, Feb 2026). DR ~91 review-site trust surface. |
| 11 | **[Hacker News — Show HN](https://news.ycombinator.com/submit)** | nofollow | 30m | The single biggest potential traffic day for a dev tool if it lands, and one of the most AI-cited sites on the internet. **Zero link equity — pure traffic and credibility.** |
| 12 | **[dev.to article](https://dev.to/enter)** | mixed | 2h | DR ~90 community, ~1.4M monthly readers. A genuine "how I set this up" tutorial with your byline link reaches developers directly and indexes fast. **Write the real article; a thin plug gets ignored.** |
| 13 | **[Indie Hackers](https://www.indiehackers.com/products/new)** | unverified | 20m | DR ~81 product page plus a community where honest build-and-revenue stories outperform any ad. **The product page alone does little — participation is the value.** |
| 14 | **[Microlaunch (free queue)](https://microlaunch.net/submit)** | unverified | 15m | Month-long exposure to an indie/maker audience instead of a one-day spike. The stated "DR 60+ dofollow" claim applies to the **paid** tier. |
| 15 | **[Startup Fame](https://startupfa.me)** | dofollow* | 15m | One of the highest-DR free dofollow claims live (DR ~82, platform-stated) — **but the free tier requires their verification badge on your homepage.** Read that as a trade, not a gift. |
| 16 | **[TinyLaunch](https://www.tinylaunch.com/submit)** | dofollow* | 10m | Stated dofollow from a DR ~72 domain, **granted only when you embed their badge with a dofollow link back.** Same trade. |
| 17 | **[Launching Next](https://www.launchingnext.com/submit/)** | unverified | 5m | The fastest submission here — open form, no account. Modest authority; a good queue-filler while bigger submissions pend. |
| 18 | **[Fazier (free tier)](https://fazier.com/submit)** | nofollow* | 15m | Legit, active, DR ~80 — but the free tier requires a link back to Fazier on your homepage or footer, and the free link is likely nofollow. |

**\* badge-for-link trades.** These are honest deals, not scams, but decide them
deliberately: you are putting an outbound dofollow on your homepage to get one
back. On a young site with few outbound links that is a real cost. Take the two
best; do not take all four.

---

## Paid — only if the free list is exhausted

Ordered by ROI, and the honest answer is usually **"not yet"**. Work every free
entry above first; a paid listing on a site with three pages published converts
nothing.

| Where | Price | Link | Verdict |
|---|---|---|---|
| **Uneed — Skip the Line** | $29.99 one-time | dofollow | Best value on this list **because the dofollow is already free** (#1 above). This only buys your choice of launch date. Pay only if timing matters. |
| **Fazier — Premium launch** | $39 one-time | dofollow | Instant launch, 15 days of promotion, and **none of the free tier's badge requirement**. |
| **Microlaunch — Pro** | $49 one-time | dofollow | Featured spots, 2× vote boost, maker audience close to a dev tool's ICP. Fair exposure buy. |
| **There's An AI For That** | $49 one-time | unverified | The largest AI-tools directory. Table stakes for an AI product, and **smaller directories scrape TAAFT**, so one listing propagates. |
| **Toolify.ai** | $99 one-time | dofollow | Big multi-language AI directory; permanent presence in an ecosystem AI users actually browse. |
| **Futurepedia — Verified** | $497 one-time | unverified | Legitimate and refundable if editorially rejected, but **5–12× the price of everything above** for an unverified link type and a less developer-shaped audience. Last on the list for a reason. |

---

## Do NOT buy — every one of these is a net negative

- **"DR 50+ dofollow backlink" gigs** (Fiverr, Legiit, SEO forums) — the DR is
  manufactured; those domains inflate each other with spam links and have zero
  real readers. You buy a vanity metric, a spam footprint, and link-scheme
  penalty risk.
- **"Submit to 100+/500+ directories" blast services** ($99–299, often upsold by
  otherwise legitimate platforms) — 90%+ are zero-traffic clones scraping each
  other. A sudden burst of identical low-quality directory links **is a spam
  footprint, not a strategy.**
- **Guest-post and link-insertion marketplaces** (including upsells on directory
  sites) — paying to insert a dofollow into existing editorial content is the
  textbook link scheme Google's spam updates target.
- **SaaSHub Featured** ($99/month) — the SEO asset, the dofollow listing, is
  already in the **free** tier. The fee buys promo placement that won't clear
  $99/mo for a niche tool.
- **Crunchbase Pro** ($49–99/month) "for the link" — Pro is a research
  subscription; it changes nothing about your profile or its link. Create the free
  profile and keep the money.

---

## Filling the list with the browser

Most of these are forms. The `browser-automation` skill fills them from the
profile:

```
mcp__browser__navigate(url="<submitUrl>")
mcp__browser__find_inputs()          # numeric-id DOM walker, cheap
mcp__browser__fill_form(...)         # from .seo/profile.json
```

**Never auto-submit a listing without showing the owner the filled form first.**
A rejected submission usually cannot be retried, and several of these platforms
ban resubmissions.

---

## The copy quality bar

Directories reject on copy more often than on product. From the setup workflow's
Part 4:

- plain English, first-person-free, concrete (what the buyer gets);
- **zero hype words** — "revolutionary", "game-changing", "cutting-edge" get
  listings rejected;
- respect the length contracts exactly: tagline ≤ 60, short ≤ 160, long 300–600.
  `seostate.py profile` refuses to save copy that breaks them, which is cheaper
  than a bounce.
