# Сценарии первого выпуска Mini App

Этот checklist отражает доказательства локальных tests/браузеров. Native owner acceptance остаётся отдельной. Инженерный staging release подтверждён: код52e56e9,active deploymentfba34520-f2a7-4d5d-aa86-1e8e3b0072b1; точные данные в STAGING-ACCEPTANCE.md. Native приёмка ожидается.

| Сценарий | Локальные Python/browser | Настоящий Telegram/внешняя система | Evidence |
|---|---|---|---|
| Viewer: Home/Стримеры/Профиль/Plus, общий вход из профиля | PASS | NOT TESTED | P09/P18,46journeys наengine |
| Streamer: Мой канал/Посты/Профиль/Plus | PASS | NOT TESTED | P13–15/P18 |
| Light/dark/Telegram themeChanged, SDK handlers/safe area/fullscreen fallback | PASS с fake SDK | NOT TESTED | P08/theme/matrix |
| Width360/390/430/768/1440, text200, short390×440, настоящий zoom200 | PASS | NOT TESTED | P18-browser-manifest |
| Back/dialog first/focus/keyboard/scroll restore/auth expiry | PASS | NOT TESTED | shell/theme/P18 |
|0/6/200, длинные имена, live/offline/stale, loading/error/empty | PASS | NOT TESTED |9matrixcases наengine |
| Поиск/добавление/follow/unfollow/pause и reload | PASS | NOT TESTED | viewer-free/APItests |
| Free50/Plus200, сохранение ранее импортированного превышения Free | PASS | NOT TESTED | database/viewer tests |
| Пять video selections включая offline, sixth blocked, atomic replace/CAS двух окон | PASS | NOT TESTED | video/video-windows/slots tests |
| Legacy togglepreview не даёт Plus/слоты; перед send проверяются общие права | PASS с fake sender | NOT TESTED | video-delivery/live-post tests |
| Expiry/refund/deselect/notify off: animation→photo и cleanup | PASS с fake sender | NOT TESTED | media lifecycle/fallback tests |
| Shared H.264/no sound/reuse/cap/budget/photo fallback | PASS контролей;1×1000 RESOURCE_STOP25s | NOT TESTED, SLA неизвестна | P18-media profiles |
| Quiet hours/basic alerts/Twitch unavailable/HTML/export Free | PASS | NOT TESTED | compatibility/history/export tests |
| Filters/category/reminders15/30/folders/history/pending/late ACK | PASS | NOT TESTED | P12/settings journeys |
| Новое channel-only подключение, прежние groups сохранены | PASS | NOT TESTED | connection/migration-copy |
| can_post true/edit false, creator, network≠missing permission | PASS с doubles | NOT TESTED | channel permissions |
| OAuth verified identity/cancel/stale/result/cookie | PASS с doubles | NOT TESTED | OAuth-results/P13 |
| Draft/шаблон/кнопки/variants, preview не send; own confirmed statistics | PASS | NOT TESTED | streamer-posts/P14 |
| Viewer150/Streamer300,4SVG/All/secondary Viewer/active status | PASS | NOT TESTED | purchase/Plus/matrix |
| Streamer→Viewer frozen buyer; independent Viewer после expiry/refund | PASS | NOT TESTED | inheritance/SQL tests |
| Stars/СБП/карта внутри App, Platega указан; unavailable/zero money | PASS локально | Первый выпуск OFF; actual invoice/provider NOT TESTED | P15/purchase guards |
| Duplicate/UNKNOWN/reconcile/refund/atomic ledger | PASS локально | NOT TESTED | payment tests |
|7дней по добровольному allowlisted server action, one-time/no money | PASS локально | NOT TESTED | viewer-trial/subscription tests |
| Own operations/privacy/foreign IDs/unsigned auth/client flags | PASS локально | Signed live API NOT TESTED | auth/subscription/purchase tests |
|5canonical legal docs/контакт/URL/DOM, mandatory owner fields/approval | PASS локально | Документы пока unavailable503, bank NOT READY | P16/legal journeys |
| Official iframe allowed/foreign origin blocked; другие XFO/CSP сохранены | PASS в Chromium/WebKit | Native Telegram Web NOT TESTED | P19-frame-final |
| Backup/remote+local restore/migration-copy/rollback/reopen/actual key | PASS в pinned staging copy | Active restore не выполнялся | MIGRATION-COPY |
| Полный suite и actual deploy/bot/menu/assets/production compare | PASS1403tests/3248subtests/2existing skips | PASS actual Bot API/public resources/DB; signed live API NOT TESTED | P20-staging-smoke/STAGING-ACCEPTANCE |

## Самостоятельная приёмка владельца

Открыть тестового бота и проверить последовательно:

1. Четыре нижние кнопки обоих режимов; Plus из меню и профиля открывает один раздел.
2. Поиск, добавить/пауза/возобновить, длинное имя и «Статус уточняется», live/offline; Back возвращает список и позицию.
3. Три темы, экран с клавиатурой, увеличение текста, короткое окно и fullscreen/safe areas.
4. Quiet hours/папки/фильтр/категория/напоминания/история по действующим правам; закрыть и повторно открыть приложение.
5. Выбрать5video включая offline, попытаться шестого, заменить; проверить статус перегрузки/фото. Реальный media send — только конкретному разрешённому получателю.
6. Streamer Twitch/канал, ошибки прав/сети, свой draft/кнопки/варианты/статистика; OAuth пройти самому. Предпросмотр ничего не публикует.
7. Viewer150 и Streamer300 с включённым Viewer, раскрыть «Все возможности» и вторичный Viewer, проверить статус/дату собственной подписки.
8. Все3 способа оплаты: каждый заканчивается «Оплата временно недоступна. Мы заканчиваем подключение платёжной системы.» Никакого оплаченного статуса/списания.
9. Профиль→Поддержка/документы: честная недоступность до реального контакта/данных/принятия редакций; проверить прежние команды/HTML/export.

В замечании указать устройство/клиент/тему, путь действий и ожидаемый результат; приложить screenshot без токенов/личных платёжных данных. Native screenshots здесь появятся только после реальной проверки владельцем.
