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
python3 $SEO/seostate.py prospect-add --domain uneed.best \
  --url https://www.uneed.best/submit-a-tool --link-type dofollow \
  --reason "free dofollow from DR 75, real founder audience" \
  --angle "submit with the free queue, no payment"
python3 $SEO/seostate.py prospect-update <id> --status contacted
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

## Seeds from awesome-submitlist — free, listed as dofollow, NOT verified here

Imported 2026-10-03 from `alvinunreal/awesome-submitlist` (`data/destinations.json`, CC0, synced 2026-09-28 from api.submitlist.io): the 44 entries typed directory / launch site / marketplace, priced free and labelled dofollow, minus everything already above.

⚠ **The link type is the LIST's claim, not a measurement.** The same file labels all 37 of its newsletters "dofollow" (a link in an email is not a backlink) and all 44 subreddits "unknown" (Reddit links are ugc/nofollow), and it lists Capterra as dofollow where the table above has it as nofollow - Capterra is left out here for that reason. Before spending time on one, open an EXISTING listing on it and read the `rel` on the outbound link. `sitelike.org` was dropped: it is a site-value directory, the class `backlinks.py mentions` files as `machine_listings`. The DR column is the list's Ahrefs figure.

| DR | Where | Type | Free tier, per the list | Audience |
|---|---|---|---|---|
| 91 | **[Nextdoor](https://business.nextdoor.com/en-us/getting-started/business-page)** | directory | free | business |
| 91 | **[ProvenExpert](https://www.provenexpert.com/en-us/register/)** | directory | free | business |
| 86 | **[GeekWire](https://www.geekwire.com/submit-startup/)** | directory | free | business, startup |
| 86 | **[Softpedia](https://www.softpedia.com/user/submit.shtml)** | directory | free | developer-tools, saas |
| 84 | **[TrustRadius](https://solutions.trustradius.com/)** | directory | Qualifying products can be listed for free, while Customer Voice and add-ons are paid. | business, saas |
| 83 | **[F6S](https://www.f6s.com/)** | directory | A basic company profile is free; Grok notes that some premium features may be paid. | business, startup |
| 79 | **[Brownbook](https://www.brownbook.net/register/)** | directory | Listing creation, profile claims, and enhanced Profile+ accounts are free. | business |
| 79 | **[Index by Dodo Payments](https://index.dodopayments.com/submit)** | directory | Listing submission is free and is subject to Dodo Payments team verification. | indie, startup |
| 78 | **[ToolPilot.ai](https://www.toolpilot.ai/pages/submit-your-ai-tool)** | directory | The free tier requires a backlink or badge and can take up to about 90 days; Priority is about US$99 one-time and Premium starts about US$19 | ai |
| 76 | **[e27](https://e27.co/startup/create/profile/)** | directory | Basic company profiles, funding submissions, and contributor content are free; Pro membership adds visibility and networking features. | community, startup |
| 75 | **[Alternative.me](https://alternative.me/how-to/submit-software/)** | directory | Software submission is free with a user account. | indie, productivity |
| 75 | **[Gust](https://gust.helpscoutdocs.com/article/211-publishing-your-profile)** | directory | Basic profile creation, publishing, and directory visibility are free; Gust Launch and related incorporation or equity-management products a | business, startup |
| 75 | **[SaaSworthy](https://saasworthy.com)** | directory | Standard includes a basic profile and organic review capture, with higher paid plans. | business, saas, startup |
| 74 | **[SelectHub](https://pmo.selecthub.com/claim-your-product/)** | directory | The basic claim and listing path is available with paid seller programs for analyst validation, leads, and enhanced profiles. | business, saas |
| 73 | **[Landbook](https://land-book.com/submission-guidelines)** | directory | Website submissions are free; templates can have a paid feature step after approval. | design |
| 73 | **[PeerPush](https://peerpush.com/)** | product-launch-site | The free plan joins a publication queue; one-time paid plans offer immediate publication, permanent links, and optional promotion. | indie, startup |
| 73 | **[SoftwareWorld](https://www.softwareworld.co/)** | directory | Basic listings are free; paid Featured and Sponsored plans offer ranking, interviews, PR, and other promotion. | business, saas |
| 72 | **[Aura++](https://auraplusplus.com/projects/submit)** | product-launch-site | Free and no-follow tiers cost US$0; Premium costs US$29 and Premium Plus costs US$69, with stronger promotional and link benefits. | indie, startup |
| 71 | **[AI Directories](https://www.aidirectori.es/submit-ai-tool)** | directory | Submitting an AI tool to the site's own catalog is free; one-time paid packages starting around US$99 cover manual submission to external di | ai |
| 71 | **[Ecomm Design](https://ecomm.design/submit/)** | directory | The submission and about pages do not list a paid listing option. | design, ecommerce |
| 71 | **[TrustMRR](https://trustmrr.com/dashboard)** | directory | The base verified-revenue listing is free; paid add-ons include dofollow and visibility upgrades, while marketplace sale plans start at US$2 | business, saas |
| 70 | **[OpenHunts](https://openhunts.com/projects/submit)** | product-launch-site | Free Launch costs US$0 with limited weekly slots and a long queue; Premium Launch starts at about US$9.90, with higher highlight options ava | indie, startup |
| 68 | **[PitchWall](https://pitchwall.co/submit)** | product-launch-site | Free Launch has a wait of 30 days or more; Pro Launch costs US$49 and Premium Launch costs US$99 for faster or featured placement. | ai, developer-tools, saas, startup |
| 67 | **[Serchen](https://www.serchen.com/get-listed/)** | directory | Basic Get Listed and profile claim are available without a required fee; premium features and advertising are separate. | business, saas |
| 62 | **[Startup Ranking](https://www.startupranking.com/startup/create)** | product-launch-site | Startup registration and listing are free; a paid option of about $99 accelerates approval from the typical 60–80 days to 24 hours, with add | saas, startup |
| 58 | **[AI With Me](https://aiwith.me/submit/)** | directory | Free submissions are available with a longer wait and lower visibility; paid one-time and monthly plans offer faster listing, higher placeme | ai, saas |
| 53 | **[Saas AI Tools](https://saasaitools.com/submit-listing-2/)** | directory | Free submissions use a review queue; an optional featured or fast-track option costs about $67 for priority. | ai, saas |
| 51 | **[Appvizer](https://help.appvizer.com/en/articles/how-do-i-reference-software-on-appvizer)** | directory | Basic software referencing is free and without commitment; paid campaigns and lead-generation options are separate. | business, saas |
| 51 | **[eBool](https://www.ebool.com/submit)** | directory | The free queue can take about six months; one-time Premium, Pro, and Super options cost about $47, $117, and $197 for priority review and st | business, saas |
| 51 | **[VentureRadar](https://www.ventureradar.com/add_company)** | directory | The standard company profile is free and reviewed, typically within 21 business days. The add-company form also offers a $75 Premium Profile | business, saas, startup |
| 50 | **[AppAgg](https://appagg.com/add/)** | directory | Grok reported free listing submission. | developer-tools, startup |
| 44 | **[TipSeason](https://www.tipseason.com/ai-tools/submit-free)** | directory | The directory offers a free queue listing and a $99 paid priority review; the site presents the free queue as up to 90 days and the paid rev | ai, productivity, startup |
| 43 | **[Startup Buffer](https://startupbuffer.com/site/submit)** | directory | A profile can be submitted for review without payment; separate paid promotion packages offer faster or tracked placement. | business, saas, startup |
| 42 | **[BestofAI](https://bestofai.com/)** | directory | The basic Add Tool path is presented as a free account action; featured sponsor placement is published at $250 per month. | ai, saas |
| 10 | **[BroUseAI](https://www.brouseai.com/submission/ai)** | directory | Basic submission is free and enters the review queue; the optional Premium plan is a one-time $29 plus VAT and advertises review within 48 h | ai, productivity |

**App marketplaces** - a listing is only possible if you ship the integration, and then it is one of the strongest links a tool site can earn: [Google Workspace Marketplace](https://developers.google.com/workspace/marketplace/how-to-publish) (DR 100); [Microsoft Marketplace](https://learn.microsoft.com/en-us/partner-center/marketplace-offers/submit-to-appsource-via-partner-center) (DR 96); [HubSpot App Marketplace](https://developers.hubspot.com/docs/apps/developer-platform/list-apps/listing-your-app/app-marketplace-listing-requirements) (DR 93); [Slack App Directory](https://api.slack.com/apps) (DR 92); [Visual Studio Marketplace](https://code.visualstudio.com/api/working-with-extensions/publishing-extension) (DR 92); [Make App Directory](https://www.make.com/en/technology-partners) (DR 90); [n8n Integrations](https://docs.n8n.io/integrations/creating-nodes/deploy/submit-community-nodes/) (DR 90); [Wellfound](https://wellfound.com/recruit/all-features/post-a-job) (DR 87); [Spot SaaS](https://www.spotsaas.com/get-listed) (DR 67).


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
