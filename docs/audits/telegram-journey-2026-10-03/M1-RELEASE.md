# M1 — восстановление фото при недоступном видео

03.10.2026. Ветка `autonomous/twitchsignal-roadmap`. Только pinned staging / `@TwitchSignalTestbot`.

## Результат

Новый release `160703a1a669dd31bc9dd15d339a1346bc927ef8`, tree `fc4b5c18a72142aac72a846f46873bf07cdcb589`, deployment `ac62b912-b6bb-4633-899b-6afa1ffecfd4`, SUCCESS. Вход: https://t.me/TwitchSignalTestbot → «Приложение»; https://worker-staging-2f74.up.railway.app/app.

В зарегистрированном Telegram-канале обычный poller обновил существующие эфирные посты: `samoylov___`, сообщение28, и `gofns`, сообщение29, теперь `photo`. Это подтверждено чтением работающей БД нового deployment в [M1-staging-after.json](M1-staging-after.json). Проверка не отправляла сообщений и не изменяла данные вручную. Нативный вид обновлённого фото отдельно не подтверждён.

## Изменения

- Из основной ветки точно перенесены `4ce3887`: восстановление принадлежащего preview-анализатору временного каталога; `dc9239e`: диапазон цвета tv в FFmpeg. Кодек, качество, отсутствие звука и цикл6→12→18→24→6 сохранены.
- Лёгкий fallback thumbnail теперь работает в зарегистрированных каналах, включая очередь и публичную подпись. Новый текстовый пост не ждёт общий пятиминутный интервал, использованный другим получателем.
- Animation→photo после потери доступа, отключения и ограничения нагрузки. Действующая разрешённая animation сохраняется. Очередь проверяет свежее notify_enabled перед photo edit; выключенный получатель не получает новый thumbnail.
- Нет миграций и дополнительных capture jobs. Video raw switch не выдаёт права: у исходного samoylov___ effective video0, identity/community/grant0. Это не обходится фиктивными связями или подпиской.

## Проверки

| Проверка | Результат / evidence |
| --- | --- |
| RED → код → PASS | Production/channel/scenarios/review RED и GREEN logs в этой папке; промежуточный failed GREEN сохранён |
| Media gate, настоящий FFmpeg и нагрузка | 211PASS/1existingWindowsSkip/43subtests; [M1-media-gate.log](M1-media-gate.log) |
| Проверка после замечания reviewer | 90PASS/15subtests; reviewer отдельно2PASS; [M1-review-GREEN.log](M1-review-GREEN.log) |
| Полный suite финального release | 1517PASS/2existingWindowsSkips/3638subtests,1117.61s; [M1-FULL-GUARD.log](M1-FULL-GUARD.log) |
| Неизменный архив и pinned deployment | 667 файлов/12834328bytes; исключены только audit/design evidence; [M1-package-manifest.json](M1-package-manifest.json) |
| Actual smoke | PASS: runtime hashes/17 HTTP assets + shell, bot8859004067, menu«Приложение»,9unsigned401,27migrations, integrityok/FK0, paymentOFF; [STAGING-staging-smoke.json](STAGING-staging-smoke.json) |
| Backup/restore | Свежий staging backup и локальный restore63tables PASS; [STAGING-backup.json](STAGING-backup.json) |
| Migration-copy | Переиспользована реальная прежняя проверка: schema/cipher зависимости идентичны; единственное изменение database.py — немиграционный SELECT. Это не новый remote прогон; [STAGING-migration-copy.json](STAGING-migration-copy.json) |
| Production | Метаданные до/после совпали: deployment466499d5-6979-4a81-8aee-67efcf628976, SHA dc9239eb0b82fb80d1788fc657205740cebc49e9 |

Scoped reviewer проверил M1 и точные переносы; найденный queued mute race исправлен с отдельным RED/PASS. Assertions не ослаблены, новых skips не добавлено. Два ранних самостоятельных full запуска остановлены до завершения после восстановления Railway-доступа; доказательством PASS служит только законченный стандартный guard на160703a. Native BEFORE — [оригинальный JPEG](M1-NATIVE-BEFORE.jpg). Native AFTER NOT TESTED: окно перекрывалось другими приложениями и фиксировался активный ввод владельца; не перехватывали его работу. Скриншота обновлённого поста нет, успех не выводится из health200.

## Оставшиеся ограничения

- Реальное видео именно для этого стримера не проверялось: отсутствуют необходимые серверные права/связь. Локальные render/fallback/expiry/revocation/mute сценарии пройдены; фото подтверждено в staging.
- Signed native API, iPhone/Android, OAuth владельца, внешние тестовые отправки и реальные оплаты — NOT TESTED. ПлатежиOFF; legal503 сохраняется и не объявляется готовностью к оплате.
- M1 закрыт как срочное исправление. Сохранённый Telegram-план: T2 частично, T4–T8 открыты. Прочие surfaces не объявляются принятыми.
- Production, HTML-отчёты/экспорт и все исторические артефакты сохранены.
