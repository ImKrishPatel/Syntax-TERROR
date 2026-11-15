"""Diagnostics for model-disagreement uncertainty proxies."""

from typing import Dict, List

import numpy as np


def evaluate_uncertainty(
    target_grid: np.ndarray,
    probability_grid: np.ndarray,
    uncertainty_grid: np.ndarray,
    n_quantiles: int = 5,
) -> Dict[str, object]:
    """Summarize uncertainty/error association and risk at fixed coverage.

    Error is absolute probability error; the optional classification error
    uses a prespecified 0.5 cutoff and is not tuned on evaluation data.
    """
    target = np.asarray(target_grid)
    probability = np.asarray(probability_grid, dtype=np.float64)
    uncertainty = np.asarray(uncertainty_grid, dtype=np.float64)
    if (
        target.ndim not in {2, 3}
        or probability.shape != target.shape
        or uncertainty.shape != target.shape
    ):
        raise ValueError(
            "Target, probability, and uncertainty arrays must have matching 2D "
            "single-sample or 3D parent-batch shapes."
        )
    if isinstance(n_quantiles, bool) or not isinstance(n_quantiles, int) or n_quantiles < 1:
        raise ValueError("n_quantiles must be a positive integer.")
    if target.size == 0 or not np.isin(target, (0, 1)).all():
        raise ValueError("Targets must be nonempty binary VIIRS occupancy grids.")
    if (
        not np.isfinite(probability).all()
        or np.any(probability < 0)
        or np.any(probability > 1)
    ):
        raise ValueError("Probabilities must be finite and in [0, 1].")
    if not np.isfinite(uncertainty).all() or np.any(uncertainty < 0):
        raise ValueError("Uncertainty values must be finite and nonnegative.")

    y_true = target.reshape(-1).astype(np.int32)
    y_probability = probability.reshape(-1)
    y_uncertainty = uncertainty.reshape(-1)
    absolute_error = np.abs(y_probability - y_true)
    classification_error = (
        (y_probability >= 0.5).astype(np.int32) != y_true
    ).astype(np.float64)

    quantile_groups = np.array_split(
        np.argsort(y_uncertainty, kind="mergesort"),
        min(n_quantiles, len(y_uncertainty)),
    )
    quantile_rows: List[Dict[str, object]] = []
    for index, cell_indices in enumerate(quantile_groups):
        quantile_rows.append(
            {
                "uncertainty_quantile": index + 1,
                "cell_count": int(cell_indices.size),
                "mean_uncertainty": float(np.mean(y_uncertainty[cell_indices])),
                "mean_absolute_probability_error": float(
                    np.mean(absolute_error[cell_indices])
                ),
                "classification_error_rate_at_0_5": float(
                    np.mean(classification_error[cell_indices])
                ),
            }
        )

    standard_deviation = float(np.std(y_uncertainty))
    error_deviation = float(np.std(absolute_error))
    correlation = None
    correlation_status = "undefined because uncertainty or error is constant"
    if standard_deviation > 0 and error_deviation > 0:
        correlation = float(np.corrcoef(y_uncertainty, absolute_error)[0, 1])
        correlation_status = "defined"

    sorted_indices = np.argsort(y_uncertainty, kind="mergesort")
    risk_coverage = []
    for coverage in (0.2, 0.4, 0.6, 0.8, 1.0):
        retained_count = max(1, int(np.ceil(len(sorted_indices) * coverage)))
        retained = sorted_indices[:retained_count]
        risk_coverage.append(
            {
                "coverage": float(retained_count / len(sorted_indices)),
                "retained_cell_count": retained_count,
                "mean_absolute_probability_error": float(
                    np.mean(absolute_error[retained])
                ),
            }
        )

    quantiles = np.quantile(y_uncertainty, [0.0, 0.5, 0.9, 0.95, 1.0])
    histogram_counts, histogram_edges = np.histogram(y_uncertainty, bins=10)
    return {
        "cell_count": int(y_true.size),
        "mean_uncertainty": float(np.mean(y_uncertainty)),
        "uncertainty_quantiles": {
            "min": float(quantiles[0]),
            "median": float(quantiles[1]),
            "p90": float(quantiles[2]),
            "p95": float(quantiles[3]),
            "max": float(quantiles[4]),
        },
        "uncertainty_histogram": {
            "counts": histogram_counts.astype(int).tolist(),
            "bin_edges": histogram_edges.astype(float).tolist(),
        },
        "uncertainty_quantile_errors": quantile_rows,
        "uncertainty_absolute_error_correlation": correlation,
        "correlation_status": correlation_status,
        "risk_coverage": risk_coverage,
    }
