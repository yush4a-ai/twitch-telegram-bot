# Payment security remediation — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Шаги отмечены
> чекбоксами (`- [ ]`). Каждая задача заканчивается прогоном своих тестов и коммитом.

**Goal:** закрыть находки повторного аудита `docs/audits/payment-security-repeat-2026-10-06`,
не сломав работающую оплату звёздами и не тронув production.

**Architecture:** работа идёт в чистой ветке `remediation/payment-security-2026-10-06`
(создана от безопасного `origin/main`). Деньги разделяются на три явных слоя: политика
(что вообще разрешено), платёж (подтверждённые деньги) и выдача доступа (entitlement),
с durable-состоянием и повторяемой сверкой между ними. Мобильный клиент получает честную
таксономию ошибок и восстановление после таймаутов.

**Tech Stack:** Python 3.12, aiogram 3.30, aiohttp, SQLite (WAL), pytest, ванильный JS
(мини-апп), Playwright (браузерная проверка).

**Spec:** `docs/audits/payment-security-repeat-2026-10-06/REPORT.md` + `FINDINGS.md` +
`FINDINGS.json` (реестр находок — источник истины).

## Global Constraints

- Production не трогать: ни деплоя, ни переменных, ни миграций боевой базы.
- Push запрещён; ветку `autonomous/twitchsignal-roadmap` не публиковать (PII).
- Не ослаблять защиты: владение каналом/актёром, бюджет Twitch, изоляция входов,
  идемпотентность платежей, fail-closed допуск, эксклюзивность писателя, недоверие к
  заголовкам пересылки, владение получателем платежа, изоляция пользователей.
- Цены и инварианты: Viewer Plus 150 ₽/мес (100 XTR), Streamer Plus 300 ₽/мес (200 XTR),
  Streamer включает Viewer тому же получателю, автопродления нет.
- Все новые проверки — RED-тест до кода, PASS после; старые тесты не ослаблять.

## Review Focus

Наиболее вероятные источники боли, для которых нужны тесты в своих задачах:

1. Повторный запрос покупки после таймаута не должен создавать второй заказ.
2. Подтверждённая оплата обязана закончиться доступом даже после истечения/отмены заказа.
3. Возврат обязан снять только доступ своего заказа.
4. Лимиты частоты не должны мешать владельцу и не должны расти в памяти.
5. Юридические тексты и кнопки не должны обещать то, чего нет.

---

### Task 1: Безопасная линия и защита от публикации (P0 · D27)

**Files:**
- Create: `scripts/git-hooks/pre-push`, `docs/audits/payment-security-remediation-2026-10-06/PII-REMEDIATION.md`

- [x] Ветка `remediation/payment-security-2026-10-06` от `origin/main`; проверено, что
      `022779b`/`216c97c` недостижимы.
- [x] Hook `pre-push` (отказ для небезопасных ссылок и для любой ветки с PII в истории);
      проверен тремя прогонами.
- [x] Пометки `DO-NOT-PUSH/*` и заметка `.git/DO-NOT-PUSH.md`.
- [x] Документ с процедурой окончательной очистки для владельца.
- [x] Commit: `chore(security): protect the release lineage from local PII history`.

**Interfaces → Produces:** ветка `remediation/payment-security-2026-10-06` — единственная
линия для дальнейших коммитов и будущих релизов.

---

### Task 2: Зависимости и CI-аудит (P1 · D7, D10, D11)

**Files:**
- Modify: `requirements.txt` (пин `streamlink`), `.github/workflows/tests.yml`
- Test: `tests/test_dependency_security.py`

- [ ] RED: тест «пины не ниже исправленных версий» (streamlink ≥ 8.6.0, aiohttp ≥ 3.14.3,
      cryptography ≥ 50.0.0, urllib3 ≥ 2.7.0) — падает на текущем пине.
- [ ] Обновить пин до минимальной исправленной версии (по официальному changelog/security-
      источнику), не поднимая остальные библиотеки.
- [ ] Прогнать тесты превью: `tests/test_preview_*.py`, `tests/test_streamlink*.py`,
      `tests/test_live_preview*.py` — захват, fallback, таймауты, очистка.
- [ ] Добавить в CI шаг аудита зависимостей (`pip-audit`/`osv-scanner`) как отдельный job,
      не блокирующий существующий прогон до первого зелёного результата.
- [ ] Commit: `fix(deps): update streamlink past the file:// redirect flaw`.

---

