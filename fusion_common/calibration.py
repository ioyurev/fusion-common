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

# Справочник сертифицированных реперных веществ МТШ-90 / LGC / NIST / ICTAC
# (Richardson & Charsley, 1998, Tables 1, 3, 4; кварц SiO2 исключён по решению ICTAC)
REFERENCE_STANDARDS: dict[str, float] = {
    "Hg (Ртуть, плавление)": -38.86,
    "H2O (Вода, плавление)": 0.0,
    "Ga (Галлий, плавление)": 29.76,
    "Benzoic acid (Бензойная кислота)": 122.35,
    "KNO3 (Нитрат калия, переход)": 128.85,
    "In (Индий, плавление)": 156.61,
    "Sn (Олово, плавление)": 231.92,
    "Bi (Висмут, плавление)": 271.40,
    "KClO4 (Перхлорат калия, переход)": 299.95,
    "Pb (Свинец, плавление)": 327.47,
    "Ni (Никель, точка Кюри Tc)": 354.40,
    "Zn (Цинк, плавление)": 419.53,
    "Al-Cu (Эвтектика Cu/66.9%Al)": 548.16,
    "K2SO4 (Сульфат калия, переход)": 583.85,
    "Al (Алюминий, плавление)": 660.33,
    "K2CrO4 (Хромат калия, переход)": 669.15,
    "Fe (Железо, точка Кюри Tc)": 770.00,
    "Ag-Cu (Эвтектика Cu/71.9%Ag)": 779.91,
    "BaCO3 (Карбонат бария, переход)": 808.85,
    "SrCO3 (Карбонат стронция, переход)": 932.75,
    "Ag (Серебро, плавление)": 961.78,
    "Au (Золото, плавление)": 1064.18,
    "K2SO4 (Сульфат калия, плавление)": 1069.85,
    "Cu (Медь, плавление)": 1084.62,
    "Ni (Никель, плавление)": 1455.0,
    "Fe (Железо, плавление)": 1538.0,
    "Pd (Палладий, плавление)": 1554.9,
    "Pt (Платина, плавление)": 1768.2,
    "Al2O3 (Корунд, плавление)": 2054.0,
}

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
