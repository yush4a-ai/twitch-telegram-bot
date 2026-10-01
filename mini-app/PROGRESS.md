# Мини-апп: видимый журнал исполнения

План: `docs/superpowers/plans/2026-10-01-native-mini-app-plus.md` с дополнением `mini-app/IMPLEMENTATION-ADDENDUM.md`. Порядок T1–T6 → T13 → T7–T11 → T14–T17 → T12. Один исполнитель. Отметка «готово» требует фактических тестов и commit; R0–R9 не повторяются.

| Этап | Состояние | Проверка / следующий шаг |
| --- | --- | --- |
| T1 Free baseline | завершён локально | Сверены HEAD/процессы, 31 SHA256, существующие пути; baseline и surface brief. Подготовительный commit отмечен в git; продуктовые тесты не требовались для docs-only этапа. |
| T2 | завершён локально | `d7d78d2`, tree `b7996dcefa01b253a98f5c72821aa6f1f8d07db3`; RED 5 отсутствующих поведений, затем 17 passed + 3 subtests в focused/related тестах, staged diff check 0. |
| T3 | завершён локально | `34b3966`, tree `12b3a67b88b90ee3a1397739a45e531727149a76`; RED 4 viewer/migration теста, затем 24 passed + 18 subtests в billing/probe наборе; temp legacy restore/integrity OK. Только mock, без денег. |
| T4 | shell локально; черновики форм ещё открыты | `a6ab8f8`, tree `b2018a4fea0d1b2b64663c405fcbf3f4d73e12b8`; 29 passed, 15 subtests; browser PASS 20 переходов, 8 снимков 360/390/768/1440 light/dark; реальный Telegram клиент NOT TESTED. |
| T5 | завершён локально | `1ae0f71`, tree `967d4512fcf58052c3ff38be6ebc300f898cdf9c`; 25 passed, 19 subtests; browser Free journey PASS, 4 снимка, длинное имя 360 px, сеть/rollback. Только temp DB/fake Twitch, native NOT TESTED. |
| T6 | следующий | Viewer Plus фильтры и Free quiet hours/digest, затем T13. |
| T13 | ожидает | 50/200 и атомарный выбор пяти видеоканалов. |
| T7–T11 | ожидает | Category detector/delivery, Free streamer connect, Streamer Plus, подписка. |
| T14–T17 | ожидает | Общая media-доставка и нагрузка, напоминания, отдельные удобства и trial. |
| T12 | последний | Полный suite на финальном snapshot, review, browser/security, backup/restore, guarded staging и честная граница native E2E. |

Известные расхождения на старте: `/viewer/api/digest` требует Plus при бесплатном bot-сценарии (T6); `/streamer/api/communities` POST требует Plus при согласованном Free подключении (T9); старый личный `preview_enabled` не реализует пять серверных слотов (T13–T14). Никакой внешний Telegram send или OAuth не выполнялся.

T1 проверка: локальные документы прошли staged whitespace check, 31/31 закреплённых файлов совпал по SHA256. Шесть предупреждений полного staged `git diff --check` относятся к исходным upstream reference-файлам security skills; байты оставлены неизменными.

T4 серверный RED: три auth теста сначала упали из-за отсутствующего `mini_app_db`; отдельный RED входа из private menu указал прежний `/viewer`. После реализации `/app/api/bootstrap` проверены подпись, возраст, дубликаты, чужой клиентский ID и выключенный production-контур. Локальный browser fixture использовал синтетическую подпись и подмену только внешнего Telegram SDK; рабочие `/app`-ассеты не содержат mock-кода. Browser-сценарий создан после серверного RED, отдельный browser RED до реализации не зафиксирован. Screenshots относятся к shell, не к законченным Free/Plus действиям. Исходящие Telegram и OAuth не выполнялись.

T5: RED четыре теста упали на отсутствующем Twitch-аргументе маршрута; browser RED остановился на отсутствующей форме поиска. Поздний RED выявил отсутствие `status` у устаревшего live-маркера, а проверка 360 px — переполнение от длинной подписи кнопки. Исправлены оба дефекта. В API подпись Telegram определяет пользователя; Free follow повторяет Twitch-проверку и атомарный лимит БД; notify теперь обновляется атомарно только при наличии собственной подписки. `requestWriteAccess` вызывается только после клика «Добавить», но нативное разрешение на устройстве ещё не проверено. Никаких исходящих сообщений или OAuth в проверках не было.