### Task 3: Политики платежей без противоречий (P2 · B1, R2, B6, B9, B10)

**Files:**
- Modify: `bot/config.py`, `bot/billing.py`, `bot/plan_catalog.py`, `bot/production_admission.py`,
  `bot/platega_provider.py`, `bot/oauth.py`
- Test: `tests/test_payment_policy_model.py`, `tests/test_platega_provider.py`

- [ ] RED: тест «случайные ключи провайдера не включают СБП/карту»; тест «контракт
      `payment_policy=off` запрещает внешние платежи, но не ломает звёзды по явному флагу»;
      тест «`BILLING_PUBLIC_STARS=0` → витрина и подготовка отказывают».
- [ ] Ввести явную модель: `stars_policy` (по `BILLING_MODE`+`BILLING_ALLOW_INVOICE`+
      `BILLING_PUBLIC_STARS`), `external_policy` (по `BILLING_ALLOW_EXTERNAL` + наличие
      провайдера), `mock_policy` (только pinned staging). Одна функция, один источник истины.
- [ ] Убрать смысловое противоречие контракта: `PRODUCTION_PAYMENT_POLICY` относится к
      внешним платежам; звёзды управляются своим флагом. Зафиксировать RULING в документе.
- [ ] Platega: контрактные тесты на method IDs (`sbp`, `bank_card`), поля запроса и
      документированный набор полей callback; при расхождении — не отправлять запрос
      (fail-closed) и оставить статус `PROVIDER SANDBOX REQUIRED`.
- [ ] Commit: `feat(billing): separate stars, external and mock payment policies`.

---

### Task 4: Деньги получены — доступ выдан (P2 · A1, A5)

**Files:**
- Modify: `bot/database.py` (миграция), `bot/billing.py`, `bot/billing_store.py`, `main.py`,
  `bot/owner_alerts.py`
- Test: `tests/test_entitlement_recovery.py`

- [ ] RED: тест «подтверждённая оплата, выдача не удалась → заказ помечен как требующий
      ручной проверки, есть запись аудита, карантин и предупреждение в логе»; тест «повторная
      сверка доводит выдачу до конца ровно один раз»; тесты падения на каждом шаге.
- [ ] Миграция: `entitlement_state` (`none|applied|review|failed`), `entitlement_error`,
      `entitlement_updated_at` в `billing_orders`; индекс по проблемным состояниям.
- [ ] В `apply_payment_evidence`: подтверждение денег и выдача доступа разделены; при
      несоответствии — `review` + `billing_audit('entitlement_review')` + карантин + лог.
- [ ] Воркер: периодическая повторная попытка выдачи для `review`/`failed` с подтверждённым
      платежом (идемпотентно через `paid-order:<order_id>`), счётчик только по реальным
      выдачам.
- [ ] Видимость: счётчик проблемных заказов в алертах владельца и в панели (read-only).
- [ ] Commit: `feat(billing): make a paid order without access visible and recoverable`.

---

### Task 5: Истечение и отмена заказа (P2 · A3, A2, A4/B7)

**Files:**
- Modify: `bot/database.py`, `bot/billing.py`, `bot/mini_app_billing.py`, `main.py`,
  `bot/admin_web.py`
- Test: `tests/test_order_expiry.py`, `tests/test_refund_machine.py`

- [ ] RED: тест «истёкший неоплаченный заказ не блокирует новую покупку» (для всех
      провайдеров); тест «поздняя подтверждённая оплата после истечения выдаёт доступ»; тест
      «гонка отмены и оплаты не поднимает исключение».
- [ ] Истечение/отмена выставляют согласованный финансовый статус; очистка вызывается из
      воркера для всех провайдеров; гонка отмены возвращает «уже закрыт» без исключения.
- [ ] Возврат: внутренняя машина состояний (`requested → provider_pending → refunded/failed`),
      идемпотентность, отзыв ровно своего гранта, независимый грант не трогается, путь только
      для владельца (панель, CSRF), внешний вызов остаётся выключенным.
- [ ] Commit: `fix(billing): expire orders safely and add a refund state machine`.

---

### Task 6: Условия, документы, поддержка (P1 · D24, P2 · B4, B5, D25)

**Files:**
- Modify: `bot/handlers/telegram_plus.py`, `bot/handlers/payments.py`, `bot/legal_documents.py`,
  `docs/legal/*.md`, `docs/legal/manifest.json`, `bot/mini_app_ui/purchase.js`
- Test: `tests/test_plus_terms_gate.py`, `tests/test_mini_app_legal.py`

