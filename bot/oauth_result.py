"""Public OAuth result presentation; never contains code/state/token or grants rights."""

from html import escape
from pathlib import Path
import re

UI_DIR = Path(__file__).with_name("oauth_result_ui")
MESSAGES = {
    "pending": ("Проверяем подключение…", "Подтверждаем ваш аккаунт Twitch. Не закрывайте страницу."),
    "verifying": ("Проверяем подключение…", "Подтверждаем ваш аккаунт Twitch. Не закрывайте страницу."),
    "connected": ("Twitch подключён", "Вернитесь в приложение, чтобы выбрать Telegram-канал."),
    "cancelled": ("Подключение отменено", "Аккаунт не подключён. Можно начать заново в приложении."),
    "expired": ("Ссылка устарела", "Откройте приложение и начните подключение заново."),
    "failed": ("Не удалось подключить Twitch", "Аккаунт не подключён. Попробуйте ещё раз в приложении."),
    "conflict": ("Аккаунт уже связан", "Проверьте привязку в боте, прежде чем подключать его снова."),
    "legacy": ("Ответ Twitch получен", "Вернитесь в бот, чтобы проверить подключение."),
}


def result_page(status: str, *, bot_username: str = "") -> str:
    if status not in MESSAGES:
        status = "failed"
    title, description = MESSAGES[status]
    link = (f'<a class="button" href="https://t.me/{escape(bot_username)}">Вернуться к боту</a>'
            if re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username) else "")
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Подключение Twitch</title><link rel="stylesheet" href="/twitch/result/ui.css">
<script type="module" src="/twitch/result/ui.js"></script></head>
<body data-result-status="{escape(status)}"><main><p class="brand">TwitchSignalBot</p>
<svg class="mark" width="40" height="40" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7h4V3h8v10h-4v8H7Z" /></svg>
<h1 id="result-title">{escape(title)}</h1><p id="result-description" role="status" aria-live="polite">{escape(description)}</p>
<p id="result-account"></p><button id="result-retry" type="button">Проверить ещё раз</button>{link}
</main></body></html>'''
