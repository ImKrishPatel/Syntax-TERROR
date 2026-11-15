"""Probabilistic Random Forest baseline for fine-scale VIIRS point evidence."""

import logging
import math
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from spatial_ml.data.samples import SpatialFeatureInput, TrainingSample

FeatureInput = TrainingSample | SpatialFeatureInput

LOGGER = logging.getLogger(__name__)

_BASE_FEATURE_NAMES = (
    "modis_frp",
    "modis_confidence_numeric",
    "modis_confidence_low",
    "modis_confidence_nominal",
    "modis_confidence_high",
    "cell_x_offset_m",
    "cell_y_offset_m",
)
_CONFIDENCE_LABELS = {"low", "nominal", "high"}


def _finite_number(value: object) -> float:
    """Return a finite numeric value, or NaN for unavailable/invalid values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _confidence_features(value: object) -> Tuple[float, float, float, float]:
    numeric = _finite_number(value)
    if math.isfinite(numeric):
        if 0 <= numeric <= 100:
            return numeric, 0.0, 0.0, 0.0
        return math.nan, 0.0, 0.0, 0.0

    label = str(value).strip().lower() if value is not None else ""
    if label not in _CONFIDENCE_LABELS:
        return math.nan, 0.0, 0.0, 0.0
    return (
        math.nan,
        float(label == "low"),
        float(label == "nominal"),
        float(label == "high"),
    )


def _sample_feature_matrix(
    sample: FeatureInput,
) -> Tuple[np.ndarray, Tuple[str, ...]]:
    grid_metadata = sample.metadata.get("grid")
    if not isinstance(grid_metadata, dict):
        raise ValueError(f"Sample {sample.modis_id!r} has no target-grid metadata.")
    resolution_m = _finite_number(grid_metadata.get("resolution_m"))
    height, width = sample.grid_shape
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError(
            f"Sample {sample.modis_id!r} has an invalid grid resolution."
        )
    if (
        grid_metadata.get("height") != height
        or grid_metadata.get("width") != width
    ):
        raise ValueError(
            f"Sample {sample.modis_id!r} grid metadata does not match its arrays."
        )

    frp = _finite_number(sample.modis_features.get("frp"))
    if frp < 0:
        frp = math.nan
    confidence = _confidence_features(sample.modis_features.get("confidence"))

    rows, columns = np.indices((height, width), dtype=np.float32)
    x_offsets = (columns + 0.5 - width / 2.0) * resolution_m
    y_offsets = (height / 2.0 - rows - 0.5) * resolution_m
    environmental = np.asarray(sample.environmental_features, dtype=np.float32)
    environmental = np.where(np.isfinite(environmental), environmental, np.nan)

    feature_names = _BASE_FEATURE_NAMES + sample.environmental_feature_names
    if len(set(feature_names)) != len(feature_names):
        raise ValueError(
            f"Sample {sample.modis_id!r} has environmental names that conflict "
            "with baseline features."
        )
    feature_arrays = [
        np.full((height, width), frp, dtype=np.float32),
        np.full((height, width), confidence[0], dtype=np.float32),
        np.full((height, width), confidence[1], dtype=np.float32),
        np.full((height, width), confidence[2], dtype=np.float32),
        np.full((height, width), confidence[3], dtype=np.float32),
        x_offsets,
        y_offsets,
    ]
    feature_arrays.extend(
        environmental[:, :, index]
        for index in range(environmental.shape[2])
    )
    matrix = np.stack(feature_arrays, axis=-1).reshape(-1, len(feature_names))
    return matrix, feature_names


def sample_to_cell_feature_matrix(
    sample: FeatureInput,
) -> Tuple[np.ndarray, Tuple[str, ...]]:
    """Return M5-compatible features in north-to-south row-major cell order."""
    return _sample_feature_matrix(sample)


@dataclass(frozen=True)
class SpatialProbabilityEstimate:
    """Per-cell estimated probability and tree-ensemble spread."""

    probability: np.ndarray
    uncertainty_std: np.ndarray
    metadata: Dict[str, object]

    def __post_init__(self) -> None:
        probability = np.asarray(self.probability, dtype=np.float32)
        uncertainty = np.asarray(self.uncertainty_std, dtype=np.float32)
        if probability.ndim != 2 or uncertainty.shape != probability.shape:
            raise ValueError(
                "Probability and uncertainty arrays must have matching 2D shapes."
            )
        if (
            not np.isfinite(probability).all()
            or np.any(probability < 0)
            or np.any(probability > 1)
        ):
            raise ValueError("Probability values must be finite and in [0, 1].")
        if not np.isfinite(uncertainty).all() or np.any(uncertainty < 0):
            raise ValueError("Uncertainty values must be finite and nonnegative.")
        object.__setattr__(self, "probability", probability)
        object.__setattr__(self, "uncertainty_std", uncertainty)


class ProbabilisticRandomForest:
    """Fit and apply a cell-level Random Forest probability baseline.

    The label is observed VIIRS point occupancy from the input sample, not the
    physical fire state or a VIIRS pixel-footprint label. Samples without any
    in-grid positive observation are excluded because they provide no local
    target evidence.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        random_state: Optional[int] = 42,
        max_depth: Optional[int] = None,
    ) -> None:
        if (
            isinstance(n_estimators, bool)
            or not isinstance(n_estimators, int)
            or n_estimators < 2
        ):
            raise ValueError("n_estimators must be an integer of at least two.")
        if max_depth is not None and (
            isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1
        ):
            raise ValueError("max_depth must be a positive integer or None.")
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.max_depth = max_depth
        self._pipeline: Optional[Pipeline] = None
        self.feature_names: Tuple[str, ...] = ()
        self.training_sample_ids: Tuple[str, ...] = ()
        self.excluded_sample_ids: Tuple[str, ...] = ()
        self.training_cell_count = 0

    def fit(self, samples: Iterable[TrainingSample]) -> "ProbabilisticRandomForest":
        """Fit using observed target grids and sample-level input features."""
        sample_list = list(samples)
        if not sample_list:
            raise ValueError("At least one training sample is required.")

        included = []
        excluded_ids = []
        for sample in sample_list:
            if sample.metadata.get("target_data_status") != "observed":
                raise ValueError(
                    f"Sample {sample.modis_id!r} must have observed VIIRS targets; "
                    "model-estimated targets cannot be used for training."
                )
            if not np.any(sample.target_grid == 1):
                excluded_ids.append(sample.modis_id)
            else:
                included.append(sample)

        if excluded_ids:
            LOGGER.warning(
                "Excluded %d sample(s) with no in-grid observed VIIRS point "
                "evidence (first ids: %s%s; all ids are kept in excluded_sample_ids)",
                len(excluded_ids),
                ", ".join(excluded_ids[:5]),
                ", ..." if len(excluded_ids) > 5 else "",
            )
        if not included:
            raise ValueError(
                "No training samples contain in-grid observed VIIRS point evidence."
            )

        expected_environment_names = included[0].environmental_feature_names
        if any(
            sample.environmental_feature_names != expected_environment_names
            for sample in included[1:]
        ):
            raise ValueError(
                "All training samples must use the same environmental features "
                "in the same order."
            )

        matrices = []
        targets = []
        feature_names: Optional[Tuple[str, ...]] = None
        for sample in included:
            matrix, sample_feature_names = sample_to_cell_feature_matrix(sample)
            if feature_names is None:
                feature_names = sample_feature_names
            elif sample_feature_names != feature_names:
                raise ValueError("Training sample feature schemas do not match.")
            matrices.append(matrix)
            targets.append(sample.target_grid.reshape(-1).astype(np.int32))

        features = np.concatenate(matrices, axis=0)
        target = np.concatenate(targets, axis=0)
        if set(np.unique(target)) != {0, 1}:
            raise ValueError(
                "Training requires both observed-positive and unoccupied "
                "target cells."
            )

        pipeline = Pipeline(
            steps=[
                (
                    "imputer",
                    SimpleImputer(
                        strategy="median",
                        add_indicator=True,
                        keep_empty_features=True,
                    ),
                ),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=self.n_estimators,
                        random_state=self.random_state,
                        max_depth=self.max_depth,
                        class_weight="balanced_subsample",
                    ),
                ),
            ]
        )
        pipeline.fit(features, target)

        self._pipeline = pipeline
        self.feature_names = feature_names or ()
        self.training_sample_ids = tuple(sample.modis_id for sample in included)
        self.excluded_sample_ids = tuple(excluded_ids)
        self.training_cell_count = int(target.size)
        return self

    def predict(self, sample: FeatureInput) -> SpatialProbabilityEstimate:
        """Predict cell probabilities and per-cell tree-to-tree spread."""
        if self._pipeline is None:
            raise RuntimeError("The Random Forest must be fitted before prediction.")
        features, feature_names = sample_to_cell_feature_matrix(sample)
        if feature_names != self.feature_names:
            raise ValueError(
                "Prediction sample feature schema does not match the fitted model."
            )

        imputer = self._pipeline.named_steps["imputer"]
        classifier = self._pipeline.named_steps["classifier"]
        transformed = imputer.transform(features)
        tree_probabilities = []
        for tree in classifier.estimators_:
            classes = np.asarray(tree.classes_)
            positive_indices = np.flatnonzero(classes == 1)
            if positive_indices.size:
                tree_probabilities.append(
                    tree.predict_proba(transformed)[:, positive_indices[0]]
                )
            else:
                tree_probabilities.append(np.zeros(len(features), dtype=float))

        ensemble = np.stack(tree_probabilities, axis=0)
        height, width = sample.grid_shape
        probability = ensemble.mean(axis=0).reshape(height, width)
        uncertainty = ensemble.std(axis=0).reshape(height, width)
        metadata: Dict[str, object] = {
            "product_type": (
                "MODEL-ESTIMATED FINE-SCALE FIRE-DETECTION "
                "PROBABILITY + UNCERTAINTY"
            ),
            "target_interpretation": (
                "Probability of observed VIIRS point occupancy under the "
                "sample-generation process; not physical fire state."
            ),
            "target_data_status": "model_estimate",
            "model": "RandomForestClassifier",
            "modis_id": sample.modis_id,
            "observation_datetime": sample.metadata.get(
                "observation_datetime"
            ),
            "grid": sample.metadata.get("grid"),
            "uncertainty_method": (
                "standard deviation of per-tree positive-class probabilities; "
                "an uncalibrated ensemble-spread proxy, not a confidence interval"
            ),
        }
        return SpatialProbabilityEstimate(
            probability=probability,
            uncertainty_std=uncertainty,
            metadata=metadata,
        )
