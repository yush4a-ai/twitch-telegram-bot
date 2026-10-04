# Сценарии проверки

| Сценарий | Фактический статус |
|---|---|
| Home41/26/7, empty/unknown, длинные имена/injection | PASS fake tests/gallery/deployed pure builder |
| Free/Viewer/Streamer,150/300,4группы/Viewer inclusion | PASS catalog tests/gallery/deployed pure builders |
| Trial/test/paid, expiry, active state | PASS fake regression на временной БД; gallery trial fixture |
| Три способа оплаты, unavailable/stale | PASS focused/full; actual paymentOFF |
| Помощь/команды/добавление/импорт/поиск/удаление | PASS fake tests/full/gallery |
| Twitch/канал/escaping/шаги/неподтверждённые права | PASS fake tests/full/gallery; ownerOAuth/requestChat NOT TESTED |
| Quiet-hours/localUTC/рейды отдельно | PASS tests/gallery; прежняя семантика сохранена |
| Ручной/автоматический отчёт, incomplete/HTML/free | PASS tests/full/gallery/deployed pure summary |
| Mini App/profile/paywall/help/legal | 18webfiles identical прошлой browserматрице; current fullPASS; actual legal503 |
| Chromium/WebKit/360/390/768/1440/dark/light | PASS304checks/14PNG |
| Backup/restore/migrationcopy | Freshbackup63tables PASS; migrationcopy explicitly reused после13blobidentity |
| StagingSHA/identity/menu/auth/payment/production | ActualPASS195fileSHA/17HTTPassets/9auth401/27versions |
| NativeTelegram/OAuth/send/edit/media/реальная оплата/ownervisual | NOT TESTED; отдельная приёмка открыта |
