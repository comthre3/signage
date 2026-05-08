# Khanshoof Marketing Orchestration Brief

**For:** Paperclip agent orchestrator
**Brand:** Khanshoof (خنشوف) — bilingual digital signage SaaS for Gulf small-business owners
**Status:** Product is live (api/app/play/yalla.khanshoof.com). Launch-marketing has NOT started.
**Languages:** English + Arabic. AR uses MSA for system/professional surfaces and Kuwaiti dialect on playful microcopy. NEVER translate the brand wordmark "Khanshoof" — it's the phonetic anchor; Arabic spelling "خنشوف" is used inside Arabic prose.

---

## 1. Product context the agents must internalize

**What Khanshoof is.** A self-serve SaaS that turns any TV / tablet / Android-box / spare laptop into a digital menu board, ad screen, or info display. Owner pairs the screen by typing a 6-character code on their phone. Content (videos, images, menus, playlists) is uploaded once in the admin and pushes to all paired screens.

**Who it's for.** Cafés, clinics, kiosks, salons, retail, gyms, and small-format restaurants in Kuwait + GCC. Owners who currently:
- Print A3 posters and tape them to the wall
- Pay a designer KWD 50–150 for a single menu redesign
- Use a Smart TV's "USB photo slideshow" hack
- Or use Western signage SaaS that doesn't speak Arabic, doesn't take KNET, and prices in USD

**Pricing (KWD primary):**
| Tier | KWD/mo | Screens | USD ≈ |
|---|---|---|---|
| Starter | 3 | up to 3 | $9.80 |
| Growth | 4 | up to 5 | $13.07 |
| Business | 8 | up to 10 | $26.14 |
| Pro | 15 | up to 25 | $49.02 |
| Enterprise | Contact us | 25+ | — |

**5-day free trial. No card required to start. Cancel anytime.**

**Differentiator (Phase 3 — early-access list now, ship later):** Paste your shop's URL or Instagram. Khanshoof reads your menu, extracts your brand palette + logo, drafts a polished playlist ready to publish. The thing that kills competitors' "ten-hour designer bills."

**Mascot:** Pixel-art artichoke (the name "Khanshoof" plays on خرشوف = artichoke + Kuwaiti expression خل نشوف = "let's see"). The mascot already appears on landing, admin header, and player pairing screen with multiple expressions (smile, kawaii, wink, heart). Use them across creatives — they're the brand's visual fingerprint.

**Tone — bilingual rules:**
- **English:** warm, dry, confident. Tech-literate but not corporate. Closer to Linear / Stripe / Notion's voice than to Salesforce.
- **Arabic — MSA:** clear, professional, polite. Never colonial-formal, never academic.
- **Arabic — Kuwaiti dialect:** allowed on hero headlines, FAQ questions, social captions, ad hooks, mascot speech bubbles. Vocabulary: بشلون (how), شغّال (works), ودّك (you want), ليش (why), شو (what), زين (good/right). Sample landing hero already in production: «شاشات شغّالة بدون وجع راس».
- **Mixed scripts:** "Khanshoof" stays Latin in EN, switches to خنشوف when fully embedded in Arabic prose. Numbers and prices stay Latin in both languages (audience reads "3 KWD" natively; "٣ د.ك" feels academic for pricing).

**Live surfaces (use these in creatives, link them in ads):**
- Landing: https://yalla.khanshoof.com
- App / signup: https://app.khanshoof.com (CTA → `#signup`)
- Player demo: https://play.khanshoof.com

---

## 2. Goals + KPIs (90-day window from first activation)

| Layer | Metric | Target |
|---|---|---|
| Acquisition | Visits to yalla.khanshoof.com | 8,000 / mo by month 3 |
| Acquisition | Trial signups | 200 / mo by month 3 |
| Activation | Trial → first screen paired | ≥ 60% |
| Conversion | Trial → paid | ≥ 18% |
| Revenue | Paid orgs at end of month 3 | 100+ |
| Brand | IG followers @khanshoof.kw | 5,000 |
| Brand | Branded search "Khanshoof Kuwait" | top result |