- [ ] RED: тест «без подтверждения условий счёт не создаётся»; тест «версия условий попадает
      в заказ»; тест «тексты не утверждают, что оплата недоступна, если звёзды включены».
- [ ] В боте: на экране тарифа и выбора способа — кнопки документов, поддержки и `/terms`;
      перед созданием счёта — короткое подтверждение условий; факт согласия в аудит заказа.
- [ ] Юридические тексты: фактическое состояние по способам (звёзды / СБП и карта), без
      выдуманных реквизитов; недостающие данные остаются явным запросом к владельцу.
- [ ] Commit: `feat(legal): show terms and support before a payment, and tell the truth`.

---

### Task 7: Мини-апп: сеть, ошибки, мобильная вёрстка (P1 · E1, E2, P2 · E3, E4, E5)

**Files:**
- Modify: `bot/mini_app_ui/purchase.js`, `api.js`, `telegram.js`, `app.css`, `app.js`
- Test: `tests/test_mini_app_error_taxonomy.py`, `scripts/mini_app_mobile_qa.cjs`

- [ ] RED: тест «таймаут подготовки оплаты не пишет „нет связи“ и восстанавливает заказ по
      ключу запроса»; тест «постоянная недоступность не предлагает повтор»; тест «403/409/429/
      timeout дают разные тексты».
- [ ] Повторный запрос с тем же ключом, экран «Проверяем статус платежа…», восстановление
      ссылки; при истёкшей подписи — перезагрузка и восстановление последней операции.
- [ ] Мобильная вёрстка: `visualViewport`, safe area, нижняя навигация, поля ввода, модалка;
      Playwright-прогоны на узких экранах и с имитацией клавиатуры (без объявления нативного
      PASS).
- [ ] Commit: `fix(mini-app): survive slow payments and mobile keyboards`.

---

### Task 8: Лимиты частоты и хранение (P2 · D13, D14, D18, D17, R1)

**Files:**
- Modify: `bot/mini_app_billing.py`, `bot/mini_app_viewer.py`, `bot/mini_app_reports.py`,
  `bot/database.py`, `main.py`
- Test: `tests/test_mini_app_rate_limits.py`, `tests/test_retention_cleanup.py`

- [ ] RED: тесты «1000 запросов одного пользователя ограничены, память не растёт», «разные
      пользователи не мешают друг другу», «владелец не ограничен жёстко».
- [ ] Per-user окна + общий предохранитель на дорогие чтения и подготовку покупки.
- [ ] Retention: чистка служебных журналов (апдейты Telegram, завершённые уведомления,
      истёкшие неоплаченные заказы без факта платежа). Финансовые записи не удаляются —
      срок хранения фиксируется как решение владельца.
- [ ] Commit: `feat(limits): bound the expensive paths and clean transient data`.

---

### Task 9: Укрепление P3 (B2, B3, B8, C1–C10, D15, D16, D21, D22, E8–E11)

**Files:** по находке (см. `FINDING-LEDGER.md`), каждая — отдельный маленький коммит.

- [ ] B2: удалить недостижимую ветку; B3: гейт политики у сверки звёзд; B8: allowlist хоста
      ссылки на счёт.
- [ ] C2: границы `user.id`; C3: лимит тела до чтения; C4: обязательный `Content-Type`;
      C5: недоверие к заголовкам пересылки (документировано); C6: `/stats` и `/health` только
      в личке от владельца; C7: HSTS и `frame-ancestors`; C9/D15: fail-closed пул сессий
      панели; C10: тесты границ.
- [ ] D21/D22: пометки об отменённых ценах; E8: единый часовой пояс; E9: актуализировать
      браузерный QA; E10: снять слушатели; E11: убрать мёртвую переменную.
- [ ] Commit: по одному на группу.

---

### Task 10: Повторный состязательный аудит и отчёты

- [ ] Прогнать полный набор тестов и concurrency/crash-сценарии (50 параллельных
      подтверждений, 50 одинаковых ключей, capture+refund, expire+capture, рестарт).
- [ ] Повторить главные атаки из аудита и записать свежие результаты.
- [ ] Заполнить `FINDING-LEDGER.md` (каждый ID → статус), `VERIFICATION.md`,
      `PLATFORM-MATRIX.md`, `REMAINING-BLOCKERS.md`, `REMEDIATION-REPORT.md`.
- [ ] Commit: `docs(audit): record remediation evidence and remaining blockers`.
