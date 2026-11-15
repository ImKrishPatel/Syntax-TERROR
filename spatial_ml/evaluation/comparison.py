"""Leakage-resistant evaluation of the Random Forest and spatial ensemble."""

import csv
import json
import math
import platform
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np
import pandas as pd

from spatial_ml.data.samples import TrainingSample
from spatial_ml.evaluation.calibration import (
    CalibrationAttempt,
    fit_calibrator_on_validation,
)
from spatial_ml.evaluation.probabilities import evaluate_probability_grids
from spatial_ml.evaluation.splitting import EvaluationSplit, spatial_block_id
from spatial_ml.evaluation.uncertainty import evaluate_uncertainty
from spatial_ml.models.probabilistic_baseline import (
    ProbabilisticRandomForest,
    SpatialProbabilityEstimate,
)
from spatial_ml.models.probabilistic_spatial import ProbabilisticSpatialEnsemble


MODEL_NAMES = ("random_forest", "spatial_ensemble")
PRODUCT_LABEL = "MODEL-ESTIMATED FINE-SCALE FIRE PROBABILITY + UNCERTAINTY"
TARGET_DEFINITION = "observed_viirs_point_occupancy"


class ProbabilityModel(Protocol):
    """Shared fitted-model interface required for fair evaluation."""

    training_sample_ids: Tuple[str, ...]
    feature_names: Tuple[str, ...]

    def predict(self, sample: TrainingSample) -> SpatialProbabilityEstimate:
        """Return a per-cell probability and uncertainty estimate."""


@dataclass(frozen=True)
class EvaluationComparison:
    """Test predictions and metrics for two models on identical parent samples."""

    report: Dict[str, object]
    sample_ids: Tuple[str, ...]
    target_grids: np.ndarray
    raw_probabilities: Mapping[str, np.ndarray]
    calibrated_probabilities: Mapping[str, Optional[np.ndarray]]
    uncertainty_grids: Mapping[str, np.ndarray]
    calibration_attempts: Mapping[str, CalibrationAttempt]
    reliability_tables: Mapping[str, pd.DataFrame]


def _validate_partition(
    samples: Sequence[TrainingSample], name: str
) -> None:
    identifiers = [sample.modis_id for sample in samples]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"{name} partition contains duplicate parent MODIS IDs.")
    for sample in samples:
        if sample.metadata.get("target_data_status") != "observed":
            raise ValueError(
                f"Sample {sample.modis_id!r} must have observed VIIRS targets."
            )
        if (
            sample.metadata.get("target_representation")
            != "binary_viirs_point_observation_occupancy"
        ):
            raise ValueError(
                f"Sample {sample.modis_id!r} does not use the observed VIIRS "
                "point-occupancy target definition."
            )


def _predictions(
    model: ProbabilityModel, samples: Sequence[TrainingSample]
) -> Tuple[np.ndarray, np.ndarray]:
    probability_grids = []
    uncertainty_grids = []
    for sample in samples:
        estimate = model.predict(sample)
        probability_grids.append(np.asarray(estimate.probability, dtype=np.float32))
        uncertainty_grids.append(
            np.asarray(estimate.uncertainty_std, dtype=np.float32)
        )
    return np.stack(probability_grids), np.stack(uncertainty_grids)


def _reliability_records(frame: pd.DataFrame) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        records.append(
            {
                key: (None if pd.isna(value) else value)
                for key, value in row.items()
            }
        )
    return records


