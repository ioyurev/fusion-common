from __future__ import annotations

import csv
import io
import json
from io import StringIO, TextIOWrapper
from pathlib import Path
from typing import IO
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import numpy as np

from fusion_common.migration import validate_manifest_dict
from fusion_common.models import MeasurementRecord

FUS_EXT_FILTER = "Файлы сырых измерений Fusion (*.fus)"


def _serialize_csv(data: dict[str, np.ndarray], columns: list[str]) -> str:
    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    if columns:
        arrays = [data[col].tolist() for col in columns]
        for row in zip(*arrays, strict=True):
            writer.writerow(row)
    return buf.getvalue()


def parse_csv_columns(text_stream: io.TextIOBase) -> dict[str, np.ndarray]:
    reader = csv.reader(text_stream)
    header = next(reader, None)
    if not header:
        return {}
    col_names = [col.strip() for col in header]
    buckets: list[list[float]] = [[] for _ in col_names]
    for row in reader:
        if not row:
            continue
        for b_list, val_str in zip(buckets, row, strict=False):
            b_list.append(float(val_str.strip()))
    return {
        name: np.array(vals, dtype=float)
        for name, vals in zip(col_names, buckets, strict=True)
    }


def save_fus(record: MeasurementRecord, path: Path | IO[bytes]) -> None:
    """Сохраняет измерение в контейнер .fus / .fusa (по пути Path или в открытый IO[bytes])."""
    cols = record.ordered_columns()
    csv_text = _serialize_csv(record.data, cols)
    manifest_json = record.manifest.model_dump_json(indent=2)

    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(path, "w", ZIP_DEFLATED) as zipf:
            zipf.writestr("manifest.json", manifest_json.encode("utf-8"))
            zipf.writestr("data.csv", csv_text.encode("utf-8"))
        record.source_path = path
        record.source_format = "FUSA" if path.suffix.lower() == ".fusa" else "FUS"
        return

    path.seek(0)
    path.truncate(0)
    with ZipFile(path, "w", ZIP_DEFLATED) as zipf:
        zipf.writestr("manifest.json", manifest_json.encode("utf-8"))
        zipf.writestr("data.csv", csv_text.encode("utf-8"))
    path.flush()


def load_fus(path: Path) -> MeasurementRecord:
    """Загружает измерение из контейнера .fus с миграцией схемы и диагностикой ошибок."""
    try:
        with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
            try:
                manifest_bytes = zipf.read("manifest.json")
            except KeyError as exc:
                raise ValueError(
                    f"Файл '{path.name}' повреждён: внутри контейнера .fus отсутствует manifest.json"
                ) from exc
            try:
                raw_dict = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Файл '{path.name}' повреждён: некорректный manifest.json ({exc})"
                ) from exc
            manifest = validate_manifest_dict(raw_dict)
            try:
                with zipf.open("data.csv", "r") as bf:
                    text_f = TextIOWrapper(bf, encoding="utf-8")
                    data = parse_csv_columns(text_f)
            except KeyError as exc:
                raise ValueError(
                    f"Файл '{path.name}' повреждён: внутри контейнера .fus отсутствует data.csv"
                ) from exc
    except BadZipFile as exc:
        raise ValueError(
            f"Файл '{path.name}' повреждён или запись измерения не была завершена "
            "(некорректный ZIP-контейнер .fus)"
        ) from exc
    return MeasurementRecord(
        manifest=manifest,
        data=data,
        source_path=path,
        source_format="FUS",
        source_version=manifest.schema_version,
    )
