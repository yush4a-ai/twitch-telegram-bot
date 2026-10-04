# Постоянная кнопка «Меню» и раскладка «Ещё»

База: cf57fbd266d9fe7259d9ae1f74114063de3c22a8. Ветка: autonomous/twitchsignal-roadmap. Запрос: attachment ed8ad016 и уточнение владельца о live. Один исполнитель, один read-only reviewer.

## План и фактическое состояние

1. Исходный аудит завершён: все ReplyKeyboard/Remove/requestChat, Home, cancellation, auth, middleware и 38 потоков. Серверные дефекты воспроизведены до исправлений. Причина визуального отсутствия в Desktop остаётся открытой.
2. RED → код → focused PASS: отдельный срок и тип успешной отправки; общий порядок selector/restore; проверка текущего intent; recovery перед долгим обработчиком и после результата/ошибки. Тёплые переходы не отправляют служебное сообщение повторно.
3. «Ещё»: три пары по схеме владельца, отдельные отчёты, owner-only admin, Home внизу. Старый menu:live и его обработчик сохранены. RED на точные строки упал до fix. Браузерный макет: 32 PASS, четыре PNG.
4. Scoped review: ошибки stale ChatShared и ложного подтверждения отмены закреплены RED и исправлены. Cold OAuth и отмена при занятом keyboard-send проверены. Финальные 16 regressions PASS / 2 subtests; reviewer подтвердил закрытие последнего finding.
5. Backup: свежая staging-копия, remote/local restore 63 таблиц PASS. Схема/шифрование/каталог и Mini App без изменений; reuse проверен по Git blobs. Guard полного финального snapshot и Testbot deployment впереди.
6. Native Desktop: после свежего наблюдения открыт настоящий @TwitchSignalTestbot, username проверен в профиле. На старом runtime8350306 видны одновременно системная «Приложение» и ReplyKeyboard «Меню». Сохранены BEFORE chat/identity без соседних переписок. Причина исходного исчезновения в клиенте не установлена. После выкладки нужны реальные переходы и AFTER; iOS/Android NOT TESTED.

Только pinned staging и @TwitchSignalTestbot. Пользователь разрешил UI-действия в своём личном чате Testbot; других получателей, групп/каналов, owner OAuth и реальных платежей это разрешение не включает. Production не изменяется. После полного guard нужны actual SHA/file hashes/getMe/default и owner MenuButton/payment OFF/production identity. Native не заменять unit или браузерным макетом.

## Перед выкладкой

Владелец разрешил сохранить 7 файлов docs/design/admin-concept-2026-10-04 отдельным документальным коммитом без изменения содержимого. Сохранено в2a418bb, hash/size зафиксированы в FOREIGN-FILES.json. Runtime-правки отдельно в0b68427.

Полный guard2a418bb остановлен на29% после двух compatibility failures, без deploy. PurchaseMenuTests искал прежнюю подпись «Тариф», теперь проверяет точную «⭐ Тариф» при сохранении menu:plus/Home4/group denials. Legacy community replay использовал SimpleNamespace без runtime MenuStore: заменён реальным aiogram Message/локальным transport/FSM. Signed API, проверки прав, одна связанная community и отсутствие повторных отправок сохранены; добавлены Menu и очищенный FSM. RED двух прежних тестов сохранён, итоговый связанный набор31 passed/30 subtests. Продуктовый код после0b68427 не менялся. Следующий шаг — полный guard чистого итогового snapshot.

Full3a0a585 завершён:1569 passed/2 skipped/3646 subtests, один failure в TariffCopyTests на прежнем exact label «Тариф». Deploy отменён. Исправлена только expected label «⭐ Тариф»; цена150/300, CTA и product/payment assertions сохранены, focused1PASS. Адресная проверка всех more_keyboard references не обнаружила других прежних exact labels. Оба skip — исходные symlink tests без Windows-привилегии; новых skip нет. Полный gate повторяется на следующем чистом snapshot.