def _metric_summary(
    targets: np.ndarray,
    probabilities: np.ndarray,
    parent_ids: Sequence[str],
    n_bins: int,
    parent_brier_by_id: Optional[Mapping[str, float]] = None,
) -> Tuple[Dict[str, object], pd.DataFrame]:
    target_flat = targets.reshape(-1)
    probability_flat = probabilities.reshape(-1)
    evaluation = evaluate_probability_grids(
        target_flat.reshape(1, -1), probability_flat.reshape(1, -1), n_bins=n_bins
    )
    cell_count = int(target_flat.size)
    positive_count = int(np.count_nonzero(target_flat == 1))
    reliability = evaluation.reliability.copy()
    populated = reliability.loc[reliability["count"] > 0]
    expected_calibration_error = float(
        np.sum(
            populated["count"].to_numpy(dtype=float)
            / cell_count
            * np.abs(
                populated["mean_probability"].to_numpy(dtype=float)
                - populated["observed_frequency"].to_numpy(dtype=float)
            )
        )
    )
    if parent_brier_by_id is None:
        if len(parent_ids) != targets.shape[0]:
            raise ValueError("Parent IDs must align with metric grid rows.")
        parent_brier_by_id = {
            str(parent_id): float(
                np.mean((probabilities[index] - targets[index]) ** 2)
            )
            for index, parent_id in enumerate(parent_ids)
        }
    parent_brier = list(parent_brier_by_id.values())
    summary: Dict[str, object] = {
        "parent_sample_count": len(set(parent_ids)),
        "target_cell_count": cell_count,
        "positive_cell_count": positive_count,
        "negative_cell_count": cell_count - positive_count,
        "positive_cell_prevalence": positive_count / cell_count,
        "brier_score": evaluation.brier_score,
        "log_loss": evaluation.log_loss,
        "roc_auc": evaluation.roc_auc,
        "pr_auc": evaluation.pr_auc,
        "expected_calibration_error": expected_calibration_error,
        "metric_status": evaluation.metric_status,
        "reliability": _reliability_records(reliability),
        "parent_level_brier": {
            "mean": float(np.mean(parent_brier)),
            "median": float(np.median(parent_brier)),
            "standard_deviation": float(np.std(parent_brier)),
            "parent_count": len(parent_brier),
            "per_parent": [
                {"parent_id": str(parent_id), "brier_score": score}
                for parent_id, score in parent_brier_by_id.items()
            ],
        },
    }
    return summary, reliability


def _subgroup_summary(
    targets: np.ndarray,
    probabilities: np.ndarray,
    samples: Sequence[TrainingSample],
    n_bins: int,
    block_size_m: Optional[float],
) -> Dict[str, object]:
    results: Dict[str, object] = {}

    year_groups: Dict[str, List[int]] = {}
    for index, sample in enumerate(samples):
        timestamp = pd.Timestamp(sample.metadata["observation_datetime"])
        year_groups.setdefault(str(timestamp.year), []).append(index)
    results["year"] = {
        key: _group_metric(targets, probabilities, samples, indices, n_bins)
        for key, indices in sorted(year_groups.items())
    }

    if block_size_m is None:
        results["spatial_block"] = {
            "status": "unavailable: split has no configured block_size_m"
        }
    else:
        block_groups: Dict[str, List[int]] = {}
        for index, sample in enumerate(samples):
            block = spatial_block_id(sample, block_size_m)
            block_groups.setdefault(block, []).append(index)
        results["spatial_block"] = {
            key: _group_metric(targets, probabilities, samples, indices, n_bins)
            for key, indices in sorted(block_groups.items())
        }

    land_cover_index = None
    if samples:
        try:
            land_cover_index = samples[0].environmental_feature_names.index(
                "land_cover"
            )
        except ValueError:
            pass
    if land_cover_index is None:
        results["land_cover"] = {
            "status": "unavailable: no land_cover feature channel"
        }
    else:
        land_cover_groups: Dict[str, List[Tuple[int, int]]] = {}
        for sample_index, sample in enumerate(samples):
            values = sample.environmental_features[:, :, land_cover_index]
            for cell_index, value in enumerate(values.reshape(-1)):
                if np.isfinite(value):
                    category = _category_label(float(value))
                    land_cover_groups.setdefault(category, []).append(
                        (sample_index, cell_index)
                    )
        if len(land_cover_groups) > 30:
            results["land_cover"] = {
                "status": (
                    "unavailable: more than 30 distinct codes; check whether "
                    "land_cover is encoded as a categorical raster"
                ),
                "distinct_code_count": len(land_cover_groups),
            }
        else:
            results["land_cover"] = {
                key: _land_cover_metric(
                    targets, probabilities, samples, cells, n_bins
                )
                for key, cells in sorted(land_cover_groups.items())
            }
    return results


