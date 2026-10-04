# Постоянная кнопка «Меню» и раскладка «Ещё»

База: cf57fbd266d9fe7259d9ae1f74114063de3c22a8. Ветка: autonomous/twitchsignal-roadmap. Запрос: attachment ed8ad016 и уточнение владельца о live. Один исполнитель, один read-only reviewer.

## План и фактическое состояние

1. Исходный аудит завершён: все ReplyKeyboard/Remove/requestChat, Home, cancellation, auth, middleware и 38 потоков. Серверные дефекты воспроизведены до исправлений. Причина визуального отсутствия в Desktop остаётся открытой.
2. RED → код → focused PASS: отдельный срок и тип успешной отправки; общий порядок selector/restore; проверка текущего intent; recovery перед долгим обработчиком и после результата/ошибки. Тёплые переходы не отправляют служебное сообщение повторно.
3. «Ещё»: три пары по схеме владельца, отдельные отчёты, owner-only admin, Home внизу. Старый menu:live и его обработчик сохранены. RED на точные строки упал до fix. Браузерный макет: 32 PASS, четыре PNG.
4. Scoped review: ошибки stale ChatShared и ложного подтверждения отмены закреплены RED и исправлены. Cold OAuth и отмена при занятом keyboard-send проверены. Финальные 16 regressions PASS / 2 subtests; reviewer подтвердил закрытие последнего finding.
5. Backup: свежая staging-копия, remote/local restore 63 таблиц PASS. Схема/шифрование/каталог и Mini App без изменений; reuse проверен по Git blobs. Guard полного финального snapshot и Testbot deployment впереди.
6. Native Desktop: ввод приостановлен после inputguard и сворачивания окна. Ответ о доступности pending; последнее чтение показало перекрывающее окно. Снимки чужих окон не сохранялись. Desktop/iOS/Android NOT TESTED.

Только pinned staging и @TwitchSignalTestbot. Пользователь разрешил UI-действия в своём личном чате Testbot; других получателей, групп/каналов, owner OAuth и реальных платежей это разрешение не включает. Production не изменяется. После полного guard нужны actual SHA/file hashes/getMe/default и owner MenuButton/payment OFF/production identity. Native не заменять unit или браузерным макетом.

## Перед выкладкой

В рабочей папке появились 7 untracked файлов docs/design/admin-concept-2026-10-04 из завершённого соседнего design task. Их содержимое не изменяется, не удаляется и не stash. Свои изменения фиксируются отдельным commit. Штатный staging guard требует чистый tree; решение о сохранении чужих макетов отдельным docs commit необходимо до guard/deploy. Hash/size сохранены в FOREIGN-FILES.json.
