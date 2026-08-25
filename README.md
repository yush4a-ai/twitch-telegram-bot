# Twitch → Telegram бот

Telegram-бот, который следит за Twitch-каналами: публикует живой пост о начале
стрима, присылает итоговый отчёт по завершении, детектирует рейды и коллабы,
ведёт VOD-архив и топ чатеров. Поддерживает группы, личку и Telegram-каналы.

## Установка (локально)

```powershell
cd twitch-bot
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Заполните `.env`:

- `TELEGRAM_BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather) (`/newbot`)
- `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` — из [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
  (Redirect URL для локальной разработки: `http://localhost:8765/twitch/callback`)

## Запуск (локально)

```powershell
.\.venv\Scripts\python main.py
```

Бот должен работать постоянно (пока процесс жив — приходят уведомления).

## Деплой на Railway

Бот целиком совместим с Railway "как есть" — процесс-worker + long polling,
без входящего HTTP-трафика для основной логики (только команда `/auth_twitch`
поднимает встроенный веб-сервер для Twitch OAuth-редиректа).

1. Залейте проект в GitHub-репозиторий, подключите его в Railway
   (New Project → Deploy from GitHub repo).
2. В Variables задайте:
   - `TELEGRAM_BOT_TOKEN`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`
   - `PUBLIC_URL` — публичный домен, который выдаёт Railway (Settings →
     Networking → Generate Domain), например `https://myproject.up.railway.app`
   - `DB_PATH` — путь к файлу БД **внутри примонтированного Volume**, например
     `/data/bot.db` (см. следующий пункт)
   - `TOKEN_ENCRYPTION_KEY` — Fernet-ключ для шифрования Twitch-токенов в SQLite.
     Сгенерировать один раз:
     `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
     Сохраните резервную копию ключа: без него зашифрованные токены восстановить нельзя.
3. Подключите Volume (Settings → Volumes → New Volume), примонтируйте его,
   например, на `/data`. Без этого база SQLite будет пересоздаваться с нуля
   при каждом деплое — вся история стримов, настройки каналов и токены
   потеряются.
4. В Twitch Developer Console (dev.twitch.tv/console/apps) добавьте
   Redirect URL: `<PUBLIC_URL>/twitch/callback`.
5. Railway сам определит `Procfile` (`worker: python main.py`) — по умолчанию
   у процесса `worker` нет входящего трафика, но `/auth_twitch` использует
   исходящий редирект, поэтому это не проблема; если понадобится, Railway
   можно переключить на `web`-процесс без дополнительных изменений в коде.

## Команды бота

- `/start` — главное меню
- `/help` — что умеет бот
- `/health` — состояние цикла опроса и фоновых задач (только для `OWNER_CHAT_ID`)
- `/track <канал>`, `/untrack <канал>`, `/list` — управление отслеживаемыми каналами
- `/report` — отчёт по последнему стриму
- `/auth_twitch` — подключить свой Twitch-аккаунт (нужно для счётчика новых фолловеров)
- `/myid` — узнать chat_id текущего чата

## Структура проекта

```
main.py                    — точка входа: собирает бота, БД, поллер, OAuth-сервер
bot/config.py               — загрузка настроек из .env / переменных окружения
bot/database.py             — слой SQLite (aiosqlite)
bot/twitch.py                — клиент Twitch Helix API
bot/chat_listener.py         — IRC-клиент чата Twitch (топ чатеров, рейды)
bot/oauth.py                 — Twitch OAuth flow + постоянный callback-сервер
bot/token_store.py           — хранение и обновление пользовательских Twitch-токенов
bot/poller.py                — фоновый цикл проверки стримов, отчёты, алерты
bot/report.py                 — генерация HTML-отчёта
bot/handlers/streams.py      — основные команды и меню
bot/handlers/auth.py          — команда /auth_twitch
bot/handlers/__init__.py     — регистрация всех роутеров
```

## Добавление нового функционала

Создайте новый файл в `bot/handlers/`, например `bot/handlers/mymodule.py`,
заведите там `Router` и опишите хендлеры (доступ к БД, Twitch-клиенту и
конфигу — через параметры функции `db: Database`, `twitch: TwitchClient`,
`config: Config`, они прокинуты через `dp["db"]` / `dp["twitch"]` /
`dp["config"]` в `main.py`). Затем зарегистрируйте роутер в
`bot/handlers/__init__.py`:

```python
from .mymodule import router as mymodule_router
dp.include_router(mymodule_router)
```
