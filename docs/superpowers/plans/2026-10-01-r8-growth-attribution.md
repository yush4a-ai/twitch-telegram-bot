# R8 Growth Attribution Implementation Plan

**Goal:** измерить на staging source → first private subscription → ever test Plus, дать безопасную referral-ссылку и закрытый от индексации SEO-прототип.

**Architecture:** pure deep-link parser и additive SQLite tables; Telegram `/start` пишет first touch, общий путь добавления канала атомарно отмечает activation. Существующие grants дают агрегированную conversion; staging-only aiohttp site и owner snapshot показывают результат без личных ID.

**Tech Stack:** Python 3.12, aiogram, aiosqlite, aiohttp, HTML/CSS, pytest/unittest; без новых внешних сервисов.

**Spec:** `docs/superpowers/specs/2026-10-01-r8-growth-attribution-design.md`.
**SEO refinement:** `docs/workflows/2026-10-01-r8-competitive-seo.md`, pinned `docs/workflows/2026-10-01-r8-seo-competition-skills-lock.json`.

## Global Constraints

- Только pinned Railway staging и `@TwitchSignalTestbot`; production deploy/DB/variables и `main` не меняются.
- Telegram `/start` payload до 64 ASCII URL-safe символов; только `src_site` и выданный `ref_<12-char code>`.
- Первый допустимый touch и первая успешная private subscription; повтор, group, self-referral, unknown code и уже активный пользователь не получают attribution.
- Никаких реальных денег, наград, цен, marketing cookies/pixels и публичной индексации staging.
- Никаких SEO rank guarantees, платных API/доменов, автоматического мониторинга или смены бренда. `src_site` не раскрывает органический запрос.
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

**Files:** `docs/design/public-site.md`, два лёгких прототипа в `docs/design/r8-concepts/`; `docs/audits/2026-10-01-r8-competitors.md`, `docs/audits/2026-10-01-r8-search-intents.md`, SEO checklist `docs/audits/2026-10-01-r8-seo-launch-checklist.md`.

**Interfaces:** выбранная композиция и страницы `/site`, `/site/for-viewers`, `/site/for-streamers`, `/site/help` передаются Task 5; исходные варианты сохраняются для обратимого пересмотра.

- [x] Закреплённые `site-architecture`, `content-strategy`, `seo-audit`, `schema` и Taste Skill прочитаны; карта четырёх страниц и реальные границы записаны в `docs/design/public-site.md`, SEO checklist дополняется при site QA.
- [x] `docs/audits/2026-10-01-r8-competitors.md`: 3 hosted Telegram сервиса и 1 self-hosted README с датой, источниками, ценовой видимостью и неизвестными метриками. `docs/audits/2026-10-01-r8-search-intents.md`: 28 гипотез, 7 задач, 4 страницы; поисковый инструмент не фиксировал регион, поэтому RU ranking/demand не заявляются.
- [x] Design read и два первых экрана созданы image generation и сохранены в `docs/design/r8-concepts/`. Владелец выбрал светлый «Из эфира в Telegram» и отдельно попросил включить «Я зритель»/«Я стример» из тёмного варианта; решение записано в `docs/design/public-site.md`.
- [x] Copy светлого сайта проверена через Stop-Slop (42/50), добровольный переход через CRO: ясный тезис, один тип CTA, два ролевых пути без срочности и выдуманного social proof. Impeccable detector указал на боковую толстую рамку; исправлено, повторный вывод `[]`. Типографика Golos, палитра, reduced motion и скриншоты зафиксированы в `docs/audits/2026-10-01-r8-site-local.md`. Два варианта и выбор владельца сохранены в предыдущем commit.

## Task 5: Многостраничный staging SEO site

**Files:** new `bot/growth_site.py` with four server-rendered HTML bodies, `bot/growth_ui/site.css`, local font files/license and owner-provided logo; `bot/oauth.py`, `main.py`; tests `tests/test_growth_site.py`, deployment packaging и R2 auth tests. Отдельные HTML-файлы не нужны: Python собирает страницы один раз при старте сервера и не делает файловый I/O на каждый запрос.