KPIs are aspirational; the orchestrator's job is to instrument tracking from day one (UTM hygiene, Plausible/PostHog on landing, Meta pixel, Google Tag).

---

## 3. Agent roster and responsibilities

The orchestrator should spawn FIVE specialist agents. They work as a team with the marketing manager as the only one with cross-cutting authority.

### 3.1 Marketing Manager (the orchestrator-of-marketing-orchestrators)
**Role:** Owns the calendar, reviews every other agent's output before it ships, owns KPIs, manages budget allocation.

**Inputs:** This brief + weekly performance digest from Campaigns + Social agents.

**Outputs (weekly):**
- Monday content+campaign brief that the other four agents pull from
- Friday performance digest (top 3 posts by engagement, top 3 ad sets by CAC, anomalies)
- Monthly retro with budget reallocation proposal

**Tools needed:** Read access to Plausible/PostHog, Meta Ads Manager, Google Ads, TikTok Ads Manager, Buffer/Hootsuite. Write access to a shared Notion/Linear/Obsidian board (whichever your stack uses).

**Cadence:** Daily standup with all agents (15min digest in chat). Weekly planning Sunday evening (Kuwait business week starts Sunday).

**Authority:** Approves all budget changes >KWD 50, all creative going live, all campaign launches.

---

### 3.2 Content Creator
**Role:** All long-form and medium-form copy. Bilingual.

**Outputs:**
- 2 blog posts/week on yalla.khanshoof.com/blog (one EN, one AR — NOT translations of each other; each speaks to its audience natively)
  - EN topics: "Why your café's TV menu kills your conversion", "Smart TV vs. Android box vs. Raspberry Pi for digital signage", "From paper menus to digital — a Kuwaiti café's 30-day playbook"
  - AR topics: «بشلون تخلي شاشتك تطلع كاش», «دليل المطاعم في الكويت لتحويل المنيو لشاشة بنص ساعة», «خنشوف vs المنيوهات الورقية»
- Email drip sequences:
  - **Trial onboarding (5 emails over 5 days):** day-0 welcome, day-1 "pair your first screen", day-2 "upload your first 3 items", day-3 mid-trial check-in, day-4 conversion CTA. Bilingual — locale follows org locale.
  - **Win-back (3 emails over 14 days post-trial-expiry)**: discount code, customer story, last call.
- Landing-page copy variants for A/B testing (hero headlines, CTAs, social-proof blocks)
- Video scripts for the Ad Designer (15s, 30s, 60s)
- Customer stories — interview a real café owner monthly, publish as a blog + IG carousel + 60s video

**Voice rules:** EN = warm/dry/confident. AR = MSA on system flows, Kuwaiti dialect on hero/CTA/playful. NEVER auto-translate one to the other; each language gets first-class copy.

**Constraints:**
- No stock corporate phrases ("revolutionize your business", "leverage synergies", «نقدم لكم حلولاً متكاملة»). Specific verbs, concrete nouns, real numbers.
- Always close with a CTA that links to https://app.khanshoof.com/#signup or the relevant live surface.
- Compliance: every customer-quote needs written permission before publishing.

**Tools:** Notion/Obsidian for drafts, Resend or Mailchimp for email send, Cloudflare Pages for blog publishing, Grammarly + a native-Arabic proofreader (human or LLM) for AR.

---

### 3.3 Ad Designer
**Role:** All visual and video creative. Bilingual.

**Outputs:**
- Static ads: Instagram Feed (1080×1080, 1080×1350), Stories (1080×1920), Meta Feed, TikTok-friendly verticals
- Video edits: 6s, 15s, 30s, 60s. Mix of (a) screen-recording walkthroughs with motion-tracked phone+TV mockups, (b) talking-head café-owner testimonials (when content is available), (c) animated mascot vignettes
- Carousels: 5–10 slide IG carousels for "how it works", "before/after", "feature deep-dives"
- Print: A4 / A5 flyers for café-district door-drops + co-marketing with KNET-branded signage suppliers
- Brand kit: color tokens, font specimens, mascot pose library, Plex Sans + Plex Sans Arabic specimen sheet

