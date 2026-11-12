from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from io import StringIO, TextIOWrapper
from pathlib import Path
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from fusion_common.calibration import (
    AdditivePolynomialCalibration,
    DirectPolynomialCalibration,
    Standard,
    evaluate_channel_temperature,
)
from fusion_common.migration import migrate_manifest_dict
from fusion_common.models import (
    ChannelDescriptor,
    MeasurementManifest,
    MeasurementRecord,
)
from fusion_common.thermometry import CjcData, PolynomialSensorModel

FUS_EXT_FILTER = "Файлы сырых измерений Fusion (*.fus)"
FUSA_EXT_FILTER = "Файлы анализа Fusion (*.fusa)"
ALL_SUPPORTED_FILTER = (
    "Все поддерживаемые файлы (*.fus *.fusa *.vtaz *.tda *.fus-mdt *.fus-fmdt)"
)


def _serialize_csv(data: dict[str, np.ndarray], columns: list[str]) -> str:
    buf = StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    if columns:
        arrays = [data[col].tolist() for col in columns]
        for row in zip(*arrays):
            writer.writerow(row)
    return buf.getvalue()


def _parse_csv_columns(text_stream: io.TextIOBase) -> dict[str, np.ndarray]:
    reader = csv.reader(text_stream)
    header = next(reader, None)
    if not header:
        return {}
    col_names = [col.strip() for col in header]
    buckets: list[list[float]] = [[] for _ in col_names]
    for row in reader:
        if not row:
            continue
        for b_list, val_str in zip(buckets, row):
            b_list.append(float(val_str.strip()))
    return {
        name: np.array(vals, dtype=float)
        for name, vals in zip(col_names, buckets)
    }


def save_fus(record: MeasurementRecord, path: Path) -> None:
    """Сохраняет измерение в контейнер .fus (или проект анализа в .fusa)."""
    cols = record.ordered_columns()
    csv_text = _serialize_csv(record.data, cols)
    manifest_json = record.manifest.model_dump_json(indent=2)

    with ZipFile(path, "w", ZIP_DEFLATED) as zipf:
        zipf.writestr("manifest.json", manifest_json.encode("utf-8"))
        zipf.writestr("data.csv", csv_text.encode("utf-8"))
    record.source_path = path
    record.source_format = (
        "FUSA" if path.suffix.lower() == ".fusa" else "FUS"
    )


def save_fusa(record: MeasurementRecord, path: Path) -> Path:
    """Сохраняет результаты анализа в отдельный файл .fusa без перезаписи сырого файла."""
    target = path if path.suffix.lower() == ".fusa" else path.with_suffix(".fusa")
    save_fus(record, target)
    return target


