from __future__ import annotations

import hashlib
import os

# Соль для псевдонимов в логах. Без неё короткий хэш можно было бы просто
# перебрать: диапазон Telegram ID конечен. Берём секрет, который уже есть у
# процесса; если его нет (локальный запуск) — случайная соль на время работы,
# тогда логи одного запуска остаются связными, а между запусками — нет.
_SALT = (
    os.getenv("LOG_SALT")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or os.urandom(16).hex()
).encode()


def mask_chat_id(chat_id: int | None) -> str:
    """Псевдоним чата для логов вместо настоящего ID.

    В личной переписке chat_id совпадает с ID пользователя Telegram, то есть это
    персональные данные — в логах, которые видит хостинг и любой, у кого есть к ним
    доступ, им не место. Псевдоним стабилен, поэтому по логам по-прежнему можно
    проследить историю одного чата, но восстановить из него исходный ID нельзя."""
    if chat_id is None:
        return "chat:?"
    digest = hashlib.blake2s(str(chat_id).encode(), key=_SALT[:32], digest_size=4).hexdigest()
    return f"chat:{digest}"
