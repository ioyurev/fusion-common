from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from loguru import logger as log
from pydantic import BaseModel, Field, field_validator

from fusion_common.thermometry import CjcData, SensorSpec

# Справочник стандартных реперных веществ МТШ-90 / ICTAC (Haines et al., Table 3 & 4)
REFERENCE_STANDARDS: dict[str, float] = {
    "H2O (Вода, плавление)": 0.0,
    "Benzoic acid (Бензойная кислота)": 122.9,
    "In (Индий)": 156.6,
    "Sn (Олово)": 231.9,
    "Bi (Висмут)": 271.4,
    "Zn (Цинк)": 419.5,
    "SiO2 (Кварц α→β)": 573.0,
    "K2SO4 (Сульфат калия)": 583.0,
    "Al (Алюминий)": 660.3,
    "Ag (Серебро)": 961.8,
    "Au (Золото)": 1064.2,
    "Cu (Медь)": 1084.6,
    "Ni (Никель)": 1455.0,
    "Pd (Палладий)": 1554.9,
    "Pt (Платина)": 1768.2,
    "Al2O3 (Корунд)": 2054.0,
}


class Standard(BaseModel):
    """Реперная точка калибровки по стандартному веществу."""

    name: str
    t_theor: float
    t_exp: float


class CalibrationStats(BaseModel):
    """Статистические показатели качества калибровки температуры."""

    r_squared: float | None
    sec: float | None
    expanded_uncertainty: float | None
    max_abs_error: float
    ss_res: float
    ss_tot: float
    residuals: list[float]
    delta_t_actual: list[float]
    delta_t_fit: list[float]
    n_points: int
    n_params: int


def calculate_coefficients(
    standards: Sequence[Standard],
    calibration_type: str = "linear",
) -> list[float]:
    """Рассчитывает коэффициенты [a, b, c] аддитивной поправки delta_T(T_exp)."""
    if calibration_type not in ("linear", "quadratic"):
        raise ValueError("Тип калибровки должен быть 'linear' или 'quadratic'")

    min_points = 2 if calibration_type == "linear" else 3
    if len(standards) < min_points:
        raise ValueError(
            f"Для расчёта {calibration_type} калибровки требуется минимум {min_points} стандарта"
        )

    t_exp = np.array([s.t_exp for s in standards], dtype=float)
    if len(np.unique(t_exp)) < min_points:
        raise ValueError(
            f"Для расчёта {calibration_type} калибровки требуется минимум {min_points} стандарта с различными экспериментальными температурами"
        )

    delta_t = np.array([s.t_theor - s.t_exp for s in standards], dtype=float)
    if calibration_type == "linear":
        c_a, c_b = np.polyfit(t_exp, delta_t, 1)  # noqa: PU001
        return [float(c_a), float(c_b), 0.0]

    c_a, c_b, c_c = np.polyfit(t_exp, delta_t, 2)  # noqa: PU001
    return [float(c_a), float(c_b), float(c_c)]


class IdentityCalibration(BaseModel):
    """Нулевая калибровка (возвращает значение без поправки)."""

    kind: Literal["identity"] = "identity"
    name: str = "ZeroCalibration"
    description: str = "Нулевая калибровка (без коррекции)"

    def apply(self, t_exp: float | np.ndarray) -> float | np.ndarray:
        if isinstance(t_exp, (int, float)):
            return float(t_exp)
        return np.asarray(t_exp, dtype=float)

    def to_formula_str(self) -> str:
        return "Tскор = Tэксп (без калибровки)"


