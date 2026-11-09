from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd


def eval_polynomial_asc(
    coeffs_asc: Sequence[float],
    x: float | np.ndarray,
) -> float | np.ndarray:
    """Вычисляет полином c0 + c1*x + ... + ck*x^k (коэффициенты по возрастанию степени)."""
    coeffs_desc = list(coeffs_asc)[::-1]
    res = np.polyval(coeffs_desc, x)
    if isinstance(x, (int, float)):
        return float(res)
    return np.asarray(res, dtype=float)


def bisection_method(
    func: Callable[[float], float],
    target_value: float,
    left: float,
    right: float,
    tolerance: float = 1e-4,
    max_iterations: int = 100,
) -> float:
    """Находит корень уравнения func(x) = target_value на отрезке [left, right]."""
    lo = float(left)
    hi = float(right)
    for _ in range(max_iterations):
        mid = 0.5 * (lo + hi)
        current_value = float(func(mid))
        if abs(current_value - target_value) < tolerance:
            return mid
        if current_value < target_value:
            lo = mid
        else:
            hi = mid
    raise ValueError(
        f"Метод бисекции не сошёлся за {max_iterations} итераций для значения {target_value}"
    )


def calculate_speed(
    x: np.ndarray,
    y: np.ndarray | None,
    temp_smoothing_window: int = 15,
    speed_smoothing_window: int = 30,
) -> np.ndarray | None:
    """Рассчитывает сглаженную производную dy/dx (например, скорость нагрева dT/dt)."""
    if y is None:
        return None
    if len(x) < 2:
        return np.zeros_like(y, dtype=float)

    smooth_y = (
        pd.Series(y)
        .rolling(window=max(1, temp_smoothing_window), center=True, min_periods=1)
        .mean()
        .to_numpy(dtype=float)
    )
    raw_speed = np.gradient(smooth_y, x)
    speed = (
        pd.Series(raw_speed)
        .rolling(window=max(1, speed_smoothing_window), center=True, min_periods=1)
        .mean()
        .to_numpy(dtype=float)
    )
    return speed