def load_fus(path: Path) -> MeasurementRecord:
    """Загружает измерение из контейнера .fus с автоматической миграцией схемы."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        raw_dict = json.loads(zipf.read("manifest.json").decode("utf-8"))
        migrated_dict = migrate_manifest_dict(raw_dict)
        manifest = MeasurementManifest.model_validate(migrated_dict)
        with zipf.open("data.csv", "r") as bf:
            text_f = TextIOWrapper(bf, encoding="utf-8")
            data = _parse_csv_columns(text_f)
    return MeasurementRecord(
        manifest=manifest,
        data=data,
        source_path=path,
        source_format="FUS",
        source_version=manifest.schema_version,
    )


def _read_vtaz_csv(text_stream: io.TextIOBase) -> tuple[np.ndarray, np.ndarray]:
    content = text_stream.read()
    lines = [
        line.removesuffix(";")
        for line in content.strip().splitlines()
        if line.strip()
    ]
    reader = csv.reader(lines)
    next(reader, None)
    x_list: list[float] = []
    y_list: list[float] = []
    for row in reader:
        if len(row) >= 2:
            x_val, y_val, *_ = row
            x_list.append(float(x_val))
            y_list.append(float(y_val))
    return np.array(x_list, dtype=float), np.array(y_list, dtype=float)


def load_vtaz(path: Path) -> MeasurementRecord:
    """Читает архивные файлы .vtaz версий 0.0 и 1.0+."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        names = set(zipf.namelist())
        if "manifest.json" in names:
            return load_fus(path)
        meta = json.loads(zipf.read("metadata.json").decode("utf-8"))
        vtaz_ver = str(meta.get("vtaz_version", "0.0"))
        ver_parts = vtaz_ver.split(".")
        first_two = ver_parts[:2]
        ver_num = float(".".join(first_two) if len(first_two) == 2 else vtaz_ver)

        with zipf.open("data_input.csv", "r") as bf:
            text_f = TextIOWrapper(bf, encoding="utf-8")
            time_arr, emf_arr = _read_vtaz_csv(text_f)  # noqa: PU001

        channels: list[ChannelDescriptor] = [
            ChannelDescriptor(column="time", label="Время", unit="s"),
            ChannelDescriptor(column="emf", label="ЭДС", unit="mV"),
        ]
        data_map: dict[str, np.ndarray] = {"time": time_arr, "emf": emf_arr}

        if ver_num >= 1.0:
            cal_raw = json.loads(zipf.read("calibration.json").decode("utf-8"))
            cal_type_raw = cal_raw.get("calibration_type", "linear")
            cal_type: Literal["offset", "linear", "quadratic", "piecewise"] = (
                cal_type_raw
                if cal_type_raw in ("offset", "linear", "quadratic", "piecewise")
                else "linear"
            )
            standards = [
                Standard.model_validate(s) for s in cal_raw.get("standards", [])
            ]
            cal_spec = AdditivePolynomialCalibration(
                name=str(cal_raw.get("name", "")),
                description=str(cal_raw.get("description", "")),
                calibration_type=cal_type,
                coefficients=list(cal_raw.get("coefficients", [0.0, 0.0, 0.0])),
                standards=standards,
            )
            tc_raw = json.loads(zipf.read("thermocouple.json").decode("utf-8"))
            tc_spec = PolynomialSensorModel(
                name="VTA Thermocouple",
                coefficients=list(tc_raw["thermocouple_coefficients"]),
            )
            cjc_raw = json.loads(zipf.read("cjc.json").decode("utf-8"))
            cjc_spec = CjcData(
                temperature=float(cjc_raw.get("temperature", 25.0)),
                e_cold=float(cjc_raw.get("e_cold", 0.0)),
            )
            temp_arr = np.asarray(
                evaluate_channel_temperature(emf_arr, tc_spec, cjc_spec, cal_spec),
                dtype=float,
            )
            data_map["temperature"] = temp_arr
            channels.append(
                ChannelDescriptor(
                    column="temperature",
                    label="Температура",
                    unit="°C",
                    source_columns=["emf"],
                    sensor=tc_spec,
                    cjc=cjc_spec,
                    calibration=cal_spec,
                )
            )
        elif "calibration.json" in names:
            cal0 = json.loads(zipf.read("calibration.json").decode("utf-8"))
            coeffs_desc = [
                float(cal0["c3"]),
                float(cal0["c2"]),
                float(cal0["c1"]),
                float(cal0["c0"]),
            ]
            direct_cal = DirectPolynomialCalibration(coefficients_desc=coeffs_desc)
            temp_arr = np.asarray(direct_cal.apply(emf_arr), dtype=float)
            data_map["temperature"] = temp_arr
            channels.append(
                ChannelDescriptor(
                    column="temperature",
                    label="Температура",
                    unit="°C",
                    source_columns=["emf"],
                    calibration=direct_cal,
                )
            )

    manifest = MeasurementManifest(
        sample=str(meta.get("sample", "")),
        operator=str(meta.get("operator", "")),
        channels=channels,
    )
    return MeasurementRecord(
        manifest=manifest,
        data=data_map,
        source_path=path,
        source_format="VTAZ",
        source_version=vtaz_ver,
    )


