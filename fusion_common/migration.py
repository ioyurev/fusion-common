from __future__ import annotations

from collections.abc import Callable
from typing import Any

CURRENT_SCHEMA_VERSION = "1.0"

MigrationFunc = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_0_9_to_1_0(raw: dict[str, Any]) -> dict[str, Any]:
    updated = dict(raw)
    updated["schema_version"] = "1.0"
    updated.setdefault("channels", [])
    return updated


# Реестр пошаговых миграций: старая_версия -> (новая_версия, функция_миграции)
MIGRATIONS: dict[str, tuple[str, MigrationFunc]] = {
    "0.9": ("1.0", _migrate_0_9_to_1_0),
    "2.0": ("1.0", _migrate_0_9_to_1_0),
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
            raise ValueError(
                f"Неподдерживаемая версия схемы .fus: '{version}' "
                f"(текущая поддерживаемая версия: '{CURRENT_SCHEMA_VERSION}')"
            )
        next_version, migrator = MIGRATIONS[version]
        data = migrator(data)
        data["schema_version"] = next_version
        version = next_version

    return data
