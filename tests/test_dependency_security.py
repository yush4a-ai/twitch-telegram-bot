"""Пины зависимостей: не ниже версий, где закрыты известные уязвимости.

Проверка держит два уровня: то, что записано в requirements.txt (это попадёт в сборку),
и то, что реально установлено в текущем окружении (иначе тесты гоняются на другой версии,
чем поедет в прод).
"""
import re
from importlib import metadata
from pathlib import Path

import pytest

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"

# Минимальные безопасные версии. Источники:
# - streamlink 8.6.0: CVE-2026-92164 / GHSA-vf2x-4v53-pm7v — HTTPSession следует
#   редиректу на file:// и отдаёт содержимое локального файла
#   (https://security-tracker.debian.org/tracker/CVE-2026-92164, fixed in 8.6.0).
# - aiohttp 3.14.3: CVE-2026-69243 (smuggling), CVE-2026-69244, CVE-2026-59881.
# - cryptography 50.0.0: CVE-2026-69247 (Bleichenbacher в pkcs7).
# - urllib3 2.7.0: CVE-2026-44431 / PYSEC-2026-141 (утечка заголовков при редиректе).
MINIMUM_SAFE = {
    "streamlink": (8, 6, 0),
    "aiohttp": (3, 14, 3),
    "cryptography": (50, 0, 0),
    "urllib3": (2, 7, 0),
}


def version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value.split("+")[0])[:3]
    return tuple(int(part) for part in parts)


def pinned_versions() -> dict[str, str]:
    pins = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_requirements_are_pinned_exactly():
    """Диапазоны делают сборку невоспроизводимой, поэтому пины строгие."""
    pins = pinned_versions()
    assert pins, "requirements.txt не содержит строгих пинов"
    for name, version in pins.items():
        assert version_tuple(version), f"не разобрать версию {name}=={version}"


@pytest.mark.parametrize("name,minimum", sorted(MINIMUM_SAFE.items()))
def test_pinned_version_is_not_below_the_safe_one(name, minimum):
    pins = pinned_versions()
    assert name in pins, f"{name} пропал из requirements.txt"
    assert version_tuple(pins[name]) >= minimum, (
        f"{name}=={pins[name]} ниже безопасной версии "
        f"{'.'.join(str(part) for part in minimum)}"
    )


@pytest.mark.parametrize("name,minimum", sorted(MINIMUM_SAFE.items()))
def test_installed_version_is_not_below_the_safe_one(name, minimum):
    """Тесты должны идти на той же версии, что попадёт в сборку."""
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{name} не установлен в этом окружении")
    assert version_tuple(installed) >= minimum, (
        f"установлен {name}=={installed}, а нужен не ниже "
        f"{'.'.join(str(part) for part in minimum)}"
    )
