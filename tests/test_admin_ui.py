import re
import unittest

import aiohttp

from bot.admin_auth import AdminAccess
from bot.oauth import OAuthCallbackServer


KEY = "staging-test-key-with-at-least-32-chars-123"

VIEWS = ("overview", "users", "access", "system", "growth", "payments")


class AdminUiRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = OAuthCallbackServer(
            "https://example.test/twitch/callback", "127.0.0.1", 0,
            admin_access=AdminAccess(KEY, enabled=True, secure_cookie=False),
        )

        async def snapshot():
            return {"environment": "staging", "audience": None}

        self.server.set_admin_snapshot_provider(snapshot)
        await self.server.start()
        self.session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        port = self.server._runner.addresses[0][1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.stop()

    async def login(self):
        async with self.session.post(
            self.base + "/admin/emergency/login", data={"access_key": KEY},
            allow_redirects=False,
        ):
            pass

    async def test_login_and_panel_routes_have_distinct_access(self):
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertIn("Вход", body)
            self.assertNotIn('id="app-nav"', body)
        for path in ("/admin/panel.css", "/admin/panel.js"):
            async with self.session.get(self.base + path) as response:
                self.assertEqual(response.status, 401)
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            body = await response.text()
            self.assertEqual(response.status, 200)
            self.assertIn('href="#main-content"', body)
            self.assertIn('src="/admin/panel.js"', body)
        async with self.session.get(self.base + "/admin/panel.css") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/css", response.headers["Content-Type"])
        async with self.session.get(self.base + "/admin/panel.js") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("javascript", response.headers["Content-Type"])

    async def test_panel_shell_has_navigation_and_every_view(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="app-nav"', html)
        for view in VIEWS:
            self.assertIn(f'id="view-{view}"', html)
        for anchor in ("#/overview", "#/users", "#/access", "#/system", "#/growth", "#/payments"):
            self.assertIn(anchor, html)

    async def test_overview_renders_metrics_and_attention_region(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        for field in ("stat-users", "stat-plus", "stat-deliveries", "stat-active-today", "stat-new-7d"):
            self.assertIn(f'id="{field}"', html)
        self.assertIn('id="attention-list"', html)
        self.assertIn('id="health-line"', html)
        for key in ("active_total", "deliveries", "attention"):
            self.assertIn(key, script)

    async def test_access_screen_has_tabs_and_empty_states(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="tab-active"', html)
        self.assertIn('id="tab-history"', html)
        self.assertIn('id="access-active-body"', html)
        self.assertIn('id="access-history-body"', html)
        self.assertIn("Ничего не найдено", html)

    async def test_system_screen_shows_queue_and_backup_honestly(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        for field in ("queue-pending", "queue-failed", "queue-oldest", "backup-state", "restore-verified"):
            self.assertIn(f'id="{field}"', html)
        for key in ("pending_jobs", "failed_jobs", "oldest_due_age_seconds", "restore_verified"):
            self.assertIn(key, script)

    async def test_placeholders_explain_why_data_is_missing(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        # Рост теперь показывает воронку, а оплаты по-прежнему честно выключены.
        self.assertIn("Как считается воронка", html)
        self.assertIn("Приём платежей не подключён", html)
        self.assertIn("Нет данных", html)

    async def test_empty_attention_is_honest_when_subsystems_unverified(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # «Нет открытых проблем» допустимо только при подтверждённом состоянии.
        self.assertIn("состояние подсистем не подтверждено", script)
        self.assertIn("health.dataset.state === 'ok'", script)

    async def test_access_rows_render_source_and_expiry(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        for marker in ("SOURCE_LABELS", "expires_at", "active_rows", "history"):
            self.assertIn(marker, script)

    async def test_grant_dialog_explains_refusals_and_hides_empty_choices(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Владелец должен понимать причину отказа, а не гадать.
        self.assertIn("twitch_required", script)
        self.assertIn("нужен подключённый Twitch", script)
        self.assertIn("not_manual", script)
        # Пустой список прав не показывается.
        self.assertIn("select.hidden = empty", script)

    async def test_owner_can_grant_access_by_telegram_id(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Друга может не быть в поиске: доступ выдаётся по ID.
        self.assertIn('id="grant-target-id"', html)
        self.assertIn('id="grant-by-id-open"', html)
        self.assertIn("typedTargetId", script)

    async def test_write_actions_take_a_fresh_csrf_mark(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Одноразовая метка не должна устаревать между автообновлениями.
        self.assertIn("async function postWrite", script)
        self.assertIn("snapshotResponse", script)
        self.assertIn("token = data.csrf;", script)

    async def test_write_actions_leave_a_receipt_and_mark_danger(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Владелец видит подтверждение записи, а отзыв отличается от выдачи.
        self.assertIn('id="card-receipt"', html)
        self.assertIn("button-danger-solid", html)
        self.assertIn("showReceipt", script)
        # «Другое» требует пояснения, поэтому поле не висит всегда.
        self.assertIn("toggleOtherNote", script)

    async def test_unknown_state_is_not_shown_as_working(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("unknown: 'Нет данных'", script)
        self.assertNotIn("unknown: 'Работает'", script)

    async def test_dates_and_truncation_are_explicit(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Сроки показываются в МСК, а усечение списка не выдаётся за полный список.
        self.assertIn("Europe/Moscow", script)
        self.assertIn("Показаны первые", script)

    async def test_people_screen_has_search_filters_and_list(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="people-query"', html)
        self.assertIn('id="people-filter"', html)
        self.assertIn('id="people-list"', html)
        self.assertIn('id="person-card"', html)

    async def test_grant_dialog_requires_reason_and_summary(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        for field in ("grant-dialog", "grant-plan", "grant-expiry", "grant-reason",
                      "grant-note", "grant-comment", "grant-summary", "grant-submit"):
            self.assertIn(f'id="{field}"', html)
        self.assertIn("Компенсация", html)
        self.assertIn("Партнёрство", html)

    async def test_revoke_confirmation_lists_remaining_rights(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        self.assertIn('id="revoke-dialog"', html)
        self.assertIn('id="revoke-summary"', html)
        self.assertIn("Права после отзыва", html)

    async def test_panel_sends_csrf_header_and_handles_conflict(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("X-Admin-CSRF", script)
        self.assertIn("Права изменились", script)
        self.assertIn("/admin/api/access/grant", script)
        self.assertIn("/admin/api/access/revoke", script)

    async def test_owner_picks_the_exact_manual_grant(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn('id="grant-target-grant"', html)
        self.assertIn('id="revoke-target-grant"', html)
        self.assertIn("selectedGrant", script)
        self.assertIn("grant-target-grant", script)

    async def test_search_reacts_to_typing_and_keeps_focus(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("peopleTimer", script)
        self.assertIn("lastPersonId", script)
        self.assertIn("people-filter", script)

    async def test_plan_details_are_spelled_out(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("Viewer Plus включён", script)
        self.assertIn("Видео доступно с Plus", script)

    async def test_missing_heavy_blocks_trigger_one_short_retry(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("followUpTimer", script)
        self.assertIn("followUpTimer = setTimeout", script)

    async def test_footer_does_not_claim_a_test_contour(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()

        # Панель работает и на production, поэтому подпись не должна утверждать staging.
        self.assertNotIn("тестового контура", html)
        self.assertIn("панель владельца", html)

    async def test_twitch_failure_explains_itself(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # «Сбой» без причины бесполезен: панель называет каналы и логины.
        self.assertIn('id="system-twitch-note"', html)
        self.assertIn("auth_blocked_names", script)
        self.assertIn("нужна авторизация", script)

    async def test_activity_metrics_are_rendered(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        self.assertIn("by_day", script)
        self.assertIn("active_today", script)
        self.assertIn("new_7d", script)


    async def test_shell_uses_graphite_rail_and_unified_radius_scale(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # Оболочка: рельс 88 px с иконкой и подписью, id навигации сохраняется.
        self.assertIn('id="app-nav"', html)
        self.assertIn('class="rail"', html)
        self.assertIn('class="rail-link', html)
        # Единая шкала скруглений направления «Сигнал»: 8/12/20 px плюс пилюли.
        self.assertIn("--radius-chip: 8px", css)
        self.assertIn("--radius-control: 12px", css)
        self.assertIn("--radius-card: 20px", css)
        self.assertIn("--radius-group: 20px", css)
        self.assertNotIn("--radius-card: 24px", css)
        self.assertIn("grid-template-columns: 88px minmax(0, 1fr)", css)
        self.assertIn("color-scheme: dark", css)


    async def test_overview_has_three_column_metrics_events_and_quick_actions(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Показатели идут тремя колонками, неделя занимает две.
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", css)
        self.assertIn("metric-wide", html)
        self.assertIn(".metric-wide", css)
        # Правая колонка: внимание и последние события; подсистемы и быстрые действия на месте.
        self.assertIn('class="aside"', html)
        self.assertIn('id="event-list"', html)
        self.assertIn('id="subsystem-list"', html)
        self.assertIn('id="quick-actions"', html)
        self.assertIn("function renderEvents(", script)
        self.assertIn("renderEvents(data.live)", script)


    async def test_people_screen_splits_list_and_card_and_admits_missing_links(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Список и карточка стоят рядом: карточка не уезжает под таблицу.
        self.assertIn('id="people-filters"', html)
        self.assertIn('class="people-split"', html)
        self.assertIn('person-card', html)
        self.assertIn(".people-split", css)
        self.assertIn(".person-card", css)
        # Незаполненные связи называются прямо, а не «Никогда».
        self.assertIn("Не подключён", script)
        self.assertNotIn("Никогда", script)


    async def test_access_screen_has_expiring_tab_with_keyboard_navigation(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # Третья вкладка «Истекающие» и её панель.
        self.assertIn('id="tab-expiring"', html)
        self.assertIn('id="access-expiring"', html)
        self.assertIn('id="access-expiring-body"', html)
        self.assertIn("access-expiring", script)
        # Переключение обобщено на список вкладок и работает стрелками.
        self.assertIn("ACCESS_TABS", script)
        self.assertIn("ArrowRight", script)
        # Цель нажатия у вкладки не меньше 44 px.
        self.assertIn("min-height: 44px", css)


    async def test_system_screen_shows_rows_with_observation_time(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Подсистемы идут строками, включая резервную копию, с временем наблюдения.
        self.assertIn('id="system-rows"', html)
        self.assertIn('id="system-backup"', html)
        self.assertIn('id="system-observed"', html)
        self.assertIn('class="sysrow"', html)
        self.assertIn(".sysrow", css)
        self.assertIn("system-backup", script)
        self.assertIn("system-observed", script)
        # Копия без проверки восстановления — «Не проверена», а не «Нет данных».
        self.assertIn("unverified: 'Не проверена'", script)


    async def test_growth_funnel_is_real_and_payments_stay_honest(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()

        # Воронка настоящая: шаги, полосы и общая картина по людям без ссылки.
        self.assertIn('id="funnel-list"', html)
        self.assertIn("renderFunnel", script)
        self.assertIn("renderFunnel(data.funnel)", script)
        self.assertIn(".funnel-step", css)
        self.assertIn("Как считается воронка", html)
        # Общая заглушка «пусто» остаётся у оплат: причина и действие, без кнопок оплаты.
        self.assertIn('class="empty"', html)
        self.assertIn(".empty", css)
        self.assertIn("border: 1px dashed var(--border)", css)
        self.assertIn("Приём платежей не подключён", html)
        self.assertIn("Выдать доступ вручную", html)
        for forbidden in ("Создать платёж", "Оформить подписку", "Оплатить"):
            self.assertNotIn(forbidden, html)


    async def test_loading_state_uses_skeleton_without_invented_numbers(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        self.assertIn("function renderSkeleton(", script)
        self.assertIn(".skeleton", css)
        self.assertIn("renderSkeleton(body, 3)", script)
        # Функция принимает и id, и элемент: иначе скелетон молча не рисуется.
        self.assertIn("typeof containerId === 'string' ? $(containerId) : containerId", script)
        self.assertIn("min-inline-size: 40%", css)
        # Скелетон не подставляет числа вместо данных.
        start = script.index("function renderSkeleton(")
        end = script.index("\nfunction ", start + 1)
        body = script[start:end]
        self.assertNotIn("value(", body)
        self.assertNotIn("'0'", body)
        # Устарело и нет доступа обрабатываются отдельно.
        self.assertIn("Данные устарели", script)
        self.assertIn("window.location.assign('/admin')", script)


    async def test_mobile_layout_keeps_bottom_navigation_and_safe_area(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # На телефоне рельс скрыт, снизу — навигация с «Ещё» и отступом под неё.
        self.assertIn("@media (max-width: 768px)", css)
        self.assertIn(".rail { display: none; }", css)
        self.assertIn("env(safe-area-inset-bottom", css)
        self.assertIn('class="mobile-nav"', html)
        self.assertIn("mobile-link", html)
        self.assertIn("mobile-more", html)
        # Уважение к системной настройке «меньше движения».
        self.assertIn("prefers-reduced-motion", css)


    async def test_review_findings_stay_fixed(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # Недоступные данные не выглядят как «пусто», окружение не врёт про staging.
        self.assertIn("События временно недоступны", script)
        self.assertIn("Состояние не проверено", html)
        self.assertIn("Окружение неизвестно", html)
        # Ошибка: разный совет для сети и для отказа сервера, плюс кнопка повтора.
        self.assertIn("Сервер не смог собрать данные", script)
        self.assertIn('id="notice-retry"', html)
        # Ошибки записи переведены, включая истёкшую сессию.
        for code in ("unauthorized", "unavailable", "invalid_content_type"):
            self.assertIn(code, script)
        # Узкие экраны: вкладки переносятся, скелетон виден в мобильной карточке.
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn(".skeleton-row td { display: block; }", css)
        # Ник рядом с именем больше не выглядит как обычный текст.
        self.assertIn(".muted-inline", css)
        # Нечисловой id в адресе не оставляет карточку в «Загрузка…».
        self.assertIn("Пользователь не найден. Проверьте Telegram ID или имя", script)
        # Без данных окружение не подписывается «staging».
        self.assertNotIn("|| 'staging'", script)
        self.assertIn("ОКРУЖЕНИЕ НЕИЗВЕСТНО", script)
        # Вкладки доступны с клавиатуры с самого начала, Home и End работают.
        self.assertIn('tabindex="0"', html)
        self.assertIn('tabindex="-1"', html)
        self.assertIn("'Home'", script)
        self.assertIn("'End'", script)
        self.assertIn("открытых проблем:", script)


    async def test_panel_serves_its_own_fonts_under_a_strict_policy(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
            policy = response.headers.get("Content-Security-Policy", "")
        # Шрифты лежат рядом с панелью и разрешены политикой безопасности.
        self.assertIn("/admin/fonts.css", html)
        self.assertIn("font-src 'self'", policy)

        async with self.session.get(self.base + "/admin/fonts.css") as response:
            self.assertEqual(response.status, 200)
            faces = await response.text()
        self.assertIn("@font-face", faces)
        self.assertIn('url("/admin/fonts/', faces)
        # Никаких обращений к чужим серверам шрифтов.
        self.assertNotIn("gstatic", faces)
        self.assertNotIn("googleapis", faces)

        first = re.search(r'url\("(/admin/fonts/[^"]+\.woff2)"\)', faces).group(1)
        async with self.session.get(self.base + first) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("Content-Type"), "font/woff2")
            self.assertTrue(await response.read())

        # Посторонние имена через тот же маршрут не отдаём.
        async with self.session.get(self.base + "/admin/fonts/panel.js") as response:
            self.assertNotEqual(response.status, 200)

    async def test_fonts_are_not_public_without_a_session(self):
        async with aiohttp.ClientSession() as anonymous:
            async with anonymous.get(self.base + "/admin/fonts.css") as response:
                self.assertEqual(response.status, 401)

    async def test_panel_faces_are_rubik_and_plex_mono(self):
        await self.login()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()
        # Выбор владельца 05.10.2026: Rubik для интерфейса, IBM Plex Mono для чисел.
        self.assertIn('"Rubik"', css)
        self.assertIn('"IBM Plex Mono"', css)
        # В стилях панели нет ни одной внешней ссылки.
        self.assertNotIn("http://", css)
        self.assertNotIn("https://", css)


    async def test_navigation_uses_drawn_icons_not_text_symbols(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # Иконки разделов — векторные, а не символы шрифта.
        self.assertNotIn("<b aria-hidden", html)
        self.assertGreaterEqual(html.count('<svg viewBox="0 0 24 24"'), 7)
        self.assertIn(".rail-link svg", css)
        # Свет — одним фиксированным слоем, он не участвует в прокрутке.
        self.assertIn('class="glow" aria-hidden="true"', html)
        self.assertIn(".glow { position: fixed", css)


    async def test_campaigns_screen_has_campaigns_and_chat_tabs(self):
        await self.login()
        async with self.session.get(self.base + "/admin") as response:
            html = await response.text()
        async with self.session.get(self.base + "/admin/panel.js") as response:
            script = await response.text()
        async with self.session.get(self.base + "/admin/panel.css") as response:
            css = await response.text()

        # Раздел есть в рельсе, мобильном меню и роутере.
        self.assertIn('data-view-link="campaigns"', html)
        self.assertIn('id="view-campaigns"', html)
        self.assertIn("'campaigns'", script)
        # Две вкладки: кампании и чат.
        self.assertIn('id="tab-campaigns"', html)
        self.assertIn('id="tab-chat"', html)
        self.assertIn('id="compose-form"', html)
        self.assertIn('id="dialogue-list"', html)
        self.assertIn('id="thread"', html)
        self.assertIn('id="reply-form"', html)
        # Панель ходит в свои маршруты и подтверждает массовую отправку словами.
        self.assertIn("/admin/api/broadcasts", script)
        self.assertIn("/admin/api/dialogues", script)
        self.assertIn("window.confirm", script)
        # В каждое сообщение добавляется кнопка отказа от рассылок.
        self.assertIn("Больше не присылать", html)
        # Картинка кампании: выбор файла и загрузка отдельным запросом.
        self.assertIn('id="compose-image"', html)
        self.assertIn("uploadCampaignImage", script)
        self.assertIn("/image", script)
        # Ответ в чате тоже может быть с картинкой, и она показывается в переписке.
        self.assertIn('id="reply-image"', html)
        self.assertIn("FormData()", script)
        self.assertIn("/admin/api/media/", script)
        self.assertIn(".bubble-image", css)
        # Незаданный владелец объясняется причиной, а не общим запретом.
        self.assertIn("owner_not_configured", script)
        # Мастер видит, какое оформление доступно и в какие лимиты укладываться.
        self.assertIn("field-hint", html)
        self.assertIn("&lt;b&gt;", html)
        # Переписка показывает, что человек прислал, даже если это не текст.
        self.assertIn("bubble-chip", css)
        self.assertIn("Стикер", script)
        self.assertIn("Голосовое", script)
        self.assertIn("Сообщений пока нет", script)
        # Диалог живой: новые сообщения подтягиваются сами, Enter отправляет.
        self.assertIn("startChatRefresh", script)
        self.assertIn("CHAT_REFRESH_MS", script)
        self.assertIn("requestSubmit()", script)
        # Чат открывается прямой ссылкой #/campaigns/chat и сам выбирает диалог.
        self.assertIn("param === 'chat'", script)
        self.assertIn("const first = rows.find", script)
        self.assertIn(".chat-split", css)
        self.assertIn(".bubble.out", css)


if __name__ == "__main__":
    unittest.main()
