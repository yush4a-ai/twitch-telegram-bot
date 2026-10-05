# System MenuButton в production — root cause и статус

## Симптом

На реальном production @TwitchSignalBot после cutover Telegram Desktop показывал у
поля ввода системную кнопку **«Меню»** вместо **«Приложение»**.

## Факты (read-only, 04.10.2026)

- Старый production artifact `dc9239eb0b82fb80d1788fc657205740cebc49e9` ставил меню
  безусловно: `bot.set_chat_menu_button(menu_button=MenuButtonCommands())`.
  Именно этому типу Telegram показывает подпись «Меню».
- Новый runtime `1c49330e773ded22d47a185866dd3921b1e00806` (deployment
  `44fe69d3-2d89-466c-9202-fcbf56e6c05a`) ставит `MenuButtonWebApp(text="Приложение",
  web_app=.../app)`, когда production admission принят (`main.py::_menu_button_for_config`,
  `main.py:532`).
- Фактическое состояние Bot API в production:
  - `getChatMenuButton()` → `{"type":"web_app","text":"Приложение",
    "url":"https://worker-production-cee5.up.railway.app/app"}`;
  - `getChatMenuButton(chat_id=<owner>)` → `{"type":"default","text":null,"url":null}`,
    то есть per-chat override для владельца **отсутствует** (старого «commands»-override нет).
- Старт deployment: 15:50:32 — монтирование volume и запуск контейнера; 15:50:39 —
  `Run polling for bot @TwitchSignalBot id=8707370390`; поллер запущен, EventSub подключён.
  ConfigError / ошибок установки кнопки меню в логах нет.
- `_menu_button_for_config` вне процесса старта (без выставленного admission) возвращает
  `MenuButtonCommands` — это ожидаемое fail-closed поведение чистой функции, а не состояние
  работающего процесса.

## Root cause

Дефекта в новом коде нет. Кнопку менял **старый** runtime (global `MenuButtonCommands`),
а Telegram Desktop показывал значение, полученное до cutover: клиент не перезапрашивает
меню уже открытого чата, пока чат/клиент не будет переоткрыт. Сервер установил
`web_app` «Приложение» при старте нового runtime.

Подтверждение владельца: после обновления чата системная кнопка стала **«Приложение»**,
persistent ReplyKeyboard **«Меню»** осталась на месте.

Evidence: `desktop-AFTER-menu-button.png` (скриншот владельца),
`PRODUCTION-menu-diagnostic.json` (Bot API + config + Railway metadata).

## Что сделано и что не сделано

- Runtime-код **не изменялся**: правка не требуется, кнопка уже корректна.
- Per-chat override для владельца **не создавался**: его нет, и он не нужен.
- Persistent ReplyKeyboard «Меню» **не трогалась**: она отдельная и остаётся
  (`bot/telegram_ui.py:18`, `bot/handlers/telegram_streamer.py:119`, покрыта тестами
  `test_telegram_navigation`, `test_telegram_menu_recovery`, `test_telegram_streamer`).
- Добавлен регрессионный тест production-ветки:
  `tests/test_mini_app_legacy_compat.py::MenuButtonCompatibilityTests::test_production_admission_uses_app_button_with_public_url`
  — фиксирует тип `MenuButtonWebApp`, текст «Приложение», URL `PUBLIC_URL/app` и
  fail-closed откат в `MenuButtonCommands` без admission/контракта/Mini App.

## Границы доказательств

- Визуальная проверка выполнена владельцем на его Telegram Desktop; собственных
  native-снимков сессия не делала.
- Signed Mini App bootstrap/viewer/streamer и iOS/Android — NOT TESTED.