def load_tda(path: Path) -> MeasurementRecord:
    """Читает архивные файлы VPTAnalyzer (.tda в кодировке cp1251)."""
    lines_raw = path.read_text(encoding="cp1251").splitlines(keepends=True)
    sample_name = ""
    operator = ""
    coeff_strs: list[str] = []
    start_idx = len(lines_raw)

    for idx, line in enumerate(lines_raw):
        stripped = line.rstrip("\n\r")
        if not stripped:
            continue
        if stripped.startswith("<"):
            if stripped.startswith("<NAME>"):
                sample_name = stripped[7:]
            elif stripped.startswith("<AUTOR>"):
                operator = stripped[8:]
            elif stripped.startswith("<FORMULE>"):
                coeff_strs = stripped[12:].split()
        else:
            start_idx = idx
            break

    lines_data = lines_raw[start_idx:-1] if start_idx < len(lines_raw) else []
    emf_list: list[float] = []
    time_days_list: list[float] = []
    for row_line in lines_data:
        tokens = row_line.strip().split()
        if len(tokens) >= 2:
            e_tok, t_tok, *_ = tokens
            emf_list.append(float(e_tok.replace(",", ".")))
            time_days_list.append(float(t_tok.replace(",", ".")))

    time_arr = np.array(time_days_list, dtype=float) * 86400.0
    emf_arr = np.array(emf_list, dtype=float)

    channels: list[ChannelDescriptor] = [
        ChannelDescriptor(column="time", label="Время", unit="s"),
        ChannelDescriptor(column="emf", label="ЭДС", unit="mV"),
    ]
    data_map: dict[str, np.ndarray] = {"time": time_arr, "emf": emf_arr}

    if coeff_strs:
        try:
            coeffs_desc = [float(item.replace(",", ".")) for item in coeff_strs]
            direct_cal = DirectPolynomialCalibration(coefficients_desc=coeffs_desc)
            temp_arr = np.asarray(direct_cal.apply(emf_arr), dtype=float)
            data_map["temperature"] = temp_arr
            channels.append(
                ChannelDescriptor(
                    column="temperature",
                    label="Температура",
                    unit="°C",
                    source_columns=["emf"],
                    calibration=direct_cal,
                )
            )
        except ValueError:
            pass

    return MeasurementRecord(
        manifest=MeasurementManifest(
            sample=sample_name,
            operator=operator,
            channels=channels,
        ),
        data=data_map,
        source_path=path,
        source_format="TDA",
        source_version="legacy",
    )


