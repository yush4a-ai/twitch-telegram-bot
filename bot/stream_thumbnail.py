from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REFRESH_SECONDS = 5 * 60
WIDTH = 640
HEIGHT = 360


def build_url(raw_url: str | None, refresh_bucket: int) -> str | None:
    """Resolve Twitch's thumbnail template and add a shared cache-busting key."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        return None
    resolved = (
        raw_url.strip()
        .replace("{width}", str(WIDTH))
        .replace("{height}", str(HEIGHT))
    )
    try:
        parts = urlsplit(resolved)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if "{" in resolved or "}" in resolved:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "_tsb"
    ]
    query.append(("_tsb", str(refresh_bucket)))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
