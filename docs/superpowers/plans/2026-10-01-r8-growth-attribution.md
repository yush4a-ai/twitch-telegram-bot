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

- [x] RED: `tests/test_growth_handlers.py` дал 3 ожидаемых failure до добавления `/invite`, private touch и команды.
- [x] GREEN: stage flag/handlers и bot link wiring; вместе с R2 command-scope тестами 10 passed, 6 subtests. Existing deep-link regressions: 84 passed, 15 subtests.
- [x] RED: `tests/test_growth_activation.py` дал 3 ожидаемых failure до activation и выбора testbot для live-post link.
- [x] GREEN: activation обновляется в транзакции успешного добавления, poller использует stage username; общий focused suite 21 passed, 19 subtests. Commit пакета.

## Task 3: Aggregate owner funnel

**Files:** `bot/database.py`, `bot/admin_metrics.py`, `bot/admin_ui/index.html`, `bot/admin_ui/panel.js`, `bot/admin_ui/panel.css`; tests `tests/test_growth_funnel.py`, `tests/test_admin_metrics.py`, R2 auth tests.

**Interfaces:** `Database.growth_funnel_snapshot()` returns fixed source buckets `{source,touched,activated,ever_test_plus}` without IDs; `AdminSnapshot.collect()` adds `growth` or null on isolated DB error.

- [x] RED: `tests/test_growth_funnel.py` дал 5 ожидаемых failure: fixed cohorts, Viewer/Streamer test grants, revoke/expiry, repeated grant, current identity, owner snapshot и markup.
- [x] GREEN: bounded aggregate SQL с индексом test grants и owner snapshot; read-only table пишет только textContent. DB/admin/auth suite 22 passed, 3 subtests; полный R8-focused suite до UI cleanup 47 passed, 17 subtests; `node --check` прошёл. Commit пакета.

## Task 4: Surface brief, content map и два визуальных варианта

**Files:** `docs/design/public-site.md`, два лёгких прототипа в `docs/design/r8-concepts/`; SEO checklist `docs/audits/2026-10-01-r8-seo-launch-checklist.md`.

**Interfaces:** выбранная композиция и страницы `/site`, `/site/for-viewers`, `/site/for-streamers`, `/site/help` передаются Task 5; исходные варианты сохраняются для обратимого пересмотра.

- [ ] Применить закреплённые `site-architecture`, `content-strategy`, `seo-audit`, `schema` и Taste Skill после чтения SKILL; карта страниц, реальные функции/ограничения, темы-гипотезы без выдуманного спроса и Google/Яндекс checklist.
- [ ] Сформулировать design read и два действительно разных первых экрана («Сигнал эфира» и «Из эфира в Telegram»); использовать доступный image generation для лёгких mockup, сохранить оба и выбрать один с мотивировкой. Не строить две полные версии сайта.
- [ ] Проверить copy через stop-slop, выбранную композицию через Impeccable, зафиксировать typography/palette/motion/reduced-motion и commit документов/вариантов.

## Task 5: Многостраничный staging SEO site

**Files:** new `bot/growth_site.py`, `bot/growth_ui/index.html`, `bot/growth_ui/for-viewers.html`, `bot/growth_ui/for-streamers.html`, `bot/growth_ui/help.html`, `bot/growth_ui/site.css`; `bot/oauth.py`, `main.py`; tests `tests/test_growth_site.py`, deployment packaging и R2 auth tests.

**Interfaces:** `install_growth_site(app, bot_username:str, public_base_url:str)` mounts четыре HTML routes, CSS и `/robots.txt` only if stage flag/exact testbot username; `OAuthCallbackServer` gets optional site values from `main`.

- [ ] RED: no site route outside stage config; staging CTA testbot `src_site`, unique title/description/H1/canonical, HTTP+HTML noindex/nofollow, robots disallow, truthful JSON-LD, working internal links, no tracking/admin label; confirm failure.
- [ ] GREEN: responsive server HTML/CSS in selected visual system with demo explicitly marked; focused HTTP tests and JS/CSS checks pass. Commit code packet.
- [ ] Browser QA at 360/390/768/1440, keyboard, contrast, reduced-motion, long Russian copy, no horizontal scroll, desktop/mobile visual review and one bounded fix pass; record screenshots/evidence.

## Task 6: Isolated Remotion demo

**Files:** new `marketing/video/` project, `docs/design/r8-video-storyboard.md`, rendered 16:9/9:16 files and poster in site assets; tests or deterministic render checks.

**Interfaces:** site consumes poster and user-controlled lightweight video; bot preview pipeline remains separate and unchanged.

- [ ] Read pinned official Remotion SKILL and selected references; pin package versions/licensing notes. Write storyboard and frame sketches for synthetic live → Telegram post → preview → watch; distinguish marketing preview from actual Telegram Animation.
- [ ] Build small isolated project, inspect short local Studio preview when available, render both aspect ratios locally with bounded concurrency and no unlicensed media/audio. Record duration/resolution/bytes/safe margins.
- [ ] Add poster, accessible play/pause and reduced-motion fallback to site without autoplay dependency; verify HTML remains useful without video, site assets package and focused tests pass. Commit packet.

## Task 7: Staging acceptance

- [ ] Spec/plan self-review без placeholders/противоречий; полный suite и code review, `git diff --check`, внешний staging backup/restore и migration drill на копии.
- [ ] Чистый commit и pinned target check; guarded staging deploy с terminal SUCCESS; testbot identity, schema/integrity, `/site` metadata/robots/video assets, admin direct denial, stage link target.
- [ ] Temp-DB journey `src_site`/ref → first activation → test Plus aggregation и replay/self/group rejection без изменения active user data; обновить `docs/STATUS.md`, `docs/DECISIONS.md`, R8 audit и перейти к R9.

## Самопроверка плана

Пакеты разделены по наблюдаемым границам: данные, bot event, агрегат, визуальный brief, site и изолированный video. Каждый пакет поведения начинается с RED и заканчивается focused GREEN/commit; staging gate выполняется только из чистого snapshot. Referral не даёт entitlement, site не индексируется и не использует production bot link. Видео не входит в runtime live preview.
