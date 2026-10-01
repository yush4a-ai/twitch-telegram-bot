"""Bounded, literal Viewer Plus alert rules for private destinations."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ViewerFilter:
    games: tuple[str, ...]
    title_keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]


def _terms(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > 5:
        raise ValueError("viewer filter requires at most five terms")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("viewer filter term is not text")
        term = value.strip()
        if (
            not 2 <= len(term) <= 40
            or any(unicodedata.category(char).startswith("C") for char in term)
            or term.casefold() in seen
        ):
            raise ValueError("viewer filter term is invalid or duplicated")
        normalized.append(term)
        seen.add(term.casefold())
    return tuple(normalized)


def validate_viewer_filter(
    games: object, title_keywords: object, exclude_keywords: object,
) -> ViewerFilter:
    return ViewerFilter(
        games=_terms(games),
        title_keywords=_terms(title_keywords),
        exclude_keywords=_terms(exclude_keywords),
    )


def matches_viewer_filter(
    rule: ViewerFilter, game_name: str | None, title: str | None,
) -> bool:
    game = (game_name or "").casefold()
    title_text = (title or "").casefold()
    if rule.games and game not in {value.casefold() for value in rule.games}:
        return False
    if rule.title_keywords and not any(
        keyword.casefold() in title_text for keyword in rule.title_keywords
    ):
        return False
    if any(keyword.casefold() in title_text for keyword in rule.exclude_keywords):
        return False
    return True
