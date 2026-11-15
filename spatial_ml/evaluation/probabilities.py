"""Probability-based metrics shared by the Random Forest and spatial model."""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import auc, log_loss, precision_recall_curve, roc_auc_score


@dataclass(frozen=True)
class ProbabilityEvaluation:
    """Binary probability scores and reliability-bin summaries."""

    brier_score: float
    log_loss: float
    roc_auc: Optional[float]
    pr_auc: Optional[float]
    reliability: pd.DataFrame
    metric_status: Dict[str, str]


def evaluate_probability_grids(
    target_grid: np.ndarray,
    probability_grid: np.ndarray,
    n_bins: int = 10,
) -> ProbabilityEvaluation:
    """Evaluate cell probabilities against a same-shaped observed VIIRS grid.

    This function reports scores only; it does not fit a calibration transform.
    ROC-AUC and PR-AUC are returned as ``None`` for single-class targets.
    """
    target = np.asarray(target_grid)
    probability = np.asarray(probability_grid, dtype=np.float64)
    if target.ndim != 2 or probability.shape != target.shape:
        raise ValueError("Target and probability grids must have matching 2D shapes.")
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError("n_bins must be a positive integer.")
    if target.size == 0:
        raise ValueError("Probability evaluation requires at least one target cell.")
    if not np.isin(target, (0, 1)).all():
        raise ValueError("Evaluation targets must be binary observed VIIRS occupancy.")
    if (
        not np.isfinite(probability).all()
        or np.any(probability < 0)
        or np.any(probability > 1)
    ):
        raise ValueError("Predicted probabilities must be finite and in [0, 1].")

    y_true = target.reshape(-1).astype(np.int32)
    y_probability = probability.reshape(-1)
    clipped = np.clip(y_probability, 1e-15, 1 - 1e-15)
    brier = float(np.mean((y_probability - y_true) ** 2))
    cross_entropy = float(
        log_loss(y_true, clipped, labels=[0, 1])
    )

    status: Dict[str, str] = {
        "brier_score": "defined",
        "log_loss": "defined",
    }
    if np.unique(y_true).size == 2:
        roc_auc: Optional[float] = float(roc_auc_score(y_true, y_probability))
        precision, recall, _ = precision_recall_curve(y_true, y_probability)
        pr_auc: Optional[float] = float(auc(recall, precision))
        status["roc_auc"] = "defined"
        status["pr_auc"] = "defined"
    else:
        roc_auc = None
        pr_auc = None
        reason = "undefined because both target classes are required"
        status["roc_auc"] = reason
        status["pr_auc"] = reason

    bin_indices = np.minimum(
        (y_probability * n_bins).astype(np.int64), n_bins - 1
    )
    reliability_rows = []
    for bin_index in range(n_bins):
        selected = bin_indices == bin_index
        count = int(np.count_nonzero(selected))
        reliability_rows.append(
            {
                "bin_index": bin_index,
                "lower_probability": bin_index / n_bins,
                "upper_probability": (bin_index + 1) / n_bins,
                "count": count,
                "mean_probability": (
                    float(np.mean(y_probability[selected])) if count else np.nan
                ),
                "observed_frequency": (
                    float(np.mean(y_true[selected])) if count else np.nan
                ),
            }
        )
    return ProbabilityEvaluation(
        brier_score=brier,
        log_loss=cross_entropy,
        roc_auc=roc_auc,
        pr_auc=pr_auc,
        reliability=pd.DataFrame(reliability_rows),
        metric_status=status,
    )
