# R8: competitive SEO quality and domain decision

Date: 2026-10-01. Owner requests the strongest feasible organic-search preparation and an inexpensive domain. This refines R8, not permission to publish, purchase, change branding or touch production.

## Outcome and boundaries
Target leadership for relevant Twitch-to-Telegram notification queries, measured per search engine, query group, country, language and device. Never guarantee rank #1, indexing, traffic or a deadline. Better technical scores alone are not ranking evidence.
Preserve the approved Taste Skill / Impeccable / Remotion visual workflow; only staging/local work, no main push, production changes, paid APIs, outreach, ads or purchases. Do not restart completed stages.
A domain purchase, public launch/indexation, webmaster-account connection and commercial brand change remain owner decisions. Domain choice does NOT change current BotFather/Railway settings automatically.

## Skill additions (project-local, pinned)
Two documentation-only skills are added under `.agents/skills`: `competitor-profiling` and `cro`. Provenance and hashes: `docs/workflows/2026-10-01-r8-seo-competition-skills-lock.json`.
Source: coreyhaines31/marketingskills commit 5b2c0007766c6a1cf1d53fd8fc73e979e0821022. Keep existing four SEO skills; no global updates and no whole-suite installation.
Read only the current task's SKILL.md and required reference. Competitor profiling's Firecrawl/DataForSEO enrichment is OPTIONAL here: use authorized public reads and cached facts; mark unavailable volume/backlink/traffic fields unknown. Do not purchase accounts, invent tool outputs or infer customer counts from traffic.
CRO is for clarity and voluntary conversion, not a ranking factor. No deceptive urgency, fake reviews, fake counters, implied official affiliation or invented revenue. Preserve useful navigation and accessible design over generic template advice.

