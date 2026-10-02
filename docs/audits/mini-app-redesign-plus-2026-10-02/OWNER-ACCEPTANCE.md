# Самостоятельная проверка TwitchSignalBot

## Состояние пакета

P01–18 реализованы; P19 финальный gate выполняется. Этот документ подготовлен для разрешённого первого staging выпуска. Точные release SHA/deployment/bot/menu и фактический статус будут записаны в `STAGING-ACCEPTANCE.md` после P20. До этого он не является подтверждением deploy.

## Вход после P20

Открыть [@TwitchSignalTestbot](https://t.me/TwitchSignalTestbot) → постоянная кнопка **Приложение**. Прямой [браузерный URL](https://worker-staging-2f74.up.railway.app/app) показывает оболочку и требует настоящего Telegram входа; он не заменяет initData.

Проверить [сценарии](SCENARIOS.md) в двух режимах. Viewer: Главная/Стримеры/Профиль/Plus. Streamer: Мой канал/Посты/Профиль/Plus. Приложение использует общие данные существующего бота; HTML-отчёты и экспорт сохранены.

## Подписка и деньги

Viewer150 ₽/месяц, Streamer300 ₽/месяц; Streamer включает Viewer для того же frozen Telegram пользователя. Покупка внутри приложения: тариф→цена→кнопка→Stars/СБП/карта; СБП/карта через открыто названную Platega. Первый выпуск денег не создаёт: все методы показывают unavailable. Статус «оплачено» и права от заглушки не возникают.

Если сервер предлагает добровольное ознакомление7дней, оно действует однократно для allowlisted владельца, без денег/автосписания. Существующие trial/grants и даты сохраняются; повторное ознакомление не сбрасывается. Эффективные функции зависят от реального текущего server state. Streamer оформление требует подтверждённого Twitch/placement; Viewer trial не считается Streamer покупкой. Отдельный технический Streamer grant требует подтверждения, автоматически здесь не выдаётся.

## Что пока не проверено

Реальные Telegram iOS/Android/Desktop/Web SDK/fullscreen/safe areas/Back/клавиатура/requestChat/OAuth-return; signed live API; owner OAuth; конкретный send/edit/delete/media transition и provider payments: NOT TESTED. WebKit/Chromium с fake SDK подтверждают браузерные сценарии. Owner acceptance ожидается.

Не проходили OAuth от имени владельца, не отправляли тестовые сообщения третьим лицам, не создавали платежи. Реальные контакты/legal approval ещё отсутствуют: подготовлены canonical исходники, routes честно недоступны; bank package NOT READY.

## Недостающие данные для денег и bank approval

Ровно10 позиций в [OWNER-INPUTS-FOR-LAUNCH](../../workflows/OWNER-INPUTS-FOR-LAUNCH.md): support; оператор; точный месяц; refund; chargeback; upgrade; XTR; merchant/test-допуск; НПД/чеки; retention. Утверждённые цены/методы/состав тарифов повторно не согласуются. Эти gates сохраняют деньги OFF, но не запрещают интерфейс/staging.

## Снимки и доказательства

Актуальные локальные browser screenshots нового runtime закреплены в [manifest](P18-browser-manifest.json):984PNG/174sourceSHA,92journeys,2520matrixchecks. [Browser/media итог](P18-QA.md), [модель угроз](mini-app-threat-model.md), [backup/migration](MIGRATION-COPY.md). Старые prototype/HTML/audit материалы сохранены отдельно.

Media1×1000 RESOURCE_STOP25s;100×10/1000×5 проверили cap2 и deferred98/4998. Эти цифры не обещают обслуживание всех потоков или Telegram SLA. При перегрузке остаются фото и честный статус.

После собственного теста владельца ожидается следующий круг замечаний. Production launch, денежная активация и банковская подача в этот выпуск не входят.
