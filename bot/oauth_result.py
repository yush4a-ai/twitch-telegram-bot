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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="robots" content="noindex,nofollow">
<title>Подключение Twitch</title><link rel="stylesheet" href="/twitch/result/ui.css">
<script type="module" src="/twitch/result/ui.js"></script></head>
<body data-result-status="{escape(status)}"><main>
<p class="brand"><span class="brand-mark" aria-hidden="true"></span><span>TwitchSignalBot</span></p>
<p class="badge" aria-hidden="true"><svg class="icon icon-wait" viewBox="0 0 24 24"><circle cx="12" cy="12" r="7.5" /><path d="M12 8.5V12l2.6 1.6" /></svg><svg class="icon icon-ok" viewBox="0 0 24 24"><path d="M5.5 12.5l4.2 4.2L18.5 8" /></svg><svg class="icon icon-warn" viewBox="0 0 24 24"><path d="M12 7.5v6" /><path d="M12 16.8h.01" /></svg></p>
<h1 id="result-title">{escape(title)}</h1><p id="result-description" role="status" aria-live="polite">{escape(description)}</p>
<p id="result-account" class="account"></p>
<div class="actions"><button id="result-retry" type="button" hidden>Проверить ещё раз</button>{link}</div>
</main></body></html>'''
