# Админ-панель владельца, фаза A — evidence

Дата: 4 октября 2026. Ветка: `autonomous/twitchsignal-roadmap`. Спека: [2026-10-04-admin-panel-v2-design.md](../../superpowers/specs/2026-10-04-admin-panel-v2-design.md). План: [2026-10-04-admin-panel-v2-phase-a.md](../../superpowers/plans/2026-10-04-admin-panel-v2-phase-a.md). Паспорт решений: [ADMIN-PANEL-PASSPORT.md](../../design/admin-concept-2026-10-04/ADMIN-PANEL-PASSPORT.md).

## Коммиты фазы

| Коммит | Что сделано |
| --- | --- |
| `833da6b` | `DESIGN.md` переведён на графит |
| `f81b260` | `bot/admin_directory.py` и его тесты |
| `091f1b5` | блоки `access`, `backup`, `deliveries`, `attention` в снапшоте |
| `208a9b4` | подключение каталога в `main.py` |
| `58debe8` | новая оболочка и экраны панели |
| `dba4f81` | финализация фазы и дизайн-пакет |
| `c30c0c1` | страница входа в графитовой системе |
| `fcdf6fb` | исправления по итогам ревью |
| `f9cc22c`, `67d4a88`, `6a0f2e6` | записи в STATUS/DECISIONS и паспорт |

## Тесты

- Focused после исправлений: 69 passed, 3 subtests (`test_admin_web`, `test_admin_ui`, `test_admin_metrics`, `test_admin_entry`, `test_admin_directory`, `test_admin_telegram_auth`), плюс `test_growth_funnel` — 73 passed.
- Полный прогон на финальном снимке: **1713 passed, 2 skipped, 3745 subtests, 7 failed**, 454 с.
- RED→GREEN по каждой задаче зафиксирован: Task 2 — 13 падений до реализации, Task 3 — 13 падений, Task 5 — 5 падений, тест бюджета каталога на прежнем коде показывал 5.03 с.

Семь падений полного прогона не относятся к фазе A:

1. `test_production_admission` (5 тестов) — Python в этой сессии не может писать за пределы рабочего каталога, `tempfile.gettempdir()` возвращает корень репозитория, а контракт оператора обязан лежать вне репозитория.
2. `test_production_copy_rehearsal` (1 тест) — требует отсутствующий артефакт `docs/audits/mini-app-redesign-plus-2026-10-02/P18-final-release-chromium/...`.
3. `test_preview_runtime::test_enabled_destination_starts_one_physical_session` (1 тест) — воспроизводится независимо от фазы, `bot/preview_runtime.py` и его тест в диапазоне фазы не менялись.

## Браузерная проверка

Локальный фикстур `tests/admin_ui_fixture.py`, headless Chromium, вход по аварийному ключу. Десять сочетаний «экран × ширина» (360/390/768/1440): горизонтальное переполнение 0, ошибок консоли 0, фокус видим (`outline: solid 2px`). Пустые состояния: метрики «Нет данных», «Активный Plus» «Нет данных», строка здоровья «Не удалось проверить», доступы «Данные временно недоступны», внимание «Открытых проблем не зафиксировано, состояние подсистем не подтверждено». Сроки показаны в МСК. Скриншоты: `docs/design/admin-concept-2026-10-04/phase-a/` (11 PNG).

Детектор Impeccable: одна находка `side-tab accent border`, исправлена; повторные прогоны чистые (`[]`).

## Что не проверено

Реальный staging-вход владельца (нужны `OWNER_CHAT_ID` и ключ в staging), нативные iOS/Android, выдача/продление/отзыв из веба, имена и отметка активности, версии staging/production. Фаза B не начиналась: она меняет `bot/database.py`, хендлеры и `main.py`, занятые параллельной работой над Mini App и release-инфраструктурой.

## Ограничение среды

Python-процессы этой сессии не могут писать вне рабочего каталога, поэтому тесты, которым нужен временный каталог вне репозитория, падают. Это ограничение харнесса, а не регрессия продукта: перечисленные выше файлы фазой A не изменялись (`git diff --name-only` по ним пуст). В обычной среде эти тесты нужно прогнать повторно перед деплоем.
