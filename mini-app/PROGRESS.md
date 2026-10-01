# Мини-апп: видимый журнал исполнения

План: `docs/superpowers/plans/2026-10-01-native-mini-app-plus.md` с дополнением `mini-app/IMPLEMENTATION-ADDENDUM.md`. Порядок T1–T6 → T13 → T7–T11 → T14–T17 → T12. Один исполнитель. Отметка «готово» требует фактических тестов и commit; R0–R9 не повторяются.

| Этап | Состояние | Проверка / следующий шаг |
| --- | --- | --- |
| T1 Free baseline | завершён локально | Сверены HEAD/процессы, 31 SHA256, существующие пути; baseline и surface brief. Подготовительный commit отмечен в git; продуктовые тесты не требовались для docs-only этапа. |
| T2–T6 | ожидает | Capabilities → оба тестовых продукта → shell → Free journey → Viewer Plus настройки. |
| T13 | ожидает | 50/200 и атомарный выбор пяти видеоканалов. |
| T7–T11 | ожидает | Category detector/delivery, Free streamer connect, Streamer Plus, подписка. |
| T14–T17 | ожидает | Общая media-доставка и нагрузка, напоминания, отдельные удобства и trial. |
| T12 | последний | Полный suite на финальном snapshot, review, browser/security, backup/restore, guarded staging и честная граница native E2E. |

Известные расхождения на старте: `/viewer/api/digest` требует Plus при бесплатном bot-сценарии (T6); `/streamer/api/communities` POST требует Plus при согласованном Free подключении (T9); старый личный `preview_enabled` не реализует пять серверных слотов (T13–T14). Никакой внешний Telegram send или OAuth не выполнялся.

T1 проверка: локальные документы прошли staged whitespace check, 31/31 закреплённых файлов совпал по SHA256. Шесть предупреждений полного staged `git diff --check` относятся к исходным upstream reference-файлам security skills; байты оставлены неизменными.
