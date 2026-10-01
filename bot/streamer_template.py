"""Validated plain-text Streamer Plus live-post settings."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TemplateButton:
    label: str
    url: str


@dataclass(frozen=True)
class StreamerTemplate:
    headline: str
    body: str
    buttons: tuple[TemplateButton, ...]


_DNS_HOST = re.compile(
    r"(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}\Z"
)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _safe_url(raw: str) -> bool:
    if not isinstance(raw, str) or not 1 <= len(raw) <= 512:
        return False
    if any(ord(char) <= 32 or ord(char) == 127 for char in raw):
        return False
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        if (
            parsed.scheme != "https" or not host
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or not _DNS_HOST.fullmatch(host)
        ):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            pass
        return not host.lower().endswith((".local", ".internal", ".test"))
    except ValueError:
        return False


def validate_streamer_template(headline: str, body: str, buttons: object) -> StreamerTemplate:
    if (
        not isinstance(headline, str) or not headline.strip()
        or _units(headline) > 60 or "\n" in headline
        or any(ord(char) < 32 for char in headline)
    ):
        raise ValueError("invalid headline")
    if (
        not isinstance(body, str) or _units(body) > 140
        or any(ord(char) < 32 and char != "\n" for char in body)
    ):
        raise ValueError("invalid body")
    if not isinstance(buttons, (list, tuple)) or len(buttons) > 2:
        raise ValueError("invalid buttons")
    clean_buttons = []
    for item in buttons:
        if not isinstance(item, dict) or set(item) != {"label", "url"}:
            raise ValueError("invalid button")
        label, url = item["label"], item["url"]
        if (
            not isinstance(label, str) or not label.strip()
            or _units(label) > 24 or any(ord(char) < 32 for char in label)
            or not _safe_url(url)
        ):
            raise ValueError("invalid button")
        clean_buttons.append(TemplateButton(label.strip(), url))
    return StreamerTemplate(headline.strip(), body.strip(), tuple(clean_buttons))
