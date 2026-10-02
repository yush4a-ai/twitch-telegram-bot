# P18 — браузеры, доступность и media-нагрузка

База `8a8a70f0e3365890ba19f121d2ce95381f5565f8`, текущие runtime bytes закреплены в `P18-browser-manifest.json`. Каждый leaf сравнивает все174 исходных SHA до/после; внешний wrapper сверяет SHA JSON/PNG. Предыдущие FAIL каталоги сохранены как история, финальные — `P18-final-release-chromium` и `P18-final-release-webkit`.

## Результат

- Chromium151.0.7922.34 и WebKit26.5: по45 последовательных journeys с новой TEMP SQLite + отдельный настоящий browser zoom200 для Viewer Plus. Итого92 прогона,2520 matrix checks и984 уникальных PNG; page errors0, внешние запросы0. Это браузерные проверки с fake SDK, не нативная приёмка Telegram.
- В каждом engine: пять ширин360/390/430/768/1440, own light/dark и Telegram light/dark; Free/Viewer/Streamer ×0/6/200. Длинные имена, live/offline/stale, loading/error/empty/auth expiry, text200360/390, short390×440, reduced motion, contrast≥4.5, доступные имена, standalone targets≥44px.
- Проверены четыре tab/единый Plus/profile/menu, SDK Back/focus/dialog/scroll restore/viewport/safe inset cleanup; actual API→mutation→reload, поиск/follow/pause/settings/channel/OAuth/posts/variants/statistics. Viewer150 и Streamer300/четыре SVG/раскрытие/secondary Viewer/inheritance/expiry. Три оплаты ведут к unavailable, права от query/paid не выдаются.
- Настоящие два окна: held stale request → canonical изменение во втором окне →409, затем offline-inclusive5 и stale replacement409; неизменность канонического выбора доказана. Повторные фото/animation права дополнительно покрывает финальный pytest.
- Реальный zoom меняет browser factor/DPR/viewport и сбрасывается в исходные метрики; CSS zoom/transform равны1/none. Снимки через протокол соответствуют физическому viewport. Text200 — отдельная проверка.

## Исправления и обзор

Два CSS дефекта: clipped focus outline у navigation-row и маленькая ссылка Home. Также исправлены ошибки QA: literal6 вместо200 при удалении папки, single-page context для двух окон, неверное имя Free gate и позднее ветвление оплачиваемых settings. Проверки канонических данных сохранены. Watchdog ставит timedOut до остановки собственного процесса и возвращает failure даже при гонке exit0; четыре regression scenarios. Fixture/guard11tests22subtests PASS.

Impeccable Operate/craft-floor и актуальные web-design-guidelines применены адресно; detector `[]`. Root просмотрел актуальные mobile light Plus, dark200/длинные имена, desktop, WebKit StreamerPlus/text200/настоящий zoom channel. Один reviewer подтвердил watchdog и5PNG двухоконного сценария. Дополнительных косметических раундов нет; стоп-слоп не менял продуктовые факты и legal gates.

Обзор выявил F1: inherited XFO DENY блокировал Telegram iframe. Scoped fix `/app` допускает только `https://web.telegram.org` через CSP.31tests44subtests PASS; два origin probes в обоих engines PASS, четыре PNG SHA независимо проверены. Admin/OAuth/legal/API/assets DENY сохранены. После этой правки повторён весь финальный browser gate. Стандартный screenshot WebKit вставлял собственный stylesheet; raw snapshot подтвердил чистую консоль, CSP не ослаблена.

## Новая нагрузка H.264 без звука24s

Все профили выполнялись последовательно, только искусственные источники/fake Telegram/TEMP DB. Ограничители не повышались: capture/session cap2,25s,96tasks,256MB growth,20CPU s,64MB disk,80pending.

| Профиль | Результат | Capture/deferred | Upload/reuse | RSS growth / tasks | Прочее |
|---|---|---|---|---|---|
|1×1000|RESOURCE_STOP elapsed25.016s,exit2|1/0|1/454|28.34MB/45|455 начатых fake edits; p95 ordinary3406ms/photo3500ms|
|100×10|PASS4.625s|2/98|2/18|43.66MB/50|expiry sessions closed, selection disabled|
|1000×5|PASS3.875s|2/4998|2/0|47.22MB/48|5000 разных потоков; expiry cleanup PASS|

Каждый профиль: external sends0, TEMP DB удалена, фактический codec проверен. Первый stop только по времени, остальные ресурсы безопасны, поэтому следующие профили разрешены. PASS последних профилей подтверждает работу ограничителя/deferred/cleanup; он не означает обслуживание всех отложенных потоков. Личный лимит5 и fake p95 не Telegram SLA. Фото при перегрузке и честный media статус обязательны; масштабирование требует отдельного измеренного плана.

## Ограничения

Настоящие Telegram iOS/Android/Desktop/Web, OAuth владельца, requestChat/write access, реальные send/edit/delete/animation→photo и платежи: NOT TESTED. Full-suite/backup/deploy относятся к P19/P20. HTML/export и старые connections не удалялись.
