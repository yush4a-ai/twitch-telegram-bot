# SDD ledger — plan: docs/audits/mini-app-polish-2026-10-03/PLAN.md

Base098736fc1e713e18bb2fa114f1d4b5fe5ce0fec9. Предыдущий чат завершил release и idle; дерево чистое перед началом. Единственный исполнитель. Пользователь разрешил аудит и самостоятельную реализацию.
Pre-flight: Q02 shell/insets → Q03 dialogs/CTA, Q03 auth lifecycle → Q04 purchase, Q02/Q03/Q04 → Q05 browser gate. Совместимые общие токены/строгие API, конфликтов нет.
Ruling: Windows ledger в mini-app вместо Bash bookkeeping; не устанавливаем сторонние helpers. Стоимость: ручная запись checkpoint.
Ruling: прежнее ожидание max(safe,content) в QA неверно по официальному iOS source. Меняем на независимые слои safe+content, env — fallback системного. Цена ошибки — геометрия других клиентов; native приёмка отдельно.
Q01 complete: исходные экраны и аудит сохранены, Chromium RED30checks/18fail (включая поздний bootstrap/pagehide и publishing), WebKit RED25/15fail; не ERROR. BEFORE/races-chromium/report.json. Product source до изменений идентичен ed164ef.
Q02 in_progress: shared shell/insets/forms. Q03/Q04 подготовлены, регрессии RED.

Q02–Q04 implemented: Chromium34checks PASS, geometry320/390/430 and dialog/time bounds; focused27tests/2717subtests PASS. BEFORE immutable. Полный браузерный gate на финальном коде впереди.
Final reviewer: Important late connection feedback and Minor delayed clearing. Последний повышен до Important: при зависшей сети success сохранялся без ограничения. REVIEW-RED WebKit10checks/3fail → исправлены scoped writers, немедленный refresh, keyboard loop → REVIEW-GREEN10PASS. Новая клавиатурная регрессия обнаружила уход Tab из диалога; исправление общих компонентов после RED. Тесты/черновики/авторизация не ослаблены.
Q05 in_progress: финальный browser batch, copy/accessibility scan, required full suite через штатный staging guard после clean commit.

Q02/Q03/Q04 complete: финальный UI tree совпадает в40browserreports; Chromium/WebKit20journeys each PASS,352PNG; targeted46checks/28PNG each PASS, дополнительный text200 focus4checks each PASS.11AFTERgallery/83screens визуально проверены,409AFTERPNG всего. Цветовые пары>=4.5,44px,4темы,text200,viewport320–1440. EVIDENCE.json проверяет current15assets и screenshot hashes.
Ruling: дополнительная focus-проба после визуального просмотра оказалась PASS без продуктовой правки; оставляем нативную прокрутку и текущий router. Стоимость: реальную мобильную клавиатуру всё ещё нужно проверить на устройстве.
Copy gate: stop-slop по изменённым surface/copy и пакету. Точные тарифы, сроки, ограничения, правовой смысл сохранены. Web-design-guidelines+impeccable detector[]; no new dependencies.7release-helper selftests PASS. Дальше clean commit → штатный guard с полным suite → проверка SHA/бота/поведения и итоговый evidence checkpoint.
