# P20–P21 — инженерный выпуск Mini App на staging

P01–P21 завершены в разрешённом объёме. Самостоятельная приёмка владельца ожидается. Production launch, банк и денежная активация в этот выпуск не входят.

## Точная версия и вход

- Ветка: `autonomous/twitchsignal-roadmap`.
- Развёрнутый код: `52e56e9633a77f7e5025a1be7dacbbf8c0769970`.
- Railway deployment: `fba34520-f2a7-4d5d-aa86-1e8e3b0072b1`, активный SUCCESS подтверждён guard и независимой проверкой.
- Bot API getMe: `@TwitchSignalTestbot`, id `8859004067`, is_bot=true.
- Постоянное меню: MenuButtonWebApp **Приложение**, `https://worker-staging-2f74.up.railway.app/app`.
- Открыть [тестового бота](https://t.me/TwitchSignalTestbot) → **Приложение** → [чеклист владельца](OWNER-ACCEPTANCE.md). Прямой HTTPS URL открывает оболочку и требует настоящего Telegram initData.

После smoke сохраняется отдельный документационный commit с доказательствами. Его SHA не считается SHA развёрнутого кода; runtime/backend/config в нём не меняются.

## Проверки релиза

| Проверка | Фактический результат |
|---|---|
| P19 полный pytest |1403passed,2skipped,3248subtests;903.06s,exit0 |
| Guard полный pytest на committed release |1403passed,2skipped,3248subtests;850.45s,exit0 |
| Два skips | Прежние Windows WinError1314 при symlink; новые skips не добавлялись |
| Code/security review | Один read-only reviewer; F1 закрыт, threat model сохранён |
| Browser QA | Chromium151/WebKit26.5;92journeys,2520matrixchecks,984PNG; widths360/390/430/768/1440,4themes,3tiers,0/6/200, text200/realzoom200/short390×440 |
| Source/artifact |174runtime/dependency files совпали с точными байтами штатного committed Git archive |
| Public resources | `/app` и14assets HTTP200; точные SHA текста после того же read_text UTF-8/universal-newline чтения, что использует сервер |
| Headers | `/app` без XFO, exact frame-ancestors https://web.telegram.org;14assets сохраняют DENY |
| Auth |4unsigned App POST и admin GET:401; signed live API NOT TESTED |
| DB | integrity=ok,foreign_key_check=[];25schema versions; миграция active staging прошла |
| Backup/migration-copy | remote/local backup/restore и отдельная копия54→60tables/20→25versions, old data/actual key/rollback/reopen PASS; backup сохранён вне Git |
| Money | immutable first-release offline; external_create=false,invoice=false; Platega callback404 |
| Legal/support |5public routes503 до реальных данных/контакта/принятия; bank NOT READY |
| Secrets |0совпадений действующих секретов в проверенных HTTP bodies;0credential patterns в последних100deploymentlogs; raw logs не экспортировались |
| Production | До/после один deployment466499d5-6979-4a81-8aee-67efcf628976/SHA dc9239eb0b82fb80d1788fc657205740cebc49e9; data/vars/deploy не менялись |

Точная проверка: [P20-staging-smoke.json](P20-staging-smoke.json); штатный журнал: [P20-guard-deploy.log](P20-guard-deploy.log). Старый6074744 — историческая версия, fresh до этого выпуска уже dc9239. Это не объявляет весь исторический production аудит завершённым.

## Операционные RED и исправления

Два initial smoke failures сохранены. Первый: HTTP header dict ошибочно искал uppercase имя; все14actual headers были DENY, Railway передал lowercase. RED actual AST loop→PASS; missing/SAMEORIGIN по-прежнему отвергаются.

Второй: ожидались raw Git blobs, но установленный Git с core.autocrlf=true создаёт CRLF в штатном archive.167из174remote files отличаются от blob только newline и EXACT совпадают с повторно собранным committed archive; содержательных изменений0. Reviewer независимо подтвердил все174archive hashes. Проверка теперь раздельно сверяет raw archive files и HTTP-текст после реального server reader; полные наборы/точные SHA сохранены. RED→7selftests PASS14.012s; подмена raw/served hashes отвергается. Исправлен только одноразовый operational probe; runtime, deploy guard и релизный код не менялись.

## Медиа и границы

Новая локальная нагрузка:1×1000 RESOURCE_STOP25s;100×10 и1000×5 PASS контролей с cap2/deferred98/4998. Это не обслуживание всех потоков и не Telegram SLA. В актуальном staging сохранена прежняя конфигурация: preview enabled,1concurrent job,2active sessions,initial delay20s/interval60s. Пять выбранных стримеров — личный лимит; при общей перегрузке остаются фото и честный статус. Никакого расширения инфраструктуры или лимитов ради PASS.

Native Telegram iOS/Android/Desktop/Web SDK, fullscreen/safe areas/Back/keyboard/requestChat/write access/OAuth return, signed live API, owner OAuth, реальные send/edit/delete/animation→photo и Stars/Platega: **NOT TESTED**. Fake sender/SDK и новые временные БД явно отделены от реального Telegram. Smoke делал только разрешённые read-only Bot API, публичные GET и unsigned отрицательные HTTP проверки; тестовых сообщений/платежей/grants не создавал.

## Сохранённый продукт

А «Собранный» + live/offline grouping, четыре нижние кнопки в обоих режимах и один role-aware Plus. Viewer150 ₽/месяц; Streamer300 ₽/месяц включает Viewer для frozen Telegram beneficiary. Free50/фото/quiet/basic/Twitch unavailable/HTML/экспорт, прежние команды и legacy groups сохранены. Plus200/5видео включая offline/атомарная замена/серверный sixth block/filters/category/reminders15–30/folders/history; Streamer own verified placement/template/buttons/variants/confirmed stats. Owner-only добровольное однократное7-day ознакомление не сбрасывалось; открытие приложения и payment unavailable не дают права.

[Сценарии](SCENARIOS.md), [снимки](P18-browser-manifest.json), [медиа/browser](P18-QA.md), [модель угроз](mini-app-threat-model.md), [backup](MIGRATION-COPY.md), [10owner inputs](../../workflows/OWNER-INPUTS-FOR-LAUNCH.md). HTML/audit/archive материалы сохранены. После самостоятельного теста ожидается следующий круг замечаний.
