# TwitchSignalBot: autonomy, design and QA update (2026-09-30)

## Authority and scope
The owner requests autonomous completion of the already approved roadmap on STAGING ONLY, with fewer avoidable errors and lower model usage. The owner will inspect the finished test product and request taste changes afterward. Treat this as delegated acceptance of reversible staging design decisions, not acceptance of an untested result.
The primary workplace MUST remain the existing visible app chat "Продолжить roadmap TwitchSignalBot". Do not start another hidden CLI implementation session or scheduled implementation agent.
Repository: C:\Users\yusha\Desktop\cloude\TG-BOT.(TwtichSignal)
Working branch: autonomous/twitchsignal-roadmap. No main/master push or merge, no production deploy, variables, tokens, DB migration, or real payments. No unrelated CigilBot/Media changes.
Read current STATUS before doing anything. Finish the current R1 checkpoint; do NOT restart R0 or discard existing work to apply this update.
Provider selection, real purchases, paid subscriptions to development services, infrastructure upgrades, external promotions and domain purchases remain deferred. Use existing staging resources or local isolated alternatives; record capacity blockers and continue independent work.

## Economical execution
1. One implementation owner; at most one scoped reviewer at a time. Parallelism only for genuinely independent work with no shared-file writers. Send reviewers the diff, requirements, tests and relevant files, not the whole chat/repository.
2. Persist a SHORT project AGENTS.md, STATUS.md and decisions at the next clean checkpoint. Keep larger design/testing references separate and read them only when relevant. Do not rewrite the approved roadmap.
3. Use the existing project map. Do NOT rerun graphify or full repository discovery at every feature/bugfix; investigate only the affected flow unless architecture changed materially.
4. Narrow the task to one complete user flow with explicit acceptance criteria. Write/update its spec and plan before product code, self-review within the owner's delegated staging scope, then implement without routine approval pauses.
5. Keep the current main-session model unless a verified native control is available. Prefer normal/default effort for routine work, stronger reasoning for auth/data/architecture and difficult bugs. Never claim a model setting changed merely because a prompt requested it.
6. Logs should show command, exit code, summary and relevant failures, not repeated full successful output. Store detailed logs as artifacts. Wait for running commands rather than launching duplicate runs or inspecting every few seconds through an LLM.
7. After two failed fixes to the same issue, stop speculative edits: reproduce and identify a root cause; use the scoped reviewer. Block only dependent work when the obstacle is external; continue independent roadmap work without pretending the blocker passed.

## Test ladder
- Each behavior change: regression test first, observe the expected failure, then minimal fix and affected tests.
- Each feature/checkpoint: related integration tests plus one independent review where risk warrants it.
- Before a staging deploy: full required suite on the exact final code snapshot. Key evidence by code tree/commit, dependency lock and environment. Reuse an identical verified result; code, dependencies or relevant environment changes invalidate it. Never deploy an older tested snapshot as though it contains newer fixes.
- After deploy: verify deployment identity, expected SHA/artifact, bot username TwitchSignalTestbot and end-to-end behavior. HTTP 200 alone is not acceptance.
- Deterministic tests run as local tools without a fresh model-generated script each time; retain reusable test code in the repo.
- Never make tests green by weakening assertions, deleting coverage, adding skips or silently accepting new screenshot baselines. An explicit test correction must preserve the intended requirement and be reviewed.

## Use the installed design skills selectively
Verified installed skill root: C:\Users\yusha\.agents\skills
- impeccable/SKILL.md (installed version 4.0.4): primary design guidance. For admin/Mini App use the Operate mode. Read only the relevant playbook. Capture PRODUCT.md and DESIGN.md once, then preserve the chosen design system.
- design-system/SKILL.md: reusable tokens and component states (spacing, type, colors, buttons, inputs, tables). Load the applicable sections, not slide-generation guidance.
- ui-ux-pro-max/SKILL.md: targeted lookup for dashboard, forms, charts and mobile patterns when needed, not an independent wholesale redesign of every screen.
- web-design-guidelines/SKILL.md: focused final accessibility and web-interface review. Retrieve its upstream guidelines as instructed; no backend-only invocation.
- stop-slop/SKILL.md: interface copy and human-readable reports. This is a PROSE skill, not a visual or functional test.
- playwright-skill/SKILL.md: browser exploration and UI checks with known staging/local targets. Retain real regression scenarios in the project test suite rather than regenerating them for every run.
Read full instructions of each chosen skill before use, but do not activate every design skill for every task. Do not reinstall/update global skills or disrupt other projects.
The panel is a working management interface, not a promotional landing page: clear statuses, readable tables, useful actions, minimal decoration. Preserve an existing TwitchSignalBot identity if present; do not copy the CigilBot visual system by assumption.
Choose one coherent initial direction using the agreed product goals. The user delegated reversible choices; do not block on a color/font vote. Mark initial visual baselines as agent-reviewed, not owner-approved.
Use shared components between the browser cabinet and Mini App when the stack allows it; adapt navigation and layout for Telegram, rather than squeezing desktop tables onto a phone.