class AdditivePolynomialCalibration(BaseModel):
    """Аддитивная калибровка температуры по реперным веществам: T_corr = T_exp + delta_T(T_exp)."""

    kind: Literal["additive_polynomial"] = "additive_polynomial"
    calibration_type: Literal["linear", "quadratic"] = "linear"
    coefficients: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    name: str = ""
    description: str = ""
    standards: list[Standard] = Field(default_factory=list)

    @field_validator("coefficients")
    @classmethod
    def validate_coefficients(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("Коэффициенты не могут быть пустыми")
        for coeff in v:
            if math.isnan(coeff) or math.isinf(coeff):
                raise ValueError("Коэффициент не может быть NaN или бесконечностью")
        res = list(v)
        while len(res) < 3:
            res.append(0.0)
        return res

    @classmethod
    def from_standards(
        cls,
        name: str,
        standards: Sequence[Standard],
        calibration_type: Literal["linear", "quadratic"] = "linear",
        description: str = "",
    ) -> AdditivePolynomialCalibration:
        coeffs = calculate_coefficients(standards, calibration_type)
        return cls(
            name=name,
            description=description,
            calibration_type=calibration_type,
            coefficients=coeffs,
            standards=list(standards),
        )

    def _poly_desc(self) -> list[float]:
        c0, c1, c2, *_ = self.coefficients
        if self.calibration_type == "linear":
            return [c0, c1]
        return [c0, c1, c2]

    def apply(self, t_exp: float | np.ndarray) -> float | np.ndarray:
        delta_t = np.polyval(self._poly_desc(), t_exp)
        res = t_exp + delta_t
        if isinstance(t_exp, (int, float)):
            return float(res)
        return np.asarray(res, dtype=float)

    def to_formula_str(self) -> str:
        c0, c1, c2, *_ = self.coefficients
        if self.calibration_type == "linear":
            return f"Tскор = Tэксп + ({c0:.3f}) * Tэксп + ({c1:.3f})"
        return f"Tскор = Tэксп + ({c0:.3f}) * Tэксп² + ({c1:.3f}) * Tэксп + ({c2:.3f})"

    def calculate_statistics(self) -> CalibrationStats:
        min_points = 2 if self.calibration_type == "linear" else 3
        if len(self.standards) < min_points:
            raise ValueError(
                f"Для расчёта статистики требуется минимум {min_points} стандарта"
            )
        t_exp = np.array([s.t_exp for s in self.standards], dtype=float)
        t_theor = np.array([s.t_theor for s in self.standards], dtype=float)
        delta_t_actual = t_theor - t_exp
        delta_t_fit = np.asarray(np.polyval(self._poly_desc(), t_exp), dtype=float)
        residuals = delta_t_actual - delta_t_fit

        ss_res = float(np.sum(residuals**2))
        delta_mean = float(np.mean(delta_t_actual))
        ss_tot = float(np.sum((delta_t_actual - delta_mean) ** 2))

        n = len(self.standards)
        p = 2 if self.calibration_type == "linear" else 3
        if n > p:
            sec_val = float(np.sqrt(ss_res / (n - p)))
            sec: float | None = sec_val
            expanded_unc: float | None = 2.0 * sec_val
        else:
            sec = None
            expanded_unc = None

        if ss_tot > 1e-12:
            r_sq: float | None = float(1.0 - (ss_res / ss_tot))
        elif ss_res < 1e-12:
            r_sq = 1.0
        else:
            r_sq = 0.0

        max_err = float(np.max(np.abs(residuals))) if len(residuals) > 0 else 0.0
        return CalibrationStats(
            r_squared=r_sq,
            sec=sec,
            expanded_uncertainty=expanded_unc,
            max_abs_error=max_err,
            ss_res=ss_res,
            ss_tot=ss_tot,
            residuals=residuals.tolist(),
            delta_t_actual=delta_t_actual.tolist(),
            delta_t_fit=delta_t_fit.tolist(),
            n_points=n,
            n_params=p,
        )


class DirectPolynomialCalibration(BaseModel):
    """Прямой полином T(x) = polyval(coefficients_desc, x) для совместимости со старыми форматами."""

    kind: Literal["direct_polynomial"] = "direct_polynomial"
    name: str = "LegacyDirectPolynomial"
    coefficients_desc: list[float] = Field(min_length=1)

    def apply(self, x: float | np.ndarray) -> float | np.ndarray:
        res = np.polyval(self.coefficients_desc, x)
        if isinstance(x, (int, float)):
            return float(res)
        return np.asarray(res, dtype=float)

    def to_formula_str(self) -> str:
        return f"T = polyval({self.coefficients_desc})"


class LookupSplineCalibration(BaseModel):
    """Неполиномиальная таблично-интерполяционная калибровка канала."""

    kind: Literal["lookup_spline"] = "lookup_spline"
    name: str = ""
    t_exp_points: list[float] = Field(min_length=2)
    t_theor_points: list[float] = Field(min_length=2)

    def apply(self, t_exp: float | np.ndarray) -> float | np.ndarray:
        res = np.interp(t_exp, self.t_exp_points, self.t_theor_points)
        if isinstance(t_exp, (int, float)):
            return float(res)
        return np.asarray(res, dtype=float)

    def to_formula_str(self) -> str:
        return f"LookupSpline({len(self.t_exp_points)} pts)"


CalibrationSpec = Annotated[
    IdentityCalibration | AdditivePolynomialCalibration | DirectPolynomialCalibration | LookupSplineCalibration,
    Field(discriminator="kind"),
]


def evaluate_channel_temperature(
    emf_mv: float | np.ndarray,
    sensor: SensorSpec | None = None,
    cjc: CjcData | None = None,
    calibration: CalibrationSpec | None = None,
) -> float | np.ndarray:
    """Единый по-канальный конвейер пересчёта: сырая ЭДС -> компенсация CJC -> датчик -> калибровка."""
    val: float | np.ndarray = (
        cjc.compensate(emf_mv) if cjc is not None else emf_mv
    )
    if sensor is not None:
        val = sensor.emf_to_temperature(val)
    if calibration is not None:
        val = calibration.apply(val)
    return val


class CalibrationManager:
    """Единый файловый менеджер калибровок температуры по стандартным образцам."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.calibrations: dict[str, AdditivePolynomialCalibration] = {}
        self.load_all()

    def save_calibration(self, calibration: AdditivePolynomialCalibration) -> None:
        safe_name = "".join(
            ch if (ch.isalnum() or ch in (" ", "-", "_")) else "_"
            for ch in calibration.name
        ).strip()
        path = self.storage_dir / f"{safe_name or 'calibration'}.json"
        path.write_text(calibration.model_dump_json(indent=2), encoding="utf-8")
        self.calibrations[calibration.name] = calibration

    def load_calibration(self, name: str) -> AdditivePolynomialCalibration:
        if name in self.calibrations:
            return self.calibrations[name]
        path = self.storage_dir / f"{name}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        cal = AdditivePolynomialCalibration.model_validate(raw)
        self.calibrations[cal.name] = cal
        return cal

    def load_all(self) -> None:
        self.calibrations.clear()
        for path in sorted(self.storage_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                cal = AdditivePolynomialCalibration.model_validate(raw)
                name = cal.name or path.stem
                cal.name = name
                self.calibrations[name] = cal
            except (OSError, ValueError) as exc:
                log.bind(path=str(path), error=str(exc)).warning(
                    "Failed to load calibration profile"
                )

    def delete_calibration(self, name: str) -> None:
        self.calibrations.pop(name, None)
        for path in self.storage_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("name") == name or path.stem == name:
                    path.unlink()
            except (OSError, ValueError):
                continue

    def list_names(self) -> list[str]:
        return list(self.calibrations.keys())
