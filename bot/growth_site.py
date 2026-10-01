"""Staging-only, server-rendered public-site prototype."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

_ASSETS = Path(__file__).with_name("growth_ui")
_BOT_USERNAME = "TwitchSignalTestbot"
_BOT_LINK = f"https://t.me/{_BOT_USERNAME}?start=src_site"
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; media-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
}
_PAGES = {
    "/site": (
        "Из эфира в Telegram · TwitchSignalBot",
        "Тестовый бот помогает зрителю следить за началом эфира, а стримеру — публиковать live-пост в Telegram-сообществе.",
    ),
    "/site/for-viewers": (
        "Уведомления о начале Twitch-эфира в Telegram · TwitchSignalBot",
        "Подпишитесь на Twitch-канал в тестовом боте и получайте личный сигнал о начале эфира. Доступны тестовые фильтры Viewer Plus.",
    ),
    "/site/for-streamers": (
        "Live-посты для Telegram-сообщества · TwitchSignalBot",
        "Подключите Twitch-канал и Telegram-сообщество, чтобы публиковать live-посты. В тестовом контуре доступен конструктор Streamer Plus.",
    ),
    "/site/help": (
        "Как настроить TwitchSignalBot · Помощь",
        "Пошаговая помощь зрителю и стримеру по настройке тестового бота, доступам и границам live-уведомлений.",
    ),
}


def _validate_base_url(value: str | None) -> str:
    if not value:
        raise ValueError("R8 site requires a public base URL")
    parsed = urlsplit(value)
    local = parsed.hostname in {"localhost", "127.0.0.1"}
    if (
        parsed.scheme not in ({"http", "https"} if local else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("R8 site requires a clean HTTPS public base URL")
    return value.rstrip("/")


def _cta(class_name: str = "button") -> str:
    return (
        f'<a class="{class_name}" href="{_BOT_LINK}" target="_blank" '
        'rel="noopener noreferrer">Открыть тестовый бот <span aria-hidden="true">↗</span></a>'
    )


def _home() -> str:
    return f"""
<main id="content">
  <section class="hero wrap" aria-labelledby="home-title">
    <div class="hero-copy">
      <p class="eyebrow"><span class="live-dot" aria-hidden="true"></span> Связь эфира и Telegram <span class="tag">Тестовый контур</span></p>
      <h1 id="home-title">Из эфира<br>в Telegram<span class="period">.</span></h1>
      <p class="lead">Бот сообщает зрителю о начале Twitch-эфира и помогает стримеру опубликовать live-пост в своём Telegram-сообществе.</p>
      {_cta()}
      <p class="small-note">Сейчас бот работает в тестовом контуре.</p>
    </div>
    <p class="aside-note" aria-hidden="true">Ближе<br>к своему зрителю <span class="scribble">↘</span></p>
  </section>

  <section class="role-section wrap" aria-labelledby="role-title">
    <div class="section-top"><p class="eyebrow">Выберите свой путь</p><h2 id="role-title">Для зрителя и стримера</h2></div>
    <div class="role-grid">
      <a class="role-card" href="/site/for-viewers"><span class="role-icon" aria-hidden="true">◉</span><span><strong>Я зритель</strong><small>Следить за началом эфира в личном чате</small></span><span class="role-arrow" aria-hidden="true">↗</span></a>
      <a class="role-card" href="/site/for-streamers"><span class="role-icon" aria-hidden="true">▣</span><span><strong>Я стример</strong><small>Настроить live-пост для сообщества</small></span><span class="role-arrow" aria-hidden="true">↗</span></a>
    </div>
  </section>

  <section class="journey wrap" aria-labelledby="journey-title">
    <div class="section-top"><p class="eyebrow">Как работает</p><h2 id="journey-title">От начала эфира — к сообщению</h2></div>
    <ol class="journey-grid">
      <li class="journey-step">
        <div class="illustration broadcast" aria-hidden="true"><div class="broadcast-rings"><span></span><span></span><span></span><i></i></div><span class="wave wave-a"></span></div>
        <span class="step-number">01 / ЭФИР</span><h3>Эфир</h3>
        <p>Подключённый Twitch-канал начинает трансляцию.</p>
      </li>
      <li class="journey-step post-step">
        <div class="illustration post-illustration">
          <div class="demo-card" aria-label="Синтетический пример live-поста">
            <div class="demo-top"><span class="demo-avatar" aria-hidden="true">●</span><strong>Стример в эфире</strong><span class="demo-label">ДЕМО</span></div>
            <p>Играет и общается с чатом. Загляните на трансляцию.</p>
            <span class="demo-action">Смотреть эфир <span aria-hidden="true">↗</span></span>
          </div>
        </div>
        <span class="step-number">02 / ПОСТ</span><h3>Пост</h3>
        <p>Бот публикует сообщение в подключённом Telegram-сообществе.</p>
      </li>
      <li class="journey-step">
        <div class="illustration viewer-illustration" aria-hidden="true"><div class="person"><span class="person-head"></span><span class="person-body"></span></div><span class="message-bubble">Эфир начался</span></div>
        <span class="step-number">03 / ЗРИТЕЛЬ</span><h3>Зритель</h3>
        <p>Участник сообщества видит пост. Личный подписчик получает отдельный сигнал.</p>
      </li>
    </ol>
  </section>