## Research before extending pages
Create a dated competitor matrix for a small set of directly relevant services using their own pages. Initial public leads: Laver Stream Bot (https://laver.stream/en), CheshirHelper (https://cheshirskiycats.ru/en/tg-bot/), and self-hosted TwiLive (https://github.com/HarkushaVlad/TwiLive-bot). They are research candidates, not a verified top-ranking list.
Separate hosted services from self-hosted code and Telegram from Discord. Record source URL/date, audience, verified functionality, pricing visibility, help content, onboarding steps and genuine gaps. Missing documentation is not proof that a competitor lacks a feature.
Map relevant search intent to one useful page: viewer live notifications, streamer channel announcements, preview explanation, game/title filters when actually supported, setup help and troubleshooting. Inspect search results with language/region recorded; do not call this global rank tracking.
Start with roughly 20-30 query hypotheses, clustered into about 6-8 intents only where genuinely distinct. Prioritize using accessible demand evidence; otherwise label demand unknown. Search Console needs live verified property data; Wordstat/Keyword Planner may require owner accounts.
Extend the current four-page site only when the intent map supports an additional page. No near-duplicate pages per spelling, city or streamer; no copied competitor prose. Comparisons must acknowledge strengths and limitations with dated sources.
Do NOT claim category-change notifications unless actual behavior is implemented and tested: initial go-live filtering is different from sending a new notification later when a streamer switches game.
Useful differentiated content: verified screenshots, working setup walkthroughs, real limits/errors, self-hosted-vs-managed explanation and a small client-side post demo using synthetic data, reusing the planned demo rather than creating another large subsystem.

## Technical acceptance (deterministic, reusable checks)
Critical marketing text and links must be readable in server HTML without video playback/JS. Use distinct descriptive titles/headings/descriptions, valid statuses, one coherent future canonical origin, crawlable internal links, correct mobile layout and factual structured data. Never invent AggregateRating, prices, users or claim rich results are guaranteed.
Measure mobile speed in a fixed lab setup and store the report. Aim for good Core Web Vitals: field LCP <=2.5s, INP <200ms and CLS <0.1 at the appropriate percentile; local lab results are not field-data proof. Remotion uses a light poster and optional deferred playback, not a render service on the bot worker. Do not replace useful HTML with a video-only/canvas landing page.
Before public launch test for accidental noindex, robots blocks, wrong canonical origin, duplicate host/path versions, broken links/assets, redirect chains and auth leakage. While staging, preserve the no-public-indexing requirement and no production changes.
IMPORTANT existing-plan correction to review: robots.txt Disallow prevents Google from reading a page's noindex. Do not describe blanket Disallow + noindex as guaranteed exclusion. Prefer access protection for confidential staging, or an intentionally crawlable non-sensitive marketing preview with noindex where approved; document chosen policy. Authenticated admin/streamer/viewer data remains server-protected regardless of robots. Do not expose private content to make a crawler test pass.
Plan RU first unless the owner selects a broader launch market; English pages only when complete and useful, with separate URLs, language links and correct alternate-language annotations. .ru is a Russia country signal, not all Russian-speaking countries; .com is generic. Domain suffix/keywords do not buy rank #1.
No hidden text, search cloaking, keyword stuffing, fabricated reviews, purchased ranking links, automated comment spam, click/behavior manipulation, expired-domain abuse or mass low-value pages. These are excluded from 'all possible'. Paid outreach requires separate approval and appropriate link disclosure.

## Measurement and ongoing improvement readiness
Prepare a small launch checklist for Google Search Console and Yandex Webmaster/Wordstat. Connect only an authorized verified property after the public-domain decision. Do not fabricate dashboards from missing account data or bypass consent/account requirements.
Metrics: indexed eligible pages, query/page impressions and clicks, top-10/top-3 query coverage by market, landing-page -> bot start -> first subscription, verified real paid conversion only after payments launch. Current source-site cohort is not proof of exact organic query attribution. Do not link identifiable Telegram users to search histories or place IDs/tokens in analytics URLs.
Plan a reusable weekly report, but do NOT create a hidden recurring executor/automation or claim monitoring was enabled. Mark later owner authorization/account links needed. For low traffic use directional observations, not fabricated statistical A/B-test confidence.
Prioritize improvement by observed page/query gaps: high impressions with low clicks, relevant queries near top 10, pages that attract visitors but do not activate. Keep original metrics timestamped and separate lab/mock from live.

## Domain decision (research, not purchase)
Checked published tariff pages on 2026-10-01; actual checkout and renewal terms may differ. Ordinary non-premium registrations, no extra hosting assumed:
- Timeweb .ru: 200 RUB registration card / 399 RUB renewal; page FAQ still says 179 RUB, so budget the visible 200 RUB card and verify checkout. Source https://timeweb.com/ru/services/domains/
- Beget .ru: 199 RUB registration / 420 RUB renewal. Source https://beget.com/ru/domains
- Timeweb .website: 149 RUB first year / 3930 RUB renewal. Low first-year price is not low long-term cost.
- Porkbun .com: 11.08 USD registration / 11.08 USD renewal in published table. Source https://porkbun.com/products/domains
Recommendation pending owner market/name approval: .ru for a low-budget Russia-first site; .com for explicitly international positioning. Prefer a durable choice rather than a disposable promo domain. No domain, hosting, paid certificate or API is purchased.
Twitch published trademark guidelines restrict incorporating its marks into domain/product names without permission. Treat this as a prelaunch brand risk, not a reason to silently rename the bot/repository or stop unrelated staging implementation. Source https://www.twitch.tv/p/nl-nl/legal/trademark/ (the current English legal redirect returned no policy body in this check).
Optional independent-domain candidate presented for discussion: cigilsignal.ru, NOT selected. WHOIS query to the IANA-listed whois.tcinet.ru timed out; availability is UNVERIFIED. Do not infer availability from an empty search result or failed DNS. No change to current domains, OAuth redirects, or BotFather configuration.

## Primary references used
- Google SEO expectations: https://developers.google.com/search/docs/fundamentals/do-i-need-seo
- Google SEO guide: https://developers.google.com/search/docs/fundamentals/seo-starter-guide
- Spam boundaries: https://developers.google.com/search/docs/essentials/spam-policies
- Noindex interaction: https://developers.google.com/search/docs/crawling-indexing/block-indexing
- International targeting: https://developers.google.com/search/docs/specialty/international/managing-multi-regional-sites
- Core Web Vitals: https://developers.google.com/search/docs/appearance/core-web-vitals
- Yandex recommendations: https://yandex.ru/support/webmaster/ru/diagnosis/recommendations
- Yandex ranking guidance: https://yandex.ru/support/webmaster/ru/yandex-indexing/rank
- Yandex query data: https://www.yandex.ru/support/webmaster/ru/service/queries-analytic
- Skill upstream/version information: https://github.com/coreyhaines31/marketingskills

## Integrate without duplicate work
At the next safe R8 checkpoint, read this file and the two relevant skills; reconcile the existing R8 spec/plan and SEO acceptance checklist. Keep this addition separate from untested product changes, preserve the existing byte-lock policy, and commit it with provenance when ready. Continue in the same visible Work chat.
Acknowledge what was actually integrated. Do not report competitor superiority, readiness to index, selected domain, launched monitoring or a completed website merely because these instructions/skills exist. Deliver a dated comparison, intent map, tested SEO checklist and a short prioritized post-launch plan. All missing external verification stays explicit.
