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
