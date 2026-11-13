from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from loguru import logger as log
from pydantic import BaseModel, Field, TypeAdapter

from fusion_common.math_utils import bisection_method, eval_polynomial_asc

# ГОСТ Р 8.585-2001, Приложение А.2: ТВР (тип А-1), 0..2500 °C (0..33.640 мВ)
DEFAULT_VR_COEFFICIENTS: list[float] = [
    0.9643027,
    79.495086,
    -4.9990310,
    0.6341776,
    -4.7440967e-2,
    2.1811337e-3,
    -5.8324228e-5,
    8.2433725e-7,
    -4.5928480e-9,
]

# ГОСТ Р 8.585-2001, Приложение А.2: ТХА (тип K), поддиапазон 0..500 °C (0..20.644 мВ)
DEFAULT_XA_COEFFICIENTS: list[float] = [
    0.0,
    2.508355e1,
    7.860106e-2,
    -2.503131e-1,
    8.315270e-2,
    -1.228034e-2,
    9.804036e-4,
    -4.413030e-5,
    1.057734e-6,
    -1.052755e-8,
]


class PolynomialSensorModel(BaseModel):
    """Однодиапазонная полиномиальная модель термопары t(E) = sum(c_i * E^i)."""

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
    """Один поддиапазон кусочно-полиномиальной градуировки ГОСТ Р 8.585-2001."""

    emf_min_mv: float
    emf_max_mv: float
    coefficients: list[float] = Field(min_length=1)


class PiecewisePolynomialSensorModel(BaseModel):
    """Кусочно-полиномиальная модель термопары по поддиапазонам ТЭДС (ГОСТ Р 8.585-2001)."""

    kind: Literal["piecewise_polynomial"] = "piecewise_polynomial"
    name: str
    description: str = ""
    segments: list[PiecewisePolynomialSegment] = Field(min_length=1)
    emf_min_mv: float = -10.0
    emf_max_mv: float = 70.0

    def _eval_scalar(self, emf_val: float) -> float:
        first_seg, *_, last_seg = self.segments
        if emf_val <= first_seg.emf_max_mv:
            return float(eval_polynomial_asc(first_seg.coefficients, emf_val))
        for seg in self.segments:
            if seg.emf_min_mv <= emf_val <= seg.emf_max_mv:
                return float(eval_polynomial_asc(seg.coefficients, emf_val))
        return float(eval_polynomial_asc(last_seg.coefficients, emf_val))

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
    """ТВР (тип А-1, Вольфрам-Рений ВР5/20) по ГОСТ Р 8.585-2001 (0..2500 °C)."""
    return PolynomialSensorModel(
        name="ВР (Вольфрам-Рений)",
        description="ГОСТ Р 8.585-2001: ТВР тип А-1 (0..2500 °C, 0..33.640 мВ)",
        coefficients=list(DEFAULT_VR_COEFFICIENTS),
        emf_min_mv=-0.1,
        emf_max_mv=33.640,
    )


def make_xa_thermocouple() -> PiecewisePolynomialSensorModel:
    """ТХА (тип K, Хромель-Алюмель) по ГОСТ Р 8.585-2001 (-200..1372 °C, 3 поддиапазона)."""
    return PiecewisePolynomialSensorModel(
        name="ХА (Хромель-Алюмель, тип K)",
        description="ГОСТ Р 8.585-2001: ТХА тип K (-200..1372 °C, -5.891..54.886 мВ)",
        emf_min_mv=-5.891,
        emf_max_mv=54.886,
        segments=[
            PiecewisePolynomialSegment(
                emf_min_mv=-5.891,
                emf_max_mv=0.0,
                coefficients=[
                    0.0,
                    2.5173462e1,
                    -1.1662878,
                    -1.0833638,
                    -8.9773540e-1,
                    -3.7342377e-1,
                    -8.6632643e-2,
                    -1.0450598e-2,
                    -5.1920577e-4,
                ],
            ),
            PiecewisePolynomialSegment(
                emf_min_mv=0.0,
                emf_max_mv=20.644,
                coefficients=list(DEFAULT_XA_COEFFICIENTS),
            ),
            PiecewisePolynomialSegment(
                emf_min_mv=20.644,
                emf_max_mv=54.886,
                coefficients=[
                    -1.318058e2,
                    4.830222e1,
                    -1.646031,
                    5.464731e-2,
                    -9.650715e-4,
                    8.802193e-6,
                    -3.110810e-8,
                ],
            ),
        ],
    )


