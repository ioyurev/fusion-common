from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from loguru import logger as log
from pydantic import BaseModel, Field

from fusion_common.math_utils import bisection_method, eval_polynomial_asc

# Коэффициенты полинома T(E) = sum(c_i * E^i) для термопары Вольфрам-Рений (ВР)
DEFAULT_VR_COEFFICIENTS: list[float] = [
    0.9643027,
    79.495086,
    -4.9990310,
    0.634176,
    -0.047440967,
    0.0021811337,
    -5.8324228e-05,
    8.2433725e-07,
    -4.5928480e-09,
]

# Коэффициенты полинома T(E) = sum(c_i * E^i) для термопары Хромель-Алюмель (ХА / Type K, МЭК 60584, 0..1370 °C, E в мВ)
DEFAULT_XA_COEFFICIENTS: list[float] = [
    0.0,
    25.08355,
    0.07860106,
    -0.2503131,
    0.08315270,
    -0.01228034,
    0.0009804036,
    -4.413030e-05,
    1.057734e-06,
    -1.052755e-08,
]


class PolynomialSensorModel(BaseModel):
    """Полиномиальная модель термопары T(E) = c0 + c1*E + ... + ck*E^k."""

    kind: Literal["polynomial"] = "polynomial"
    name: str
    description: str = ""
    coefficients: list[float] = Field(min_length=1)
    emf_min_mv: float = -10.0
    emf_max_mv: float = 70.0

    def emf_to_temperature(self, emf: float | np.ndarray) -> float | np.ndarray:
        return eval_polynomial_asc(self.coefficients, emf)

    def temperature_to_emf(self, target_temp: float) -> float:
        return bisection_method(
            func=lambda e: float(self.emf_to_temperature(e)),
            target_value=target_temp,
            left=self.emf_min_mv,
            right=self.emf_max_mv,
        )


class PiecewisePolynomialSegment(BaseModel):
    """Один поддиапазон кусочно-полиномиальной градуировки."""

    emf_min_mv: float
    emf_max_mv: float
    coefficients: list[float] = Field(min_length=1)


class PiecewisePolynomialSensorModel(BaseModel):
    """Кусочно-полиномиальная модель термопары по поддиапазонам ЭДС."""

    kind: Literal["piecewise_polynomial"] = "piecewise_polynomial"
    name: str
    description: str = ""
    segments: list[PiecewisePolynomialSegment] = Field(min_length=1)
    emf_min_mv: float = -10.0
    emf_max_mv: float = 70.0

    def _eval_scalar(self, emf_val: float) -> float:
        first_seg, *_ = self.segments
        chosen = first_seg
        for seg in self.segments:
            chosen = seg
            if seg.emf_min_mv <= emf_val <= seg.emf_max_mv:
                break
        return float(eval_polynomial_asc(chosen.coefficients, emf_val))

    def emf_to_temperature(self, emf: float | np.ndarray) -> float | np.ndarray:
        if isinstance(emf, (int, float)):
            return self._eval_scalar(float(emf))
        arr = np.asarray(emf, dtype=float)
        return np.array([self._eval_scalar(float(v)) for v in arr], dtype=float)

    def temperature_to_emf(self, target_temp: float) -> float:
        return bisection_method(
            func=self._eval_scalar,
            target_value=target_temp,
            left=self.emf_min_mv,
            right=self.emf_max_mv,
        )


class LookupSplineSensorModel(BaseModel):
    """Таблично-интерполяционная (неполиномиальная) модель датчика."""

    kind: Literal["lookup_spline"] = "lookup_spline"
    name: str
    description: str = ""
    emf_points_mv: list[float] = Field(min_length=2)
    temp_points_c: list[float] = Field(min_length=2)

    def emf_to_temperature(self, emf: float | np.ndarray) -> float | np.ndarray:
        res = np.interp(emf, self.emf_points_mv, self.temp_points_c)
        if isinstance(emf, (int, float)):
            return float(res)
        return np.asarray(res, dtype=float)

    def temperature_to_emf(self, target_temp: float) -> float:
        return float(np.interp(target_temp, self.temp_points_c, self.emf_points_mv))


SensorSpec = Annotated[
    PolynomialSensorModel | PiecewisePolynomialSensorModel | LookupSplineSensorModel,
    Field(discriminator="kind"),
]


class CjcData(BaseModel):
    """Данные компенсации холодного спая для конкретного канала."""

    temperature: float = 25.0
    e_cold: float = 0.0

    @classmethod
    def from_sensor(cls, cjc_temp_c: float, sensor: SensorSpec) -> CjcData:
        e_cold = sensor.temperature_to_emf(cjc_temp_c)
        return cls(temperature=cjc_temp_c, e_cold=e_cold)

    def compensate(self, raw_emf: float | np.ndarray) -> float | np.ndarray:
        if isinstance(raw_emf, (int, float)):
            return float(raw_emf) + self.e_cold
        return np.asarray(raw_emf, dtype=float) + self.e_cold


def make_vr_thermocouple() -> PolynomialSensorModel:
    """Создаёт встроенный профиль вольфрам-рениевой термопары (ВР)."""
    return PolynomialSensorModel(
        name="ВР (Вольфрам-Рений)",
        description="Высокотемпературная вольфрам-рениевая термопара для ВТА",
        coefficients=list(DEFAULT_VR_COEFFICIENTS),
        emf_min_mv=-1.0,
        emf_max_mv=35.0,
    )


def make_xa_thermocouple() -> PolynomialSensorModel:
    """Создаёт встроенный профиль хромель-алюмелевой термопары (ХА / Тип K)."""
    return PolynomialSensorModel(
        name="ХА (Хромель-Алюмель, тип K)",
        description="Стандартная термопара ХА (тип K, МЭК 60584, 0..1370 °C) для ДТА",
        coefficients=list(DEFAULT_XA_COEFFICIENTS),
        emf_min_mv=-6.0,
        emf_max_mv=55.0,
    )


class ThermocoupleManager:
    """Файловый менеджер пользовательских и стандартных профилей термопар."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.sensors: dict[str, SensorSpec] = {}
        self._ensure_defaults_and_load()

    def _ensure_defaults_and_load(self) -> None:
        self.load_all()
        if not self.sensors:
            for preset in (make_vr_thermocouple(), make_xa_thermocouple()):
                self.save_sensor(preset)

    def save_sensor(self, sensor: SensorSpec) -> None:
        safe_stem = "".join(
            ch if (ch.isalnum() or ch in (" ", "-", "_")) else "_"
            for ch in sensor.name
        ).strip()
        path = self.storage_dir / f"{safe_stem or 'sensor'}.json"
        path.write_text(sensor.model_dump_json(indent=2), encoding="utf-8")
        self.sensors[sensor.name] = sensor

    def load_sensor(self, name: str) -> SensorSpec:
        if name in self.sensors:
            return self.sensors[name]
        raise KeyError(f"Профиль термопары '{name}' не найден")

    def load_all(self) -> None:
        self.sensors.clear()
        from pydantic import TypeAdapter

        adapter: TypeAdapter[SensorSpec] = TypeAdapter(SensorSpec)
        for path in sorted(self.storage_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                sensor = adapter.validate_python(raw)
                self.sensors[sensor.name] = sensor
            except (OSError, ValueError) as exc:
                log.bind(path=str(path), error=str(exc)).warning(
                    "Failed to load thermocouple profile"
                )

    def delete_sensor(self, name: str) -> None:
        self.sensors.pop(name, None)
        for path in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("name") == name:
                    path.unlink()
            except (OSError, ValueError):
                continue

    def list_names(self) -> list[str]:
        return list(self.sensors.keys())
