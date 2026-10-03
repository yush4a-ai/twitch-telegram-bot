# Telegram UI: короткий вход и сохранение всех функций

**Goal:** реализовать запрос владельца §§0–36: простой обычный Telegram UI, постоянное Меню и общий Plus при payment OFF; guarded pinned staging после всех gates.
**Architecture:** существующий aiogram router/FSM + shared builders и subscription/BillingService contract. Одна DB и Mini App, сохранение legacy callbacks/commands/deep links/HTML. Один исполнитель, один финальный read-only reviewer.
**Tech Stack:** Python 3.12, aiogram, SQLite, aiohttp; существующий Mini App web runtime.
**Spec:** `docs/audits/telegram-ui-2026-10-03/OWNER-REQUEST.md` (оба точных owner attachments), `UI-MAP.md` (полный baseline аудит).

## Global Constraints

- Ветка autonomous/twitchsignal-roadmap, исходный HEAD608acff. Production/push/main/payments/secrets/paid infra не трогать.
- Только локальные fake sender/temp DB и guarded @TwitchSignalTestbot. Native Telegram/owner OAuth/outbound recipient проверки без отдельного разрешения NOT TESTED.
- System MenuButtonWebApp Приложение /app остаётся. Никаких client hacks.
- Цены catalog Viewer150/Streamer300; тот же frozen beneficiary/effective Viewer; никакой второй billing и fake paid/trial label.
- Preserve all HTML/export, callbacks, commands, legacy groups, old deep links and verified subscription data.
- RED → код → PASS → scoped self review → commit; один финальный reviewer, full suite final snapshot перед guard, guard повторяет suite.
- Не запускать старые R0–R9/whole graphify/вторую разработку. Нет глобальных установок.

## Review Focus

Cross-user/private/group callbacks, old messages, missing/inaccessible message, malformed IDs, expired/cancelled intents, Menu racing OAuth/channel/add confirmations; UI cancellation must not revoke saved data. Same catalog/ledger/readiness and no checkout/POST/invoice/grant even with injected permissive providers. Frozen Streamer inheritance after unlink, trial/test source honesty. HTML/text reports, Free quiet, legacy group routing. Exactly four home actions/admin deeper. Native claims bounded to evidence.

## Task 1: аудит и единая навигация

- Аудит всех 58 button constructions/31 callbacks/16 commands и no-delete карта до кода.
- RED tests /start/Menu/callback home same builder, exact four rows, owner admin only More, App /app, reply only Menu flags, Menu before FSM clears drafts only.
- Implement shared home/reply/More builders; register Menu route before state inputs. Preserve old commands and legacy viewer path deeper.
- PASS focused navigation/entry/growth/startup. Обновить STATUS/progress, scoped diff review, commit.
- Produces: home builder, cancellation helper, More routing; consumed Task2/3/4.

## Task 2: Add с подтверждением

- RED actual new flow lookup→confirm→atomic add; no add before confirm; stale/wrong actor rejected; duplicates, Free50/Plus200, Menu midinput; old addfound/track remain.
- Implement short prompt/found confirmation/success2 buttons using current validation/atomic DB. Safe Twitch URL parser.
- PASS navigation/Add + existing tracking/legacy regressions. Commit.

## Task 3: Streamer / подключения / безопасная отмена

- RED verified/unverified journey, OAuth shared intent/cancel/late result, channel-only shared request ownership/expiry/Menu; legacy connections preserved.
- Implement shared OAuth/community intent entry, restore persistent Menu after share, existing Mini App posts/settings entry, no new group suggestions.
- Fence legacy OAuth/import late results after Menu without removing old commands.
- PASS streamer/access/channel/old OAuth/import regressions. Commit.

## Task 4: Telegram Plus через общий backend

- RED role from verified identity, catalog150/300/includesViewer, actual expiry/source/frozen ownership, all three callbacks clickable→same unavailable, no orders/POST/invoice/grants.
- Extract shared read-only subscription state preserving existing API JSON shape. Add BillingService first-release purchase contract reused by Mini App/Telegram. No change existing test-only checkout/trial routes.
- Implement Plus/secondary offer/active/purchase/back helpers and callback authorization.
- PASS shared API/billing/catalog/inheritance/payment regressions. Commit.

## Task 5: редкие функции, copy и compatibility

- RED old-function→new-path tests, report linking inside Reports, Free quiet/back, separate short about/help, ready legal/configured support only, old route manifest preserved, stale callbacks recovery.
- Implement minimal copy/path changes, all old handlers/commands/HTML/export retained. Stop-slop pass across changed human copy.
- PASS focused compatibility/copy/report/group/quiet tests; persist OLD FUNCTION → NEW PATH → TEST table and fake-sender UI evidence. Commit.

## Task 6: финальное ревью и pinned staging

- Один fresh read-only reviewer всей task range against spec/audit/plan. RED→PASS fixes if real findings.
- Full suite on final runtime snapshot, backup/restore copy and fresh pinned production metadata before staging; unchanged DB schema if no migration.
- Clean committed tree → existing staging guard (full suite repeated) → exact deployment SUCCESS/SHA/Testbot identity/system App/artifact verification/payment OFF/production metadata unchanged. No raw deploy bypass.
- Evidence/report/docs commit separated from deployed code SHA. User manual11 steps + persistent Menu/nativeNOTTESTED, no owner OAuth/send/payment claims. Exact final Russian text Clipboard verify, short report and stop.

## Проверки на каждой задаче

Команды focused pytest берутся из новых tests/test_telegram_navigation.py, tests/test_telegram_add.py, tests/test_telegram_streamer.py, tests/test_telegram_plus.py и существующих scoped modules. Expected: RED конкретного поведения до implementation, затем exit0 без weakening/skips. Final Expected: pytest exit0; guard clean/pins/exactSHA; actual deployed runtime evidence; native tests лишь при реальной авторизованной проверке.
