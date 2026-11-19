# Инструкции для Fusion Common

## Контроль качества и команды разработки

Все команды выполняются из корня `fusion-common`:

- Обязательные проверки качества:
  - `uv run ruff check . --fix`
  - `PYTHONUTF8=1 uv run mypy . --pretty`
  - `uv run vulture`
  - `uv run pytest`

## Архитектурные правила

1. Пакет `fusion_common` содержит общее функциональное и сериализационное ядро (`Functional Core` на чистом `numpy` и `pydantic` без `pandas`), а также общие утилиты сплэш-экрана (`splash.py`) и сборки (`packaging.py`).
2. Контейнеры сырых измерений `.fus` и результатов анализа `.fusa` (схема v1.0 с граничной миграцией и валидацией `validate_manifest_dict` / `IncompatibleFileVersionError` / `is_newer_version` в `migration.py`) следуют принципу открытости/закрытости (OCP): датчики (`sensor`), компенсация холодного спая (`cjc`), калибровки (`calibration`) и метаданные канала (`metadata`, включая `exo_up`) привязаны к каждому отдельному каналу `ChannelDescriptor`. Даунгрейд-миграции для более новых схем запрещены; `load_fus` возвращает понятный `ValueError` на битых ZIP-архивах и выставляет `source_format="FUSA"` для `.fusa`.
3. В `fusion_common` входят только сущности, используемые одновременно и в `fusion-collection`, и в `fusion-analysis`: кодек базового контейнера `.fus` (`load_fus`, `save_fus`, `parse_csv_columns`, `FUS_EXT_FILTER`), модели термопар ГОСТ и калибровок для `ChannelDescriptor` и общие утилиты сборки/сплэша/документации.
4. **Сквозная нумерация логов (`splash.py`):** `allocate_log_file` присваивает каждому запуску монотонный порядковый номер (`00001_YYYY-MM-DD_HH-MM-SS.log` через `.log_seq` с восстановлением по именам файлов) и ограничивает хранение последними `200` файлами по номеру вместо очистки по системным часам.
