from __future__ import annotations

import html
import json
from datetime import datetime, timezone


def _fmt_offset(seconds: int) -> str:
    """Секунды от начала стрима -> ч:мм:сс или мм:сс."""
    hours, rem = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _build_change_timeline(
    samples: list[tuple[float, int, str, str]], start_ts: float
) -> list[dict]:
    """Список смен title/game_name с таймкодом от начала стрима (в секундах)."""
    events: list[dict] = []
    last_title: str | None = None
    last_game: str | None = None

    for sampled_at, _viewer_count, title, game_name in samples:
        offset = max(0, int(sampled_at - start_ts))
        if last_title is None:
            events.append(
                {"offset": offset, "type": "start", "title": title, "game": game_name}
            )
        else:
            if title != last_title:
                events.append(
                    {"offset": offset, "type": "title", "title": title, "game": game_name}
                )
            if game_name != last_game:
                events.append(
                    {"offset": offset, "type": "game", "title": title, "game": game_name}
                )
        last_title = title
        last_game = game_name

    return events


def _event_label(event: dict) -> str:
    if event["type"] == "start":
        return (
            f'Начало стрима — <b>{html.escape(event["title"])}</b> '
            f'<span class="event-game">{html.escape(event["game"])}</span>'
        )
    if event["type"] == "title":
        return f'Смена названия → <b>{html.escape(event["title"])}</b>'
    if event["type"] == "raid":
        if event.get("raider_name"):
            return (
                f'⚡ Рейд от <b>{html.escape(event["raider_name"])}</b> — '
                f'{event["join_count"]} зрителей'
            )
        return f'⚡ Вероятный рейд — <b>{event["join_count"]}</b> новых зрителей за 30 сек'
    return f'Смена категории → <b>{html.escape(event["game"])}</b>'


def _stat_tile(label: str, value: str, *, warn: bool = False) -> str:
    warn_mark = ' <span class="warn-badge" title="Данные могут быть неполными">⚠</span>' if warn else ""
    return (
        '<div class="stat">'
        f'<div class="stat-label">{label}{warn_mark}</div>'
        f'<div class="stat-value">{value}</div>'
        "</div>"
    )


def _chatters_note(unique_chatters: int | None, reliable: bool) -> str:
    if unique_chatters is None:
        return ""
    base = (
        "Считаются заходы в чат (JOIN) — не все зрители открывают чат, "
        "поэтому число всегда меньше реальной аудитории."
    )
    if reliable:
        return f'<p class="note">{base}</p>'
    return (
        '<p class="note note-warn">⚠ За стрим долго не было новых JOIN при живом чате — '
        "похоже, Twitch отключил join/part-рассылку на этом канале. "
        f"Число уникальных чатеров занижено. {base}</p>"
    )


