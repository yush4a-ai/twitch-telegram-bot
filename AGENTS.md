# TwitchSignalBot working rules

## Scope and authority
Use the existing visible app chat "Продолжить roadmap TwitchSignalBot" and branch `autonomous/twitchsignal-roadmap`. One implementation owner; no hidden parallel CLI development or second controller.
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
