from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np


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


def centered_moving_average(y: np.ndarray, window: int) -> np.ndarray:
    """Вычисляет центрированное скользящее среднее за O(N) на чистом NumPy без обрезки краёв."""
    arr = np.asarray(y, dtype=float)
    n = len(arr)
    w = max(1, int(window))
    if n == 0 or w == 1:
        return arr.copy()

    idx = np.arange(n)
    half = w // 2
    starts = np.maximum(0, idx - half)
    ends = np.minimum(n, idx + (w - half))
    csum = np.concatenate((np.array([0.0]), np.cumsum(arr, dtype=float)))
    counts = np.maximum(1, ends - starts)
    return np.asarray((csum[ends] - csum[starts]) / counts, dtype=float)


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

    smooth_y = centered_moving_average(y, max(1, temp_smoothing_window))
    raw_speed = np.asarray(np.gradient(smooth_y, x), dtype=float)
    return centered_moving_average(raw_speed, max(1, speed_smoothing_window))
