# P08 — три темы и SDK

BASE `2a71f82`; один исполнитель, scoped design-system/SDK review. Primary mobile-app-ui-design, accepted A semantic palette; composition переносится в P09, не в этой задаче.

## Изменения

Light по умолчанию независимо от OS/Telegram; собственный dark #171717/#242424 без синего tint. Сохранённый выбор light/dark/telegram переживает reload и storage denied. Telegram choice читает whitelist безопасных ThemeParams и themeChanged; реальные bg/text/button/link/header/bottom применяются к семантическим CSS tokens и SDK chrome colors. Не только colorScheme. Невалидные цвета не становятся CSS, low contrast получает читаемый fallback. Theme module включён только в явный asset allowlist; CSP без unsafe-inline сохранён.

SDK ready/expand/fullscreen при наличии, fallback отказа, max safe/content по четырём сторонам с OS env, finite/bounded viewport. BackButton handler не дублируется; dispose снимает свои handlers/subscribers и завершает pending request promises. Старые requestWriteAccess/requestChat/link contracts сохранены. Theme controller экспортируется для общего Profile P09; QA не добавляет query flags или auth bypass в продукт.

## Проверки

- `P08-assets-red.log`: theme.js404; `P08-browser-red.log`/`P08-chromium-red-qa.json`: тёмный Telegram включал прежний синий dark при первом входе.
- Финальный scoped Python: **9 passed /20 subtests**, exit0, `P08-assets-pass.log`.
- Chromium151/WebKit26.5: **PASS по7 PNG**, JS errors0/external0, `theme-*-qa.json` с source/image SHA. Actual `/app`, TEMP SQLite, signed fixture identity и SDK double.
- First light даже с dark OS/SDK, own neutral dark, reload/event/denied storage, custom Telegram light/dark, header/bottom CSS, full safe/content max/viewport, repeated init/dispose, one back handler, rejected fullscreen проверены.
- Фактический просмотр PNG выявил кадр незавершённого CSS transition. Для снимков штатно отключены конечные animations; переснято без изменений UI ради baseline. Прежние14 PNG сохранены в `P08-before-animation-settle/`.

Official [ThemeParams и SDK events](https://core.telegram.org/bots/webapps#themeparams) перечитаны. Реальные Telegram fullscreen/safe areas/BackButton/native NOT TESTED; эти browser PASS не подтверждают устройство. Production/staging/money/outbound не менялись. Следующий P09 shell/profile/navigation; UI journey полнота P10–P17.