def load_fus_mdt(path: Path) -> MeasurementRecord:
    """Читает архивные файлы ДТА .fus-mdt (v1.0)."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        names = set(zipf.namelist())
        if "manifest.json" in names:
            return load_fus(path)
        version_str = zipf.read("VERSION").decode("utf-8").strip()
        with zipf.open("data.csv", "r") as bf:
            text_f = TextIOWrapper(bf, encoding="utf-8")
            cols = _parse_csv_columns(text_f)

        info_dict: dict[str, str] = {}
        if "info.json" in names:
            try:
                info_dict = json.loads(zipf.read("info.json").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                info_dict = {}

        cal_temp_coeffs: list[float] | None = None
        if "calibration.fus-cal" in names:
            cal_bytes = io.BytesIO(zipf.read("calibration.fus-cal"))
            with ZipFile(cal_bytes, "r") as cal_zip:
                if "coeff_temp.csv" in cal_zip.namelist():
                    arr = np.loadtxt(cal_zip.open("coeff_temp.csv"), dtype=float)
                    cal_temp_coeffs = [
                        float(v) for v in np.atleast_1d(arr).tolist()
                    ]

    time_arr = cols["Time/s"]
    t1_arr = cols["T1/mV"]
    t2_arr = cols["T2/mV"]
    dta_arr = t1_arr - t2_arr

    channels: list[ChannelDescriptor] = [
        ChannelDescriptor(column="time", label="Время", unit="s"),
        ChannelDescriptor(column="emf_1", label="Термопара 1", unit="mV"),
        ChannelDescriptor(column="emf_2", label="Термопара 2", unit="mV"),
        ChannelDescriptor(
            column="dta",
            label="ДТА",
            unit="mV",
            source_columns=["emf_1", "emf_2"],
            metadata={"exo_up": True},
        ),
    ]
    data_map: dict[str, np.ndarray] = {
        "time": time_arr,
        "emf_1": t1_arr,
        "emf_2": t2_arr,
        "dta": dta_arr,
    }

    if cal_temp_coeffs:
        direct_cal = DirectPolynomialCalibration(coefficients_desc=cal_temp_coeffs)
        temp_arr = np.asarray(direct_cal.apply(t1_arr), dtype=float)
        data_map["temperature"] = temp_arr
        channels.append(
            ChannelDescriptor(
                column="temperature",
                label="Температура",
                unit="°C",
                source_columns=["emf_1"],
                calibration=direct_cal,
            )
        )

    return MeasurementRecord(
        manifest=MeasurementManifest(
            sample=str(info_dict.get("sample", "")),
            operator=str(info_dict.get("operator", "")),
            comment=str(info_dict.get("comment", "")),
            channels=channels,
        ),
        data=data_map,
        source_path=path,
        source_format="FUS-MDT",
        source_version=version_str,
    )


def load_fus_fmdt(path: Path) -> MeasurementRecord:
    """Читает архивные отфильтрованные файлы .fus-fmdt (v1.0)."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        if "manifest.json" in zipf.namelist():
            return load_fus(path)
        version_str = zipf.read("VERSION").decode("utf-8").strip()
        with zipf.open("data.csv", "r") as bf:
            text_f = TextIOWrapper(bf, encoding="utf-8")
            cols = _parse_csv_columns(text_f)

    channels: list[ChannelDescriptor] = []
    data_map: dict[str, np.ndarray] = {}
    for col_str, arr in cols.items():
        if "/" in col_str:
            name_part, unit_part = col_str.split("/", maxsplit=1)  # noqa: PU001
        else:
            name_part, unit_part = col_str, ""
        norm_key = (
            "time"
            if name_part == "Time"
            else ("temperature" if name_part == "Temp." else name_part.lower())
        )
        meta = {"exo_up": True} if norm_key == "dta" else {}
        channels.append(
            ChannelDescriptor(
                column=norm_key,
                label=name_part,
                unit=unit_part,
                metadata=meta,
            )
        )
        data_map[norm_key] = arr

    return MeasurementRecord(
        manifest=MeasurementManifest(channels=channels),
        data=data_map,
        source_path=path,
        source_format="FUS-FMDT",
        source_version=version_str,
    )


FormatLoader = Callable[[Path], MeasurementRecord]

FORMAT_LOADERS: dict[str, FormatLoader] = {
    ".tda": load_tda,
    ".vtaz": load_vtaz,
    ".fus-mdt": load_fus_mdt,
    ".fus-fmdt": load_fus_fmdt,
    ".fus": load_fus,
    ".fusa": load_fus,
}


def register_format_loader(extension: str, loader: FormatLoader) -> None:
    """Регистрирует загрузчик для нового файлового расширения без изменения диспетчера (OCP)."""
    norm_ext = (
        extension.lower()
        if extension.startswith(".")
        else f".{extension.lower()}"
    )
    FORMAT_LOADERS[norm_ext] = loader


def load_measurement(path: Path) -> MeasurementRecord:
    """Универсальная точка входа для загрузки любого зарегистрированного формата измерений."""
    suffix = path.suffix.lower()
    loader = FORMAT_LOADERS.get(suffix)
    if loader is None:
        raise ValueError(f"Неподдерживаемый формат файла измерения: {suffix}")
    return loader(path)
