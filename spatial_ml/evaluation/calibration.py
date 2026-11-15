"""Validation-only probability calibration with JSON-serializable artifacts."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

CALIBRATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class CalibrationAttempt:
    """Calibration availability and validation-only fit provenance."""

    calibrator: Optional["ProbabilityCalibrator"]
    status: str
    reason: Optional[str]
    validation_parent_ids: Tuple[str, ...]
    validation_cell_count: int
    positive_cell_count: int
    negative_cell_count: int


@dataclass(frozen=True)
class ProbabilityCalibrator:
    """Platt or isotonic transform fitted using validation probabilities only."""

    method: str
    parameters: Dict[str, object]
    provenance: Dict[str, object]

    def apply(self, probabilities: np.ndarray) -> np.ndarray:
        values = np.asarray(probabilities, dtype=np.float64)
        if (
            not np.isfinite(values).all()
            or np.any(values < 0)
            or np.any(values > 1)
        ):
            raise ValueError("Probabilities to calibrate must be finite and in [0, 1].")
        if self.method == "platt":
            clipped = np.clip(values, 1e-12, 1.0 - 1e-12)
            logits = np.log(clipped / (1.0 - clipped))
            logit_values = (
                float(self.parameters["slope"]) * logits
                + float(self.parameters["intercept"])
            )
            calibrated = np.empty_like(logit_values)
            positive = logit_values >= 0
            calibrated[positive] = 1.0 / (1.0 + np.exp(-logit_values[positive]))
            exponentials = np.exp(logit_values[~positive])
            calibrated[~positive] = exponentials / (1.0 + exponentials)
            return calibrated.astype(np.float32)
        if self.method == "isotonic":
            return np.interp(
                values,
                np.asarray(self.parameters["x_thresholds"], dtype=np.float64),
                np.asarray(self.parameters["y_thresholds"], dtype=np.float64),
            ).astype(np.float32)
        raise ValueError(f"Unsupported calibration method {self.method!r}.")

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "calibration_version": CALIBRATION_VERSION,
            "method": self.method,
            "parameters": self.parameters,
            "provenance": self.provenance,
        }
        with destination.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "ProbabilityCalibrator":
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"Calibration artifact does not exist: {source}")
        with source.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("calibration_version") != CALIBRATION_VERSION:
            raise ValueError("Unsupported calibration artifact version.")
        method = payload.get("method")
        if method not in {"platt", "isotonic"}:
            raise ValueError("Calibration artifact has an unsupported method.")
        return cls(
            method=method,
            parameters=payload["parameters"],
            provenance=payload["provenance"],
        )


def fit_calibrator_on_validation(
    target_grids: Sequence[np.ndarray],
    probability_grids: Sequence[np.ndarray],
    validation_parent_ids: Sequence[str],
    *,
    method: str = "platt",
    min_cells: int = 20,
    min_per_class: int = 5,
    min_parent_samples: int = 3,
    split_configuration: Optional[Dict[str, object]] = None,
    random_seed: int = 42,
) -> CalibrationAttempt:
    """Fit a calibrator from validation-only grids, or report unavailable.

    Parent IDs must be unique, so all cells from a parent remain a single
    calibration unit. This function intentionally has no test-set argument.
    """
    if method not in {"platt", "isotonic"}:
        raise ValueError("method must be 'platt' or 'isotonic'.")
    if isinstance(min_cells, bool) or not isinstance(min_cells, int) or min_cells < 1:
        raise ValueError("min_cells must be a positive integer.")
    if (
        isinstance(min_per_class, bool)
        or not isinstance(min_per_class, int)
        or min_per_class < 1
    ):
        raise ValueError("min_per_class must be a positive integer.")
    if (
        isinstance(min_parent_samples, bool)
        or not isinstance(min_parent_samples, int)
        or min_parent_samples < 1
    ):
        raise ValueError("min_parent_samples must be a positive integer.")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ValueError("random_seed must be an integer.")
    if (
        len(target_grids) != len(probability_grids)
        or len(target_grids) != len(validation_parent_ids)
    ):
        raise ValueError(
            "Validation targets, probabilities, and parent IDs must have equal lengths."
        )
    parent_ids = tuple(str(parent_id) for parent_id in validation_parent_ids)
    if len(set(parent_ids)) != len(parent_ids):
        raise ValueError("Validation parent IDs must be unique.")

    if not target_grids:
        return CalibrationAttempt(
            calibrator=None,
            status="unavailable",
            reason="Validation partition contains no eligible parent samples.",
            validation_parent_ids=(),
            validation_cell_count=0,
            positive_cell_count=0,
            negative_cell_count=0,
        )

    flattened_targets = []
    flattened_probabilities = []
    for target_grid, probability_grid in zip(target_grids, probability_grids):
        target = np.asarray(target_grid)
        probability = np.asarray(probability_grid, dtype=np.float64)
        if target.ndim != 2 or probability.shape != target.shape:
            raise ValueError(
                "Validation target and probability grids must have matching 2D shapes."
            )
        if not np.isin(target, (0, 1)).all():
            raise ValueError("Calibration targets must be binary VIIRS occupancy.")
        if (
            not np.isfinite(probability).all()
            or np.any(probability < 0)
            or np.any(probability > 1)
        ):
            raise ValueError(
                "Validation probabilities must be finite and in [0, 1]."
            )
        flattened_targets.append(target.reshape(-1).astype(np.int32))
        flattened_probabilities.append(probability.reshape(-1))

    y_true = np.concatenate(flattened_targets)
    y_probability = np.concatenate(flattened_probabilities)
    positive_count = int(np.count_nonzero(y_true == 1))
    negative_count = int(np.count_nonzero(y_true == 0))
    unavailable_reason = None
    if len(parent_ids) < min_parent_samples:
        unavailable_reason = (
            f"Validation has {len(parent_ids)} parent samples; at least "
            f"{min_parent_samples} are required."
        )
    elif y_true.size < min_cells:
        unavailable_reason = (
            f"Validation has {y_true.size} cells; at least {min_cells} are required."
        )
    elif min(positive_count, negative_count) < min_per_class:
        unavailable_reason = (
            "Validation lacks sufficient class diversity: "
            f"{positive_count} positive and {negative_count} negative cells; "
            f"at least {min_per_class} of each are required."
        )
    elif np.unique(y_probability).size < 2:
        unavailable_reason = (
            "Validation probabilities have fewer than two distinct values; "
            "calibration would be unreliable."
        )
    if unavailable_reason is not None:
        return CalibrationAttempt(
            calibrator=None,
            status="unavailable",
            reason=unavailable_reason,
            validation_parent_ids=parent_ids,
            validation_cell_count=int(y_true.size),
            positive_cell_count=positive_count,
            negative_cell_count=negative_count,
        )

    clipped = np.clip(y_probability, 1e-12, 1.0 - 1e-12)
    if method == "platt":
        calibration_features = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
        estimator = LogisticRegression(
            C=1e6,
            solver="lbfgs",
            max_iter=1000,
            random_state=random_seed,
        )
        estimator.fit(calibration_features, y_true)
        parameters: Dict[str, object] = {
            "slope": float(estimator.coef_[0, 0]),
            "intercept": float(estimator.intercept_[0]),
        }
    else:
        estimator = IsotonicRegression(out_of_bounds="clip")
        estimator.fit(y_probability, y_true)
        parameters = {
            "x_thresholds": estimator.X_thresholds_.astype(float).tolist(),
            "y_thresholds": estimator.y_thresholds_.astype(float).tolist(),
        }

    provenance: Dict[str, object] = {
        "fit_partition": "validation",
        "validation_parent_ids": list(parent_ids),
        "validation_parent_count": len(parent_ids),
        "validation_cell_count": int(y_true.size),
        "positive_cell_count": positive_count,
        "negative_cell_count": negative_count,
        "random_seed": random_seed,
        "split_configuration": split_configuration or {},
    }
    calibrator = ProbabilityCalibrator(method, parameters, provenance)
    return CalibrationAttempt(
        calibrator=calibrator,
        status="available",
        reason=None,
        validation_parent_ids=parent_ids,
        validation_cell_count=int(y_true.size),
        positive_cell_count=positive_count,
        negative_cell_count=negative_count,
    )
