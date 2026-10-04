# Постоянная кнопка «Меню» и раскладка «Ещё»

База: cf57fbd266d9fe7259d9ae1f74114063de3c22a8. Ветка: autonomous/twitchsignal-roadmap. Запрос: attachment ed8ad016 и уточнение владельца о live. Один исполнитель, один read-only reviewer.

## План и фактическое состояние

1. Исходный аудит завершён: все ReplyKeyboard/Remove/requestChat, Home, cancellation, auth, middleware и 38 потоков. Серверные дефекты воспроизведены до исправлений. Причина визуального отсутствия в Desktop остаётся открытой.
2. RED → код → focused PASS: отдельный срок и тип успешной отправки; общий порядок selector/restore; проверка текущего intent; recovery перед долгим обработчиком и после результата/ошибки. Тёплые переходы не отправляют служебное сообщение повторно.
3. «Ещё»: три пары по схеме владельца, отдельные отчёты, owner-only admin, Home внизу. Старый menu:live и его обработчик сохранены. RED на точные строки упал до fix. Браузерный макет: 32 PASS, четыре PNG.
4. Scoped review: ошибки stale ChatShared и ложного подтверждения отмены закреплены RED и исправлены. Cold OAuth и отмена при занятом keyboard-send проверены. Финальные 16 regressions PASS / 2 subtests; reviewer подтвердил закрытие последнего finding.
5. Backup: свежая staging-копия, remote/local restore 63 таблиц PASS. Схема/шифрование/каталог и Mini App без изменений; reuse проверен по Git blobs. Guard39bc821 завершён:1570 passed/2 исходных skip/3646 subtests. Deployment5211c9f8-6a2f-4834-9aa6-88706a4a34a7 SUCCESS. Actual getMe/195 hashes/17 assets/unsigned denials/payment OFF/production unchanged PASS; deployed pure builders More/live/Menu PASS. Итог: RELEASE.md.
6. Native Desktop: после свежего наблюдения открыт настоящий @TwitchSignalTestbot, username проверен в профиле. На старом runtime8350306 видны одновременно системная «Приложение» и ReplyKeyboard «Меню»; Menu открывает Home. Сохранены BEFORE chat/identity/menu/more без соседних переписок. Причина исходного исчезновения в клиенте не установлена. При подготовке AFTER пользователь остановил Computer Use физическим Escape: app input прекращён, последующих CU вызовов нет. AFTER/новые native flows/iOS/Android NOT TESTED. Нативная приёмка остаётся открытой.

Только pinned staging и @TwitchSignalTestbot. Пользователь разрешил UI-действия в своём личном чате Testbot; других получателей, групп/каналов, owner OAuth и реальных платежей это разрешение не включает. Production не изменён. Actual SHA/file hashes/getMe/default и owner effective MenuButton/payment OFF/production identity подтверждены. Native не заменять unit или браузерным макетом.

## Перед выкладкой

Владелец разрешил сохранить 7 файлов docs/design/admin-concept-2026-10-04 отдельным документальным коммитом без изменения содержимого. Сохранено в2a418bb, hash/size зафиксированы в FOREIGN-FILES.json. Runtime-правки отдельно в0b68427.

Полный guard2a418bb остановлен на29% после двух compatibility failures, без deploy. PurchaseMenuTests искал прежнюю подпись «Тариф», теперь проверяет точную «⭐ Тариф» при сохранении menu:plus/Home4/group denials. Legacy community replay использовал SimpleNamespace без runtime MenuStore: заменён реальным aiogram Message/локальным transport/FSM. Signed API, проверки прав, одна связанная community и отсутствие повторных отправок сохранены; добавлены Menu и очищенный FSM. RED двух прежних тестов сохранён, итоговый связанный набор31 passed/30 subtests. Продуктовый код после0b68427 не менялся. Следующий шаг — полный guard чистого итогового snapshot.

Full3a0a585 завершён:1569 passed/2 skipped/3646 subtests, один failure в TariffCopyTests на прежнем exact label «Тариф». Deploy отменён. Исправлена только expected label «⭐ Тариф»; цена150/300, CTA и product/payment assertions сохранены, focused1PASS. Адресная проверка всех more_keyboard references не обнаружила других прежних exact labels. Оба skip — исходные symlink tests без Windows-привилегии; новых skip нет. Полный gate повторяется на следующем чистом snapshot.