def format_duration_seconds(seconds: int) -> str:
    hours, remainder = divmod(max(seconds, 0), 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def build_report_html(
    login: str,
    started_at_iso: str,
    duration_text: str,
    peak_viewers: int,
    avg_viewers: int,
    samples: list[tuple[float, int, str, str]],
    new_followers: str | None = None,
    chat_activity: list[tuple[float, int]] | None = None,
    unique_chatters: int | None = None,
    unique_chatters_reliable: bool = True,
    chatter_nicks: list[tuple[str, float]] | None = None,
    top_clips: list | None = None,
    vod_url: str | None = None,
    top_chatters: list[tuple[str, int]] | None = None,
    raid_events: list[tuple[float, int, str | None]] | None = None,
    collab_logins: list[str] | None = None,
) -> str:
    try:
        start_dt = datetime.strptime(started_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        start_ts = start_dt.timestamp()
        started_human = start_dt.strftime("%d.%m.%Y, %H:%M UTC")
    except ValueError:
        start_ts = samples[0][0] if samples else 0.0
        started_human = started_at_iso

    viewer_points = [
        {"t": max(0, int(s - start_ts)), "v": v, "ts": s} for s, v, _title, _game in samples
    ]
    chat_points = [
        {"t": max(0, int(minute_ts - start_ts)), "v": count, "ts": minute_ts}
        for minute_ts, count in (chat_activity or [])
    ]
    chatter_entries = [
        {"nick": nick, "t": max(0, int(joined_at - start_ts))}
        for nick, joined_at in (chatter_nicks or [])
    ]
    events = _build_change_timeline(samples, start_ts)
    for raid_ts, join_count, raider_name in (raid_events or []):
        events.append(
            {
                "offset": max(0, int(raid_ts - start_ts)),
                "type": "raid",
                "join_count": join_count,
                "raider_name": raider_name,
            }
        )
    events.sort(key=lambda e: e["offset"])

    # ось X тянется на всю длительность стрима, а не только до последнего замера
    axis_max_t = max(
        [p["t"] for p in viewer_points] + [p["t"] for p in chat_points] + [0]
    )

    # текущая категория/название на каждый момент, для общего тултипа
    context_at = [
        {"t": max(0, int(s - start_ts)), "title": title, "game": game}
        for s, _v, title, game in samples
    ]

    login_esc = html.escape(login)
    collab_html = ""
    if collab_logins:
        names = ", ".join(html.escape(c) for c in collab_logins)
        collab_html = f'<div class="collab-badge">🤝 Коллаб с {names}</div>'

    events_html = "".join(
        f'<li class="event event-{e["type"]}">'
        f'<span class="event-time">{_fmt_offset(e["offset"])}</span>'
        f'<span class="event-dot" aria-hidden="true"></span>'
        f'<span class="event-label">{_event_label(e)}</span>'
        "</li>"
        for e in events
    )

    # Вторичные показатели идут после hero-числа, в порядке важности
    tiles = [
        _stat_tile("Среднее зрителей", str(avg_viewers)),
        _stat_tile("Длительность", html.escape(duration_text)),
    ]
    if new_followers is not None:
        tiles.append(_stat_tile("Новых фолловеров", html.escape(new_followers)))
    if unique_chatters is not None:
        tiles.append(
            _stat_tile(
                "Уникальных чатеров",
                str(unique_chatters),
                warn=not unique_chatters_reliable,
            )
        )
    tiles_html = "".join(tiles)

    vod_link_html = ""
    if vod_url:
        vod_link_html = (
            f'<a class="vod-link" href="{html.escape(vod_url)}" target="_blank" '
            'rel="noopener noreferrer">▶ Смотреть VOD на Twitch</a>'
        )

    clips_panel = ""
    if top_clips:
        def _clip_card(clip) -> str:
            views = f"{clip.view_count:,}".replace(",", " ")
            return f"""
      <a class="clip-card" href="{html.escape(clip.url)}" target="_blank" rel="noopener noreferrer">
        <div class="clip-thumb-wrap">
          <img class="clip-thumb" src="{html.escape(clip.thumbnail_url)}" alt="" loading="lazy">
          <span class="clip-play" aria-hidden="true">▶</span>
        </div>
        <div class="clip-info">
          <div class="clip-title">{html.escape(clip.title or "(без названия)")}</div>
          <div class="clip-meta">👁 {views} · {html.escape(clip.creator_name or "—")}</div>
        </div>
      </a>"""

        cards = "".join(_clip_card(clip) for clip in top_clips)
        clips_panel = f"""
  <section class="panel">
    <header class="panel-head">
      <h2>Лучшие клипы <span class="count">{len(top_clips)}</span></h2>
      <p class="panel-sub">Топ по просмотрам за время стрима</p>
    </header>
    <div class="clips-grid">{cards}</div>
  </section>"""

    top_chatters_panel = ""
    if top_chatters:
        rows = "".join(
            f'<li class="top-chatter-row">'
            f'<span class="top-chatter-rank">{i}</span>'
            f'<span class="top-chatter-nick">{html.escape(nick)}</span>'
            f'<span class="top-chatter-count">{count}</span>'
            "</li>"
            for i, (nick, count) in enumerate(top_chatters, 1)
        )
        top_chatters_panel = f"""
  <section class="panel">
    <header class="panel-head">
      <h2>Топ чатеров <span class="count">{len(top_chatters)}</span></h2>
      <p class="panel-sub">По числу сообщений за стрим</p>
    </header>
    <ul class="top-chatters-list">{rows}</ul>
  </section>"""

    chatters_panel = ""
    if chatter_entries:
        chatters_panel = f"""
  <section class="panel">
    <header class="panel-head">
      <h2>Уникальные чатеры <span class="count">{len(chatter_entries)}</span></h2>
      <p class="panel-sub">Время — первый заход в чат от начала стрима</p>
    </header>
    <input type="search" id="nick-search" class="search" placeholder="Поиск по нику…"
           aria-label="Поиск по нику" autocomplete="off">
    <div class="sort-row">
      <button type="button" class="sort-btn is-active" data-sort="time">По времени входа</button>
      <button type="button" class="sort-btn" data-sort="alpha">По алфавиту</button>
    </div>
    <ul id="nick-list" class="nick-list"></ul>
  </section>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Стрим {login_esc} — отчёт</title>
<style>
  :root {{
    color-scheme: dark;
    --surface:      #1a1a19;
    --plane:        #0d0d0d;
    --text-1:       #ffffff;
    --text-2:       #c3c2b7;
    --text-muted:   #898781;
    --grid:         #2c2c2a;
    --axis:         #383835;
    --border:       rgba(255,255,255,0.10);
    --viewers:      #3987e5;
    --chat:         #d95926;
    --good:         #0ca30c;
    --warning:      #fab219;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 20px 64px;
    background: var(--plane);
    color: var(--text-1);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.5;
    display: flex;
    justify-content: center;
  }}
  .wrap {{ width: 100%; max-width: 920px; }}

  /* ---------- Шапка ---------- */
  .head {{ margin-bottom: 28px; }}
  .head h1 {{
    font-size: 15px;
    font-weight: 600;
    color: var(--text-2);
    margin: 0 0 2px;
    letter-spacing: .01em;
  }}
  .head .channel {{ color: var(--text-1); }}
  .head time {{ font-size: 13px; color: var(--text-muted); }}
  .collab-badge {{
    display: inline-flex;
    margin-top: 8px;
    padding: 3px 10px;
    background: rgba(255,255,255,0.06);
    border: 1px solid var(--border);
    border-radius: 999px;
    font-size: 12.5px;
    color: var(--text-2);
  }}

  /* ---------- Hero + статы ---------- */
  .summary {{
    display: grid;
    grid-template-columns: minmax(180px, 260px) 1fr;
    gap: 20px;
    align-items: stretch;
    margin-bottom: 12px;
  }}
  .hero {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 22px 24px;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .hero-label {{
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: .06em;
    color: var(--text-muted);
  }}
  .hero-value {{
    font-size: 56px;
    font-weight: 650;
    line-height: 1.05;
    margin-top: 6px;
  }}
  .hero-sub {{ font-size: 13px; color: var(--text-2); margin-top: 4px; }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
    grid-auto-rows: 1fr;
    gap: 12px;
  }}
  .stat {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .stat-label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .05em;
    color: var(--text-muted);
  }}
  .stat-value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  .warn-badge {{ color: var(--warning); }}

  /* ---------- Панели ---------- */
  .panel {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 22px;
    margin-top: 20px;
  }}
  .panel-head {{ margin-bottom: 18px; }}
  .panel h2 {{
    font-size: 14px;
    font-weight: 600;
    color: var(--text-2);
    margin: 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .count {{
    font-size: 12px;
    font-weight: 600;
    color: var(--text-muted);
    background: rgba(255,255,255,0.06);
    border-radius: 999px;
    padding: 1px 8px;
  }}
  .panel-sub, .note {{
    font-size: 12.5px;
    color: var(--text-muted);
    margin: 4px 0 0;
  }}
  .note {{ margin: 10px 2px 0; }}
  .note-warn {{ color: var(--warning); }}

  /* ---------- Графики ---------- */
  .chart-head {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 14px;
  }}
  .chart-peak {{ font-size: 12.5px; color: var(--text-muted); }}
  .chart-peak b {{ color: var(--text-2); font-weight: 600; }}
  .chart-box {{ position: relative; }}
  .chart-box svg {{ width: 100%; height: 220px; display: block; overflow: visible; }}
  .tick {{ font-size: 11px; fill: var(--text-muted); }}
  .tick-line {{ stroke: var(--grid); stroke-width: 1; }}
  .axis-line {{ stroke: var(--axis); stroke-width: 1; }}
  .crosshair {{ stroke: var(--text-muted); stroke-width: 1; opacity: 0; }}
  .empty-msg {{ fill: var(--text-muted); font-size: 13px; }}

  .tip {{
    position: absolute;
    pointer-events: none;
    background: var(--plane);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 8px 11px;
    font-size: 12.5px;
    white-space: nowrap;
    opacity: 0;
    transition: opacity .1s;
    box-shadow: 0 6px 20px rgba(0,0,0,.45);
    z-index: 5;
  }}
  .tip-time {{ color: var(--text-muted); font-size: 11.5px; }}
  .tip-row {{ display: flex; align-items: center; gap: 7px; margin-top: 3px; }}
  .tip-key {{ width: 14px; height: 2px; border-radius: 2px; flex-shrink: 0; }}
  .tip-val {{ font-weight: 650; font-variant-numeric: tabular-nums; }}
  .tip-name {{ color: var(--text-2); }}
  .tip-context {{
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
    max-width: 240px;
    white-space: normal;
  }}
  .tip-game {{ color: var(--viewers); font-weight: 600; font-size: 12px; }}
  .tip-title {{ color: var(--text-2); font-size: 12px; margin-top: 2px; }}

  .legend {{ display: flex; gap: 18px; margin-top: 14px; flex-wrap: wrap; }}
  .legend-item {{
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 13px;
    color: var(--text-2);
    cursor: pointer;
    user-select: none;
  }}
  .legend-item input {{ accent-color: var(--viewers); margin: 0; }}
  .legend-item:has(input:not(:checked)) {{ color: var(--text-muted); opacity: .6; }}
  .legend-swatch {{ width: 14px; height: 3px; border-radius: 2px; flex-shrink: 0; }}
  .legend-swatch--viewers {{ background: var(--viewers); }}
  .legend-swatch--chat {{ background: none; border-top: 2px dashed var(--chat); height: 0; }}

  /* ---------- Таймлайн ---------- */
  .events {{ list-style: none; margin: 0; padding: 0; }}
  .event {{
    display: grid;
    grid-template-columns: 58px 12px 1fr;
    align-items: start;
    gap: 10px;
    padding: 9px 0;
    font-size: 14px;
    position: relative;
  }}
  .event + .event::before {{
    content: "";
    position: absolute;
    left: 63px;
    top: -9px;
    height: 18px;
    width: 1px;
    background: var(--axis);
  }}
  .event-time {{
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    font-size: 13px;
    padding-top: 1px;
  }}
  .event-dot {{
    width: 9px; height: 9px;
    border-radius: 50%;
    margin-top: 6px;
    background: var(--text-muted);
    box-shadow: 0 0 0 2px var(--surface);
  }}
  .event-start .event-dot {{ background: var(--good); }}
  .event-title .event-dot {{ background: var(--viewers); }}
  .event-game .event-dot {{ background: var(--chat); }}
  .event-raid .event-dot {{ background: var(--warning); }}
  .event-label {{ color: var(--text-2); }}
  .event-label b {{ color: var(--text-1); font-weight: 600; }}
  .event-game {{ color: var(--text-muted); }}

  .vod-link {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    padding: 10px 16px;
    background: var(--plane);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text-1);
    text-decoration: none;
    font-size: 13.5px;
    font-weight: 600;
    transition: border-color .15s;
  }}
  .vod-link:hover {{ border-color: rgba(255,255,255,0.28); }}

  /* ---------- Клипы ---------- */
  .clips-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
  }}
  .clip-card {{
    display: block;
    text-decoration: none;
    color: inherit;
    border-radius: 10px;
    overflow: hidden;
    background: var(--plane);
    border: 1px solid var(--border);
    transition: border-color .15s;
  }}
  .clip-card:hover {{ border-color: rgba(255,255,255,0.28); }}
  .clip-thumb-wrap {{
    position: relative;
    aspect-ratio: 16 / 9;
    background: var(--grid);
  }}
  .clip-thumb {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .clip-play {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 22px;
    color: #fff;
    background: rgba(0,0,0,0.15);
    opacity: 0;
    transition: opacity .15s;
  }}
  .clip-card:hover .clip-play {{ opacity: 1; }}
  .clip-info {{ padding: 10px 12px; }}
  .clip-title {{
    font-size: 13.5px;
    color: var(--text-1);
    line-height: 1.35;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}
  .clip-meta {{ margin-top: 5px; font-size: 12px; color: var(--text-muted); }}

  /* ---------- Топ чатеров ---------- */
  .top-chatters-list {{ list-style: none; margin: 0; padding: 0; }}
  .top-chatter-row {{
    display: grid;
    grid-template-columns: 28px 1fr auto;
    align-items: center;
    gap: 12px;
    padding: 9px 2px;
    border-bottom: 1px solid var(--grid);
    font-size: 14px;
  }}
  .top-chatter-row:last-child {{ border-bottom: none; }}
  .top-chatter-rank {{
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    font-size: 12.5px;
  }}
  .top-chatter-nick {{ color: var(--text-1); overflow: hidden; text-overflow: ellipsis; }}
  .top-chatter-count {{
    color: var(--text-2);
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }}

  /* ---------- Чатеры ---------- */
  .search {{
    width: 100%;
    padding: 10px 13px;
    background: var(--plane);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text-1);
    font-size: 14px;
    font-family: inherit;
  }}
  .search:focus {{ outline: none; border-color: var(--viewers); }}
  .search::placeholder {{ color: var(--text-muted); }}
  .sort-row {{ display: flex; gap: 8px; margin: 12px 0 14px; }}
  .sort-btn {{
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--text-muted);
    font-size: 12.5px;
    font-family: inherit;
    padding: 5px 12px;
    cursor: pointer;
  }}
  .sort-btn:hover {{ color: var(--text-2); }}
  .sort-btn.is-active {{
    color: var(--text-1);
    border-color: rgba(255,255,255,0.28);
    background: rgba(255,255,255,0.06);
  }}
  .nick-list {{
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 2px 28px;
    max-height: 420px;
    overflow-y: auto;
  }}
  .nick-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    padding: 7px 2px;
    border-bottom: 1px solid var(--grid);
    font-size: 13.5px;
  }}
  .nick-name {{ overflow: hidden; text-overflow: ellipsis; }}
  .nick-time {{
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    font-size: 12.5px;
    flex-shrink: 0;
  }}
  .nick-empty {{ color: var(--text-muted); font-size: 13.5px; padding: 8px 2px; }}

  @media (max-width: 640px) {{
    body {{ padding: 20px 14px 48px; }}
    .summary {{ grid-template-columns: 1fr; }}
    .hero-value {{ font-size: 44px; }}
    .event {{ grid-template-columns: 50px 12px 1fr; }}
    .event + .event::before {{ left: 55px; }}
  }}
</style>
</head>
<body>
<div class="wrap">

  <header class="head">
    <h1>Отчёт по стриму · <span class="channel">{login_esc}</span></h1>
    <time>{html.escape(started_human)}</time>
    {collab_html}
  </header>

  <div class="summary">
    <div class="hero">
      <div class="hero-label">Пик зрителей</div>
      <div class="hero-value">{peak_viewers}</div>
      <div class="hero-sub">в среднем {avg_viewers} за стрим</div>
    </div>
    <div class="stats">{tiles_html}</div>
  </div>
  {_chatters_note(unique_chatters, unique_chatters_reliable)}

  <section class="panel">
    <div class="chart-head">
      <h2>Динамика стрима</h2>
      <span class="chart-peak">пик зрителей <b>{peak_viewers}</b></span>
    </div>
    <div class="chart-box">
      <svg id="chart-main" viewBox="0 0 800 260" preserveAspectRatio="none"
           role="img" aria-label="Зрители и активность чата за стрим"></svg>
      <div class="tip" id="tip-main"></div>
    </div>
    <div class="legend" id="chart-legend">
      <label class="legend-item">
        <input type="checkbox" checked data-series="viewers">
        <span class="legend-swatch legend-swatch--viewers"></span> Зрители
      </label>
      <label class="legend-item">
        <input type="checkbox" checked data-series="chat">
        <span class="legend-swatch legend-swatch--chat"></span> Чат (сообщ./мин)
      </label>
    </div>
  </section>

  <section class="panel">
    <header class="panel-head"><h2>События стрима</h2></header>
    <ul class="events">{events_html or '<li class="nick-empty">Нет данных о сменах названия или категории.</li>'}</ul>
    {vod_link_html}
  </section>
{clips_panel}
{top_chatters_panel}
{chatters_panel}
</div>

<script>
const viewerPoints = {json.dumps(viewer_points)};
const chatPoints = {json.dumps(chat_points)};
const chatterEntries = {json.dumps(chatter_entries)};
const contextAt = {json.dumps(context_at)};
const axisMaxT = {axis_max_t};
const streamStartMs = {start_ts * 1000};

const W = 800, H = 260, PAD_L = 44, PAD_R = 12, PAD_T = 14, PAD_B = 26;

function fmtOffset(t) {{
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  return h > 0 ? `${{h}}:${{mm}}:${{ss}}` : `${{mm}}:${{ss}}`;
}}

/** Реальное время (часы:минуты, локальное время браузера) для смещения t секунд от старта. */
function fmtClock(t) {{
  const d = new Date(streamStartMs + t * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${{hh}}:${{mm}}`;
}}

/** Ровные деления оси: 1/2/5 * 10^n */
function niceTicks(maxV, count) {{
  if (maxV <= 0) return [0];
  const raw = maxV / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = 0; v <= maxV + step * 0.001; v += step) ticks.push(Math.round(v));
  return ticks;
}}

function contextForT(t) {{
  if (!contextAt.length) return null;
  let best = contextAt[0];
  for (const c of contextAt) {{ if (c.t <= t) best = c; else break; }}
  return best;
}}

function drawCombinedChart() {{
  const svg = document.getElementById('chart-main');
  const tip = document.getElementById('tip-main');
  if (!svg) return;

  const hasViewers = viewerPoints.length >= 2;
  const hasChat = chatPoints.length >= 2;

  if (!hasViewers && !hasChat) {{
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', W / 2); t.setAttribute('y', H / 2);
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('class', 'empty-msg');
    t.textContent = 'Недостаточно данных для графика';
    svg.appendChild(t);
    return;
  }}

  const maxT = Math.max(axisMaxT || 0, 1);
  // общая шкала Y — честный масштаб для обеих серий, без искажения
  const rawMaxV = Math.max(
    hasViewers ? Math.max(...viewerPoints.map(p => p.v)) : 0,
    hasChat ? Math.max(...chatPoints.map(p => p.v)) : 0,
    1
  );
  const ticks = niceTicks(rawMaxV, 4);
  const maxV = Math.max(ticks[ticks.length - 1], 1);

  const x = t => PAD_L + (t / maxT) * (W - PAD_L - PAD_R);
  const y = v => H - PAD_B - (v / maxV) * (H - PAD_T - PAD_B);

  const NS = 'http://www.w3.org/2000/svg';
  const el = (name, attrs) => {{
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }};

  for (const tv of ticks) {{
    const yy = y(tv);
    svg.appendChild(el('line', {{ x1: PAD_L, y1: yy, x2: W - PAD_R, y2: yy, class: 'tick-line' }}));
    const label = el('text', {{ x: PAD_L - 8, y: yy + 4, 'text-anchor': 'end', class: 'tick' }});
    label.textContent = tv;
    svg.appendChild(label);
  }}

  svg.appendChild(el('line', {{
    x1: PAD_L, y1: H - PAD_B, x2: W - PAD_R, y2: H - PAD_B, class: 'axis-line'
  }}));
  for (const frac of [0, 0.25, 0.5, 0.75, 1]) {{
    const tv = Math.round(maxT * frac);
    const anchor = frac === 0 ? 'start' : frac === 1 ? 'end' : 'middle';
    const label = el('text', {{ x: x(tv), y: H - PAD_B + 16, 'text-anchor': anchor, class: 'tick' }});
    label.textContent = fmtClock(tv);
    svg.appendChild(label);
  }}

  if (hasViewers) {{
    const line = viewerPoints.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ' ' + y(p.v).toFixed(1)).join(' ');
    const area = line + ` L ${{x(viewerPoints[viewerPoints.length - 1].t).toFixed(1)}} ${{H - PAD_B}}`
                      + ` L ${{x(viewerPoints[0].t).toFixed(1)}} ${{H - PAD_B}} Z`;
    const defs = el('defs', {{}});
    const grad = el('linearGradient', {{ id: 'grad-viewers', x1: 0, y1: 0, x2: 0, y2: 1 }});
    grad.appendChild(el('stop', {{ offset: '0%', 'stop-color': '#3987e5', 'stop-opacity': '0.22' }}));
    grad.appendChild(el('stop', {{ offset: '100%', 'stop-color': '#3987e5', 'stop-opacity': '0' }}));
    defs.appendChild(grad);
    svg.appendChild(defs);
    svg.appendChild(el('path', {{ d: area, fill: 'url(#grad-viewers)', class: 'series-viewers' }}));
    svg.appendChild(el('path', {{
      d: line, fill: 'none', stroke: '#3987e5', 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round', class: 'series-viewers'
    }}));
  }}

  if (hasChat) {{
    const line = chatPoints.map((p, i) => (i ? 'L' : 'M') + x(p.t).toFixed(1) + ' ' + y(p.v).toFixed(1)).join(' ');
    svg.appendChild(el('path', {{
      d: line, fill: 'none', stroke: '#d95926', 'stroke-width': 2,
      'stroke-dasharray': '5 4', 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      class: 'series-chat'
    }}));
  }}

  // Слой наведения: общий crosshair + по точке на серию
  const cross = el('line', {{ y1: PAD_T, y2: H - PAD_B, class: 'crosshair' }});
  svg.appendChild(cross);
  const dotViewers = el('circle', {{
    r: 4.5, fill: '#3987e5', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0, class: 'series-viewers'
  }});
  const dotChat = el('circle', {{
    r: 4.5, fill: '#d95926', stroke: 'var(--surface)', 'stroke-width': 2, opacity: 0, class: 'series-chat'
  }});
  svg.appendChild(dotViewers);
  svg.appendChild(dotChat);

  function nearest(points, t) {{
    let best = points[0], bestD = Infinity;
    for (const p of points) {{
      const d = Math.abs(p.t - t);
      if (d < bestD) {{ bestD = d; best = p; }}
    }}
    return best;
  }}

  const box = svg.parentElement;
  function onMove(ev) {{
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * W;
    const t = Math.max(0, Math.min(maxT, (px - PAD_L) / (W - PAD_L - PAD_R) * maxT));

    const bx = x(t);
    cross.setAttribute('x1', bx); cross.setAttribute('x2', bx);
    cross.style.opacity = 0.4;

    tip.innerHTML = '';
    const timeEl = document.createElement('div');
    timeEl.className = 'tip-time';
    timeEl.textContent = fmtClock(t);
    tip.appendChild(timeEl);

    let nearestT = t;

    if (hasViewers && document.querySelector('[data-series="viewers"]').checked) {{
      const p = nearest(viewerPoints, t);
      nearestT = p.t;
      dotViewers.setAttribute('cx', x(p.t)); dotViewers.setAttribute('cy', y(p.v));
      dotViewers.style.opacity = 1;
      const row = document.createElement('div');
      row.className = 'tip-row';
      const key = document.createElement('span'); key.className = 'tip-key'; key.style.background = '#3987e5';
      const val = document.createElement('span'); val.className = 'tip-val'; val.textContent = p.v;
      const name = document.createElement('span'); name.className = 'tip-name'; name.textContent = 'зрителей';
      row.append(key, val, name);
      tip.appendChild(row);
    }} else {{ dotViewers.style.opacity = 0; }}

    if (hasChat && document.querySelector('[data-series="chat"]').checked) {{
      const p = nearest(chatPoints, t);
      dotChat.setAttribute('cx', x(p.t)); dotChat.setAttribute('cy', y(p.v));
      dotChat.style.opacity = 1;
      const row = document.createElement('div');
      row.className = 'tip-row';
      const key = document.createElement('span'); key.className = 'tip-key'; key.style.background = '#d95926';
      const val = document.createElement('span'); val.className = 'tip-val'; val.textContent = p.v;
      const name = document.createElement('span'); name.className = 'tip-name'; name.textContent = 'сообщ./мин';
      row.append(key, val, name);
      tip.appendChild(row);
    }} else {{ dotChat.style.opacity = 0; }}

    const ctx = contextForT(nearestT);
    if (ctx) {{
      const wrap = document.createElement('div');
      wrap.className = 'tip-context';
      const game = document.createElement('div');
      game.className = 'tip-game';
      game.textContent = ctx.game;
      const title = document.createElement('div');
      title.className = 'tip-title';
      title.textContent = ctx.title;
      wrap.append(game, title);
      tip.appendChild(wrap);
    }}

    const leftPx = (bx / W) * rect.width;
    tip.style.left = Math.min(Math.max(leftPx + 14, 4), rect.width - 260) + 'px';
    tip.style.top = '8px';
    tip.style.opacity = 1;
  }}
  function onLeave() {{
    cross.style.opacity = 0;
    dotViewers.style.opacity = 0;
    dotChat.style.opacity = 0;
    tip.style.opacity = 0;
  }}
  box.addEventListener('pointermove', onMove);
  box.addEventListener('pointerleave', onLeave);

  for (const cb of document.querySelectorAll('#chart-legend input[type="checkbox"]')) {{
    cb.addEventListener('change', () => {{
      const cls = 'series-' + cb.dataset.series;
      for (const node of svg.querySelectorAll('.' + cls)) {{
        node.style.display = cb.checked ? '' : 'none';
      }}
    }});
  }}
}}

drawCombinedChart();

/* ---------- Список чатеров ---------- */
let currentSort = 'time';

function renderNickList() {{
  const container = document.getElementById('nick-list');
  const input = document.getElementById('nick-search');
  if (!container) return;

  const query = (input ? input.value : '').trim().toLowerCase();
  let matches = chatterEntries.filter(e => e.nick.toLowerCase().includes(query));
  matches = matches.slice().sort(
    currentSort === 'alpha'
      ? (a, b) => a.nick.localeCompare(b.nick)
      : (a, b) => a.t - b.t
  );

  container.innerHTML = '';
  if (matches.length === 0) {{
    const li = document.createElement('li');
    li.className = 'nick-empty';
    li.textContent = 'Никого не найдено.';
    container.appendChild(li);
    return;
  }}
  for (const entry of matches) {{
    const li = document.createElement('li');
    li.className = 'nick-row';
    const name = document.createElement('span');
    name.className = 'nick-name';
    name.textContent = entry.nick;
    const t = document.createElement('span');
    t.className = 'nick-time';
    t.textContent = fmtOffset(entry.t);
    li.append(name, t);
    container.appendChild(li);
  }}
}}

const nickSearch = document.getElementById('nick-search');
if (nickSearch) {{
  renderNickList();
  nickSearch.addEventListener('input', renderNickList);
  for (const btn of document.querySelectorAll('.sort-btn')) {{
    btn.addEventListener('click', () => {{
      currentSort = btn.dataset.sort;
      for (const b of document.querySelectorAll('.sort-btn')) b.classList.remove('is-active');
      btn.classList.add('is-active');
      renderNickList();
    }});
  }}
}}
</script>
</body>
</html>
"""