## UI and Mini App QA
Plan key journeys first: owner sign-in/access denial, empty dashboard, healthy/degraded/offline/stale diagnostics, community linking, post customization, alert settings, mock Plus activation/expiry and duplicate mock payment handling.
Check widths around 360, 390, 768 and 1440 pixels as test configurations, light/dark Telegram themes, long Russian titles, empty/error/loading states, keyboard/focus, no horizontal overflow and readable touch controls.
Use Playwright browser tests, screenshot comparisons in a pinned environment, and axe-core accessibility checks where compatible. One batched visual review, one consolidated correction, then confirmation; continue only for concrete unresolved defects, not endless cosmetic polish.
Playwright Test Agents planner/generator can help create reusable tests. Use healer only for evidenced test-infrastructure/locator issues; forbid skipping broken behavior or weakening assertions to pass.
Mini App: validate Telegram initData on the server, reject missing/expired/tampered data, enforce roles and object ownership server-side, support safe areas/viewport changes, native BackButton/MainButton when appropriate, and cleanup event handlers.
Browser fixtures for Telegram must be explicitly TEST ONLY and cannot grant real access. Desktop browser/WebKit emulation is not proof of Telegram iOS/Android behavior. Test the actual available Telegram client separately; label unavailable native-device checks NOT TESTED.
Preserve preview: H.264 MP4 without sound, Telegram animation, 6->12->18->24->6, unchanged encoding-quality settings, existing size guard. Old 30s preview is superseded; negative tests against regression remain allowed.
Verify actual Telegram response type for previews, not only DB labels or health endpoints. Existing video-to-animation migration and uncertain API responses are regression cases, not guaranteed successes. Restrict outbound checks to verified test recipients.
Do not mistake sequential copies of a live SQLite DB and its WAL for a guaranteed consistent online snapshot. For any future backup drill, use a supported consistent snapshot/SQLite backup method and isolated restore verification; no production mutations under this authorization.

## Completion evidence
At each checkpoint report task IDs, changed commit/tree, tests and failures/skips, visible screenshots/preview URL, actual staging deployment identity and next task. Keep the report brief.
At final staging handoff provide working admin/Mini App entry points, a small set of desktop/mobile screenshots, a scenario checklist and explicit limitations. Mock statistics/payments must be labeled and not presented as real usage/revenue.

## Research candidates from discussion (verify before adoption)
These are references/candidates, not already installed or security-vetted additions. Do not execute install commands from a website automatically. Inspect license, full instructions and scripts; pin versions, prefer project-local scope, do not pass secrets to external tools.
- OpenAI skill context loading: https://developers.openai.com/codex/skills/
- OpenAI workflow and concise AGENTS guidance: https://developers.openai.com/codex/learn/best-practices/
- Impeccable upstream: https://github.com/pbakaus/impeccable/blob/main/README.md
- Playwright official test agents (planner/generator/healer, Codex support): https://playwright.dev/docs/test-agents
- Playwright screenshot comparisons: https://playwright.dev/docs/test-snapshots
- Playwright accessibility with axe: https://playwright.dev/docs/accessibility-testing
- Telegram official API/security/theming/viewport reference: https://core.telegram.org/bots/webapps
- Community Mini App guide candidate: https://github.com/Rithprohos/telegram-mini-app-skills/blob/main/SKILL.md (large general guide; use relevant checked sections, not a wholesale context load).
- Alternative community skill considered: https://github.com/sickn33/agentic-awesome-skills/blob/main/skills/telegram-mini-app/SKILL.md (contains TON/crypto/monetization scope we do NOT need now; do not import wholesale).
- Telegram UI, a COMMUNITY React component library, not a skill or a mandate to switch frontend stacks: https://github.com/telegram-mini-apps-dev/TelegramUI
Prefer a compact project-specific Mini App checklist referencing official Telegram docs over loading several competing all-purpose skills. Adapt only relevant UI/auth/testing patterns; no TON, crypto, wallet or real payment integration.

## Apply at the next checkpoint
Acknowledge this update in the visible work chat. Reconcile it with current docs and safely persist concise rules there. Continue the existing roadmap; do not spawn a new controller and do not restart a healthy running step. Keep missing credentials/native-device checks explicit rather than inventing passing evidence.

## Confirmed integration requirements (owner approval in current conversation)
- The owner explicitly approved integrating these quality/design/economy rules into the existing ongoing work. This is a workflow addendum, not a new roadmap or a reason to restart completed R0/R1.
- R2 MUST deliver a browser-based, responsive owner admin panel. Existing Telegram /stats and /health can provide reusable backend data and remain supplementary controls, but a Telegram-only menu does NOT satisfy the agreed admin-panel requirement.
- Do not choose a Telegram-only replacement merely to avoid implementing authenticated HTTP access. Design secure owner access and a staging-only web surface; if credentials are unavailable, build an isolated test fixture, clearly label the unverified deployed login, and continue other independent work.
- Mini App is a later user/streamer interface, with shared components where appropriate; it does not replace the owner's web admin panel. Keep roles and permissions distinct.
- Use the installed design/testing skills listed above. New internet skills/tools are optional candidates that require inspection before use; do not install several overlapping packages or claim they are already integrated.
- Preserve the actual approved preview contract: approximately 6 -> 12 -> 18 -> 24 -> 6, existing 10 MiB delivery guard and unchanged encoding quality. The size guard is an empirical precaution, not a universal Telegram guarantee.
- Synthetic 20k/30k/40k load scenarios must use fake recipients/local service doubles, never mass-message real Telegram/Twitch users or load-test their public APIs.
- Keep real billing, production releases, external advertising, purchases and paid infrastructure expansion deferred; a blocked commercial step does not prevent testing independent later roadmap features.
- The currently running full test suite should complete once. Do not launch an additional full suite for these documentation-only instructions unless they change execution/build behavior.
- The owner has delegated reversible staging design and implementation decisions. Keep written specs/plans and self-review, but do not ask for routine stage/color/layout approvals; final owner visual acceptance is still pending until they actually review the finished test product.
- On the next safe checkpoint, read this permanent path, reconcile the short AGENTS.md, and record adoption in the same visible Work chat plus STATUS/DECISIONS. Commit only when safe for the single active implementation owner.
