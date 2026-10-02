# P16: документы и поддержка

База72ab5ce. Один исполнитель и один read-only reviewer p09_focus_review.

## Что работает

Пять канонических исходников в docs/legal: Privacy, Agreement, Support, Tariffs, Payments. Версия/SHA нормализованного LF/catalog/принятие/обязательные сведения проверяются перед публикацией. Обязательные данные заданы в коде и не снимаются изменением manifest. Allowlist файлов, ограничение размера, экранирование HTML, проверенные HTTPS/mailto; unsafe и неканонические локальные адреса отвергаются.

Signed support/state использует SUPPORT_USERNAME/SUPPORT_EMAIL без defaults. Этот же контакт получает /paysupport. Config передаётся через существующий OAuth server; отдельной БД/сервера нет. В профиле и покупке доступны список документов и полнотекстовый reader с Back/загрузкой/ошибкой/обновлением. Публичные reader/CSS/JSON содержат no-store/noindex/CSP. Исходный текст хранится один раз; прежние HTML/экспорт/черновики сохранены.

Реальные реквизиты не получены, все canonical owner_accepted=false. Публичные документы возвращают503 с честным статусом, контакт отсутствует, bank_ready=false. Это не препятствует независимому UI/staging. Полный текст проверен только на отдельной TEMP-копии с явно локальными реквизитами и принятием; это не юридическое принятие владельца.

## Проверки

- RED отсутствующих маршрутов/контакта, обязательных полей и URI сохранены. P16-scoped-pass.log:55passed104subtests25.88s; P16-api-pass.log:28passed29subtests6.28s. Никакие assertions не ослаблены.
- Expanded P16-pass.log:391passed144subtests,2failed. Выявлены две устаревшие заглушки: P07 Botconstructor нарушал isolation gate; WAL positional mock исчерпывался после foreign_keysON. Исправлены fake-клиентом с typed AnswerPreCheckoutQuery/Dispatcher и SQL-aware mock. Оба первоначальных assertions сохранены и входят в scoped PASS. Это не full-suite PASS; финальный gate P19 впереди.
- P16-confirmed:4PASS reports/44 уникальных PNG, Chromium/WebKit, готовая TEMP-копия и неподготовленная canonical,360/390/text200/клавиатура/Back/доступ до выбора оплаты. JS/errors0, external0; source/image SHA проверены. Полный текст всех5 документов читается,150/300 согласованы.
- Reviewer воспроизвёл URL-normalization и mailto recipient defects. RED→fix→PASS, независимый actual-module URI confirmation сохранён. Email recipient encoded; query/hash не меняют адресата. Открытых подтверждённых findings нет.
- Detector[]; scoped Stop-Slop/factual copy review выполнен, юридический смысл и отсутствующие owner inputs сохранены. Free фото-copy исправлена до публикации, SHA обновлён.

P16-ready содержал пересечение overview/document-support PNG; эти image reports не финальные. P16-final был до последних URI fixes. Оба пакета сохранены как история; актуальное evidence только P16-confirmed.

## Граница

Нет реального bank submission, Telegram отправки, owner OAuth, invoice/PlategaPOST, платежа или native acceptance. Payment OFF и canonical bank NOT READY. Production не изменялась. Дальше P17 copy/compatibility, затем P18–P21.
