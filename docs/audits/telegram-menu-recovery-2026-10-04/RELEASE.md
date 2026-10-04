# Меню: итог выкладки 04.10.2026

## Проверенная версия

Ветка autonomous/twitchsignal-roadmap. Guard и активный staging:39bc82171114a0a6b20ad4251bd68c4cc2d3f3f0. Deployment5211c9f8-6a2f-4834-9aa6-88706a4a34a7, SUCCESS. Runtime-изменения отдельно в0b68427; макеты админки сохранены без изменений по разрешению владельца в2a418bb. После guard документация и smoke-helper уточнены; runtime не менялся.

Входы: [Testbot](https://t.me/TwitchSignalTestbot), [Mini App](https://worker-staging-2f74.up.railway.app/app), [локальный макет «Ещё»](http://127.0.0.1:8874/more.html). Прямой browser-вход не заменяет подписанную Telegram-сессию.

## Проверки

- Полный штатный gate:1570 passed,2 skipped,3646 subtests passed,1004.99s; FULL-39bc821-PASS.log. Оба skip исходные: symlink segment validation без Windows-привилегий. Assertions не ослаблялись, новых skip нет.
- Новые16 menu regressions/2 subtests, compatibility31/30 subtests и tariff1PASS; scoped reviewer подтвердил runtime и изменения фикстур. More layout Chromium/WebKit32PASS,4PNG: локальный макет, не Telegram.
- Actual getMe: TwitchSignalTestbot,8859004067;195 runtime files и17 HTTP assets совпали с committed archive. Shell/CSP,9 unsigned401,27 migrations/integrity/FK, legal503 и payment offline/invoice OFF/external create OFF/callback404 подтверждены. Последние100 deployment logs: credential-shaped matches0, raw logs не экспортированы.
- Общий MenuButton: web_app «Приложение» → staging/app. Для владельца API вернул строго default/null/null, эффективная кнопка наследует общую. [Официальный контракт Telegram](https://core.telegram.org/api/bots/menu). Первый helper ошибочно требовал scoped web_app: сохранён FAIL, positive inheritance RED и12 selftests PASS; commands override/wrong global URL/malformed default остаются отклонёнными. Reviewer подтвердил helper, настройки Telegram не менялись.
- На развёрнутом runtime pure builders: точные More rows/callbacks, menu:live, persistent Menu, Home/unknown/report/catalog PASS. Это не фактическая доставка или native UI.
- Backup remote/local restore63 tables PASS; migration-copy/schema/cipher/catalog и неизменённая Mini App QA переиспользованы после проверки13/18/3 файловых идентичностей. Активную БД не заменяли.
- Production deployment466499d5-6979-4a81-8aee-67efcf628976/SHA dc9239eb0b82fb80d1788fc657205740cebc49e9 сохранились. HTML/export, Free-права и старые группы сохранены. Push/merge/реальных платежей нет.

## Native: выполнено и ограничение

До выкладки открыт настоящий Testbot, username проверен в профиле. Desktop показывал вместе системную «Приложение» и нижнюю ReplyKeyboard «Меню»; нажатие «Меню» вернуло Home с настоящими счётчиками. Исходный More без live сохранён. BEFORE PNG содержат только Testbot; другие переписки исключены.

При подготовке проверки после выкладки Computer Use сообщил: “Computer Use was stopped by the user with the physical Escape key.” Root прекратил Windows app input; последующих CU вызовов не было. AFTER screenshots, /start на новом runtime, новое More/live в Desktop, WebApp return, selector dialog/cancel и закрыть/открыть чат: NOT TESTED. Нативную приёмку не объявляем завершённой.

Исходная причина визуального исчезновения «Меню» не установлена; серверные cache/restore/cancellation/restart ошибки подтверждены RED и исправлены независимо. Клиентская видимость не выводится из успешного send или health200.

iOS/Android, OAuth владельца, реальное завершение выбора канала, новые отправки другим получателям/группам/каналам и реальные платежи: NOT TESTED. Следующая проверка — Desktop AFTER, когда владелец продолжит управление; дальнейший продуктовый редизайн этим checkpoint не запускается.
