"""Compose safe Streamer Plus text and controlled buttons over the Free post."""

from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .live_post import LivePostContent
from .streamer_template import StreamerTemplate


def compose_streamer_post(base: LivePostContent, template: StreamerTemplate) -> LivePostContent:
    extra = f"<b>{escape(template.headline)}</b>"
    if template.body:
        extra += f"\n{escape(template.body)}"
    html = f"{extra}\n\n{base.html}"
    if len(html.encode("utf-16-le")) // 2 > 1024:
        return base
    rows = list(base.reply_markup.inline_keyboard) if base.reply_markup else []
    rows.extend([
        InlineKeyboardButton(text=button.label, url=button.url)
    ] for button in template.buttons)
    return LivePostContent(
        html=html,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )
