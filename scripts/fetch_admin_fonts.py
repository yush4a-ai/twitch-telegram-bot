"""Скачивает локальные субсеты шрифтов для панели владельца.

Панель не должна тянуть ресурсы из интернета в рантайме, поэтому шрифты
складываются в репозиторий один раз. Берём только нужные начертания и только
кириллицу с латиницей: полный вариативный файл весит сотни килобайт, субсет —
десятки. Лицензии гарнитур лежат рядом в `bot/admin_ui/fonts/`.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "bot" / "admin_ui" / "fonts"
CSS_PATH = ROOT / "bot" / "admin_ui" / "fonts.css"
LAB_DIR = ROOT / "docs" / "design" / "admin-redesign-2026-10-05" / "variants-v2" / "fonts3"
LAB_CSS = ROOT / "docs" / "design" / "admin-redesign-2026-10-05" / "variants-v2" / "lab.css"

# Гарнитура -> (слаг файла, нужные начертания). Выбор владельца 05.10.2026:
# Rubik для интерфейса и заголовков, IBM Plex Mono для чисел и машинных подписей.
FAMILIES = {
    "Rubik": ("rubik", (400, 500, 700)),
    "IBM Plex Mono": ("ibm-plex-mono", (500,)),
}
# Кандидаты для подбора: только гарнитуры с кириллицей.
LAB_FAMILIES = {
    "Unbounded": ("unbounded", (500,)),
    "Onest": ("onest", (400, 600)),
    "JetBrains Mono": ("jetbrains-mono", (500,)),
    "Rubik": ("rubik", (400, 700)),
    "Play": ("play", (400, 700)),
    "Golos Text": ("golos-text", (400, 700)),
    "Wix Madefor Display": ("wix-madefor-display", (700,)),
    "Wix Madefor Text": ("wix-madefor-text", (400,)),
    "IBM Plex Mono": ("ibm-plex-mono", (500,)),
    "Martian Mono": ("martian-mono", (500,)),
    "Manrope": ("manrope", (400, 700)),
    "Montserrat": ("montserrat", (700,)),
    "Inter Tight": ("inter-tight", (400, 600)),
    "Oswald": ("oswald", (500,)),
}
SUBSETS = ("cyrillic", "latin")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BLOCK = re.compile(r"/\*\s*([\w\-\[\]]+)\s*\*/\s*@font-face\s*\{(.*?)\}", re.S)


def _css_url(family: str, weight: int) -> str:
    name = family.replace(" ", "+")
    return f"https://fonts.googleapis.com/css2?family={name}:wght@{weight}&display=swap"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=40) as response:  # noqa: S310 - адрес наш, не пользовательский
        return response.read()


def _field(block: str, name: str) -> str:
    match = re.search(rf"{name}:\s*([^;]+);", block)
    return match.group(1).strip() if match else ""


def download(families: dict, target_dir: pathlib.Path, css_path: pathlib.Path, url_prefix: str) -> int:
    target_dir.mkdir(parents=True, exist_ok=True)
    faces: list[str] = []
    saved = 0
    for family, (slug, weights) in families.items():
        for weight in weights:
            css = _get(_css_url(family, weight)).decode("utf-8")
            for subset, block in BLOCK.findall(css):
                if subset not in SUBSETS:
                    continue
                source = re.search(r"url\((https://[^)]+)\)", block)
                if not source:
                    continue
                target = target_dir / f"{slug}-{weight}-{subset}.woff2"
                target.write_bytes(_get(source.group(1)))
                saved += 1
                faces.append(
                    "@font-face {\n"
                    f'  font-family: "{family}";\n'
                    "  font-style: normal;\n"
                    f"  font-weight: {weight};\n"
                    "  font-display: swap;\n"
                    f'  src: url("{url_prefix}{target.name}") format("woff2");\n'
                    f"  unicode-range: {_field(block, 'unicode-range')};\n"
                    "}\n"
                )
    header = (
        "/* Локальные субсеты шрифтов панели. Сгенерировано scripts/fetch_admin_fonts.py.\n"
        "   Кириллица и латиница, только используемые начертания. Лицензии — в fonts/. */\n\n"
    )
    css_path.write_text(header + "\n".join(faces), encoding="utf-8")
    return saved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lab", action="store_true", help="набор кандидатов для подбора гарнитур")
    arguments = parser.parse_args()
    if arguments.lab:
        saved = download(LAB_FAMILIES, LAB_DIR, LAB_CSS, "fonts3/")
        print(f"кандидатов: {saved} файлов")
        print(f"стили: {LAB_CSS.relative_to(ROOT)}")
        return 0 if saved else 1
    saved = download(FAMILIES, FONT_DIR, CSS_PATH, "/admin/fonts/")
    print(f"файлов шрифтов: {saved}")
    print(f"стилей: {CSS_PATH.relative_to(ROOT)}")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
