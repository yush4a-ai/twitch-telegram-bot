# R8 Growth Attribution Implementation Plan

**Goal:** измерить на staging source → first private subscription → ever test Plus, дать безопасную referral-ссылку и закрытый от индексации SEO-прототип.

**Architecture:** pure deep-link parser и additive SQLite tables; Telegram `/start` пишет first touch, общий путь добавления канала атомарно отмечает activation. Существующие grants дают агрегированную conversion; staging-only aiohttp site и owner snapshot показывают результат без личных ID.

**Tech Stack:** Python 3.12, aiogram, aiosqlite, aiohttp, HTML/CSS, pytest/unittest; без новых внешних сервисов.

**Spec:** `docs/superpowers/specs/2026-10-01-r8-growth-attribution-design.md`.

## Global Constraints

- Только pinned Railway staging и `@TwitchSignalTestbot`; production deploy/DB/variables и `main` не меняются.
- Telegram `/start` payload до 64 ASCII URL-safe символов; только `src_site` и выданный `ref_<12-char code>`.
- Первый допустимый touch и первая успешная private subscription; повтор, group, self-referral, unknown code и уже активный пользователь не получают attribution.
- Никаких реальных денег, наград, цен, marketing cookies/pixels и публичной индексации staging.
- R2 owner-only auth, R7 Viewer API, существующие `track_` и `link_` сохраняют границы.

## Review Focus

- Коллизия referral code: генерация повторяется под DB lock и не заменяет код другого владельца (Task 1).
- `/start` в группе и подмена `from_user.id` относительно private chat не создают touch (Task 2).
- Гонка duplicate/limit при добавлении канала не отмечает activation (Task 2).
- Отозванный/истёкший test grant считается только как исторический **ever test Plus**, но не revenue (Task 3).
- Неподтверждённый site bot username и прямой `/admin` URL не раскрывают ростовые данные (Tasks 3–4).

## Task 1: Deep links и DB ledger

**Files:** `bot/deep_links.py`, `bot/database.py`; tests `tests/test_growth_links.py`, `tests/test_growth_ledger.py`, existing deep-link regressions.

**Interfaces:** `parse_growth_start_payload(payload: str) -> tuple[str,str] | None`; `build_growth_deep_link(bot_username: str, payload: str) -> str`; `Database.get_or_create_growth_referral_code(user_id:int, *,now:float|None=None)->str`; `Database.record_growth_touch(user_id:int,payload:str,*,now:float|None=None)->bool`; `Database.growth_funnel_snapshot()->list[dict[str,int|str]]` in Task 3.

- [x] RED: `tests/test_growth_links.py` дал 2 ожидаемых failure на отсутствующих функциях; valid `src_site`/ref code, invalid length/alphabet/unknown prefix, safe bot username и 64-byte limit.
- [x] GREEN: pure parser/builder и explicit `build_track_deep_link(..., bot_username=...)`; 8 focused/deep-link regression tests, 16 subtests passed.
- [x] RED: `tests/test_growth_ledger.py` дал 3 ожидаемых failure на отсутствующих методах/версии; один код на owner, коллизия, unknown/self/repeat/active touch и положительный ID. Private actor проверит Task 2.
- [x] GREEN: `r8_001_growth_attribution`, referral/attribution tables, код и first-touch атомарны; migration old/new и focused suite 8 passed, 13 subtests. Commit пакета.

## Task 2: Bot entry and activation

**Files:** `bot/config.py`, `bot/handlers/streams.py`, `bot/database.py`, `bot/poller.py`, `main.py`; tests `tests/test_growth_handlers.py`, `tests/test_growth_activation.py`, existing R2/R7/deep-link tests.

**Interfaces:** `Config.growth_enabled` is local/pinned-staging only; `StreamPoller(..., bot_username: str=TELEGRAM_BOT_USERNAME)` uses it for live-post track links. `Database.add_channel[_with_limit]` updates first activation within successful insert transaction.

- [ ] RED: `/invite` private stage link has opaque code/testbot; group/prod disabled; `src_`/`ref_` `/start` records only exact private actor and keeps old `track_`/`link_` behavior. Run and confirm failure.
- [ ] GREEN: stage flag/handlers and bot link wiring; focused handler/regression tests pass.
- [ ] RED: only successful first add sets `activated_at`, duplicate/limit/group/replay does not; stage live-post track link points to testbot, default path remains compatible. Confirm failure.
- [ ] GREEN: atomic activation updates and poller username injection; focused tests pass. Commit this packet.

## Task 3: Aggregate owner funnel

**Files:** `bot/database.py`, `bot/admin_metrics.py`, `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`; tests `tests/test_growth_funnel.py`, `tests/test_admin_metrics.py`, R2 auth tests.

**Interfaces:** `Database.growth_funnel_snapshot()` returns fixed source buckets `{source,touched,activated,ever_test_plus}` without IDs; `AdminSnapshot.collect()` adds `growth` or null on isolated DB error.

- [ ] RED: site/referral cohorts, activated subset, Viewer and Streamer test grants, revoke/expiry, repeated grant, zero rows, no IDs/codes and current-identity caveat; confirm failure.
- [ ] GREEN: single bounded aggregate SQL/query and owner snapshot; render read-only funnel card with textContent and empty/unavailable states. Focused DB/admin tests and JS syntax pass. Commit this packet.

## Task 4: SEO site prototype

**Files:** new `bot/growth_site.py`, `bot/growth_ui/index.html`, `bot/growth_ui/for-streamers.html`, `bot/growth_ui/site.css`; `bot/oauth.py`, `main.py`; tests `tests/test_growth_site.py`, deployment packaging and R2 auth tests.

**Interfaces:** `install_growth_site(app, bot_username:str, public_base_url:str)` mounts `/site`, `/site/for-streamers`, `/site/site.css`, `/robots.txt` only if stage flag and exact testbot username; `OAuthCallbackServer` gets optional site values from `main`.

- [ ] RED: no site route outside stage config; staging CTA testbot `src_site`, unique title/description/canonical, noindex/nofollow and robots disallow, no third-party scripts/tracking or admin label; confirm failure.
- [ ] GREEN: static HTML/CSS and route installation with strict bot username, no user-specific data; focused HTTP tests and 390/1440 browser check pass. Commit this packet.

## Task 5: Staging acceptance

- [ ] Spec/plan self-review без placeholders/противоречий; полный suite и code review, `git diff --check`, внешний staging backup/restore и migration drill на копии.
- [ ] Чистый commit и pinned target check; guarded staging deploy с terminal SUCCESS; testbot identity, schema/integrity, `/site` metadata/robots, admin direct denial, stage link target.
- [ ] Temp-DB journey `src_site`/ref → first activation → test Plus aggregation и replay/self/group rejection без изменения active user data; обновить `docs/STATUS.md`, `docs/DECISIONS.md`, R8 audit и перейти к R9.

## Самопроверка плана

Пакеты разделены по наблюдаемым границам: данные, bot event, агрегат, site. Каждый продуктовый пакет начинается с RED и заканчивается focused GREEN/commit; staging gate выполняется только из чистого snapshot. Referral не даёт entitlement, а site не индексируется и не использует production bot link.