def _category_label(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return format(value, ".8g")


def _group_metric(
    targets: np.ndarray,
    probabilities: np.ndarray,
    samples: Sequence[TrainingSample],
    indices: Sequence[int],
    n_bins: int,
) -> Dict[str, object]:
    selected_targets = targets[np.asarray(indices)]
    selected_probabilities = probabilities[np.asarray(indices)]
    parent_ids = [samples[index].modis_id for index in indices]
    summary, _ = _metric_summary(
        selected_targets, selected_probabilities, parent_ids, n_bins
    )
    return summary


def _land_cover_metric(
    targets: np.ndarray,
    probabilities: np.ndarray,
    samples: Sequence[TrainingSample],
    cells: Sequence[Tuple[int, int]],
    n_bins: int,
) -> Dict[str, object]:
    target_values = np.asarray(
        [targets[sample_index].reshape(-1)[cell_index] for sample_index, cell_index in cells]
    )
    probability_values = np.asarray(
        [
            probabilities[sample_index].reshape(-1)[cell_index]
            for sample_index, cell_index in cells
        ]
    )
    parent_ids = sorted({samples[sample_index].modis_id for sample_index, _ in cells})
    parent_brier = {
        parent_id: float(
            np.mean(
                [
                    (
                        probabilities[sample_index].reshape(-1)[cell_index]
                        - targets[sample_index].reshape(-1)[cell_index]
                    )
                    ** 2
                    for sample_index, cell_index in cells
                    if samples[sample_index].modis_id == parent_id
                ]
            )
        )
        for parent_id in parent_ids
    }
    summary, _ = _metric_summary(
        target_values.reshape(1, -1),
        probability_values.reshape(1, -1),
        parent_ids,
        n_bins,
        parent_brier_by_id=parent_brier,
    )
    return summary


def _partition_summary(samples: Sequence[TrainingSample]) -> Dict[str, object]:
    timestamps = [
        pd.Timestamp(sample.metadata["observation_datetime"]) for sample in samples
    ]
    latitudes = [float(sample.metadata["modis_latitude"]) for sample in samples]
    longitudes = [float(sample.metadata["modis_longitude"]) for sample in samples]
    return {
        "parent_sample_count": len(samples),
        "observation_start": min(timestamps).isoformat(),
        "observation_end": max(timestamps).isoformat(),
        "latitude_bounds": [min(latitudes), max(latitudes)],
        "longitude_bounds": [min(longitudes), max(longitudes)],
    }


def _package_versions() -> Dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in ("numpy", "pandas", "scikit-learn", "torch"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def compare_models(
    split: EvaluationSplit,
    random_forest: ProbabilisticRandomForest,
    spatial_ensemble: ProbabilisticSpatialEnsemble,
    *,
    calibration_method: str = "platt",
    min_calibration_cells: int = 20,
    min_calibration_per_class: int = 5,
    min_calibration_parents: int = 3,
    n_bins: int = 10,
    input_provenance: Optional[Dict[str, object]] = None,
) -> EvaluationComparison:
    """Compare fitted models without test-fitted decisions or population drift."""
    if calibration_method not in {"platt", "isotonic"}:
        raise ValueError("calibration_method must be 'platt' or 'isotonic'.")
    if isinstance(n_bins, bool) or not isinstance(n_bins, int) or n_bins < 1:
        raise ValueError("n_bins must be a positive integer.")
    for name in ("train", "validation", "test"):
        _validate_partition(getattr(split, name), name)
    if not split.test or not split.validation:
        raise ValueError("Held-out validation and test partitions must be nonempty.")
    train_ids = {sample.modis_id for sample in split.train}
    validation_ids = {sample.modis_id for sample in split.validation}
    test_ids = {sample.modis_id for sample in split.test}
    if train_ids & (validation_ids | test_ids):
        raise ValueError("Training parents overlap a held-out evaluation partition.")

    expected_training_ids = set(getattr(random_forest, "training_sample_ids", ()))
    spatial_training_ids = set(getattr(spatial_ensemble, "training_sample_ids", ()))
    if not expected_training_ids or expected_training_ids != spatial_training_ids:
        raise ValueError(
            "Both models must be fitted on the same nonempty parent-sample population."
        )
    if not expected_training_ids.issubset(train_ids):
        raise ValueError("A model was fitted using samples outside the training split.")
    if expected_training_ids & (validation_ids | test_ids):
        raise ValueError("A held-out parent sample was used to fit a model.")
    if tuple(getattr(random_forest, "feature_names", ())) != tuple(
        getattr(spatial_ensemble, "feature_names", ())
    ):
        raise ValueError("The two models use different input feature schemas.")

    target_grids = np.stack(
        [np.asarray(sample.target_grid, dtype=np.float32) for sample in split.test]
    )
    if not np.isin(target_grids, (0, 1)).all():
        raise ValueError("Test targets must be binary observed VIIRS occupancy.")
    models = {
        "random_forest": random_forest,
        "spatial_ensemble": spatial_ensemble,
    }
    raw_predictions: Dict[str, np.ndarray] = {}
    uncertainties: Dict[str, np.ndarray] = {}
    calibrated: Dict[str, Optional[np.ndarray]] = {}
    attempts: Dict[str, CalibrationAttempt] = {}
    reliability_tables: Dict[str, pd.DataFrame] = {}
    model_reports: Dict[str, object] = {}
    block_size = split.configuration.get("block_size_m")
    block_size_m = float(block_size) if block_size is not None else None

    for name, model in models.items():
        test_probability, test_uncertainty = _predictions(model, split.test)
        validation_probability, _ = _predictions(model, split.validation)
        raw_predictions[name] = test_probability
        uncertainties[name] = test_uncertainty
        attempt = fit_calibrator_on_validation(
            [sample.target_grid for sample in split.validation],
            list(validation_probability),
            [sample.modis_id for sample in split.validation],
            method=calibration_method,
            min_cells=min_calibration_cells,
            min_per_class=min_calibration_per_class,
            min_parent_samples=min_calibration_parents,
            split_configuration={
                **split.configuration,
                "strategy": split.strategy,
            },
        )
        attempts[name] = attempt
        calibrated_probability = (
            attempt.calibrator.apply(test_probability)
            if attempt.calibrator is not None
            else None
        )
        calibrated[name] = calibrated_probability

        raw_summary, raw_reliability = _metric_summary(
            target_grids,
            test_probability,
            [sample.modis_id for sample in split.test],
            n_bins,
        )
        reliability_tables[f"{name}_raw"] = raw_reliability
        calibrated_summary = None
        if calibrated_probability is not None:
            calibrated_summary, calibrated_reliability = _metric_summary(
                target_grids,
                calibrated_probability,
                [sample.modis_id for sample in split.test],
                n_bins,
            )
            reliability_tables[f"{name}_calibrated"] = calibrated_reliability
        model_reports[name] = {
            "uncertainty_method": (
                "standard deviation across fitted decision trees"
                if name == "random_forest"
                else "standard deviation across independently initialized CNN ensemble members"
            ),
            "calibration": {
                "status": attempt.status,
                "reason": attempt.reason,
                "method": calibration_method,
                "fit_partition": "validation",
                "validation_parent_ids": list(attempt.validation_parent_ids),
                "validation_cell_count": attempt.validation_cell_count,
                "positive_cell_count": attempt.positive_cell_count,
                "negative_cell_count": attempt.negative_cell_count,
            },
            "test_raw": raw_summary,
            "test_calibrated": calibrated_summary,
            "uncertainty_diagnostics": evaluate_uncertainty(
                target_grids, test_probability, test_uncertainty
            ),
            "subgroups_raw": _subgroup_summary(
                target_grids,
                test_probability,
                split.test,
                n_bins,
                block_size_m,
            ),
            "subgroups_calibrated": (
                _subgroup_summary(
                    target_grids,
                    calibrated_probability,
                    split.test,
                    n_bins,
                    block_size_m,
                )
                if calibrated_probability is not None
                else None
            ),
        }

    report: Dict[str, object] = {
        "report_version": "1.0.0",
        "product_label": PRODUCT_LABEL,
        "target_definition": TARGET_DEFINITION,
        "target_interpretation": (
            "Binary observed VIIRS point occupancy in modeled local cells; "
            "not a physical pixel footprint or perfect fire-state label."
        ),
        "split": {
            "strategy": split.strategy,
            "configuration": split.configuration,
            "dropped_parent_ids": list(split.dropped_parent_ids),
            "dropped_parent_count": len(split.dropped_parent_ids),
            "train": _partition_summary(split.train),
            "validation": _partition_summary(split.validation),
            "test": _partition_summary(split.test),
            "test_sample_ids": [sample.modis_id for sample in split.test],
        },
        "comparison_population": {
            "test_parent_sample_ids": [sample.modis_id for sample in split.test],
            "identical_for_models": True,
            "test_parent_sample_count": len(split.test),
            "target_cell_count": int(target_grids.size),
            "positive_cell_count": int(np.count_nonzero(target_grids == 1)),
            "negative_cell_count": int(np.count_nonzero(target_grids == 0)),
        },
        "training_population": {
            "model_fit_parent_sample_ids": sorted(expected_training_ids),
            "model_fit_parent_sample_count": len(expected_training_ids),
        },
        "model_configuration": {
            "feature_names": list(random_forest.feature_names),
            "random_forest": {
                "n_estimators": random_forest.n_estimators,
                "max_depth": random_forest.max_depth,
                "random_state": random_forest.random_state,
            },
            "spatial_ensemble": {
                "grid_height": spatial_ensemble.grid_height,
                "grid_width": spatial_ensemble.grid_width,
                "hidden_channels": spatial_ensemble.hidden_channels,
                "conv_blocks": spatial_ensemble.conv_blocks,
                "ensemble_size": spatial_ensemble.ensemble_size,
                "epochs": spatial_ensemble.epochs,
                "batch_size": spatial_ensemble.batch_size,
                "learning_rate": spatial_ensemble.learning_rate,
                "random_seed": spatial_ensemble.random_seed,
                "member_seeds": list(spatial_ensemble.member_seeds),
            },
            "calibration_method": calibration_method,
            "metric_reliability_bins": n_bins,
        },
        "software_versions": _package_versions(),
        "input_provenance": input_provenance or {},
        "models": model_reports,
        "performance_claim_status": (
            "Evaluation values describe only the supplied held-out local data; "
            "synthetic fixtures are software tests, not scientific evidence."
        ),
    }
    return EvaluationComparison(
        report=report,
        sample_ids=tuple(sample.modis_id for sample in split.test),
        target_grids=target_grids,
        raw_probabilities=raw_predictions,
        calibrated_probabilities=calibrated,
        uncertainty_grids=uncertainties,
        calibration_attempts=attempts,
        reliability_tables=reliability_tables,
    )


def write_evaluation_artifacts(
    evaluation: EvaluationComparison,
    output_dir: str | Path,
) -> Dict[str, Path]:
    """Write reproducible JSON, Markdown, CSV, NPZ, and calibration artifacts."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths: Dict[str, Path] = {}
    metrics_path = destination / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as stream:
        json.dump(evaluation.report, stream, indent=2, allow_nan=False)
    paths["metrics"] = metrics_path

    report_path = destination / "report.md"
    report_path.write_text(_markdown_report(evaluation.report), encoding="utf-8")
    paths["report"] = report_path

    prediction_arrays: Dict[str, np.ndarray] = {
        "sample_ids": np.asarray(evaluation.sample_ids, dtype=str),
        "target_grids": evaluation.target_grids,
    }
    for name in MODEL_NAMES:
        prediction_arrays[f"{name}_raw_probability"] = evaluation.raw_probabilities[name]
        prediction_arrays[f"{name}_uncertainty"] = evaluation.uncertainty_grids[name]
        calibrated = evaluation.calibrated_probabilities[name]
        if calibrated is not None:
            prediction_arrays[f"{name}_calibrated_probability"] = calibrated
    prediction_path = destination / "test_predictions.npz"
    np.savez_compressed(prediction_path, **prediction_arrays)
    paths["predictions"] = prediction_path

    reliability_path = destination / "reliability.csv"
    with reliability_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "model",
                "variant",
                "bin_index",
                "lower_probability",
                "upper_probability",
                "count",
                "mean_probability",
                "observed_frequency",
            ),
        )
        writer.writeheader()
        for key, table in evaluation.reliability_tables.items():
            model_name, variant = key.rsplit("_", 1)
            for row in table.to_dict(orient="records"):
                writer.writerow({"model": model_name, "variant": variant, **row})
    paths["reliability"] = reliability_path

    for name, attempt in evaluation.calibration_attempts.items():
        if attempt.calibrator is not None:
            path = destination / f"{name}_calibrator.json"
            attempt.calibrator.save(path)
            paths[f"{name}_calibrator"] = path
    return paths


def _markdown_report(report: Mapping[str, object]) -> str:
    split = report["split"]
    population = report["comparison_population"]
    models = report["models"]
    lines = [
        "# Held-out spatial probability evaluation",
        "",
        f"**Product:** {report['product_label']}",
        "",
        f"**Target:** {report['target_interpretation']}",
        "",
        "## Evaluation design",
        "",
        f"- Split strategy: `{split['strategy']}`",
        f"- Test parent samples: {population['test_parent_sample_count']}",
        f"- Test target cells: {population['target_cell_count']}",
        f"- Positive / negative cells: {population['positive_cell_count']} / "
        f"{population['negative_cell_count']}",
        f"- Same test population for both models: {population['identical_for_models']}",
        "- Calibration is fitted on validation parents only; test data are not used "
        "to choose or fit calibration.",
        "",
        "## Test metrics",
        "",
        "| Model | Variant | Brier | Log loss | ROC-AUC | PR-AUC | ECE |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for model_name, model in models.items():
        for variant in ("test_raw", "test_calibrated"):
            metrics = model[variant]
            if metrics is None:
                status = model["calibration"]["reason"] or "unavailable"
                row = (
                    f"| {model_name} | calibrated unavailable ({status}) "
                    "| NA | NA | NA | NA | NA |"
                )
                lines.append(
                    row
                )
                continue
            lines.append(
                "| {model} | {variant} | {brier:.6f} | {loss:.6f} | {roc} | "
                "{pr} | {ece:.6f} |".format(
                    model=model_name,
                    variant="raw" if variant == "test_raw" else "calibrated",
                    brier=metrics["brier_score"],
                    loss=metrics["log_loss"],
                    roc=_format_metric(metrics["roc_auc"]),
                    pr=_format_metric(metrics["pr_auc"]),
                    ece=metrics["expected_calibration_error"],
                )
            )
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- VIIRS labels are observed point occupancy in modeled cells, not perfect "
            "physical-fire ground truth.",
            "- Ensemble disagreement is an uncalibrated uncertainty proxy, not a "
            "confidence interval.",
            "- Results apply only to the supplied held-out local data. Synthetic "
            "fixtures are software tests and must not be reported as model performance.",
            "- Historical outputs, if later produced, must remain labeled "
            f"**{PRODUCT_LABEL}**.",
            "",
        ]
    )
    return "\n".join(lines)


def _format_metric(value: object) -> str:
    if value is None:
        return "undefined"
    if not math.isfinite(float(value)):
        return "undefined"
    return f"{float(value):.6f}"
