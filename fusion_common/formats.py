from __future__ import annotations

import csv
import io
import json
from io import StringIO, TextIOWrapper
from pathlib import Path
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd

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

FUS_EXT_FILTER = "Файлы измерений Fusion (*.fus)"
ALL_SUPPORTED_FILTER = (
    "Все поддерживаемые измерения (*.fus *.vtaz *.tda *.fus-mdt *.fus-fmdt)"
)


def save_fus(record: MeasurementRecord, path: Path) -> None:
    """Сохраняет измерение в единый расширяемый по OCP контейнер .fus (v1.0)."""
    df = record.to_dataframe()
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)
    manifest_json = record.manifest.model_dump_json(indent=2)

    with ZipFile(path, "w", ZIP_DEFLATED) as zipf:
        zipf.writestr("manifest.json", manifest_json.encode("utf-8"))
        zipf.writestr("data.csv", csv_buf.getvalue().encode("utf-8"))


def load_fus(path: Path) -> MeasurementRecord:
    """Загружает измерение из контейнера .fus с автоматической миграцией схемы."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        raw_dict = json.loads(zipf.read("manifest.json").decode("utf-8"))
        migrated_dict = migrate_manifest_dict(raw_dict)
        manifest = MeasurementManifest.model_validate(migrated_dict)
        with zipf.open("data.csv", "r") as bf:
            df = pd.read_csv(bf, sep=",", decimal=".")
    data = {str(col): df[col].to_numpy(dtype=float) for col in df.columns}
    return MeasurementRecord(
        manifest=manifest,
        data=data,
        source_path=path,
        source_format="FUS",
        source_version=manifest.schema_version,
    )


def _read_vtaz_csv(text_stream: io.TextIOBase) -> tuple[np.ndarray, np.ndarray]:
    content = text_stream.read()
    lines = [line.removesuffix(";") for line in content.strip().splitlines() if line.strip()]
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
            cal_type: Literal["linear", "quadratic"] = (
                "quadratic" if cal_type_raw == "quadratic" else "linear"
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
    df = pd.read_csv(
        StringIO("".join(lines_data)),
        sep=" ",
        header=None,
        usecols=[0, 1],
        decimal=",",
        names=["emf", "time_days"],
    )
    time_arr = (df["time_days"] * 86400.0).to_numpy(dtype=float)
    emf_arr = df["emf"].to_numpy(dtype=float)

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
    """Читает архивные файлы ДТА .fus-mdt (v1.0) с игнорированием устаревшей чувствительности."""
    with ZipFile(path, "r", ZIP_DEFLATED) as zipf:
        names = set(zipf.namelist())
        if "manifest.json" in names:
            return load_fus(path)
        version_str = zipf.read("VERSION").decode("utf-8").strip()
        with zipf.open("data.csv", "r") as bf:
            df = pd.read_csv(bf, sep=",", decimal=".")

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
                    cal_temp_coeffs = [float(v) for v in np.atleast_1d(arr).tolist()]

    time_arr = df["Time/s"].to_numpy(dtype=float)
    t1_arr = df["T1/mV"].to_numpy(dtype=float)
    t2_arr = df["T2/mV"].to_numpy(dtype=float)
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
            df = pd.read_csv(bf, sep=",", decimal=".")

    channels: list[ChannelDescriptor] = []
    data_map: dict[str, np.ndarray] = {}
    for col in df.columns:
        col_str = str(col)
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
        data_map[norm_key] = df[col].to_numpy(dtype=float)

    return MeasurementRecord(
        manifest=MeasurementManifest(channels=channels),
        data=data_map,
        source_path=path,
        source_format="FUS-FMDT",
        source_version=version_str,
    )


def load_measurement(path: Path) -> MeasurementRecord:
    """Универсальная точка входа для загрузки любого поддерживаемого файла измерений."""
    suffix = path.suffix.lower()
    if suffix == ".tda":
        return load_tda(path)
    if suffix == ".vtaz":
        return load_vtaz(path)
    if suffix == ".fus-mdt":
        return load_fus_mdt(path)
    if suffix == ".fus-fmdt":
        return load_fus_fmdt(path)
    if suffix == ".fus":
        return load_fus(path)
    raise ValueError(f"Неподдерживаемый формат файла измерения: {suffix}")
