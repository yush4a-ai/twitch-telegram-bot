# P19: независимый обзор P01–P18

База: 3f4fec509b0d630d9c1a3e39aa9f6384a866b36e.
HEAD при начале обзора: 8a8a70f0e3365890ba19f121d2ce95381f5565f8; P18 рабочие изменения.
Один read-only reviewer. Файлы проекта не изменены. Локальные TEMP fixtures и fake SDK; реальные Telegram/OAuth/платежи/production не запускались.

## Подтверждённая находка F1 — запрет iframe Mini App

Приоритет P1, функциональная совместимость. bot/mini_app_web.py:63–67 наследует SECURITY_HEADERS с X-Frame-Options: DENY; HTML shell на строках71–80 заменяет только script-src. bot/admin_web.py:20 содержит общий DENY.

Actual /app на собственной TEMP БД работает top-level (h1 «Главная»), но внутри iframe блокируется до исполнения app.js. Chromium получил chrome-error://chromewebdata/ и точное сообщение Refused to display ... X-Frame-Options ... deny. Probe сохраняет настоящие HTTP headers, PNG и frame URLs.

Evidence: iframe-probe.cjs, iframe-red.json, iframe-red.png в этом TEMP каталоге. SDK запрос https://telegram.org перехвачен и abort; fake SDK установлен до загрузки, внешнего обращения нет. Это доказательство браузерного механизма, не приёмка настоящего Telegram.

Primary: https://core.telegram.org/api/web-events описывает iframe Mini App в web MTProto клиентах. https://telegram.org/apps содержит официальные WebA/WebK links, перенаправляющие на https://web.telegram.org/a/ и /k/. Вывод о минимальном разрешённом origin: https://web.telegram.org.

Рекомендация: только HTML /app исключить из общего XFO DENY и добавить header CSP frame-ancestors https://web.telegram.org; сохранить прочие директивы и DENY у admin/OAuth/legal/assets/API. Без wildcard, отключения CSP и общего послабления. RED/PASS: разрешённый Telegram origin loads с fake SDK; иной origin blocked; точные headers и границы других surface неизменны.

## Проверенные границы доверия

- Интернет → JSON routes: bounded body, unique JSON/query fields, signed HMAC initData, finite fresh auth_date. Идентичность берётся из проверенного user.id; client user_id/paid/mode не выдаёт прав.
- Authenticated viewer → SQL: own tracking/filters/folders/reminders/video/history; IDs разрешаются на сервере. Лимит50/200 проверяется под write lock/BEGIN IMMEDIATE, пять выбранных включая offline и CAS version остаются серверными.
- Current Twitch identity → publishing placement: current broadcaster plus stored own community, structured live Telegram permission checker; новые intents channel-only, старые groups сохранены.
- OAuth state → intent: random memory state, one-use future, DB owner/status/deadline gates; токены проверяются и сохраняются вместе с identity, cancel/expiry не превращается в успех. Result ticket HttpOnly/SameSite/path scope, result URL сам не авторитет.
- Browser purchase → ledger: prepare в первом release безусловно503/unavailable, без service/provider вызова. Default runtime offline, main без monetary provider, Platega callback не установлен. Existing owner-only mock QA и добровольный однократный7-day trial отдельно gated по принятому плануP15/P21.
- Provider transport → durable facts: только injected network_free local contracts и approved sandbox policy. Durable attempt до отправки; creation_unknown/refund unknown не повторяет POST. Callback notice не grant; reconciliation проверяет canonical money/method/order/attempt/provider. Fact/grant/order в общей транзакции, unique references/request keys, frozen beneficiary triggers, один unresolved order на buyer.
- Streamer entitlement → Viewer: frozen buyer не текущий linked Twitch и не участники Telegram-канала. Independent Viewer переживает окончание/отзыв Streamer; current publisher по-прежнему требует broadcaster/placement.
- Media effect → sender: legacy togglepreview не пишет selection/flag; common effective SQL и recheck перед animation; grant expiry/refund/deselect/notify off возвращает photo, unknown transition восстанавливается, payload reuse не выдаёт прав. Shared capture/load bounds сохраняются; пять пользовательских слотов не серверная SLA.
- Server text/URL → DOM: textContent/createElement; нет innerHTML/eval/string handlers в Mini App. Legal reader rebuilds escaped allowlisted nodes; public HTTPS/mailto defense, canonical owner acceptance/data/SHA/catalog gates. Scoped storage не содержит initData/session/Twitch/provider secret.

## Репозиторные regression contracts

Актуальные test_mini_app_auth, test_admin_telegram_auth, test_mini_app_purchase, test_mini_app_subscription, test_payment_lifecycle_v3, test_plus_payment_migrations, test_entitlement_inheritance, test_channel_permissions_redesign, test_oauth_result_pages, test_mini_app_streamer_connect, test_viewer_preview_slots, test_viewer_video_delivery, test_mini_app_legacy_compat, test_mini_app_legal, test_stars_provider, test_platega_provider покрывают названные отрицательные пути. Reviewer не запускал полный suite и не присваивает новым изменениям старый PASS.

Дополнительный bounded malformed JSON probe: 2227-byte nested body1100 и обычный27-byte body без initData оба401 JSON, внешний запрос0; unchecked parser exception не подтверждён.

P18 closure: actual watchdog expression timedOut устанавливается до kill, finish forces-1; guard сохраняет четыре fake-process scenarios и exact owned PID/T/F. Reduced motion computed transition0s дополнен. P18-window-confirmation report PASS, native NOT TESTED, errors0/external0, все5 PNG SHA проверены независимо,174 source hashes recorded.

## Пределы и остаточные проверки

Других подтверждённых auth/IDOR/monetary/entitlement дефектов в прослеженных изменённых и supporting runtime путях нет. Обзор ограничен этим diff и согласованными границами, не полный аудит исторического production, SCA, юридическое заключение или доказательство отсутствия всех уязвимостей.

До release остаются root final snapshot full suite, backup/restore/migration-copy/foreign_key_check, точные staging target/SHA/asset/bot identity и actual rollout smoke. После F1 исправления прежнее browser evidence историческое, финальные browser gates повторяются на новом snapshot.

Реальные Telegram/native/OAuth/отправки/Stars/Platega, provider merchant schema/hosts и bank approval NOT TESTED. Payments OFF остаётся обязательным; month policy/XTR/legal owner data/approval/refund/chargeback/upgrade gates не закрыты локальными fixtures.