</main>"""


def _viewers() -> str:
    return f"""
<main id="content" class="inner wrap">
  <nav class="breadcrumb" aria-label="Путь"><a href="/site">Главная</a><span aria-hidden="true">/</span><span>Зрителям</span></nav>
  <section class="inner-hero"><p class="eyebrow">Для зрителя · тестовый бот</p><h1>Любимый канал вышел в эфир? Узнайте в Telegram.</h1><p class="lead">Добавьте Twitch-канал в личном чате бота. Когда начнётся новый эфир, бот отправит вам live-сигнал со ссылкой на трансляцию.</p>{_cta()}</section>
  <section class="content-grid" aria-label="Как настроить">
    <div class="text-card"><span class="step-number">01 / ПОДПИСКА</span><h2>Добавьте канал</h2><p>Откройте тестовый бот и укажите Twitch-канал, за которым хотите следить. Список подписок можно изменить в том же чате.</p></div>
    <div class="text-card"><span class="step-number">02 / СИГНАЛ</span><h2>Получите сообщение</h2><p>Бот сообщает о новом начале эфира. Возможны задержки и недоступность внешних сервисов; моментальную доставку мы не обещаем.</p></div>
    <div class="text-card"><span class="step-number">03 / VIEWER PLUS</span><h2>Выберите важные эфиры</h2><p>Тестовый Viewer Plus позволяет задать фильтр игры или заголовка при начале эфира и сводку после тихих часов.</p></div>
  </section>
  <aside class="note-panel"><h2>Как работает фильтр</h2><p>Фильтр применяется к новому live-событию. Смена игры во время уже идущего эфира сама по себе не создаёт отдельное уведомление.</p></aside>
  <p class="next-link">Ведёте свой канал? <a href="/site/for-streamers">Путь стримера <span aria-hidden="true">↗</span></a></p>
</main>"""


def _streamers() -> str:
    return f"""
<main id="content" class="inner wrap">
  <nav class="breadcrumb" aria-label="Путь"><a href="/site">Главная</a><span aria-hidden="true">/</span><span>Стримерам</span></nav>
  <section class="inner-hero"><p class="eyebrow">Для стримера · тестовый бот</p><h1>Расскажите сообществу о начале эфира.</h1><p class="lead">Подключите Twitch-канал и Telegram-сообщество. После подтверждения прав бот сможет опубликовать live-пост со ссылкой на ваш эфир.</p>{_cta()}</section>
  <section class="content-grid" aria-label="Как настроить">
    <div class="text-card"><span class="step-number">01 / ПОДКЛЮЧЕНИЕ</span><h2>Подтвердите канал</h2><p>Пройдите подключение Twitch через тестового бота. Пост создаётся только для подключённого стримера и выбранного сообщества.</p></div>
    <div class="text-card"><span class="step-number">02 / СООБЩЕСТВО</span><h2>Дайте боту доступ</h2><p>Добавьте бота в Telegram-сообщество с правом публикации. Перед началом работы проверьте доступы и выбранный чат.</p></div>
    <div class="text-card"><span class="step-number">03 / STREAMER PLUS</span><h2>Оформите live-пост</h2><p>Тестовый Plus даёт безопасный конструктор текста и статистику. Реальных платежей и публичных тарифов на этом контуре нет.</p></div>
  </section>
  <aside class="note-panel"><h2>Превью эфира</h2><p>При доступности источника бот может добавить короткое Telegram Animation превью до 24 секунд. При сбое публикация использует безопасный вариант без видео.</p></aside>
  <p class="next-link">Хотите личные сигналы? <a href="/site/for-viewers">Путь зрителя <span aria-hidden="true">↗</span></a></p>
</main>"""


def _help() -> str:
    return f"""
