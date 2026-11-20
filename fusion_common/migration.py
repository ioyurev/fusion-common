from __future__ import annotations

import re
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

from loguru import logger as log
from pydantic import ValidationError

from fusion_common.models import MeasurementManifest

CURRENT_SCHEMA_VERSION = "1.0"
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")

MigrationFunc = Callable[[dict[str, Any]], dict[str, Any]]


class IncompatibleFileVersionError(ValueError):
    """Файл создан более новой версией Fusion и не поддерживается текущей сборкой."""


def _parse_version_tuple(ver_str: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.match(ver_str)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(file_ver: str, current_ver: str) -> bool:
    """Проверяет, является ли семантическая версия file_ver строго новее current_ver."""
    parsed_file = _parse_version_tuple(file_ver)
    parsed_curr = _parse_version_tuple(current_ver)
    if parsed_file is None or parsed_curr is None:
        return False
    width = max(len(parsed_file), len(parsed_curr))
    f_pad = parsed_file + (0,) * (width - len(parsed_file))
    c_pad = parsed_curr + (0,) * (width - len(parsed_curr))
    return f_pad > c_pad


def _current_common_version() -> str:
    try:
        return pkg_version("fusion-common")
    except PackageNotFoundError:
        return ""


def _format_software_hint(data: dict[str, Any]) -> str:
    parts: list[str] = []
    sw_ver = str(data.get("software_version") or "").strip()
    common_ver = str(data.get("common_version") or "").strip()
    if sw_ver:
        parts.append(f"ПО v{sw_ver.lstrip('v')}")
    if common_ver:
        parts.append(f"fusion-common v{common_ver.lstrip('v')}")
    return f" ({', '.join(parts)})" if parts else ""


def _migrate_0_9_to_1_0(raw: dict[str, Any]) -> dict[str, Any]:
    updated = dict(raw)
    updated["schema_version"] = "1.0"
    updated.setdefault("channels", [])
    return updated


# Реестр пошаговых миграций: старая_версия -> (новая_версия, функция_миграции)
MIGRATIONS: dict[str, tuple[str, MigrationFunc]] = {
    "0.9": ("1.0", _migrate_0_9_to_1_0),
}


def migrate_manifest_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """Приводит сырой словарь manifest.json к актуальной версии схемы на границе загрузки."""
    data = dict(raw)
    version = str(data.get("schema_version", CURRENT_SCHEMA_VERSION))
    visited: set[str] = set()

    while version != CURRENT_SCHEMA_VERSION:
        if version in visited:
            raise ValueError(f"Обнаружен цикл миграции схемы для версии {version}")
        visited.add(version)
        if version not in MIGRATIONS:
            if is_newer_version(version, CURRENT_SCHEMA_VERSION):
                sw_hint = _format_software_hint(data)
                raise IncompatibleFileVersionError(
                    f"Неподдерживаемая версия схемы .fus: '{version}' "
                    f"(текущая поддерживаемая версия: '{CURRENT_SCHEMA_VERSION}'). "
                    f"Файл создан в более новой версии Fusion{sw_hint}. "
                    "Обновите программу до последней версии."
                )
            raise ValueError(
                f"Неподдерживаемая версия схемы .fus: '{version}' "
                f"(текущая поддерживаемая версия: '{CURRENT_SCHEMA_VERSION}')"
            )
        next_version, migrator = MIGRATIONS[version]
        data = migrator(data)
        data["schema_version"] = next_version
        version = next_version

    return data


def validate_manifest_dict(
    raw: dict[str, Any],
    *,
    current_common_version: str | None = None,
) -> MeasurementManifest:
    """Мигрирует и валидирует manifest.json с диагностикой файлов из более новых версий ПО."""
    migrated = migrate_manifest_dict(raw)
    cur_common = (
        current_common_version
        if current_common_version is not None
        else _current_common_version()
    )
    file_common = str(migrated.get("common_version") or "").strip()
    file_sw = str(migrated.get("software_version") or "").strip()

    try:
        manifest = MeasurementManifest.model_validate(migrated)
    except ValidationError as exc:
        if file_common and cur_common and is_newer_version(file_common, cur_common):
            sw_part = f", приложение v{file_sw.lstrip('v')}" if file_sw else ""
            raise IncompatibleFileVersionError(
                "Не удалось прочитать файл: он создан в более новой версии Fusion "
                f"(fusion-common v{file_common.lstrip('v')}{sw_part}; "
                f"установлена библиотека v{cur_common.lstrip('v')}). "
                "Обновите программу до последней версии."
            ) from exc
        raise

    if file_common and cur_common and is_newer_version(file_common, cur_common):
        log.bind(
            file_common_version=file_common,
            current_common_version=cur_common,
            software_version=file_sw,
        ).warning("Opened file created by a newer fusion-common version")
    return manifest
