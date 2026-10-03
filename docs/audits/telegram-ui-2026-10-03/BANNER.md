# Статичный welcome banner

Встроенный imagegen; supporting reference bot/growth_ui/brand-logo.png. Новый файл bot/assets/telegram-welcome.png, старый bot_welcome_banner.png сохранён. PNG проверен визуально: точное TwitchSignalBot, светлая композиция, графит/фиолетовый/синий, без UI chrome/цен/CTA. Runtime не генерирует изображения; загрузка один раз с дальнейшим per-bot file_id reuse.

Prompt: Use case ads-marketing. Static Telegram welcome banner, wide landscape 2:1. Minimal native mobile identity; preserve recognizable compact violet speech-bubble mark with two graphite slots and exact name TwitchSignalBot. Light off-white background, graphite clean geometric typography, restrained violet/blue signal motif, spacious composition. Only brand name, no slogan/prices/buttons/mock device/neon, inset content. Supporting input brand-logo.png.

Official Telegram styles и ограничения методов проверены по https://core.telegram.org/bots/api#inlinekeyboardbutton и #editmessagecaption / #editmessagemedia. aiogram3.30.0 имеет поле style. Caption1024/text4096; при необходимости фото→текст отправляется одно новое меню, старые inline-кнопки снимаются; после этого редактируется текст. Новый пользователь получает статичный banner, возвращающийся — сводку. Нет фоновых edits. Home сообщает только verified Twitch, не выдаёт сохранённый notify toggle за проверенное publishing permission.
