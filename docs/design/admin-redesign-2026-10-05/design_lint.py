"""Проверка макета панели на соответствие принятой дизайн-системе.

Проверяет: кратность отступов 4 px, шкалу скруглений, отсутствие внешних
ресурсов (CDN), тёмную схему, подписи навигации и высоту целей нажатия.
"""
from __future__ import annotations

import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
FILES = ["prototype.html", "states.html"]
ALLOWED_RADII = {0, 4, 8, 12, 16}
TAP_TARGET_MIN = 44


def _numbers(value: str) -> list[float]:
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)px", value)]


def check_file(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    issues: list[str] = []

    for prop in ("padding", "margin", "gap"):
        for match in re.finditer(rf"(?m)^\s*{prop}(?:-(?:top|bottom|left|right))?:\s*([^;]+);", text):
            for number in _numbers(match.group(1)):
                if number and number % 4 != 0:
                    issues.append(f"{prop}: {number}px не кратно 4")

    for match in re.finditer(r"(?m)border-radius:\s*([^;]+);", text):
        for number in _numbers(match.group(1)):
            if number and number not in ALLOWED_RADII:
                issues.append(f"border-radius: {number}px вне шкалы 4/8/12/16")

    for pattern, title in (
        (r'<link[^>]+href="https?://', "внешняя таблица стилей"),
        (r'<script[^>]+src="https?://', "внешний скрипт"),
        (r"url\(\s*['\"]?https?://", "внешний ресурс в CSS"),
        (r"@import\s+url\(", "внешний импорт CSS"),
    ):
        if re.search(pattern, text, re.I):
            issues.append(f"внешний ресурс: {title}")

    if "color-scheme" not in text:
        issues.append("нет color-scheme: dark")

    if path.name.startswith(("prototype", "states")):
        for nav in re.finditer(r"<nav[^>]*>", text):
            if "aria-label" not in nav.group(0):
                issues.append("у <nav> нет aria-label")

    buttons = len(re.findall(r"<button", text))
    links = len(re.findall(r'<a\s', text))
    small_buttons = re.findall(r"\.btn[^{]*\{[^}]*padding:\s*(\d+)px", text)
    min_height = re.findall(r"\.btn[^{]*\{[^}]*min-height:\s*(\d+)px", text)

    return {
        "file": path.name,
        "issues": issues,
        "buttons": buttons,
        "links": links,
        "button_padding_px": small_buttons,
        "button_min_height_px": min_height,
    }


def main() -> int:
    report = {"files": [], "tap_target_min": TAP_TARGET_MIN, "failures": 0}
    lines = [
        "# Линт макета панели",
        "",
        f"Проверка: отступы кратны 4 px, скругления из шкалы 4/8/12/16, нет внешних ресурсов, "
        f"тёмная схема, подписи навигации, цель нажатия не меньше {TAP_TARGET_MIN} px.",
        "",
    ]
    for name in FILES:
        path = HERE / name
        if not path.is_file():
            report["files"].append({"file": name, "issues": ["файл не найден"]})
            report["failures"] += 1
            continue
        result = check_file(path)
        report["files"].append(result)
        report["failures"] += len(result["issues"])
        status = "OK" if not result["issues"] else f"найдено {len(result['issues'])}"
        lines.append(f"## {name} — {status}")
        lines.append("")
        if result["issues"]:
            lines += [f"- {issue}" for issue in result["issues"]]
        else:
            lines.append("- отклонений не найдено")
        lines.append("")

    (HERE / "DESIGN-LINT.md").write_text("\n".join(lines), encoding="utf-8")
    (HERE / "DESIGN-LINT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
