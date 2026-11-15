from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

from fusion_common.thermometry import CjcData, SensorSpec

CalibrationKind = Literal["offset", "linear", "quadratic", "piecewise"]


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
    if calibration_type not in ("offset", "linear", "quadratic", "piecewise"):
        raise ValueError(
            "Тип калибровки должен быть 'offset', 'linear', 'quadratic' или 'piecewise'"
        )

    min_points = (
        1
        if calibration_type == "offset"
        else (3 if calibration_type == "quadratic" else 2)
    )
    if len(standards) < min_points:
        raise ValueError(
            f"Для расчёта {calibration_type} калибровки требуется минимум {min_points} стандарта"
        )

    t_exp = np.array([s.t_exp for s in standards], dtype=float)
    if min_points > 1 and len(np.unique(t_exp)) < min_points:
        raise ValueError(
            f"Для расчёта {calibration_type} калибровки требуется минимум {min_points} стандарта с различными температурами"
        )

    delta_t = np.array([s.t_theor - s.t_exp for s in standards], dtype=float)
    if calibration_type == "offset":
        return [0.0, float(np.mean(delta_t)), 0.0]
    if calibration_type in ("linear", "piecewise"):
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
    calibration_type: CalibrationKind = "linear"
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
        calibration_type: CalibrationKind = "linear",
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

    @property
    def valid_temp_range(self) -> tuple[float, float] | None:
        if not self.standards:
            return None
        exps = [s.t_exp for s in self.standards]
        return float(min(exps)), float(max(exps))

    def _poly_desc(self) -> list[float]:
        c0, c1, c2, *_ = self.coefficients
        if self.calibration_type in ("offset", "linear", "piecewise"):
            return [c0, c1]
        return [c0, c1, c2]

    def _eval_delta(self, t_exp: float | np.ndarray) -> float | np.ndarray:
        if self.calibration_type == "piecewise" and len(self.standards) >= 2:
            order = np.argsort([s.t_exp for s in self.standards])
            x_pts = np.array([self.standards[i].t_exp for i in order], dtype=float)
            y_pts = np.array(
                [self.standards[i].t_theor - self.standards[i].t_exp for i in order],
                dtype=float,
            )
            return np.interp(t_exp, x_pts, y_pts)
        if isinstance(t_exp, (int, float)):
            c0, c1, c2, *_ = self.coefficients
            t_f = float(t_exp)
            if self.calibration_type in ("offset", "linear", "piecewise"):
                return c0 * t_f + c1
            return (c0 * t_f + c1) * t_f + c2
        return np.polyval(self._poly_desc(), t_exp)

    def apply(self, t_exp: float | np.ndarray) -> float | np.ndarray:
        delta_t = self._eval_delta(t_exp)
        res = t_exp + delta_t
        if isinstance(t_exp, (int, float)):
            return float(res)
        return np.asarray(res, dtype=float)

    def to_formula_str(self) -> str:
        c0, c1, c2, *_ = self.coefficients
        if self.calibration_type == "offset":
            return f"Tскор = Tэксп + ({c1:.3f}) °C"
        if self.calibration_type == "piecewise":
            return f"Tскор = Tэксп + ΔT_кусочн ({len(self.standards)} реперов)"
        if self.calibration_type == "linear":
            return f"Tскор = Tэксп + ({c0:.3f}) * Tэксп + ({c1:.3f})"
        return f"Tскор = Tэксп + ({c0:.3f}) * Tэксп² + ({c1:.3f}) * Tэксп + ({c2:.3f})"

    def calculate_statistics(self) -> CalibrationStats:
        min_points = (
            1
            if self.calibration_type == "offset"
            else (3 if self.calibration_type == "quadratic" else 2)
        )
        if len(self.standards) < min_points:
            raise ValueError(
                f"Для расчёта статистики требуется минимум {min_points} стандарта"
            )
        t_exp = np.array([s.t_exp for s in self.standards], dtype=float)
        t_theor = np.array([s.t_theor for s in self.standards], dtype=float)
        delta_t_actual = t_theor - t_exp
        delta_t_fit = np.asarray(self._eval_delta(t_exp), dtype=float)
        residuals = delta_t_actual - delta_t_fit

        ss_res = float(np.sum(residuals**2))
        delta_mean = float(np.mean(delta_t_actual))
        ss_tot = float(np.sum((delta_t_actual - delta_mean) ** 2))

        n = len(self.standards)
        p = (
            1
            if self.calibration_type == "offset"
            else (3 if self.calibration_type == "quadratic" else 2)
        )
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
        if isinstance(x, (int, float)):
            x_f = float(x)
            acc = 0.0
            for coeff in self.coefficients_desc:
                acc = acc * x_f + coeff
            return acc
        return np.asarray(np.polyval(self.coefficients_desc, x), dtype=float)

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
    IdentityCalibration
    | AdditivePolynomialCalibration
    | DirectPolynomialCalibration
    | LookupSplineCalibration,
    Field(discriminator="kind"),
]


def evaluate_channel_temperature(
    emf_mv: float | np.ndarray,
    sensor: SensorSpec | None = None,
    cjc: CjcData | None = None,
    calibration: CalibrationSpec | None = None,
) -> float | np.ndarray:
    """Единый по-канальный конвейер пересчёта: сырая ЭДС -> компенсация CJC -> датчик -> калибровка."""
    val: float | np.ndarray = cjc.compensate(emf_mv) if cjc is not None else emf_mv
    if sensor is not None:
        val = sensor.emf_to_temperature(val)
    if calibration is not None:
        val = calibration.apply(val)
    return val