<main id="content" class="inner wrap">
  <nav class="breadcrumb" aria-label="Путь"><a href="/site">Главная</a><span aria-hidden="true">/</span><span>Помощь</span></nav>
  <section class="inner-hero"><p class="eyebrow">Помощь · тестовый контур</p><h1>Начните с нужного сценария.</h1><p class="lead">Тестовый бот объединяет личные live-сигналы для зрителей и посты для Telegram-сообществ стримеров.</p>{_cta()}</section>
  <section class="help-list" aria-label="Частые вопросы">
    <article><span class="step-number">01 / ЗРИТЕЛЬ</span><h2>Как получать личные уведомления?</h2><p>Откройте бот в личном чате, добавьте Twitch-канал и проверьте список подписок. <a href="/site/for-viewers">Инструкция зрителю ↗</a></p></article>
    <article><span class="step-number">02 / СТРИМЕР</span><h2>Как опубликовать live-пост?</h2><p>Подключите Twitch-канал, выберите сообщество и дайте боту право публикации. <a href="/site/for-streamers">Инструкция стримеру ↗</a></p></article>
    <article><span class="step-number">03 / ГРАНИЦЫ</span><h2>Почему сигнал может прийти позже?</h2><p>Доставка зависит от Twitch, Telegram и работы тестового контура. Видео превью может быть недоступно; в этом случае пост может выйти без него.</p></article>
  </section>
</main>"""


_BODIES = {
    "/site": _home,
    "/site/for-viewers": _viewers,
    "/site/for-streamers": _streamers,
    "/site/help": _help,
}


def _render(path: str, base_url: str) -> str:
    title, description = _PAGES[path]
    canonical = base_url + path
    schema = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title,
            "description": description,
            "url": canonical,
            "inLanguage": "ru",
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <meta name="theme-color" content="#f7f9fc">
  <title>{escape(title)}</title>
  <meta name="description" content="{escape(description, quote=True)}">
  <link rel="canonical" href="{escape(canonical, quote=True)}">
  <link rel="icon" href="/site/brand-logo.png" type="image/png">
  <link rel="stylesheet" href="/site/site.css">
  <script type="application/ld+json">{schema}</script>
</head>
<body>
  <a class="skip-link" href="#content">Перейти к содержимому</a>
  <header class="site-header wrap">
    <a class="brand" href="/site" aria-label="TwitchSignalBot — главная"><img src="/site/brand-logo.png" width="225" height="72" alt="TwitchSignalBot"></a>
    <nav class="main-nav" aria-label="Основная навигация">
      <a href="/site/for-viewers">Зрителям</a><a href="/site/for-streamers">Стримерам</a><a href="/site/help">Помощь</a>
    </nav>
    {_cta("button header-cta")}
  </header>
  {_BODIES[path]()}
  <footer class="site-footer wrap"><span>Тестовый прототип · TwitchSignalBot</span><span>Без реальных платежей</span><a href="/site/help">Помощь <span aria-hidden="true">↗</span></a></footer>
</body>
</html>"""


def install_growth_site(app: web.Application, bot_username: str, public_base_url: str | None) -> None:
    """Mount only the exact test bot. Never infer this from a production host."""
    if bot_username != _BOT_USERNAME:
        raise ValueError("R8 site is restricted to TwitchSignalTestbot")
    base_url = _validate_base_url(public_base_url)
    pages = {path: _render(path, base_url) for path in _PAGES}

    async def page(request: web.Request) -> web.Response:
        return web.Response(
            text=pages[request.path], content_type="text/html", charset="utf-8",
            headers=_HEADERS,
        )

    async def css(_: web.Request) -> web.Response:
        return web.Response(
            text=(_ASSETS / "site.css").read_text(encoding="utf-8"),
            content_type="text/css", headers=_HEADERS,
        )

    async def logo(_: web.Request) -> web.Response:
        return web.Response(
            body=(_ASSETS / "brand-logo.png").read_bytes(),
            content_type="image/png", headers=_HEADERS,
        )

    async def font(request: web.Request) -> web.Response:
        return web.Response(
            body=(_ASSETS / request.path.rsplit("/", 1)[-1]).read_bytes(),
            content_type="font/woff2", headers=_HEADERS,
        )

    async def robots(_: web.Request) -> web.Response:
        # Crawlers must be allowed to fetch /site to observe noindex.
        return web.Response(
            text="User-agent: *\nAllow: /site\n",
            content_type="text/plain", headers=_HEADERS,
        )

    for path in pages:
        app.router.add_get(path, page)
    app.router.add_get("/site/site.css", css)
    app.router.add_get("/site/brand-logo.png", logo)
    for name in ("golos-cyrillic.woff2", "golos-latin.woff2"):
        app.router.add_get(f"/site/{name}", font)
    app.router.add_get("/robots.txt", robots)