**Interfaces:** `install_growth_site(app, bot_username:str, public_base_url:str)` mounts четыре HTML routes, CSS и `/robots.txt` only if stage flag/exact testbot username; `OAuthCallbackServer` gets optional site values from `main`.

- [x] RED: 4 ожидаемых failure до site interface; отсутствие маршрутов без привязки, отказ production username, testbot `src_site`, уникальные title/description/H1/canonical, HTTP+HTML noindex, robots Allow `/site`, правдивая JSON-LD, отсутствие admin label. После реализации focused 4 passed, 12 subtests.
- [x] GREEN: серверный HTML/CSS в выбранной системе с явной карточкой «ДЕМО»; R8/R2 regression 26 passed, 29 subtests. Site packet `4b39485` сохранён.
- [x] Browser QA 360/390/768/1440, клавиатура, контраст, reduced motion, длинный русский текст и переполнение: `docs/design/r8-site-qa/browser-checks.json`, четыре скриншота, browser errors 0. Из найденного исправлены CSP для локальных шрифтов и обрезанная мобильная карточка.
- [x] Одиночный фиксированный mobile lab `docs/design/r8-site-qa/mobile-lab.json` повторён после видео; loopback RTT не подтверждён фактическим TTFB, поэтому field CWV не заявляются. `docs/audits/2026-10-01-r8-seo-launch-checklist.md` фиксирует reversal проверки для будущего публичного релиза, staging noindex не снят.

## Task 6: Isolated Remotion demo

**Files:** new `marketing/video/` project, `docs/design/r8-video-storyboard.md`, rendered 16:9/9:16 files and poster in site assets; tests or deterministic render checks.

**Interfaces:** site consumes poster and user-controlled lightweight video; bot preview pipeline remains separate and unchanged.

- [x] Pinned official Remotion SKILL and selected references прочитаны; `docs/design/r8-video-storyboard.md` и licensing note записаны. Все Remotion-пакеты закреплены на `4.0.530` после проверки дефекта официального архива `4.0.531`.
- [x] Изолированный проект собран, Studio открыта, осмотрены кадры 30/90/150/210 двух композиций; оба формата локально отрендерены с concurrency 2 без аудио и чужих медиа. Размеры, длительность и safe area в `docs/audits/2026-10-01-r8-video-local.md`.
- [x] Постер и native controls без autoplay/preload добавлены; reduced-motion и текстовый fallback сохранены. TDD video test 5 passed/17 subtests, browser playback/Range проверены, `python -m scripts.verify_r8_video` проходит. Пакет ожидает commit.

## Task 7: Staging acceptance

- [ ] Spec/plan self-review без placeholders/противоречий; полный suite и code review, `git diff --check`, внешний staging backup/restore и migration drill на копии.
- [ ] Чистый commit и pinned target check; guarded staging deploy с terminal SUCCESS; testbot identity, schema/integrity, `/site` metadata/robots/video assets, admin direct denial, stage link target.
- [ ] Temp-DB journey `src_site`/ref → first activation → test Plus aggregation и replay/self/group rejection без изменения active user data; обновить `docs/STATUS.md`, `docs/DECISIONS.md`, R8 audit и перейти к R9.
- [ ] Сохранить датированный SEO checklist и короткий план после запуска: Search Console/Яндекс только после подтверждения домена/аккаунтов, недельный отчёт как шаблон без automation; domain/Twitch mark risk и отсутствие реальных метрик явно оставить открытыми.

## Самопроверка плана

Пакеты разделены по наблюдаемым границам: данные, bot event, агрегат, визуальный brief, site и изолированный video. Каждый пакет поведения начинается с RED и заканчивается focused GREEN/commit; staging gate выполняется только из чистого snapshot. Referral не даёт entitlement, site не индексируется и не использует production bot link. Видео не входит в runtime live preview.
