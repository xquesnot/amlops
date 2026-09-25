"""Data-drift detectors used by the monitor (activity O7).

* PSI (Population Stability Index) on quantile bins of the reference sample;
  common rule of thumb: < 0.1 stable, 0.1-0.2 moderate, > 0.2 significant.
* Two-sample Kolmogorov-Smirnov statistic with the asymptotic p-value
  (Kolmogorov distribution series), no SciPy dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10, eps: float = 1e-6) -> float:
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    r = np.histogram(reference, edges)[0] / len(reference)
    c = np.histogram(current, edges)[0] / len(current)
    r, c = np.clip(r, eps, None), np.clip(c, eps, None)
    return float(np.sum((c - r) * np.log(c / r)))


def ks_2samp(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a = np.sort(np.asarray(a, dtype=float))
    b = np.sort(np.asarray(b, dtype=float))
    grid = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, grid, side="right") / len(a)
    cdf_b = np.searchsorted(b, grid, side="right") / len(b)
    d = float(np.max(np.abs(cdf_a - cdf_b)))
    n = len(a) * len(b) / (len(a) + len(b))
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, float(min(max(p, 0.0), 1.0))


@dataclass
class DriftReport:
    feature: str
    detector: str
    statistic: float
    threshold: float
    drift: bool
    p_value: float | None = None


class DriftDetector:
    def __init__(self, detector: str = "psi", threshold: float = 0.2, alpha: float = 0.01):
        if detector not in ("psi", "ks"):
            raise ValueError("detector must be 'psi' or 'ks'")
        self.detector, self.threshold, self.alpha = detector, threshold, alpha

    def check(self, reference: dict[str, np.ndarray], current: dict[str, np.ndarray]) -> list[DriftReport]:
        reports = []
        for feat, ref in reference.items():
            cur = current[feat]
            if self.detector == "psi":
                s = psi(ref, cur)
                reports.append(DriftReport(feat, "psi", s, self.threshold, s > self.threshold))
            else:
                d, p = ks_2samp(ref, cur)
                reports.append(DriftReport(feat, "ks", d, self.threshold, d > self.threshold and p < self.alpha, p))
        return reports
