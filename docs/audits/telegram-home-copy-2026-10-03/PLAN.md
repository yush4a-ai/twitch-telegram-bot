# Оформление Telegram Home — 03.10.2026

Запрос владельца: исправить текстовое оформление главной по присланному screenshot. BASE `62572db`, ветка `autonomous/twitchsignal-roadmap`.

## План

1. RED → компактные блоки и HTML-иерархия → focused PASS.
2. Проверка длинного текста, экранирования, честных счётчиков и состояния неизвестных эфиров; визуальный HTML preview с точным caption из runtime. Это макет типографики, не нативный Telegram screenshot.
3. Scoped review → commit → полный штатный gate на итоговом snapshot → pinned Testbot → проверка фактического SHA/бота/runtime и отчёт.

## Границы

Меняется только `build_home`: счётчики выделены, ники и категории имеют разные роли, блок Twitch сокращён. Источники данных, клавиатуры, права, фото/видео, цены, HTML/export и payment OFF сохраняются. Приветствие нового пользователя сохраняется. Без production, отправки тестовых сообщений, OAuth и денег.

## Факты

- RED: 3 FAIL / 1 PASS на новом тесте до реализации. PASS: 32 проверки Home/navigation/add после правки.
- Длинные внешние строки экранируются до HTML; видимый caption укладывается в 1024 UTF-16 единицы. Live query failure остаётся unknown; all-muted обозначен явно; три показанных стримера и общий счётчик сохранены.
- Impeccable/typeset: обычный Telegram шрифт; bold для заголовков, чисел и ников; italic для категорий и оговорки о последней проверке; пустые строки разделяют блоки. Вебовые шрифты/CSS к Telegram не применяются. Mechanical source scan: 0 findings (не проверяет Telegram entities).
- Stop-slop: убрано повторное название и общая подсказка добавления у возвращающегося пользователя; подключение Twitch не означает включённые публикации.
- Подробный full/staging результат будет записан после фактической проверки. Native Desktop/iOS/Android и приёмка владельца пока NOT TESTED.

## Контрольная точка перед full gate

Шаги 1–2 завершены. Focused32PASS, smoke-helper8PASS и unknown-start1PASS. Scoped self-review завершён, критичных замечаний нет. Chromium390/1440 макет проверен. Свежая staging backup/remote+local restore: PASS, integrityok,63tables,3,416,064bytes. Шаг3: commit и штатный full gate на неизменном snapshot; результат ещё не объявлен.
