# TwitchSignalBot: скиллы для Native Mini App + Plus

Дата проверки: 01.10.2026. Только этот проект; глобальные установки не менялись.
Пользователь попросил учитывать найденные навыки и не допускать конфликтов. Это распределение задач, а не требование загрузить все инструкции на каждый шаг.

## Проверенная установка

| Навык | Где находится | Применение |
|---|---|---|
| telegram-mini-app-skill | `.agents/skills/telegram-mini-app-skill/SKILL.md` | Основной справочник Telegram UI/lifecycle/safe areas |
| telegram-webapps | `.agents/skills/telegram-webapps/SKILL.md` | Второй справочник навигации, bot setup и UI; с ограничениями ниже |
| security-best-practices | `.agents/skills/security-best-practices/SKILL.md` | Обзор безопасности Python/JS без смены aiohttp на иной сервер |
| security-threat-model | `.agents/skills/security-threat-model/SKILL.md` | Отдельный обзор trust boundaries: пользователь, Twitch, чат, заказ, очередь |

Установлено 31 файл инструкций/справок/метаданных/лицензий. Upstream-скрипты, HTML/CSS/JS assets и evals из telegram-webapps (16 файлов) сознательно НЕ установлены и НЕ запускались. Не пытаться копировать отсутствующие helpers по указанию SKILL.md: эти инструкции перекрываются настоящим workflow. Это справочная поставка, не установка runtime.
Закреплены commit: Rithprohos `ae8cfc1e34894b7a28ba784c6e5de7ca265be40e`; yaniv-golan `f82df26d58e47e9ff7825b6332c70fbcf14d71de`; openai/skills `49f948faa9258a0c61caceaf225e179651397431`.
Файловые SHA256 и список исключённых helpers: `docs/workflows/2026-10-01-mini-app-skills-lock.json`. Перед исполнением перепроверить manifest. Его наличие не является результатом security-аудита продукта.

## Уже установлены на компьютере

Корень: `C:\Users\yusha\.agents\skills`.
Impeccable 4.0.4, design-system, ui-ux-pro-max, web-design-guidelines, playwright-skill, stop-slop и imagegen-frontend-mobile присутствуют. Также есть apple-design/minimalist-ui, но они не становятся новым независимым визуальным руководителем.
Не дублировать и не обновлять глобальные навыки. Если текущий Codex не перечисляет нужный навык, явно прочитать существующий SKILL.md по этому пути; не объявлять автоматически, что активная сессия уже перезагрузила список.

## Роли и очередь применения

Проектирование/план: superpowers brainstorming/writing-plans по правилам пользователя. Реализация: executing-plans, test-driven-development; при дефекте systematic-debugging; перед завершением verification-before-completion и scoped code review.
Impeccable в режиме Operate ведёт интерфейс; design-system поддерживает единые компоненты/состояния. ui-ux-pro-max используется для узкого вопроса, а не полного редизайна после каждого экрана.
imagegen-frontend-mobile используется только для запрошенных макетов-изображений. Макеты — отдельные иллюстрации направления, не замена HTML и не доказательство работы.
Telegram guide применяется при lifecycle/navigation/selection; официальная документация определяет текущие доступные API. security-best-practices — при auth/capabilities/billing; security-threat-model — при отдельной проверке границ.
Playwright проверяет реальные браузерные действия с изолированными fixtures. web-design-guidelines проверяет доступность/UI. stop-slop редактирует человеческие тексты, не код/протокол/тестовые данные.
Taste Skill и существующие SEO-навыки остаются для сайта. Remotion — только изолированные demo videos, не анимации интерфейса и не live preview.

## Обнаруженные конфликты и принятые ограничения

1. Community telegram-webapps требует копировать свои boilerplate и валидатор. В этом проекте уже есть проверенный auth и UI. НЕ заменять их wholesale: Python helper сворачивает duplicate-поля в dict, использует другой срок свежести и не проверяет будущую auth_date как наш контракт. Использовать `telegram_identity.verify_webapp_user` и негативные тесты.
2. Community mock предлагается включать по отсутствию URL-параметра. У нас mocks разрешены только в отдельной тестовой fixture. На рабочем /app отсутствие initData должно давать безопасный экран входа, а не фальшивый аккаунт.
3. Community setup/payment guidance содержит создание новых ботов, production deploy и оплату. У нас используется существующий testbot/pinned staging; новые боты, production и реальные счета не создаются.
4. Пример smoke-helper передаёт токен аргументом процесса и сам делает POST. Эти helpers не установлены; настоящие токены нельзя передавать командной строкой/печатать. Пользоваться существующими безопасными тестами и локальными секретами-fixtures.
5. Общий совет включать CORS для любого Telegram WebView неприменим автоматически к same-origin /app. Не вводить permissive CORS. Совет одобрять оплату заранее при медленной проверке не применять; сейчас real checkout вообще выключен.
6. Жёсткое «все цвета только из Telegram» и imagegen-предпочтения текстур не отменяют светлую минималистичную палитру владельца. Свои semantic tokens согласуют системные поверхности/темы; не добавлять стекло, шум и неон ради навыка.
7. Сумма двух safe-area значений с фиксированными fallback из примера не является универсально верной формулой. Проверять фактическое перекрытие в SDK/клиенте, не добавлять второй раз уже учтённый системный отступ.
8. Старый навык не знает всех новых методов. В официальной документации requestChat требует Bot API 9.6 и prepared keyboard request; callback не выдаёт права сообщества. Эти условия важнее общего примера.

Прочитаны основные инструкции четырёх новых навыков, relevant auth/helper code и текущие проектные правила. Это ограниченная проверка пригодности; не утверждать, что весь upstream прошёл аудит или все примеры безопасны. Ни один скачанный helper не исполнялся.

## Поздние кандидаты из старого чата

Expo: expo-router / expo-data-fetching / expo-design-system / expo-native-ui. Android: adaptive / navigation-3 / testing-setup. Также обсуждались SwiftUI, Flutter, agent-device/dogfood и Maestro.
Эти наборы не установлены этим изменением. Их названия сохранены как кандидаты/справочные источники, а не как проверенные обязательные зависимости. Не переписывать веб-приложение под их платформу. Применение device-инструмента требует проверки точного источника, лицензии и наличия разрешённого устройства.
Основные браузерные проверки уже покрывает установленный playwright-skill. Реальные Telegram iOS/Android проверки по-прежнему отдельные и не объявляются пройденными по браузерной эмуляции.

## Первичные источники

- https://github.com/Rithprohos/telegram-mini-app-skills
- https://github.com/yaniv-golan/telegram-webapps-skill
- https://github.com/openai/skills/tree/main/skills/.curated/security-best-practices
- https://github.com/openai/skills/tree/main/skills/.curated/security-threat-model
- https://core.telegram.org/bots/webapps
- https://core.telegram.org/bots/payments-stars