**Visual rules:**
- Pastel palette already established: cream / butter / peach / mint / lavender / rose / plum. Match exactly — pull tokens from `landing/styles.css :root` block.
- Mascot expressions: smile (default), kawaii (cute hero), wink (playful), heart (success), all in `khanshoof_assets/`
- Type: IBM Plex Sans (EN), IBM Plex Sans Arabic (AR), IBM Plex Serif for display headlines
- NO stock photos of generic-corporate-people-pointing-at-screens. Real screens, real menus, real Gulf storefronts. Source from real customers when possible; otherwise stylized illustrations + mascot.
- Pricing in creatives: "3 KWD/mo" (Latin digits, KWD primary). Optional secondary "≈ $9.80 USD" for international/expat-focused ads.

**Bilingual constraint:** Every creative ships in EN AND AR. RTL-aware composition (don't blindly flip — sometimes the layout needs redesigning, e.g. arrows that point at things should reverse direction; mascot can stay un-flipped).

**Tools:** Figma + Canva for static, CapCut/Descript/After Effects for video, Adobe Firefly or Midjourney for generative b-roll only with brand-safe prompts, the brand kit in `khanshoof_assets/`.

---

### 3.4 Campaign Manager (paid acquisition)
**Role:** Allocates budget, builds audiences, runs experiments, attributes results.

**Channels and starting splits:**
| Channel | Starting % of paid budget | Why |
|---|---|---|
| Meta (IG + FB) | 45% | GCC small-business owners live here |
| TikTok Ads | 25% | Younger café/clinic owners; high reach in Kuwait |
| Google Search | 20% | Branded + competitor + intent ("digital menu Kuwait", «شاشة منيو») |
| Snapchat | 5% | Strong in Kuwait/KSA for hospitality |
| LinkedIn | 5% | Boutique B2B for clinics + premium retail |

**Audiences to seed:**
- Lookalike of the first 100 paid customers (build once we have them)
- Interest-targeted: small business owners in Kuwait, café/restaurant managers, clinic admins, retail franchises
- Geo: Kuwait first (Salmiya, Hawalli, Mubarak Al-Abdullah, Salwa, Bayan), then Bahrain → Qatar → KSA
- Retargeting: landing-page visitors who didn't sign up (7-day window), trial-started-but-no-screen-paired

**Experimentation cadence:**
- Always run two creatives per ad set. Kill the loser at 100 impressions if CTR diff > 30%, else 500.
- Weekly creative rotation — content/ad-design agents must keep a 4-week pipeline ahead.
- Monthly landing-page A/B test (Plausible-tracked).

**Reporting (weekly to Marketing Manager):**
- CAC by channel
- Top 3 creatives by CTR / CPC / signup conversion
- Anomalies (spend spikes, audience exhaustion, frequency >3.5)
- Budget reallocation recommendation for next week

**Constraints:**
- Hard cap: monthly paid budget ≤ KWD X (Marketing Manager sets this; default starting cap KWD 1,500/mo across all channels).
- No "engagement campaigns" or "page likes" goals — every campaign optimizes for a downstream conversion (trial signup, paid conversion, app install if/when relevant).
- Every ad's destination URL gets a UTM tag: `?utm_source=meta&utm_medium=cpc&utm_campaign=<name>&utm_content=<creative-id>`.
- Compliance: KNET / Niupay logos can be shown as "we accept" badges only after written approval; don't fake endorsements.

**Tools:** Meta Business Manager, Google Ads, TikTok Ads Manager, Snapchat Ads Manager, LinkedIn Campaign Manager. Plausible/PostHog for landing attribution. Optional: a Notion board for creative approval workflow.

---

### 3.5 Social Media Expert
**Role:** Daily presence on @khanshoof.kw across IG, TikTok, X, LinkedIn, Snapchat. Replies to DMs and comments. Builds a Kuwait small-business community.

**Posting cadence (per channel):**
| Channel | Posts/wk | Format |
|---|---|---|
| Instagram Feed | 4 | 2 carousels, 1 single image, 1 reel |
| Instagram Stories | 7 (daily) | Mix of polls, behind-the-scenes, customer reposts |
| TikTok | 3 | Vertical video, mascot vignettes, café-owner reactions |
| X (Twitter) | 5 | Quick wins, build-in-public, replies to Kuwait F&B accounts |
| LinkedIn | 2 | Founder voice, B2B product updates |
| Snapchat | 2 | Stories featuring Gulf customers using Khanshoof live |

**Content pillars (apply across channels):**
1. **"Built in Kuwait"** — process posts, screenshots, founder commentary (40%)
2. **"Real shops, real screens"** — customer features, before/after (30%)
3. **"How it works"** — quick tutorials, tips, 5-minute setups (20%)
4. **"Mascot Mondays"** — pure brand-character content, no CTA (10%)

**DM + reply playbook:**
- All DMs in the language they came in (don't AR→EN auto-translate)
- Sales questions → handoff to a calendar booking link or trial-signup CTA, NOT to the agent's own answer
- Bug reports / complaints → escalate to founder within 1 hour
- Comments: always reply, even with an emoji, within 4 hours during Kuwait business hours (8am–10pm AST)

**Trends to ride (rolling):**
- Kuwait F&B influencer reposts when they show their menus (offer free Pro upgrade for anyone who tags)
- Ramadan / national-holiday signage angle (timely seasonal content)
- TikTok "POV: you're a Kuwaiti café owner..." trends

**Constraints:**
- Voice: friendly, local, not too corporate. AR posts use Kuwaiti dialect when the topic is playful.
- Never use auto-translation in posts. Every Arabic post is written natively (or has been native-reviewed).
- Reposts of customer content require their explicit permission first.
- No engagement-bait ("comment YES if you agree"). No follow-for-follow.

**Tools:** Buffer or Hootsuite for scheduling, Meta Business Suite for IG, native TikTok Studio, Linkup or Linkboost for LinkedIn drip, ChatGPT / Claude for first-draft caption ideation (always human-reviewed before posting).

---

## 4. Bilingual operating model — how the team handles EN+AR together

This is the trickiest part. Defaults:

- **Every campaign brief from the Marketing Manager is bilingual from the start.** Not "ship EN first, translate later." Both versions are first-class.
- **Each agent has an internal AR reviewer step.** If the agent itself drafts AR, a second pass (human native or a separate LLM agent specialized in Arabic copywriting) reviews before publishing.
- **Channel localization rules:**
  - IG / TikTok: post BOTH languages on different days (don't double-up — algorithm penalizes near-duplicate). EN audience targets expat / international; AR audience targets local-language Kuwaitis. Same campaign, different audiences, different posts.
  - LinkedIn: EN-primary (B2B audience). One AR post per week for visibility.
  - X: bilingual — quote-tweet your own EN post with AR commentary, or vice versa. Both versions in one thread is fine.
  - Email: respect `org.locale` — backend already returns it. EN trial gets EN drips, AR org gets AR drips.
- **Geo overlay:** ads in Kuwait → AR-primary, EN-secondary. Ads in Bahrain/Saudi → AR-primary. Ads in UAE → AR + EN parity. Future: ads in Egypt → MSA (no Kuwaiti dialect on hero — the dialect doesn't travel).

---

## 5. 90-day campaign calendar (orchestrator-fillable template)

The Marketing Manager generates this weekly; below is the seed template.

### Month 1: "Khanshoof exists" (awareness)
- **Week 1:** Soft launch — one founder post on each channel, no paid spend. Track organic baseline.
- **Week 2:** First paid sprint — Meta + TikTok, KWD 200 total, optimizing for trial signup. Two ad creatives per channel, A/B'd.
- **Week 3:** First 3 customer features (assuming we have signups). Push the strongest one as a paid creative.
- **Week 4:** Retro + reallocation. Scale winners, kill losers.

### Month 2: "Khanshoof works" (proof)
- 3 customer case-study blog posts (one per week)
- Add Google Search to the channel mix (branded + competitor terms)
- Launch the trial-onboarding email drip (5-day sequence)
- First Ramadan/Eid-themed creative (timing-sensitive)

### Month 3: "Khanshoof is the obvious choice" (conversion + scale)
- Increase budget to KWD 1,500/mo split across channels per the table above
- Launch the AI-menu-generation early-access waiting list as a separate campaign (Phase 3 buzz)
- First influencer partnership (one Kuwait F&B account, one micro-clinic account)
- Affiliate / referral program kickoff: 1-month free for both referrer + referee

---

## 6. Output formats the orchestrator should expect from each agent

To make the agents inter-operable, standardize outputs as JSON-tagged Markdown:

```markdown
---
agent: content-creator
date: 2026-04-30
locale: ar
channel: blog
campaign: month1-awareness
status: draft
---
# Title in Arabic

[body content...]

## CTA
[link]

## Reviewer notes
- Native-AR reviewer: pending
- Approved by Marketing Manager: pending
```

The Marketing Manager's review step flips `status: draft` → `status: approved` and writes one-line approval notes. Anything `status: approved` is ready for the Social agent or Campaign agent to schedule/launch.

---

## 7. Risks + non-goals

**Risks the orchestrator should flag:**
- Translating EN posts directly into AR via auto-translate without native review → publishes broken Arabic, kills credibility. **Hard rule: human or specialist-LLM Arabic review before AR publish.**
- Over-promising the AI menu generator (Phase 3) before it ships → pre-orders / refund liabilities. **Rule: AI menu generation is "early access waitlist" until shipped, never "available now".**
- Mixing Kuwaiti dialect with Egyptian/Levantine on ads targeting Kuwait → reads as inauthentic. **Rule: Kuwaiti dialect ONLY on Kuwait-targeted creative. Other GCC creative uses MSA.**
- Buying followers / engagement → Meta and TikTok algorithms punish this. **Rule: only organic + paid-with-conversion-goal growth.**

**Non-goals (don't pursue these):**
- TV / radio advertising (too expensive vs. CAC math at this stage)
- Trade shows (deferred until a real showroom presence)
- English-only "international expansion" before Kuwait + nearest GCC are saturated
- Cold outreach scripts to random businesses (low conversion, brand-damage risk)

---

## 8. First week deliverable from the orchestrator

When the orchestrator boots, expected first-week output:
- [ ] Marketing Manager: a Notion/Obsidian board with month-1 calendar populated
- [ ] Content Creator: 2 blog posts drafted (1 EN, 1 AR), 1 trial-onboarding email sequence drafted
- [ ] Ad Designer: 4 launch creatives (2 IG feed, 1 IG story, 1 TikTok 15s) in EN and AR
- [ ] Campaign Manager: Meta + TikTok Ads Manager set up with pixel, conversion events, audiences seeded
- [ ] Social Media Expert: 1 founder-voice launch post on each channel + 7 IG stories drafted

Marketing Manager reviews and either approves or returns each with revisions before week-2 budget is unlocked.

---

## Appendix A — quick reference

- **Resend (transactional email):** already wired; sender `Khanshoof <noreply@khanshoof.com>`. For marketing email, get a separate verified subdomain (`marketing.khanshoof.com`) so a deliverability hit on marketing doesn't poison transactional.
- **Domain map:** root `khanshoof.com` is owned but unused; `yalla.khanshoof.com` serves landing today. Decide before launch whether to redirect root → yalla, or move landing to root.
- **Brand assets:** `khanshoof_assets/` directory in the repo root has mascot face set + logo files.
- **Existing landing copy** (use as voice reference): https://yalla.khanshoof.com — read it before writing anything new.
