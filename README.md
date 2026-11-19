# Fusion Common

<p align="center">
  <img src="assets/icon.svg" width="128" alt="Fusion Common Icon">
</p>

Общая библиотека доменных моделей, эталонных термопар ГОСТ Р 8.585-2001, калибровок температуры, файловых кодеков и сборочных утилит для экосистемы термического анализа **Fusion** (`fusion-collection`, `fusion-analysis`).

## Возможности

- **По-канальный кодек контейнеров `.fus` и `.fusa` (v1.0):** единое чтение и запись (`load_fus`, `save_fus`, `parse_csv_columns`) для неприкосновенных сырых измерений (`.fus`) и проектов анализа (`.fusa`) с пограничной миграцией и диагностикой версий (`validate_manifest_dict`, `IncompatibleFileVersionError`, `is_newer_version`).
- **Полиморфные модели термопар по ГОСТ Р 8.585-2001 (`SensorSpec`):** встроенный набор всех 12 стандартных типов термопар (`make_gost_thermocouples`: ТВР А-1/А-2/А-3, ТХА K, ТПП S/R, ТПР B, ТНН N, ТЖК J, ТХКн E, ТМК T, ТХК L) и компенсация холодного спая (`CjcData`).
- **Модели калибровки температуры (`CalibrationSpec`):** нулевая, аддитивная полиномиальная и кусочно-линейная калибровка по реперам со статистикой ($R^2$, $\text{SEC}$, $U = 2\cdot\text{SEC}$), прямая полиномиальная и таблично-сплайновая калибровки.
- **Общие утилиты интерфейса, логирования и сборки:** сквозная нумерация логов (`setup_fusion_logging`, `allocate_log_file`), сплэш-экран и иконки (`fusion-splash`), сборка HTML-справки (`fusion-docs`) и упаковка дистрибутивов/инсталляторов (`fusion-packaging`).

## Требования и проверка

- Python >= 3.13, менеджер пакетов `uv`
- Запуск тестов: `uv run pytest`

## Лицензия

Проект распространяется на условиях лицензии **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)** (см. [LICENSE.md](LICENSE.md) и [LICENSE.ru.md](LICENSE.ru.md)).
