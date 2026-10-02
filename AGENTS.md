# TwitchSignalBot working rules

## Scope and authority
Use the owner-requested NEW visible app chat "Мини-апп" in the existing TwitchSignalBot project and branch `autonomous/twitchsignal-roadmap`. One implementation owner; no hidden parallel CLI development or second controller.
The owner approved the roadmap and delegated reversible staging decisions. Continue spec -> plan -> implementation -> tests -> staging evidence without routine approval pauses. Actual owner visual acceptance remains pending.
Only `@TwitchSignalTestbot` and verified test recipients. Never push/merge main or master, deploy to production, alter production data/config/secrets, or change CigilBot/Media.
Real payments/provider choice, public promotion, purchases and paid infrastructure upgrades are deferred. Use mocks/local alternatives and record external blockers; continue independent work.

## Current task and guidance
Read `docs/STATUS.md`, the current stage spec/plan and `docs/ROADMAP.md`; preserve completed work and current uncommitted changes.
Read `docs/workflows/2026-09-30-autonomy-design-testing.md` when planning execution, UI or QA. Apply this approved addendum at the next checkpoint; do not restart R0/R1.
R2 must be a responsive browser owner admin panel, not only Telegram /stats or /health. Existing commands can supply data. Mini App is a separate user/streamer surface.
Preview remains MP4/H.264/no audio as Telegram animation: ~6 -> 12 -> 18 -> 24 -> 6, existing size guard, unchanged encoding quality; do not restore the old 30-second target.

## Quality and usage
Use the existing audit/map; no repeated whole-repository graphify runs unless material architecture changes require one. Load only relevant files and skills.
One bounded user journey per task. Write regression tests before behavior changes; run focused checks during edits and the required full gate on the final code snapshot before deployment. Reuse identical validated evidence; never claim old tests cover new code.
Verify the new deployment identity, artifact/SHA, bot identity and actual behavior; health HTTP 200 or a DB label alone is insufficient. Mark unavailable native-device tests unverified.
Do not weaken assertions, add skips, disable functionality or silently update visual baselines to hide failures. Stop speculative fixes after two failures and investigate with a scoped reviewer.
Keep logs compact and test scripts reusable. At most one focused reviewer at a time; no concurrent writers to the same files. Do not change the main model/effort without a verified control.

## UI and completion
For admin/product UI use installed impeccable as primary guidance, design-system for shared components, ui-ux-pro-max for targeted lookups, web-design-guidelines for review, playwright-skill for browser QA, and stop-slop for copy. Read each selected SKILL.md before use; do not load all skills for every task.
Check desktop/mobile, themes, loading/empty/error/stale states, auth/permissions and actual user journeys. Bound cosmetic polish to a batched review, fixes and confirmation; unresolved functional defects still block acceptance.
Inspect new internet skills/scripts and pin approved versions before project-local adoption. Do not alter global skill installations or claim candidates are installed.
At checkpoints update STATUS/DECISIONS with commit/tree, test evidence, deployment state and next task. Final handoff includes working staging entry points, screenshots, scenario checklist and explicit mock/unverified limitations.

## R8 Stop-Slop Copy Gate
For R8 and final product-copy review, read `docs/workflows/2026-10-01-r8-stop-slop-gate.md`. Apply installed `stop-slop` to human-facing prose after each completed surface and once across all changed surfaces before R8 acceptance. Do not run it on machine/code content or trade away factual SEO terms, placeholders, structured data, legal meaning or product truth for style.

## R8 public site, SEO and demo videos
Read `docs/workflows/2026-10-01-r8-seo-design-remotion.md` BEFORE the R8 spec/plan or public-site work. Owner-approved direction: two lightweight visual concepts, Taste Skill as lead, Impeccable review, official Remotion for isolated local demo videos. Four pinned SEO skills and Remotion instructions are installed project-locally in `.agents/skills`; their provenance is in `docs/workflows/2026-10-01-r8-skills-lock.json`. Keep staging noindex, preserve live preview and all existing product work; no public launch or paid services. Do not use the admin design as the public-site template.

## Next user Mini App + Plus stage (2026-10-01)
The owner approved the two-mode minimal user app and requested a Codex implementation plan including Free/Viewer Plus/Streamer Plus. Read `docs/superpowers/specs/2026-10-01-native-mini-app-plus-design.md`, `docs/superpowers/plans/2026-10-01-native-mini-app-plus.md` and `docs/workflows/2026-10-01-mini-app-skills.md` before this stage. Handoff: `docs/workflows/2026-10-01-mini-app-codex-handoff.md`.
The owner now explicitly authorizes implementation in a NEW visible chat "Мини-апп". Read `mini-app/START-HERE.md`, the approved scope update and IMPLEMENTATION-ADDENDUM (T13–T17) in addition to T1–T12. Old chat is history, not a second writer. T12 is the final gate after all extensions. Do not restart R0–R9.
The skills workflow overrides conflicting community examples: keep existing strict auth, no production/dev mocks on live routes, no downloaded helper execution, no real payments. Existing global skills remain unchanged. Approved personal limits are Free 50 / Viewer Plus 200 tracked streamers and 5 selected video-preview streamers. Prices, real purchase durations and other limits remain separate owner decisions.

## Mini App redesign guidance — 2026-10-02
For the next Mini App redesign, `docs/workflows/2026-10-02-redesign-skills.md` overrides earlier Mini App design-role guidance: `mobile-app-ui-design` leads, `apple-liquid-glass` is a subordinate visual reference, and the four installed Expo skills are adapted references only. Keep design-system, impeccable as reviewer, playwright-skill and stop-slop. Do not use ui-ux-pro-max for this redesign or alter global skill installations.
Read `mini-app/REDESIGN-PROMPT-2026-10-02.md` for the complete latest brief. First present visual directions and obtain the owner's choice, then agree the design/implementation plan before changing the product. Skill installation and saved prompts do not launch development.
Preserve existing functionality, HTML export and old group connections; the new streamer connection flow is Telegram-channel-only. Never copy mock phone chrome or migrate the web app to Expo/React Native because a reference skill recommends it. Existing production, payments, secrets and outbound-test restrictions remain in force. Public-site design roles are unchanged.

## Approved implementation — 2026-10-02
Latest owner prompt `650afe6a-f60f-4201-bc2e-938a49c9d84a` authorizes sequential P01–P21 and guarded pinned staging after all gates. Viewer150 RUB/month, Streamer300 RUB/month; previous200 canceled. Role-aware Plus with four primary blocks, disclosures and secondary Viewer link. First staging payment OFF, no invoices/external payment POST/fake grants. Follow current plan and `mini-app/REDESIGN-PROGRESS.md`; preserve historical HTML/export. Production and owner OAuth/outbound-recipient restrictions remain.
