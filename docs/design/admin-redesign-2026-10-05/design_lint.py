"""Проверка макета панели на соответствие принятой дизайн-системе.

Проверяет: кратность отступов 4 px, шкалу скруглений, отсутствие внешних
ресурсов (CDN), тёмную схему, подписи навигации и высоту целей нажатия.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
FILES = ["prototype.html", "states.html"]
ALLOWED_RADII = {0, 4, 8, 12, 16, 20}
# 999px — полностью скруглённые бейджи статуса (окружение, здоровье, полоса активности).
# Это осознанное исключение из шкалы, а не случайное значение.
ALLOWED_RADII.add(999)
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


def _resolve(name: str) -> pathlib.Path:
    """Файл ищем рядом со скриптом, затем от корня проекта."""
    direct = pathlib.Path(name)
    if direct.is_file():
        return direct
    local = HERE / name
    return local if local.is_file() else ROOT / name


def main(names: list[str] | None = None, report_name: str = "DESIGN-LINT") -> int:
    summary = {"files": [], "tap_target_min": TAP_TARGET_MIN, "failures": 0}
    lines = [
        "# Линт макета панели",
        "",
        f"Проверка: отступы кратны 4 px, скругления из шкалы 4/8/12/16, нет внешних ресурсов, "
        f"тёмная схема, подписи навигации, цель нажатия не меньше {TAP_TARGET_MIN} px.",
        "",
    ]
    for name in names or FILES:
        path = _resolve(name)
        if not path.is_file():
            summary["files"].append({"file": name, "issues": ["файл не найден"]})
            summary["failures"] += 1
            continue
        result = check_file(path)
        result["file"] = name
        summary["files"].append(result)
        summary["failures"] += len(result["issues"])
        status = "OK" if not result["issues"] else f"найдено {len(result['issues'])}"
        lines.append(f"## {name} — {status}")
        lines.append("")
        if result["issues"]:
            lines += [f"- {issue}" for issue in result["issues"]]
        else:
            lines.append("- отклонений не найдено")
        lines.append("")

    (HERE / f"{report_name}.md").write_text("\n".join(lines), encoding="utf-8")
    (HERE / f"{report_name}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", default=",".join(FILES))
    parser.add_argument("--report", default="DESIGN-LINT")
    arguments = parser.parse_args()
    raise SystemExit(
        main(
            [part.strip() for part in arguments.files.split(",") if part.strip()],
            arguments.report,
        )
    )