def make_gost_thermocouples() -> list[SensorSpec]:
    """Возвращает полный набор из 12 стандартных типов термопар по ГОСТ Р 8.585-2001 (Приложение А.2)."""
    return [
        make_vr_thermocouple(),
        PolynomialSensorModel(
            name="ТВР (тип А-2, Вольфрам-Рений)",
            description="ГОСТ Р 8.585-2001: ТВР тип А-2 (0..1800 °C, 0..27.232 мВ)",
            emf_min_mv=-0.1,
            emf_max_mv=27.232,
            coefficients=[
                1.1196428,
                8.0569397e1,
                -6.2279122,
                0.9337015,
                -8.2608051e-2,
                4.4110979e-3,
                -1.3610551e-4,
                2.2183851e-6,
                -1.4527698e-8,
            ],
        ),
        PolynomialSensorModel(
            name="ТВР (тип А-3, Вольфрам-Рений)",
            description="ГОСТ Р 8.585-2001: ТВР тип А-3 (0..1800 °C, 0..26.773 мВ)",
            emf_min_mv=-0.1,
            emf_max_mv=26.773,
            coefficients=[
                0.8769216,
                8.1483231e1,
                -5.9344173,
                0.8699340,
                -7.6797687e-2,
                4.1814387e-3,
                -1.3439670e-4,
                2.342409e-6,
                -1.6988727e-8,
            ],
        ),
        make_xa_thermocouple(),
        PiecewisePolynomialSensorModel(
            name="ТПП (тип S, Платинородий-Платина ПР-10/ПлТ)",
            description="ГОСТ Р 8.585-2001: ТПП тип S (-50..1768.1 °C, -0.235..18.694 мВ)",
            emf_min_mv=-0.235,
            emf_max_mv=18.694,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-0.235,
                    emf_max_mv=1.874,
                    coefficients=[
                        0.0,
                        1.84949460e2,
                        -8.00504062e1,
                        1.02237430e2,
                        -1.52248592e2,
                        1.88821343e2,
                        -1.59085941e2,
                        8.23027880e1,
                        -2.34181944e1,
                        2.79786260,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=1.874,
                    emf_max_mv=10.332,
                    coefficients=[
                        1.291507177e1,
                        1.466298863e2,
                        -1.534713402e1,
                        3.145945973,
                        -4.163257839e-1,
                        3.187963771e-2,
                        -1.291637500e-3,
                        2.183475087e-5,
                        -1.447379511e-7,
                        8.211272125e-9,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=10.332,
                    emf_max_mv=17.536,
                    coefficients=[
                        -8.087801117e1,
                        1.621573104e2,
                        -8.536869453,
                        4.719686976e-1,
                        -1.441693666e-2,
                        2.081618890e-4,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=17.536,
                    emf_max_mv=18.694,
                    coefficients=[
                        5.333875126e4,
                        -1.235892298e4,
                        1.092657613e3,
                        -4.265693686e1,
                        6.247205420e-1,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТПП (тип R, Платинородий-Платина ПР-13/ПлТ)",
            description="ГОСТ Р 8.585-2001: ТПП тип R (-50..1768.1 °C, -0.225..21.103 мВ)",
            emf_min_mv=-0.225,
            emf_max_mv=21.103,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-0.225,
                    emf_max_mv=1.923,
                    coefficients=[
                        0.0,
                        1.8891380e2,
                        -9.3835290e1,
                        1.3068619e2,
                        -2.2703580e2,
                        3.5145659e2,
                        -3.8953900e2,
                        2.8239471e2,
                        -1.2607281e2,
                        3.1353611e1,
                        -3.3187769,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=1.923,
                    emf_max_mv=11.361,
                    coefficients=[
                        1.334584505e1,
                        1.472644573e2,
                        -1.844024844e1,
                        4.031129726,
                        -6.249428360e-1,
                        6.468412046e-2,
                        -4.458750426e-3,
                        1.994710149e-4,
                        -5.313401790e-6,
                        6.481976217e-8,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=11.361,
                    emf_max_mv=19.739,
                    coefficients=[
                        -8.199599416e1,
                        1.553962042e2,
                        -8.342197663,
                        4.279433549e-1,
                        -1.191577910e-2,
                        1.492290091e-4,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=19.739,
                    emf_max_mv=21.103,
                    coefficients=[
                        3.406177836e4,
                        -7.023729171e3,
                        5.582903813e2,
                        -1.952394635e1,
                        2.560740231e-1,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТПР (тип B, Платинородий ПР-30/ПР-6)",
            description="ГОСТ Р 8.585-2001: ТПР тип B (250..1820 °C, 0.291..13.820 мВ)",
            emf_min_mv=0.291,
            emf_max_mv=13.820,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=0.291,
                    emf_max_mv=2.431,
                    coefficients=[
                        9.8423321e1,
                        6.9971500e2,
                        -8.4765304e2,
                        1.0052644e3,
                        -8.3345952e2,
                        4.5508542e2,
                        -1.5523037e2,
                        2.9886750e1,
                        -2.4742860,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=2.431,
                    emf_max_mv=13.820,
                    coefficients=[
                        2.1315071e2,
                        2.8510504e2,
                        -5.2742887e1,
                        9.9160804,
                        -1.2965303,
                        1.1195870e-1,
                        -6.0625199e-3,
                        1.8661696e-4,
                        -2.4878585e-6,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТНН (тип N, Нихросил-Нисил)",
            description="ГОСТ Р 8.585-2001: ТНН тип N (-200..1300 °C, -3.990..47.513 мВ)",
            emf_min_mv=-3.990,
            emf_max_mv=47.513,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-3.990,
                    emf_max_mv=0.0,
                    coefficients=[
                        0.0,
                        3.8436847e1,
                        1.1010485,
                        5.2229312,
                        7.2060525,
                        5.8488586,
                        2.7754916,
                        7.7075166e-1,
                        1.1582665e-1,
                        7.3138868e-3,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=0.0,
                    emf_max_mv=20.613,
                    coefficients=[
                        0.0,
                        3.86896e1,
                        -1.08267,
                        4.70205e-2,
                        -2.12169e-6,
                        -1.17272e-4,
                        5.39280e-6,
                        -7.98156e-8,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=20.613,
                    emf_max_mv=47.513,
                    coefficients=[
                        1.972485e1,
                        3.300943e1,
                        -3.915159e-1,
                        9.855391e-3,
                        -1.274371e-4,
                        7.767022e-7,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТЖК (тип J, Железо-Константан)",
            description="ГОСТ Р 8.585-2001: ТЖК тип J (-210..1200 °C, -8.095..69.553 мВ)",
            emf_min_mv=-8.095,
            emf_max_mv=69.553,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-8.095,
                    emf_max_mv=0.0,
                    coefficients=[
                        0.0,
                        1.9528268e1,
                        -1.2286185,
                        -1.0752178,
                        -5.9086933e-1,
                        -1.7256713e-1,
                        -2.8131513e-2,
                        -2.3963370e-3,
                        -8.3823321e-5,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=0.0,
                    emf_max_mv=42.919,
                    coefficients=[
                        0.0,
                        1.978425e1,
                        -2.001204e-1,
                        1.036969e-2,
                        -2.549687e-4,
                        3.585153e-6,
                        -5.344285e-8,
                        5.099890e-10,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=42.919,
                    emf_max_mv=69.553,
                    coefficients=[
                        -3.11358187e3,
                        3.00543684e2,
                        -9.94773230,
                        1.70276630e-1,
                        -1.43033468e-3,
                        4.73886084e-6,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТХКн (тип E, Хромель-Константан)",
            description="ГОСТ Р 8.585-2001: ТХКн тип E (-200..1000 °C, -8.825..76.373 мВ)",
            emf_min_mv=-8.825,
            emf_max_mv=76.373,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-8.825,
                    emf_max_mv=0.0,
                    coefficients=[
                        0.0,
                        1.6977288e1,
                        -4.3514970e-1,
                        -1.5859697e-1,
                        -9.2502871e-2,
                        -2.6084314e-2,
                        -4.1360199e-3,
                        -3.4034030e-4,
                        -1.1564890e-5,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=0.0,
                    emf_max_mv=76.373,
                    coefficients=[
                        0.0,
                        1.7057035e1,
                        -2.3301759e-1,
                        6.5435585e-3,
                        -7.3562749e-5,
                        -1.7896001e-6,
                        8.4036165e-8,
                        -1.3735879e-9,
                        1.0629823e-11,
                        -3.2447087e-14,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТМК (тип T, Медь-Константан)",
            description="ГОСТ Р 8.585-2001: ТМК тип T (-200..400 °C, -5.603..20.872 мВ)",
            emf_min_mv=-5.603,
            emf_max_mv=20.872,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-5.603,
                    emf_max_mv=0.0,
                    coefficients=[
                        0.0,
                        2.5949192e1,
                        -2.1316967e-1,
                        7.9018692e-1,
                        4.2527777e-1,
                        1.3304473e-1,
                        2.0241446e-2,
                        1.2668171e-3,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=0.0,
                    emf_max_mv=20.872,
                    coefficients=[
                        0.0,
                        2.592800e1,
                        -7.602961e-1,
                        4.637791e-2,
                        -2.165394e-3,
                        6.048144e-5,
                        -7.293422e-7,
                    ],
                ),
            ],
        ),
        PiecewisePolynomialSensorModel(
            name="ТХК (тип L, Хромель-Копель)",
            description="ГОСТ Р 8.585-2001: ТХК тип L (-200..800 °C, -9.488..66.466 мВ)",
            emf_min_mv=-9.488,
            emf_max_mv=66.466,
            segments=[
                PiecewisePolynomialSegment(
                    emf_min_mv=-9.488,
                    emf_max_mv=0.0,
                    coefficients=[
                        1.1573067e-4,
                        1.5884573e1,
                        4.0458554e-2,
                        -0.3170064,
                        -0.1666128,
                        -5.1946958e-2,
                        -9.5288883e-3,
                        -1.0301283e-3,
                        -6.0654431e-5,
                        -1.5131878e-6,
                    ],
                ),
                PiecewisePolynomialSegment(
                    emf_min_mv=0.0,
                    emf_max_mv=66.466,
                    coefficients=[
                        7.2069422e-3,
                        1.5775525e1,
                        -0.2261183,
                        9.4286756e-3,
                        -3.5394655e-4,
                        1.0050886e-5,
                        -1.9323678e-7,
                        2.3816891e-9,
                        -1.7130654e-11,
                        5.4857331e-14,
                    ],
                ),
            ],
        ),
    ]


class ThermocoupleManager:
    """Файловый менеджер пользовательских и стандартных профилей термопар ГОСТ Р 8.585-2001."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.sensors: dict[str, SensorSpec] = {}
        self._ensure_defaults_and_load()

    def _ensure_defaults_and_load(self) -> None:
        self.load_all()
        for preset in make_gost_thermocouples():
            existing = self.sensors.get(preset.name)
            if existing is None or existing != preset:
                self.save_sensor(preset)

    def save_sensor(self, sensor: SensorSpec) -> None:
        safe_stem = "".join(
            ch if (ch.isalnum() or ch in (" ", "-", "_")) else "_" for ch in sensor.name
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
