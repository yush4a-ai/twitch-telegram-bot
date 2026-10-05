"""Проверка контраста принятого макета панели по WCAG 2.1.

Считает коэффициент контраста для пар «текст на фоне» из DESIGN.md и прототипа.
Порог: 4.5:1 для обычного текста, 3:1 для крупного и элементов интерфейса.
"""
from __future__ import annotations

import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

TOKENS = {
    "canvas": "#08070B",
    "surface": "#100F15",
    "raised": "#16141C",
    "border": "#1E1D21",
    "text": "#F4F2F8",
    "muted": "#9C97A8",
    "faint": "#8A8595",
    "accent": "#B98CFF",
    "accent_ink": "#1B1030",
    "good": "#A8F06B",
    "warn": "#FFC46B",
    "danger": "#FF8A9B",
}

# (передний план, фон, что это, требуемый порог)
PAIRS = [
    ("text", "canvas", "основной текст на фоне приложения", 4.5),
    ("text", "surface", "основной текст на поверхности", 4.5),
    ("text", "raised", "основной текст на приподнятой поверхности", 4.5),
    ("muted", "canvas", "вторичный текст на фоне", 4.5),
    ("muted", "surface", "вторичный текст на поверхности", 4.5),
    ("muted", "raised", "вторичный текст на приподнятой поверхности", 4.5),
    ("faint", "surface", "тихие подписи на поверхности", 4.5),
    ("accent", "canvas", "accent на фоне (ссылки, «Подробнее»)", 4.5),
    ("accent", "surface", "accent на поверхности", 4.5),
    ("accent_ink", "accent", "текст на главной кнопке", 4.5),
    ("good", "surface", "успех на поверхности", 4.5),
    ("warn", "surface", "внимание на поверхности", 4.5),
    ("danger", "surface", "ошибка на поверхности", 4.5),
    ("good", "canvas", "успех на фоне приложения", 4.5),
    ("warn", "canvas", "внимание на фоне приложения", 4.5),
    ("danger", "canvas", "ошибка на фоне приложения", 4.5),
    ("border", "canvas", "разделитель на фоне (элемент интерфейса)", 1.0),
]


def _channel(value: int) -> float:
    part = value / 255
    return part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(front: str, back: str) -> float:
    a, b = luminance(front), luminance(back)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    lines = [
        "# Контраст панели владельца — проверка по отрисованным цветам",
        "",
        "Считается по WCAG 2.1 из значений `DESIGN.md` и одобренного прототипа.",
        "Порог 4.5:1 — обычный текст, 3:1 — крупный текст и элементы интерфейса.",
        "",
        "| Что | Передний план | Фон | Контраст | Порог | Итог |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    failures = 0
    results = []
    for front, back, title, limit in PAIRS:
        ratio = contrast(TOKENS[front], TOKENS[back])
        ok = ratio >= limit
        if not ok:
            failures += 1
        results.append(
            {
                "what": title,
                "front": TOKENS[front],
                "back": TOKENS[back],
                "ratio": round(ratio, 2),
                "limit": limit,
                "pass": ok,
            }
        )
        lines.append(
            f"| {title} | `{TOKENS[front]}` | `{TOKENS[back]}` | {ratio:.2f}:1 | {limit}:1 | "
            + ("PASS" if ok else "**FAIL**")
            + " |"
        )
    lines += [
        "",
        f"Проверено пар: {len(PAIRS)}. Ниже порога: {failures}.",
        "",
    ]
    (HERE / "CONTRAST.md").write_text("\n".join(lines), encoding="utf-8")
    (HERE / "CONTRAST.json").write_text(
        json.dumps({"tokens": TOKENS, "results": results, "failures": failures}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
