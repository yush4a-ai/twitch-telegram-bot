# Выпуск оформления описаний — 04.10.2026

Runtime `83503067736c0a2468726d7eba0a7b339183cb0a`, ветка autonomous/twitchsignal-roadmap. Deployment `55fe18be-799a-4475-86c4-c912dd05340d`: active SUCCESS. Финальный documentation-only commit не заменяет deployed SHA.

## Изменения и доказательства

- Аудит92источников/1525русских выражений. Длинные Telegram-описания главной, тарифа/подписки/оплаты, помощи/команд, подключения, добавления/импорта, настроек/тихих часов и отчётов разделены на заголовки и блоки фактов. Mini App уже имеет группы/подробности;18websources идентичны проверенной версии. AUDIT.md/INVENTORY.json.
- Цены150/300, Free50/Plus200/5видео, Viewer inclusion, права, trial, безопасное фото/видео и резервный HTML/export сохранены. Новых изображений0;3одобренных PNG byte-identical. ASSET-IDENTITY.json.
- TDD RED→код→PASS;110focusedPASS/17subtests, release smoke selftest8PASS. Один read-only reviewer: blockers0; HTML/escaping/length и paid/test/trial проверены. Старые семантические assertions не ослаблены.
- Стандартный full guard:1554PASS/2прежнихWindows-skips/3644subtests/1014.45s. FULL-release-PASS.log. Пропуски связаны с symlink в test_preview_analysis_concat.py и test_preview_render_input.py; этих tests не меняли. Два старых прерванных Home full logs сохранены, не считаются PASS.
- Immutable Git-упаковка673files/16,107,133bytes; исключены только audit/design upload bytes. RELEASE-PACKAGE.json. Никаких push/merge main/master/production.
- Chromium/WebKit304checks/14PNG:19экранов ×4ширины ×2темы ×2движка. Фактический текст handlers/builders с fake sender/in-memory SQLite. BROWSER-QA.json. Mini App browser evidence reused после18blobidentity; не native Telegram.
- Fresh staging backup remote/local restore:63tables/integrityok/3,416,064bytes/SHAf89a953c9af9e318416cf38dba901e24baba50b0126f4bc8e3410d22863b05ec. STAGING-backup.json. Migration-copy reused после13schema/cipher/catalog Gitblobidentity; не новый migration run.
- Actual staging:195runtimeSHA256,17HTTPassets+shell, bot8859004067/TwitchSignalTestbot, постоянный MenuButtonWebApp«Приложение»,9unsigned401,27schema versions/integrityok/FK0. Paymentoffline/externalCreatefalse/invoicefalse/livecallback404/legal503. STAGING-staging-smoke.json. Из61полученной записи bounded100log запроса credentialpatternmatches0; не полный historical audit.
- Actual pure builders на опубликованном runtime:4группы обоих тарифов и точные catalogdescription, Homeссылки/счётчики, unknownstatus и reportHTML/неполные данные PASS. STAGING-copy-behavior.json. Отправок/мутацийБД/платежей0.
- Production до/после `466499d5-6979-4a81-8aee-67efcf628976` / `dc9239eb0b82fb80d1788fc657205740cebc49e9` совпадает.

## Вход и ограничения

Telegram https://t.me/TwitchSignalTestbot → «Меню» → «Тариф» / «Моя подписка», «Помощь», «Я стример», настройки и отчёты. Mini App https://worker-staging-2f74.up.railway.app/app открывается через «Приложение» в Testbot с Telegram auth.

Локальная галерея http://127.0.0.1:8874/, сохранённый preview.html. Переключаются19экранов и темы; Telegram-кнопки иллюстративны. Сервер отдаёт только HTML/3PNG, не репозиторий/БД/секреты.

Native Telegram Desktop/iOS/Android и визуальная приёмка **NOT TESTED**. Цвет/шрифт/геометрия blockquote определяются клиентом. Реальные send/edit/media, ownerOAuth и платежи **NOT TESTED**; внешние сообщения не отправлялись. Legal/support/bank readiness не заменены вымышленными данными, legal остаётся503. Mediaнагрузка прошлого функционального выпуска не повторялась ради copy: capture/encoding/лимиты неизменны, poller diff только форматирует итоговый отчёт.
