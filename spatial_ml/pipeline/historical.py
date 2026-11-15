"""Batch historical MODIS inference with explicit exploratory-evidence gates."""

import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import rasterio

from spatial_ml import config
from spatial_ml.data.cleaning import clean_firms_records
from spatial_ml.data.firms import normalize_firms_schema
from spatial_ml.data.samples import SpatialFeatureInput
from spatial_ml.data.target_grid import LocalTargetGrid
from spatial_ml.evaluation.calibration import ProbabilityCalibrator
from spatial_ml.evaluation.calibration import CALIBRATION_VERSION
from spatial_ml.features.environmental import (
    RasterFeatureSource,
    extract_environmental_features,
)
from spatial_ml.models.probabilistic_baseline import (
    SpatialProbabilityEstimate,
    sample_to_cell_feature_matrix,
)
from spatial_ml.models.probabilistic_spatial import (
    MODEL_VERSION,
    PREPROCESSING_VERSION,
    ProbabilisticSpatialEnsemble,
)
from spatial_ml.pipeline.overlap import generate_observation_ids

HISTORICAL_PRODUCT_LABEL = (
    "MODEL-ESTIMATED FINE-SCALE FIRE-DETECTION PROBABILITY + UNCERTAINTY"
)
OUTPUT_SCHEMA_VERSION = "1.0.0"
SOURCE_ID_COLUMNS = (
    "source_observation_id",
    "firms_id",
    "record_id",
    "observation_id",
    "id",
)
OUTPUT_COLUMNS = (
    "parent_modis_observation_id",
    "target_cell_id",
    "cell_center_latitude",
    "cell_center_longitude",
    "grid_resolution_m",
    "observation_datetime",
    "source_latitude",
    "source_longitude",
    "source_timestamp",
    "satellite",
    "instrument",
    "raw_estimated_detection_probability",
    "calibrated_estimated_detection_probability",
    "uncertainty_estimate",
    "uncertainty_type",
    "uncertainty_method",
    "source_sensor",
    "source_file_name",
    "source_file_sha256",
    "source_row_number",
    "source_observation_id",
    "model_name",
    "model_version",
    "preprocessing_version",
    "calibration_version",
    "calibration_method",
    "historical_period",
    "estimated",
    "reference_observation_available",
    "missing_feature_channels",
    "outside_training_support",
    "outside_training_support_features",
    "outside_training_geographic_bounds",
    "outside_training_date_range",
)
FAILURE_COLUMNS = (
    "batch_index",
    "source_row_number",
    "source_observation_id",
    "source_reason_code",
    "source_reason",
)


class PredictionValidationError(ValueError):
    """A model output or generated batch failed a required quality check."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class HistoricalRunConfig:
    """Local input, model, geographic/date selection, and output settings."""

    modis_csv: Path
    model_artifact: Path
    output_dir: Path = config.HISTORICAL_OUTPUTS_DIR / "run"
    selected_model: str = "spatial_ensemble"
    start_date: str = config.HISTORICAL_DEFAULT_START_DATE
    end_date: str = config.HISTORICAL_DEFAULT_END_DATE
    batch_size: int = config.HISTORICAL_INFERENCE_BATCH_SIZE
    bbox: Optional[Tuple[float, float, float, float]] = None
    environmental_sources: Tuple[RasterFeatureSource, ...] = ()
    calibration_artifact: Optional[Path] = None
    m7_evaluation_report: Optional[Path] = None
    allow_exploratory: bool = False
    resume: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "modis_csv", Path(self.modis_csv))
        object.__setattr__(self, "model_artifact", Path(self.model_artifact))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.calibration_artifact is not None:
            object.__setattr__(
                self, "calibration_artifact", Path(self.calibration_artifact)
            )
        if self.m7_evaluation_report is not None:
            object.__setattr__(
                self, "m7_evaluation_report", Path(self.m7_evaluation_report)
            )
        if self.selected_model != "spatial_ensemble":
            raise ValueError(
                "Only the saved spatial_ensemble artifact can currently be loaded "
                "safely; the Random Forest has no existing serialization interface."
            )
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or self.batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer.")
        start = _parse_date(self.start_date, "start_date")
        end = _parse_date(self.end_date, "end_date")
        earliest = pd.Timestamp(config.HISTORICAL_DEFAULT_START_DATE)
        latest = pd.Timestamp(config.HISTORICAL_DEFAULT_END_DATE)
        if start > end:
            raise ValueError("start_date must be on or before end_date.")
        if start < earliest or end > latest:
            raise ValueError(
                "Historical date filters must stay within "
                f"{earliest.date()} through {latest.date()}."
            )
        if self.bbox is not None:
            west, south, east, north = self.bbox
            if (
                not all(math.isfinite(value) for value in self.bbox)
                or not -180 <= west < east <= 180
                or not -90 <= south < north <= 90
            ):
                raise ValueError(
                    "bbox must be (west, south, east, north) with valid "
                    "non-wrapping WGS84 bounds."
                )
        names = [source.name for source in self.environmental_sources]
        if len(set(names)) != len(names):
            raise ValueError("Environmental source names must be unique.")


@dataclass(frozen=True)
class HistoricalRunResult:
    """Persistent processing result locations and completion counts."""

    output_dir: Path
    report_path: Path
    manifest_path: Path
    status: str
    processed_observation_count: int
    prediction_record_count: int
    failed_observation_count: int


def _parse_date(value: object, name: str) -> pd.Timestamp:
    try:
        result = pd.Timestamp(value).normalize()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid ISO date.") from exc
    if pd.isna(result):
        raise ValueError(f"{name} must be a valid ISO date.")
    if result.tzinfo is not None:
        result = result.tz_convert("UTC").tz_localize(None)
    return result


def _utc_naive_timestamp(value: object, name: str) -> pd.Timestamp:
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid timestamp.") from exc
    if pd.isna(result):
        raise ValueError(f"{name} must be a valid timestamp.")
    if result.tzinfo is not None:
        result = result.tz_convert("UTC").tz_localize(None)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_input_headers(path: Path) -> None:
    try:
        columns = pd.read_csv(path, dtype=str, nrows=0).columns
    except pd.errors.EmptyDataError as exc:
        raise ValueError("Historical MODIS FIRMS CSV has no header.") from exc
    normalized = {
        str(column).strip().lstrip("\ufeff").lower() for column in columns
    }
    has_latitude = bool(normalized & {"latitude", "lat"})
    has_longitude = bool(normalized & {"longitude", "lon", "long"})
    has_datetime = bool(
        normalized
        & {
            "datetime",
            "acq_datetime",
            "acquisition_datetime",
            "timestamp",
        }
    ) or (
        bool(normalized & {"date", "acq_date", "acquisition_date"})
        and bool(normalized & {"time", "acq_time", "acquisition_time"})
    )
    if not has_latitude or not has_longitude or not has_datetime:
        raise ValueError(
            "Historical MODIS FIRMS CSV must contain latitude, longitude, and "
            "either a timestamp or acquisition date/time columns."
        )
    if not (normalized & {"satellite", "sat", "platform"}):
        raise ValueError(
            "Historical MODIS FIRMS CSV must include a satellite column so "
            "Terra/Aqua coverage can be validated."
        )


def _canonical_json_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _evaluation_supports_selection(
    report_path: Optional[Path],
    selected_model: str,
    model: ProbabilisticSpatialEnsemble,
    model_artifact_sha256: str,
) -> Tuple[bool, bool, str]:
    if report_path is None:
        return False, False, "No Milestone 7 evaluation report was supplied."
    if not report_path.is_file():
        raise FileNotFoundError(f"Milestone 7 report does not exist: {report_path}")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the Milestone 7 report: {exc}") from exc

    if not isinstance(report, dict):
        return False, False, "The M7 report root must be a JSON object."
    evidence = report.get("evaluation_evidence", {})
    population = report.get("comparison_population", {})
    models = report.get("models", {})
    if not isinstance(evidence, dict) or evidence.get(
        "classification"
    ) != "real_observed_firms":
        return (
            False,
            False,
            "The M7 report does not explicitly attest that evaluation used "
            "real observed FIRMS data.",
        )
    if not evidence.get("attested_by"):
        return False, False, "The M7 real-data evidence has no named attestation."
    if not isinstance(population, dict) or not population.get(
        "identical_for_models"
    ):
        return (
            False,
            False,
            "The M7 models were not confirmed on an identical test population.",
        )
    if not isinstance(models, dict) or not {
        "random_forest",
        "spatial_ensemble",
    }.issubset(models):
        return False, False, "The M7 report does not contain both model results."
    training = report.get("training_population", {})
    if not isinstance(training, dict) or set(
        map(str, training.get("model_fit_parent_sample_ids", ()))
    ) != set(model.training_sample_ids):
        return (
            False,
            False,
            "The M7 report does not match the saved model's training parents.",
        )
    model_configuration = report.get("model_configuration", {})
    if not isinstance(model_configuration, dict) or tuple(
        model_configuration.get("feature_names", ())
    ) != tuple(model.feature_names):
        return (
            False,
            False,
            "The M7 report feature schema does not match the saved model.",
        )
    model_artifacts = report.get("model_artifacts", {})
    spatial_artifact = (
        model_artifacts.get("spatial_ensemble")
        if isinstance(model_artifacts, dict)
        else None
    )
    if (
        not isinstance(spatial_artifact, dict)
        or spatial_artifact.get("sha256") != model_artifact_sha256
    ):
        return (
            False,
            False,
            "The M7 report does not identify this exact saved model artifact.",
        )
    spatial_configuration = model_configuration.get("spatial_ensemble")
    expected_spatial_configuration = {
        "grid_height": model.grid_height,
        "grid_width": model.grid_width,
        "hidden_channels": model.hidden_channels,
        "conv_blocks": model.conv_blocks,
        "ensemble_size": model.ensemble_size,
        "epochs": model.epochs,
        "batch_size": model.batch_size,
        "learning_rate": model.learning_rate,
        "random_seed": model.random_seed,
        "member_seeds": list(model.member_seeds),
    }
    if spatial_configuration != expected_spatial_configuration:
        return (
            False,
            False,
            "The M7 report configuration does not match the saved model.",
        )

    if not all(
        isinstance(models[name], dict)
        for name in ("random_forest", "spatial_ensemble")
    ):
        return False, False, "The M7 report model results must be JSON objects."
    rf_metrics = models["random_forest"].get("test_calibrated")
    cnn_metrics = models["spatial_ensemble"].get("test_calibrated")
    if rf_metrics is None or cnn_metrics is None:
        rf_metrics = models["random_forest"].get("test_raw")
        cnn_metrics = models["spatial_ensemble"].get("test_raw")
    if not isinstance(rf_metrics, dict) or not isinstance(cnn_metrics, dict):
        return False, False, "The M7 report has no comparable probability metrics."

    def finite_metric(metrics: Mapping[str, object], name: str) -> Optional[float]:
        try:
            value = float(metrics[name])
        except (KeyError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    rf_brier = finite_metric(rf_metrics, "brier_score")
    cnn_brier = finite_metric(cnn_metrics, "brier_score")
    rf_loss = finite_metric(rf_metrics, "log_loss")
    cnn_loss = finite_metric(cnn_metrics, "log_loss")
    rf_ece = finite_metric(rf_metrics, "expected_calibration_error")
    cnn_ece = finite_metric(cnn_metrics, "expected_calibration_error")
    if None in (rf_brier, cnn_brier, rf_loss, cnn_loss, rf_ece, cnn_ece):
        return (
            False,
            False,
            "The M7 report is missing finite Brier, log-loss, or ECE values.",
        )
    preferred = (
        "spatial_ensemble"
        if cnn_brier < rf_brier and cnn_loss < rf_loss and cnn_ece <= rf_ece
        else "random_forest"
        if rf_brier < cnn_brier and rf_loss < cnn_loss and rf_ece <= cnn_ece
        else None
    )
    if preferred != selected_model:
        return (
            False,
            True,
            "The selected model does not meet the predeclared M7 gate: lower "
            "Brier score and log loss, with no worse ECE, on the same real "
            "held-out population.",
        )
    return (
        True,
        True,
        "The selected model passed the attested real-data M7 Brier/log-loss/ECE gate.",
    )


def load_historical_artifacts(
    config_value: HistoricalRunConfig,
) -> Tuple[
    ProbabilisticSpatialEnsemble,
    Optional[ProbabilityCalibrator],
    Dict[str, object],
]:
    """Load a compatible saved CNN and only validation-provenanced calibration."""
    if not config_value.model_artifact.is_file():
        raise FileNotFoundError(
            "A trained spatial ensemble artifact is required. Train and save it "
            f"from M7 first: {config_value.model_artifact}"
        )
    model = ProbabilisticSpatialEnsemble.load(config_value.model_artifact)
    model_artifact_sha256 = _sha256(config_value.model_artifact)
    if model.artifact_preprocessing_version != PREPROCESSING_VERSION:
        raise ValueError(
            "Model preprocessing version is missing or incompatible; expected "
            f"{PREPROCESSING_VERSION!r}, got "
            f"{model.artifact_preprocessing_version!r}."
        )
    if not model.training_sample_ids or not model.training_parent_metadata:
        raise ValueError("Model artifact lacks training-sample provenance.")
    preprocessing_summary = model.preprocessing_summary()
    if tuple(preprocessing_summary) != model.feature_names:
        raise ValueError(
            "Saved preprocessing feature order does not match model feature names."
        )
    for metadata in model.training_parent_metadata:
        try:
            latitude = float(metadata["modis_latitude"])
            longitude = float(metadata["modis_longitude"])
            _utc_naive_timestamp(
                metadata["observation_datetime"], "training observation datetime"
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "Model artifact lacks valid training date/geographic provenance."
            ) from exc
        if (
            metadata.get("grid_height") != model.grid_height
            or metadata.get("grid_width") != model.grid_width
            or not isinstance(metadata.get("grid_resolution_m"), (int, float))
            or not math.isfinite(float(metadata["grid_resolution_m"]))
            or float(metadata["grid_resolution_m"]) <= 0
            or not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
            or metadata.get("modis_satellite") is None
        ):
            raise ValueError(
                "Model artifact has incomplete or incompatible training/grid provenance."
            )
    resolutions = {
        float(metadata["grid_resolution_m"])
        for metadata in model.training_parent_metadata
    }
    if len(resolutions) != 1:
        raise ValueError("Training samples used inconsistent target-grid resolutions.")
    training_ids = set(model.training_sample_ids)
    if training_ids != {
        str(metadata.get("modis_id"))
        for metadata in model.training_parent_metadata
    }:
        raise ValueError(
            "Saved training IDs do not match model training-parent provenance."
        )

    calibrator = None
    calibration_info: Dict[str, object] = {
        "status": "not_supplied",
        "version": None,
        "method": None,
        "validation_parent_ids": [],
    }
    if config_value.calibration_artifact is not None:
        calibrator = ProbabilityCalibrator.load(config_value.calibration_artifact)
        provenance = calibrator.provenance
        validation_ids = provenance.get("validation_parent_ids")
        if (
            provenance.get("fit_partition") != "validation"
            or not isinstance(validation_ids, list)
            or not validation_ids
            or set(map(str, validation_ids)) & training_ids
        ):
            raise ValueError(
                "Calibration artifact is not demonstrably validation-fitted or "
                "overlaps the model's training parents."
            )
        calibration_info = {
            "status": "loaded_validation_fitted",
            "version": CALIBRATION_VERSION,
            "method": calibrator.method,
            "validation_parent_ids": list(map(str, validation_ids)),
            "validation_parent_count": len(validation_ids),
            "provenance": provenance,
        }
    evaluation_supported, comparison_is_valid, selection_reason = (
        _evaluation_supports_selection(
            config_value.m7_evaluation_report,
            config_value.selected_model,
            model,
            model_artifact_sha256,
        )
    )
    if comparison_is_valid and not evaluation_supported:
        raise ValueError(
            "The attested M7 held-out comparison does not support the selected "
            f"model: {selection_reason}"
        )
    if not evaluation_supported and not config_value.allow_exploratory:
        raise ValueError(
            "No real held-out M7 result supports model selection. For an "
            "explicitly exploratory, unvalidated pilot, set allow_exploratory=True."
        )
    if calibrator is not None:
        if config_value.m7_evaluation_report is None:
            raise ValueError(
                "A calibration artifact requires its matching M7 evaluation "
                "report for model/artifact provenance verification."
            )
        evaluation_report = json.loads(
            config_value.m7_evaluation_report.read_text(encoding="utf-8")
        )
        if not isinstance(evaluation_report, dict):
            raise ValueError("The M7 report root must be a JSON object.")
        model_artifacts = evaluation_report.get("model_artifacts", {})
        model_artifact_info = (
            model_artifacts.get("spatial_ensemble")
            if isinstance(model_artifacts, dict)
            else None
        )
        if (
            not isinstance(model_artifact_info, dict)
            or model_artifact_info.get("sha256") != model_artifact_sha256
        ):
            raise ValueError(
                "The M7 report does not identify this exact saved model artifact."
            )
        evaluation_models = evaluation_report.get("models", {})
        selected_report = (
            evaluation_models.get(config_value.selected_model)
            if isinstance(evaluation_models, dict)
            else None
        )
        if not isinstance(selected_report, dict):
            raise ValueError(
                "The M7 report is missing the selected model's calibration record."
            )
        report_calibration = selected_report.get("calibration", {})
        reported_validation_ids = report_calibration.get(
            "validation_parent_ids", ()
        )
        calibration_artifacts = evaluation_report.get("calibration_artifacts", {})
        calibration_artifact_info = (
            calibration_artifacts.get(config_value.selected_model)
            if isinstance(calibration_artifacts, dict)
            else None
        )
        if (
            report_calibration.get("status") != "available"
            or report_calibration.get("method") != calibrator.method
            or not isinstance(reported_validation_ids, (list, tuple))
            or set(map(str, reported_validation_ids))
            != set(calibration_info["validation_parent_ids"])
            or not isinstance(calibration_artifact_info, dict)
            or calibration_artifact_info.get("sha256")
            != _sha256(config_value.calibration_artifact)
        ):
            raise ValueError(
                "Calibration artifact hash, method, or validation IDs do not "
                "match the selected M7 model's recorded calibration."
            )

    training_metadata = list(model.training_parent_metadata)
    dates = [
        _utc_naive_timestamp(
            item["observation_datetime"], "training observation datetime"
        )
        for item in training_metadata
        if item.get("observation_datetime") is not None
    ]
    coordinates = [
        (float(item["modis_longitude"]), float(item["modis_latitude"]))
        for item in training_metadata
        if item.get("modis_longitude") is not None
        and item.get("modis_latitude") is not None
    ]
    satellites = Counter(
        str(item["modis_satellite"])
        for item in training_metadata
        if item.get("modis_satellite") is not None
    )
    instruments = Counter(
        str(item["modis_instrument"])
        for item in training_metadata
        if item.get("modis_instrument") is not None
    )
    training_provenance: Dict[str, object] = {
        "training_parent_count": len(model.training_sample_ids),
        "training_sample_ids_sha256": _canonical_json_hash(
            {"sample_ids": sorted(model.training_sample_ids)}
        ),
        "training_date_start": min(dates).isoformat() if dates else None,
        "training_date_end": max(dates).isoformat() if dates else None,
        "training_geographic_bounds": (
            {
                "west": min(point[0] for point in coordinates),
                "south": min(point[1] for point in coordinates),
                "east": max(point[0] for point in coordinates),
                "north": max(point[1] for point in coordinates),
            }
            if coordinates
            else None
        ),
        "training_satellite_counts": dict(satellites),
        "training_instrument_counts": dict(instruments),
        "grid_height": model.grid_height,
        "grid_width": model.grid_width,
        "grid_resolution_m": next(iter(resolutions)),
        "target_definition": "observed_viirs_occupancy",
    }
    model_provenance: Dict[str, object] = {
        "model_name": "ProbabilisticSpatialEnsemble",
        "model_version": MODEL_VERSION,
        "artifact_sha256": model_artifact_sha256,
        "artifact_file_name": config_value.model_artifact.name,
        "preprocessing_version": model.artifact_preprocessing_version,
        "feature_names": list(model.feature_names),
        "input_channel_names": list(model.input_channel_names),
        "training": training_provenance,
        "evaluation": {
            "report_file_name": (
                config_value.m7_evaluation_report.name
                if config_value.m7_evaluation_report is not None
                else None
            ),
            "report_sha256": (
                _sha256(config_value.m7_evaluation_report)
                if config_value.m7_evaluation_report is not None
                else None
            ),
            "selection_criteria": (
                "On the same attested real held-out population: strictly lower "
                "Brier score and log loss, with expected calibration error no "
                "worse than the alternative model."
            ),
            "selection_reason": selection_reason,
        },
        "selection_status": (
            "real_held_out_supported"
            if evaluation_supported
            else "exploratory_unvalidated"
        ),
        "selection_reason": selection_reason,
    }
    return model, calibrator, {
        "model": model_provenance,
        "calibration": calibration_info,
        "preprocessing_summary": preprocessing_summary,
        "evaluation_supported": evaluation_supported,
    }


def _source_id_column(columns: Iterable[object]) -> Optional[object]:
    columns_by_name = {
        str(column).strip().lstrip("\ufeff").lower(): column for column in columns
    }
    return next(
        (columns_by_name[name] for name in SOURCE_ID_COLUMNS if name in columns_by_name),
        None,
    )


def _normalize_chunk(raw: pd.DataFrame, row_offset: int) -> pd.DataFrame:
    prepared = raw.copy()
    prepared["source_row_number"] = np.arange(
        row_offset + 2, row_offset + len(raw) + 2, dtype=np.int64
    )
    source_id_column = _source_id_column(raw.columns)
    prepared["source_observation_id"] = (
        raw[source_id_column].astype("object")
        if source_id_column is not None
        else None
    )
    columns_by_name = {
        str(column).strip().lstrip("\ufeff").lower(): column
        for column in raw.columns
    }
    for normalized, aliases in (
        ("source_latitude", ("latitude", "lat")),
        ("source_longitude", ("longitude", "lon", "long")),
        (
            "source_timestamp",
            (
                "datetime",
                "acq_datetime",
                "acquisition_datetime",
                "timestamp",
            ),
        ),
    ):
        source_column = next(
            (columns_by_name[name] for name in aliases if name in columns_by_name),
            None,
        )
        prepared[normalized] = (
            raw[source_column].astype("object") if source_column is not None else None
        )
    date_column = next(
        (
            columns_by_name[name]
            for name in ("date", "acq_date", "acquisition_date")
            if name in columns_by_name
        ),
        None,
    )
    time_column = next(
        (
            columns_by_name[name]
            for name in ("time", "acq_time", "acquisition_time")
            if name in columns_by_name
        ),
        None,
    )
    if date_column is not None and time_column is not None:
        source_timestamp = prepared["source_timestamp"].replace("", np.nan)
        date_time_fallback = (
            raw[date_column].astype(str) + " " + raw[time_column].astype(str)
        )
        prepared["source_timestamp"] = source_timestamp.fillna(date_time_fallback)
    return normalize_firms_schema(prepared, "MODIS")


def _record_failure(
    batch_index: int,
    row: Mapping[str, object],
    reason_code: str,
    reason: str,
) -> Dict[str, object]:
    return {
        "batch_index": batch_index,
        "source_row_number": row.get("source_row_number"),
        "source_observation_id": _clean_scalar(
            row.get("source_observation_id")
        ),
        "source_reason_code": reason_code,
        "source_reason": reason,
    }


def _clean_scalar(value: object) -> object:
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def _clean_with_failures(
    normalized: pd.DataFrame, batch_index: int
) -> Tuple[pd.DataFrame, List[Dict[str, object]], Dict[str, int]]:
    failures: List[Dict[str, object]] = []
    counts = Counter()
    if normalized.empty:
        cleaned = normalized.copy()
        cleaned["confidence_valid"] = pd.Series(dtype=bool)
        counts.update(
            {
                "cleaned_record_count": 0,
                "invalid_confidence_count": 0,
                "missing_frp_count": 0,
                "missing_confidence_count": 0,
            }
        )
        return cleaned, failures, dict(counts)

    latitude = pd.to_numeric(normalized["latitude"], errors="coerce")
    longitude = pd.to_numeric(normalized["longitude"], errors="coerce")
    valid_coordinates = (
        latitude.notna()
        & longitude.notna()
        & latitude.between(-90, 90)
        & longitude.between(-180, 180)
    )
    datetimes = pd.to_datetime(
        normalized["datetime"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    normalized["datetime"] = datetimes
    valid_datetimes = datetimes.notna()
    for index, row in normalized.iterrows():
        reasons = []
        if not bool(valid_coordinates.loc[index]):
            reasons.append("invalid_coordinates")
            counts["invalid_coordinate_count"] += 1
        if not bool(valid_datetimes.loc[index]):
            reasons.append("invalid_timestamp")
            counts["invalid_timestamp_count"] += 1
        if reasons:
            failures.append(
                _record_failure(
                    batch_index,
                    row,
                    "+".join(reasons),
                    "Record failed the existing FIRMS cleaning coordinate/time checks.",
                )
            )

    duplicate_keys = [
        "sensor",
        "latitude",
        "longitude",
        "datetime",
        "satellite",
        "instrument",
    ]
    valid_rows = normalized.loc[valid_coordinates & valid_datetimes]
    duplicate_mask = valid_rows.duplicated(subset=duplicate_keys, keep="first")
    for _, row in valid_rows.loc[duplicate_mask].iterrows():
        counts["duplicate_source_count"] += 1
        failures.append(
            _record_failure(
                batch_index,
                row,
                "duplicate_source_record",
                "Duplicate observation removed by the existing FIRMS cleaning key.",
            )
        )

    if valid_rows.empty:
        cleaned = valid_rows.copy()
        cleaned["confidence_valid"] = pd.Series(dtype=bool)
        counts.update(
            {
                "cleaned_record_count": 0,
                "invalid_confidence_count": 0,
                "missing_frp_count": 0,
                "missing_confidence_count": 0,
            }
        )
        return cleaned, failures, dict(counts)

    cleaned = clean_firms_records(valid_rows)
    counts["cleaned_record_count"] = len(cleaned)
    counts["invalid_confidence_count"] = int(
        (~cleaned["confidence_valid"]).sum()
    )
    counts["missing_frp_count"] = int(cleaned["frp"].isna().sum())
    counts["missing_confidence_count"] = int(cleaned["confidence"].isna().sum())
    return cleaned, failures, dict(counts)


def _satellite_kind(value: object) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in {"t", "terra"} or "terra" in text:
        return "Terra"
    if text in {"a", "aqua"} or "aqua" in text:
        return "Aqua"
    return None


def _within_bbox(
    latitude: float,
    longitude: float,
    bbox: Optional[Tuple[float, float, float, float]],
) -> bool:
    if bbox is None:
        return True
    west, south, east, north = bbox
    return west <= longitude <= east and south <= latitude <= north


def _make_feature_input(
    row: pd.Series,
    model: ProbabilisticSpatialEnsemble,
    environmental_sources: Sequence[RasterFeatureSource],
    grid_resolution_m: float,
) -> Tuple[SpatialFeatureInput, List[str]]:
    latitude = float(row["latitude"])
    longitude = float(row["longitude"])
    grid = LocalTargetGrid(
        latitude,
        longitude,
        resolution_m=grid_resolution_m,
        width=model.grid_width,
        height=model.grid_height,
    )
    empty_environment = np.empty((*grid.shape, 0), dtype=np.float32)
    empty_input = SpatialFeatureInput(
        modis_id=str(row["modis_id"]),
        modis_features={"frp": row.get("frp"), "confidence": row.get("confidence")},
        environmental_features=empty_environment,
        environmental_feature_names=(),
        grid_shape=grid.shape,
        metadata={"modis_id": str(row["modis_id"]), "grid": grid.metadata},
    )
    _, base_feature_names = sample_to_cell_feature_matrix(empty_input)
    if model.feature_names[: len(base_feature_names)] != base_feature_names:
        raise ValueError(
            "Saved model does not use the established MODIS feature schema."
        )
    expected_environmental_names = model.feature_names[len(base_feature_names) :]
    supplied_names = {source.name for source in environmental_sources}
    terrain_names = {"slope_degrees", "aspect_sin", "aspect_cos"}
    derive_terrain = (
        "elevation_m" in supplied_names
        and bool(set(expected_environmental_names) & terrain_names)
    )
    allowed_sources = set(expected_environmental_names)
    if derive_terrain:
        allowed_sources.add("elevation_m")
    unexpected_sources = supplied_names - allowed_sources
    if unexpected_sources:
        raise ValueError(
            "Environmental sources are not part of this model schema: "
            + ", ".join(sorted(unexpected_sources))
        )
    observed_features: Dict[str, np.ndarray] = {}
    if environmental_sources:
        extracted = extract_environmental_features(
            grid,
            environmental_sources,
            derive_terrain_from="elevation_m" if derive_terrain else None,
        )
        observed_features = {
            name: extracted.values[:, :, index]
            for index, name in enumerate(extracted.feature_names)
        }
    ordered_features = np.full(
        (*grid.shape, len(expected_environmental_names)),
        np.nan,
        dtype=np.float32,
    )
    missing_features = []
    for index, name in enumerate(expected_environmental_names):
        values = observed_features.get(name)
        if values is None:
            missing_features.append(name)
        else:
            ordered_features[:, :, index] = values
            if not np.isfinite(values).all():
                missing_features.append(name)

    metadata = {
        "modis_id": str(row["modis_id"]),
        "observation_datetime": pd.Timestamp(row["datetime"]).isoformat(),
        "modis_latitude": latitude,
        "modis_longitude": longitude,
        "modis_satellite": _clean_scalar(row.get("satellite")),
        "modis_instrument": _clean_scalar(row.get("instrument")),
        "source_sensor": "MODIS",
        "grid": grid.metadata,
    }
    feature_input = SpatialFeatureInput(
        modis_id=str(row["modis_id"]),
        modis_features={
            "frp": row.get("frp"),
            "confidence": row.get("confidence"),
        },
        environmental_features=ordered_features,
        environmental_feature_names=tuple(expected_environmental_names),
        grid_shape=grid.shape,
        metadata=metadata,
    )
    _, actual_feature_names = sample_to_cell_feature_matrix(feature_input)
    if actual_feature_names != model.feature_names:
        raise ValueError(
            "Historical feature schema does not exactly match the saved model."
        )
    return feature_input, missing_features


class _FeatureShiftTracker:
    """Streaming feature statistics against the model's fitted normalization."""

    def __init__(self, reference: Mapping[str, Mapping[str, object]]) -> None:
        self.reference = reference
        self.statistics = {
            name: {
                "finite_count": 0,
                "missing_cell_count": 0,
                "observation_missing_count": 0,
                "minimum": math.inf,
                "maximum": -math.inf,
                "sum": 0.0,
                "sum_squares": 0.0,
                "outside_three_scale_count": 0,
            }
            for name in reference
        }

    def update(self, matrix: np.ndarray) -> List[List[str]]:
        values = np.asarray(matrix, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != len(self.statistics):
            raise ValueError("Shift-diagnostic matrix does not match model features.")
        per_cell_outliers: List[List[str]] = [
            [] for _ in range(values.shape[0])
        ]
        for index, name in enumerate(self.statistics):
            column = values[:, index]
            finite_mask = np.isfinite(column)
            finite = column[finite_mask]
            item = self.statistics[name]
            item["missing_cell_count"] += int(np.count_nonzero(~finite_mask))
            if not finite_mask.all():
                item["observation_missing_count"] += 1
            if finite.size == 0:
                continue
            reference = self.reference[name]
            mean = float(reference["mean"])
            scale = max(float(reference["scale"]), 1e-12)
            outlier_mask = np.zeros(values.shape[0], dtype=bool)
            outlier_mask[finite_mask] = (
                np.abs(finite - mean) > 3.0 * scale
            )
            item["outside_three_scale_count"] += int(
                np.count_nonzero(outlier_mask)
            )
            for row_index in np.flatnonzero(outlier_mask):
                per_cell_outliers[int(row_index)].append(name)
            item["finite_count"] += int(finite.size)
            item["minimum"] = min(float(item["minimum"]), float(np.min(finite)))
            item["maximum"] = max(float(item["maximum"]), float(np.max(finite)))
            item["sum"] += float(np.sum(finite, dtype=np.float64))
            item["sum_squares"] += float(np.sum(finite * finite, dtype=np.float64))
        return per_cell_outliers

    def report(self) -> Dict[str, object]:
        results = {}
        for name, item in self.statistics.items():
            count = int(item["finite_count"])
            missing = int(item["missing_cell_count"])
            if count:
                mean = float(item["sum"]) / count
                variance = max(float(item["sum_squares"]) / count - mean * mean, 0.0)
                historical_range: Optional[List[float]] = [
                    float(item["minimum"]),
                    float(item["maximum"]),
                ]
                historical_std: Optional[float] = math.sqrt(variance)
            else:
                mean = None
                historical_range = None
                historical_std = None
            scale_outlier_count = int(item["outside_three_scale_count"])
            results[name] = {
                "finite_cell_count": count,
                "missing_cell_count": missing,
                "observation_with_missing_cells_count": int(
                    item["observation_missing_count"]
                ),
                "historical_min_max": historical_range,
                "historical_mean": mean,
                "historical_standard_deviation": historical_std,
                "training_mean": float(self.reference[name]["mean"]),
                "training_scale": float(self.reference[name]["scale"]),
                "outside_three_training_scales_cell_count": scale_outlier_count,
                "outside_training_support_fraction": (
                    scale_outlier_count / count if count else None
                ),
                "training_missing_indicator": bool(
                    self.reference[name]["training_missing_indicator"]
                ),
            }
        return {
            "support_rule": (
                "A finite historical feature value is flagged when it differs "
                "from the saved training mean by more than three saved training "
                "scales. This is a screening heuristic, not a reliability guarantee."
            ),
            "features": results,
        }


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_csv_atomic(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    columns: Sequence[str],
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _empty_csv(path: Path, columns: Sequence[str]) -> None:
    _write_csv_atomic(path, (), columns)


def _metadata_coverage(
    model: ProbabilisticSpatialEnsemble,
) -> Dict[str, object]:
    metadata = model.training_parent_metadata
    dates = [
        _utc_naive_timestamp(
            item["observation_datetime"], "training observation datetime"
        )
        for item in metadata
        if item.get("observation_datetime") is not None
    ]
    coords = [
        (float(item["modis_longitude"]), float(item["modis_latitude"]))
        for item in metadata
        if item.get("modis_longitude") is not None
        and item.get("modis_latitude") is not None
    ]
    satellites = Counter(
        str(item["modis_satellite"])
        for item in metadata
        if item.get("modis_satellite") is not None
    )
    instruments = Counter(
        str(item["modis_instrument"])
        for item in metadata
        if item.get("modis_instrument") is not None
    )
    return {
        "date_start": min(dates).isoformat() if dates else None,
        "date_end": max(dates).isoformat() if dates else None,
        "grid_resolution_m": next(
            float(item["grid_resolution_m"])
            for item in metadata
            if item.get("grid_resolution_m") is not None
        ),
        "geographic_bounds": (
            {
                "west": min(point[0] for point in coords),
                "south": min(point[1] for point in coords),
                "east": max(point[0] for point in coords),
                "north": max(point[1] for point in coords),
            }
            if coords
            else None
        ),
        "satellite_counts": dict(satellites),
        "instrument_counts": dict(instruments),
    }


def _training_support_flags(
    feature_input: SpatialFeatureInput,
    training_coverage: Mapping[str, object],
) -> Tuple[bool, bool]:
    geographic = training_coverage.get("geographic_bounds")
    outside_geographic = False
    if isinstance(geographic, dict):
        outside_geographic = not (
            float(geographic["west"])
            <= float(feature_input.metadata["modis_longitude"])
            <= float(geographic["east"])
            and float(geographic["south"])
            <= float(feature_input.metadata["modis_latitude"])
            <= float(geographic["north"])
        )
    date_start = training_coverage.get("date_start")
    date_end = training_coverage.get("date_end")
    observation_date = _utc_naive_timestamp(
        feature_input.metadata["observation_datetime"],
        "historical observation datetime",
    )
    outside_date = (
        observation_date < pd.Timestamp(date_start)
        or observation_date > pd.Timestamp(date_end)
        if date_start is not None and date_end is not None
        else False
    )
    return outside_geographic, outside_date


class _RunningScalarStats:
    """Constant-memory summary for a stream of emitted scalar predictions."""

    def __init__(self) -> None:
        self.count = 0
        self.minimum = math.inf
        self.maximum = -math.inf
        self.total = 0.0
        self.total_squares = 0.0

    def update(self, values: np.ndarray) -> None:
        array = np.asarray(values, dtype=np.float64).reshape(-1)
        finite = array[np.isfinite(array)]
        if not finite.size:
            return
        self.count += int(finite.size)
        self.minimum = min(self.minimum, float(np.min(finite)))
        self.maximum = max(self.maximum, float(np.max(finite)))
        self.total += float(np.sum(finite, dtype=np.float64))
        self.total_squares += float(np.sum(finite * finite, dtype=np.float64))

    def report(self) -> Dict[str, object]:
        if not self.count:
            return {
                "count": 0,
                "minimum": None,
                "maximum": None,
                "mean": None,
                "standard_deviation": None,
            }
        mean = self.total / self.count
        variance = max(self.total_squares / self.count - mean * mean, 0.0)
        return {
            "count": self.count,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "mean": mean,
            "standard_deviation": math.sqrt(variance),
        }


def _prediction_rows(
    feature_input: SpatialFeatureInput,
    estimate: SpatialProbabilityEstimate,
    model: ProbabilisticSpatialEnsemble,
    calibrator: Optional[ProbabilityCalibrator],
    source_file_sha256: str,
    source_file_name: str,
    row: pd.Series,
    missing_features: Sequence[str],
    outlier_features_by_cell: Sequence[Sequence[str]],
    outside_geographic: bool,
    outside_date: bool,
) -> List[Dict[str, object]]:
    probability = np.asarray(estimate.probability, dtype=np.float64)
    uncertainty = np.asarray(estimate.uncertainty_std, dtype=np.float64)
    expected_shape = (model.grid_height, model.grid_width)
    if probability.shape != expected_shape or uncertainty.shape != expected_shape:
        raise PredictionValidationError(
            "incomplete_prediction_batch",
            "Model prediction dimensions do not match its saved grid.",
        )
    if (
        not np.isfinite(probability).all()
        or np.any(probability < 0)
        or np.any(probability > 1)
    ):
        raise PredictionValidationError(
            "invalid_probability_output",
            "Model produced probabilities outside [0, 1].",
        )
    if not np.isfinite(uncertainty).all() or np.any(uncertainty < 0):
        raise PredictionValidationError(
            "invalid_uncertainty_output",
            "Model produced invalid negative or nonfinite uncertainty.",
        )
    calibrated = calibrator.apply(probability) if calibrator is not None else None
    if calibrated is not None and (
        not np.isfinite(calibrated).all()
        or np.any(calibrated < 0)
        or np.any(calibrated > 1)
    ):
        raise PredictionValidationError(
            "invalid_calibrated_probability_output",
            "Calibration produced probabilities outside [0, 1].",
        )
    grid = LocalTargetGrid(
        float(feature_input.metadata["modis_latitude"]),
        float(feature_input.metadata["modis_longitude"]),
        resolution_m=float(feature_input.metadata["grid"]["resolution_m"]),
        width=model.grid_width,
        height=model.grid_height,
    )
    rows = []
    for row_index in range(model.grid_height):
        for column_index in range(model.grid_width):
            cell_index = row_index * model.grid_width + column_index
            x_m, y_m = grid.cell_center_local(row_index, column_index)
            longitude, latitude = grid.local_to_geographic(x_m, y_m)
            per_cell_outliers = list(outlier_features_by_cell[cell_index])
            rows.append(
                {
                    "parent_modis_observation_id": feature_input.modis_id,
                    "target_cell_id": f"r{row_index:02d}_c{column_index:02d}",
                    "cell_center_latitude": float(latitude),
                    "cell_center_longitude": float(longitude),
                    "grid_resolution_m": grid.resolution_m,
                    "observation_datetime": pd.Timestamp(
                        feature_input.metadata["observation_datetime"]
                    ).isoformat(),
                    "source_latitude": _clean_scalar(row.get("source_latitude")),
                    "source_longitude": _clean_scalar(row.get("source_longitude")),
                    "source_timestamp": _clean_scalar(row.get("source_timestamp")),
                    "satellite": _clean_scalar(row.get("satellite")),
                    "instrument": _clean_scalar(row.get("instrument")),
                    "raw_estimated_detection_probability": float(
                        probability[row_index, column_index]
                    ),
                    "calibrated_estimated_detection_probability": (
                        float(calibrated[row_index, column_index])
                        if calibrated is not None
                        else ""
                    ),
                    "uncertainty_estimate": float(
                        uncertainty[row_index, column_index]
                    ),
                    "uncertainty_type": "model_disagreement_proxy",
                    "uncertainty_method": estimate.metadata["uncertainty_method"],
                    "source_sensor": "MODIS",
                    "source_file_name": source_file_name,
                    "source_file_sha256": source_file_sha256,
                    "source_row_number": int(row["source_row_number"]),
                    "source_observation_id": _clean_scalar(
                        row.get("source_observation_id")
                    ),
                    "model_name": "ProbabilisticSpatialEnsemble",
                    "model_version": estimate.metadata["model_version"],
                    "preprocessing_version": model.artifact_preprocessing_version,
                    "calibration_version": (
                        CALIBRATION_VERSION if calibrator else ""
                    ),
                    "calibration_method": calibrator.method if calibrator else "",
                    "historical_period": "2000-2011",
                    "estimated": True,
                    "reference_observation_available": False,
                    "missing_feature_channels": json.dumps(list(missing_features)),
                    "outside_training_support": bool(per_cell_outliers),
                    "outside_training_support_features": json.dumps(per_cell_outliers),
                    "outside_training_geographic_bounds": outside_geographic,
                    "outside_training_date_range": outside_date,
                }
            )
    return rows


def _merge_failure_batches(batch_dir: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as output_stream:
        writer = csv.DictWriter(output_stream, fieldnames=FAILURE_COLUMNS)
        writer.writeheader()
        for source in sorted(batch_dir.glob("failures-*.csv")):
            with source.open("r", newline="", encoding="utf-8") as input_stream:
                reader = csv.DictReader(input_stream)
                for row in reader:
                    writer.writerow(row)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    os.replace(temporary, destination)


def _first_malformed_row(path):
    """Return (data_row_index, message) of the first row with too many fields, or None.

    pandas 3's C parser silently drops extra fields instead of raising, so a
    malformed row would otherwise be read as a valid-looking record.
    """
    import csv

    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        rows = csv.reader(handle)
        header = next(rows, None)
        if header is None:
            return None
        width = len(header)
        for line_number, row in enumerate(rows, start=2):
            if len(row) > width:
                return line_number - 2, f"Expected {width} fields in line {line_number}, saw {len(row)}"
    return None


def run_historical_estimation(
    run_config: HistoricalRunConfig,
) -> HistoricalRunResult:
    """Process a local historical FIRMS CSV in bounded, restartable batches."""
    if not run_config.modis_csv.is_file():
        raise FileNotFoundError(
            "Historical MODIS FIRMS CSV was not found. Supply a local, "
            f"manually downloaded file with --modis: {run_config.modis_csv}"
        )
    _validate_input_headers(run_config.modis_csv)
    model, calibrator, artifact_provenance = load_historical_artifacts(run_config)
    source_hash = _sha256(run_config.modis_csv)
    model_hash = str(artifact_provenance["model"]["artifact_sha256"])
    calibration_hash = (
        _sha256(run_config.calibration_artifact)
        if run_config.calibration_artifact is not None
        else None
    )
    environmental_manifest = [
        {
            "name": source.name,
            "path": str(Path(source.path).resolve()),
            "sha256": _sha256(Path(source.path)),
            "band": source.band,
            "resampling": source.resampling.name,
        }
        for source in run_config.environmental_sources
    ]
    signature_payload: Dict[str, object] = {
        "input_sha256": source_hash,
        "model_sha256": model_hash,
        "calibration_sha256": calibration_hash,
        "environmental_sources": environmental_manifest,
        "start_date": _parse_date(run_config.start_date, "start_date").date().isoformat(),
        "end_date": _parse_date(run_config.end_date, "end_date").date().isoformat(),
        "bbox": list(run_config.bbox) if run_config.bbox else None,
        "batch_size": run_config.batch_size,
        "selected_model": run_config.selected_model,
    }
    run_signature = _canonical_json_hash(signature_payload)
    output_dir = Path(run_config.output_dir)
    batches_dir = output_dir / "batches"
    manifest_path = output_dir / "manifest.json"
    if (
        output_dir.exists()
        and any(output_dir.iterdir())
        and not manifest_path.exists()
    ):
        raise FileExistsError(
            f"Output directory is not empty and has no matching manifest: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    batches_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        if not run_config.resume:
            raise FileExistsError(
                f"Output manifest already exists: {manifest_path}; use resume=True "
                "to replay and recover the same run, or choose another output directory."
            )
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Existing output manifest is unreadable: {exc}") from exc
        if previous.get("run_signature") != run_signature:
            raise ValueError(
                "Cannot resume: source, model, calibration, filters, or batch size "
                "differ from the existing run."
            )

    start = _parse_date(run_config.start_date, "start_date")
    end = _parse_date(run_config.end_date, "end_date")
    training_coverage = _metadata_coverage(model)
    feature_tracker = _FeatureShiftTracker(model.preprocessing_summary())
    counters: Counter = Counter()
    satellite_counts: Counter = Counter()
    instrument_counts: Counter = Counter()
    geographic_tiles = set()
    latitude_min, latitude_max = math.inf, -math.inf
    longitude_min, longitude_max = math.inf, -math.inf
    source_date_min, source_date_max = None, None
    selected_date_min, selected_date_max = None, None
    probability_stats = _RunningScalarStats()
    calibrated_probability_stats = _RunningScalarStats()
    uncertainty_stats = _RunningScalarStats()
    failed_records_count = 0
    prediction_record_count = 0
    processed_observation_count = 0
    batch_summaries = []
    data_warnings: List[str] = []
    manifest: Dict[str, object] = {
        "manifest_version": OUTPUT_SCHEMA_VERSION,
        "run_signature": run_signature,
        "status": "in_progress",
        "configuration": signature_payload,
        "batches": [],
    }
    _atomic_json(manifest_path, manifest)

    with tempfile.TemporaryDirectory(prefix="spatial_ml_history_") as temporary_dir:
        connection = sqlite3.connect(Path(temporary_dir) / "seen.sqlite")
        connection.execute(
            "CREATE TABLE source_ids (observation_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "CREATE TABLE prediction_cells ("
            "observation_id TEXT NOT NULL, cell_id TEXT NOT NULL, "
            "PRIMARY KEY (observation_id, cell_id))"
        )
        row_offset = 0
        reader = None
        try:
            malformed = _first_malformed_row(run_config.modis_csv)
            reader = pd.read_csv(
                run_config.modis_csv,
                dtype=str,
                chunksize=run_config.batch_size,
                nrows=malformed[0] if malformed else None,   # rows before the bad line are still used
            )
            for batch_index, raw_batch in enumerate(reader):
                batch_counts: Counter = Counter()
                batch_predictions: List[Dict[str, object]] = []
                batch_failures: List[Dict[str, object]] = []
                batch_prediction_keys = set()
                normalized = _normalize_chunk(raw_batch, row_offset)
                row_offset += len(raw_batch)
                batch_counts["input_record_count"] = len(raw_batch)
                cleaned, cleaning_failures, cleaning_counts = _clean_with_failures(
                    normalized, batch_index
                )
                batch_failures.extend(cleaning_failures)
                batch_counts.update(cleaning_counts)

                if not cleaned.empty:
                    cleaned["modis_id"] = generate_observation_ids(cleaned, "MODIS")
                    for _, row in cleaned.iterrows():
                        observation_id = str(row["modis_id"])
                        inserted = connection.execute(
                            "INSERT OR IGNORE INTO source_ids VALUES (?)",
                            (observation_id,),
                        ).rowcount
                        if not inserted:
                            batch_counts["duplicate_source_count"] += 1
                            batch_failures.append(
                                _record_failure(
                                    batch_index,
                                    row,
                                    "duplicate_source_record",
                                    "Observation identity was already seen in an earlier batch.",
                                )
                            )
                            continue

                        timestamp = pd.Timestamp(row["datetime"])
                        source_date_min = (
                            timestamp if source_date_min is None
                            else min(source_date_min, timestamp)
                        )
                        source_date_max = (
                            timestamp if source_date_max is None
                            else max(source_date_max, timestamp)
                        )
                        if timestamp.normalize() < start or timestamp.normalize() > end:
                            batch_counts["filtered_outside_date_range_count"] += 1
                            continue

                        satellite_kind = _satellite_kind(row.get("satellite"))
                        if satellite_kind is None:
                            batch_counts["unsupported_satellite_count"] += 1
                            batch_failures.append(
                                _record_failure(
                                    batch_index,
                                    row,
                                    "unsupported_satellite",
                                    "Satellite is missing or is not a recognized Terra/Aqua MODIS identifier.",
                                )
                            )
                            continue
                        instrument = row.get("instrument")
                        if pd.notna(instrument) and str(instrument).strip() and (
                            "modis" not in str(instrument).strip().lower()
                        ):
                            batch_counts["unsupported_instrument_count"] += 1
                            batch_failures.append(
                                _record_failure(
                                    batch_index,
                                    row,
                                    "unsupported_instrument",
                                    "Historical inference accepts MODIS instrument observations only.",
                                )
                            )
                            continue
                        sensor_start = pd.Timestamp(
                            config.TERRA_MODIS_START_DATE
                            if satellite_kind == "Terra"
                            else config.AQUA_MODIS_START_DATE
                        )
                        if timestamp.normalize() < sensor_start:
                            batch_counts["before_satellite_start_count"] += 1
                            batch_failures.append(
                                _record_failure(
                                    batch_index,
                                    row,
                                    "before_satellite_start",
                                    f"{satellite_kind} MODIS support begins "
                                    f"{sensor_start.date()}.",
                                )
                            )
                            continue

                        latitude = float(row["latitude"])
                        longitude = float(row["longitude"])
                        if not _within_bbox(latitude, longitude, run_config.bbox):
                            batch_counts["filtered_outside_geographic_subset_count"] += 1
                            continue
                        selected_date_min = (
                            timestamp if selected_date_min is None
                            else min(selected_date_min, timestamp)
                        )
                        selected_date_max = (
                            timestamp if selected_date_max is None
                            else max(selected_date_max, timestamp)
                        )
                        latitude_min = min(latitude_min, latitude)
                        latitude_max = max(latitude_max, latitude)
                        longitude_min = min(longitude_min, longitude)
                        longitude_max = max(longitude_max, longitude)
                        geographic_tiles.add(
                            (
                                math.floor((latitude + 90.0) / 5.0),
                                math.floor((longitude + 180.0) / 5.0),
                            )
                        )
                        satellite_counts[satellite_kind] += 1
                        if pd.notna(row.get("instrument")):
                            instrument_counts[str(row["instrument"])] += 1

                        try:
                            feature_input, missing_features = _make_feature_input(
                                row,
                                model,
                                run_config.environmental_sources,
                                float(training_coverage["grid_resolution_m"]),
                            )
                            matrix, feature_names = sample_to_cell_feature_matrix(
                                feature_input
                            )
                            if feature_names != model.feature_names:
                                raise ValueError(
                                    "Historical feature schema differs from the model artifact."
                                )
                            outlier_features = feature_tracker.update(matrix)
                            if missing_features:
                                batch_counts[
                                    "missing_feature_channel_observation_count"
                                ] += 1
                                for feature_name in missing_features:
                                    batch_counts[
                                        f"missing_feature_{feature_name}_observation_count"
                                    ] += 1
                            outside_geographic, outside_date = (
                                _training_support_flags(
                                    feature_input, training_coverage
                                )
                            )
                            estimate = model.predict(feature_input)
                            rows = _prediction_rows(
                                feature_input,
                                estimate,
                                model,
                                calibrator,
                                source_hash,
                                run_config.modis_csv.name,
                                row,
                                missing_features,
                                outlier_features,
                                outside_geographic,
                                outside_date,
                            )
                            for output_row in rows:
                                key = (
                                    output_row["parent_modis_observation_id"],
                                    output_row["target_cell_id"],
                                )
                                duplicate_in_batch = key in batch_prediction_keys
                                exists = connection.execute(
                                    "SELECT 1 FROM prediction_cells "
                                    "WHERE observation_id = ? AND cell_id = ?",
                                    key,
                                ).fetchone()
                                if duplicate_in_batch or exists:
                                    raise PredictionValidationError(
                                        "duplicate_prediction_record",
                                        f"Duplicate prediction cell key {key!r}."
                                    )
                                batch_prediction_keys.add(key)
                            if len(rows) != model.grid_height * model.grid_width:
                                raise PredictionValidationError(
                                    "incomplete_prediction_batch",
                                    "Prediction grid produced an incomplete cell batch."
                                )
                            connection.executemany(
                                "INSERT INTO prediction_cells VALUES (?, ?)",
                                [
                                    (
                                        output_row[
                                            "parent_modis_observation_id"
                                        ],
                                        output_row["target_cell_id"],
                                    )
                                    for output_row in rows
                                ],
                            )
                            probability_stats.update(estimate.probability)
                            uncertainty_stats.update(estimate.uncertainty_std)
                            if calibrator is not None:
                                calibrated_probability_stats.update(
                                    calibrator.apply(estimate.probability)
                                )
                            batch_predictions.extend(rows)
                            batch_counts["processed_observation_count"] += 1
                        except (
                            FileNotFoundError,
                            RuntimeError,
                            ValueError,
                            rasterio.errors.RasterioError,
                        ) as exc:
                            batch_counts["failed_observation_count"] += 1
                            reason_code = (
                                exc.reason_code
                                if isinstance(exc, PredictionValidationError)
                                else "inference_failure"
                            )
                            if isinstance(exc, PredictionValidationError):
                                batch_counts[reason_code] += 1
                            batch_failures.append(
                                _record_failure(
                                    batch_index,
                                    row,
                                    reason_code,
                                    str(exc),
                                )
                            )
                batch_predictions_path = (
                    batches_dir / f"predictions-{batch_index:06d}.csv"
                )
                batch_failures_path = (
                    batches_dir / f"failures-{batch_index:06d}.csv"
                )
                _write_csv_atomic(
                    batch_predictions_path, batch_predictions, OUTPUT_COLUMNS
                )
                _write_csv_atomic(batch_failures_path, batch_failures, FAILURE_COLUMNS)
                connection.commit()
                batch_status = (
                    "partial"
                    if batch_failures
                    else "complete"
                )
                summary = {
                    "batch_index": batch_index,
                    "input_record_count": len(raw_batch),
                    "cleaned_record_count": int(
                        batch_counts.get("cleaned_record_count", 0)
                    ),
                    "processed_observation_count": int(
                        batch_counts.get("processed_observation_count", 0)
                    ),
                    "prediction_record_count": len(batch_predictions),
                    "failed_observation_count": int(
                        batch_counts.get("failed_observation_count", 0)
                    ),
                    "failure_record_count": len(batch_failures),
                    "status": batch_status,
                    "prediction_file": str(batch_predictions_path.relative_to(output_dir)),
                    "failure_file": str(batch_failures_path.relative_to(output_dir)),
                    "quality_counts": {
                        key: int(value)
                        for key, value in batch_counts.items()
                    },
                }
                batch_summaries.append(summary)
                failed_records_count += len(batch_failures)
                prediction_record_count += len(batch_predictions)
                processed_observation_count += int(
                    batch_counts.get("processed_observation_count", 0)
                )
                counters.update(batch_counts)
                manifest["batches"] = batch_summaries
                _atomic_json(manifest_path, manifest)
            if malformed:
                raise pd.errors.ParserError(malformed[1])
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            counters["fatal_input_error_count"] += 1
            data_warnings.append(f"Input CSV could not be fully parsed: {exc}")
        finally:
            if reader is not None:
                reader.close()
            connection.close()

    if not batch_summaries:
        _empty_csv(batches_dir / "predictions-000000.csv", OUTPUT_COLUMNS)
        _empty_csv(batches_dir / "failures-000000.csv", FAILURE_COLUMNS)
    _merge_failure_batches(batches_dir, output_dir / "failed_observations.csv")
    shift_report = feature_tracker.report()
    training_satellites = artifact_provenance["model"]["training"]["training_satellite_counts"]
    historical_satellites = dict(satellite_counts)
    training_bounds = training_coverage["geographic_bounds"]
    training_dates = {
        "start": training_coverage["date_start"],
        "end": training_coverage["date_end"],
    }
    if run_config.bbox is None:
        data_warnings.append(
            "Global processing was requested, but the observed geographic extent "
            "does not prove complete global input coverage."
        )
    if not processed_observation_count:
        data_warnings.append(
            "No historical MODIS observations were successfully predicted."
        )
    if any(item["status"] == "partial" for item in batch_summaries):
        data_warnings.append(
            "One or more batches contain failed or rejected observations; inspect "
            "failed_observations.csv before interpreting the output."
        )
    status = (
        "empty_with_failures"
        if failed_records_count
        and not counters.get("processed_observation_count", 0)
        else "empty"
        if not batch_summaries or not counters.get("processed_observation_count", 0)
        else "partial"
        if failed_records_count
        else "complete"
    )
    if counters.get("fatal_input_error_count"):
        status = "incomplete"
    shift_report.update(
        {
            "historical_satellite_counts": historical_satellites,
            "training_satellite_counts": training_satellites,
            "historical_instrument_counts": dict(instrument_counts),
            "training_instrument_counts": training_coverage["instrument_counts"],
            "satellite_support_status": (
                "comparable"
                if training_satellites
                else "unavailable: model artifact lacks training satellite metadata"
            ),
            "training_geographic_bounds": training_bounds,
            "historical_geographic_bounds": (
                {
                    "west": longitude_min,
                    "south": latitude_min,
                    "east": longitude_max,
                    "north": latitude_max,
                }
                if math.isfinite(latitude_min) and math.isfinite(longitude_min)
                else None
            ),
            "historical_five_degree_tile_count": len(geographic_tiles),
            "training_date_coverage": training_dates,
            "historical_date_coverage": {
                "source_cleaned_start": (
                    source_date_min.isoformat() if source_date_min is not None else None
                ),
                "source_cleaned_end": (
                    source_date_max.isoformat() if source_date_max is not None else None
                ),
                "selected_start": (
                    selected_date_min.isoformat()
                    if selected_date_min is not None
                    else None
                ),
                "selected_end": (
                    selected_date_max.isoformat()
                    if selected_date_max is not None
                    else None
                ),
            },
            "training_geographic_extrapolation": (
                "per-observation flags compare locations to the training bounding box; "
                "a bounding box is not a measure of ecological representativeness"
            ),
            "training_temporal_extrapolation": (
                "Historical 2000-2011 observations are outside the overlap-period "
                "training dates when those dates begin in 2012 or later."
            ),
        }
    )
    shift_path = output_dir / "distribution_shift.json"
    _atomic_json(shift_path, shift_report)

    quality_report: Dict[str, object] = {
        "status": status,
        "counts": {key: int(value) for key, value in counters.items()},
        "processed_observation_count": processed_observation_count,
        "prediction_record_count": prediction_record_count,
        "failed_observation_count": failed_records_count,
        "duplicate_prediction_record_count": int(
            counters.get("duplicate_prediction_record_count", 0)
        ),
        "probability_range_check": (
            "passed for every emitted prediction"
            if prediction_record_count
            else "not_applicable_no_predictions"
        ),
        "invalid_probability_output_count": int(
            counters.get("invalid_probability_output", 0)
        ),
        "invalid_calibrated_probability_output_count": int(
            counters.get("invalid_calibrated_probability_output", 0)
        ),
        "invalid_uncertainty_output_count": int(
            counters.get("invalid_uncertainty_output", 0)
        ),
        "incomplete_prediction_batch_count": int(
            counters.get("incomplete_prediction_batch", 0)
        ),
        "invalid_coordinate_count": int(counters.get("invalid_coordinate_count", 0)),
        "invalid_timestamp_count": int(counters.get("invalid_timestamp_count", 0)),
        "missing_confidence_count": int(counters.get("missing_confidence_count", 0)),
        "missing_frp_count": int(counters.get("missing_frp_count", 0)),
        "filtered_outside_date_range_count": int(
            counters.get("filtered_outside_date_range_count", 0)
        ),
        "filtered_outside_geographic_subset_count": int(
            counters.get("filtered_outside_geographic_subset_count", 0)
        ),
        "unsupported_satellite_count": int(
            counters.get("unsupported_satellite_count", 0)
        ),
        "unsupported_instrument_count": int(
            counters.get("unsupported_instrument_count", 0)
        ),
        "before_satellite_start_count": int(
            counters.get("before_satellite_start_count", 0)
        ),
        "coverage": {
            "requested_start": start.date().isoformat(),
            "requested_end": end.date().isoformat(),
            "requested_bbox": list(run_config.bbox) if run_config.bbox else "global",
            "observed_date_start": (
                selected_date_min.isoformat() if selected_date_min is not None else None
            ),
            "observed_date_end": (
                selected_date_max.isoformat() if selected_date_max is not None else None
            ),
            "observed_geographic_bounds": shift_report[
                "historical_geographic_bounds"
            ],
            "global_coverage_completeness": "not_verified",
        },
        "warnings": data_warnings,
        "failed_observations_file": "failed_observations.csv",
    }
    quality_path = output_dir / "quality_control.json"
    _atomic_json(quality_path, quality_report)
    report: Dict[str, object] = {
        "report_version": OUTPUT_SCHEMA_VERSION,
        "product_label": HISTORICAL_PRODUCT_LABEL,
        "scientific_interpretation": (
            "Estimated probability of observed VIIRS point occupancy under the "
            "overlap-period sample-generation process; not physical fire state "
            "or a physical VIIRS pixel measurement."
        ),
        "validation_status": artifact_provenance["model"]["selection_status"],
        "model_selection": artifact_provenance["model"],
        "model_provenance": artifact_provenance["model"],
        "calibration": artifact_provenance["calibration"],
        "preprocessing_version": model.artifact_preprocessing_version,
        "historical_period": "2000-2011",
        "requested_date_range": {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
        },
        "input_provenance": {
            "file_name": run_config.modis_csv.name,
            "file_sha256": source_hash,
            "input_size_bytes": run_config.modis_csv.stat().st_size,
            "sensor": "MODIS",
            "environmental_sources": environmental_manifest,
        },
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "output_schema": list(OUTPUT_COLUMNS),
        "outputs": {
            "prediction_batch_directory": "batches",
            "failed_observations": "failed_observations.csv",
            "quality_control": "quality_control.json",
            "distribution_shift": "distribution_shift.json",
            "manifest": "manifest.json",
        },
        "batch_size": run_config.batch_size,
        "batch_summaries": batch_summaries,
        "quality_control": quality_report,
        "distribution_shift": shift_report,
        "prediction_summaries": {
            "raw_estimated_detection_probability": probability_stats.report(),
            "calibrated_estimated_detection_probability": (
                calibrated_probability_stats.report()
            ),
            "uncertainty_estimate": uncertainty_stats.report(),
        },
        "uncertainty_interpretation": (
            "The CNN ensemble standard deviation is an uncalibrated model "
            "disagreement proxy, not a statistical confidence interval. "
            "Probability calibration is a separate transformation and does "
            "not calibrate uncertainty."
        ),
        "warnings": data_warnings,
    }
    report_path = output_dir / "historical_processing_report.json"
    _atomic_json(report_path, report)
    markdown_path = output_dir / "historical_processing_report.md"
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    manifest.update(
        {
            "status": status,
            "processed_observation_count": processed_observation_count,
            "prediction_record_count": prediction_record_count,
            "failed_observation_count": failed_records_count,
            "report_file": report_path.name,
        }
    )
    _atomic_json(manifest_path, manifest)
    return HistoricalRunResult(
        output_dir=output_dir,
        report_path=report_path,
        manifest_path=manifest_path,
        status=status,
        processed_observation_count=processed_observation_count,
        prediction_record_count=prediction_record_count,
        failed_observation_count=failed_records_count,
    )


def _markdown_report(report: Mapping[str, object]) -> str:
    quality = report["quality_control"]
    model = report["model_provenance"]
    shift = report["distribution_shift"]
    lines = [
        "# Historical MODIS fine-scale probability processing",
        "",
        f"**Product:** {report['product_label']}",
        "",
        f"**Validation status:** `{report['validation_status']}`",
        "",
        f"**Model:** {model['model_name']} {model['model_version']}",
        "",
        f"**Processing status:** `{quality['status']}`",
        "",
        "## Coverage and quality",
        "",
        f"- Processed observations: {quality['processed_observation_count']}",
        f"- Prediction records: {quality['prediction_record_count']}",
        f"- Mean raw probability: "
        f"{report['prediction_summaries']['raw_estimated_detection_probability']['mean']}",
        f"- Mean model-disagreement proxy: "
        f"{report['prediction_summaries']['uncertainty_estimate']['mean']}",
        f"- Failed/rejected records: {quality['failed_observation_count']}",
        f"- Requested date range: {report['requested_date_range']['start']} to "
        f"{report['requested_date_range']['end']}",
        f"- Observed geographic bounds: "
        f"{quality['coverage']['observed_geographic_bounds']}",
        "- Complete global coverage verified: no",
        "",
        "## Distribution-shift screening",
        "",
        str(shift["support_rule"]),
        "",
        f"- Historical Terra/Aqua counts: {shift['historical_satellite_counts']}",
        f"- Training Terra/Aqua counts: {shift['training_satellite_counts']}",
        f"- Historical 5-degree geographic tiles: "
        f"{shift['historical_five_degree_tile_count']}",
        "",
        "## Interpretation limits",
        "",
        "- Every probability is an estimate, not a confirmed binary fire detection.",
        "- A target cell is a modeled local grid cell, not a physical VIIRS pixel.",
        "- Ensemble standard deviation is model disagreement, not a confidence interval.",
        "- Historical dates precede VIIRS overlap training; temporal and geographic "
        "extrapolation can make estimates unreliable.",
        "- Inspect `failed_observations.csv`, `quality_control.json`, and "
        "`distribution_shift.json` before interpretation.",
        "",
    ]
    if report["validation_status"] == "exploratory_unvalidated":
        lines.extend(
            [
                "**Exploratory and unvalidated:** no real held-out M7 report supports "
                "scientific model selection.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "The selected model passed the explicitly attested M7 held-out "
                "selection gate; this does not remove historical distribution-shift "
                "or extrapolation limitations.",
                "",
            ]
        )
    return "\n".join(lines)


def _parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Estimate historical MODIS fine-scale probabilities from local "
            "inputs and a saved spatial CNN artifact. This command never downloads data."
        )
    )
    parser.add_argument("--modis", required=True, type=Path, help="Local MODIS FIRMS CSV")
    parser.add_argument(
        "--model-artifact",
        required=True,
        type=Path,
        help="Saved ProbabilisticSpatialEnsemble artifact",
    )
    parser.add_argument(
        "--model-name",
        required=True,
        choices=("spatial_ensemble",),
        help="Explicit model selection; currently the only safely loadable artifact",
    )
    parser.add_argument("--m7-evaluation-report", type=Path)
    parser.add_argument("--allow-exploratory", action="store_true")
    parser.add_argument("--calibration-artifact", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=config.HISTORICAL_OUTPUTS_DIR / "run",
    )
    parser.add_argument("--start-date", default=config.HISTORICAL_DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=config.HISTORICAL_DEFAULT_END_DATE)
    parser.add_argument("--batch-size", type=int, default=config.HISTORICAL_INFERENCE_BATCH_SIZE)
    parser.add_argument("--bbox", help="Optional WGS84 west,south,east,north subset")
    parser.add_argument(
        "--environment",
        action="append",
        default=[],
        metavar="FEATURE=PATH",
        help="Local raster for a model feature; repeat for multiple channels",
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        bbox = (
            tuple(float(value) for value in arguments.bbox.split(","))
            if arguments.bbox
            else None
        )
        if bbox is not None and len(bbox) != 4:
            raise ValueError("--bbox must contain west,south,east,north.")
        sources = []
        for specification in arguments.environment:
            name, separator, path = specification.partition("=")
            if not separator or not name.strip() or not path.strip():
                raise ValueError(
                    "--environment values must use FEATURE=/path/to/raster."
                )
            resampling = (
                rasterio.enums.Resampling.nearest
                if name.strip() == "land_cover"
                else rasterio.enums.Resampling.bilinear
            )
            sources.append(
                RasterFeatureSource(name.strip(), Path(path), resampling=resampling)
            )
        run_config = HistoricalRunConfig(
            modis_csv=arguments.modis,
            model_artifact=arguments.model_artifact,
            output_dir=arguments.output_dir,
            selected_model=arguments.model_name,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            batch_size=arguments.batch_size,
            bbox=bbox,
            environmental_sources=tuple(sources),
            calibration_artifact=arguments.calibration_artifact,
            m7_evaluation_report=arguments.m7_evaluation_report,
            allow_exploratory=arguments.allow_exploratory,
            resume=arguments.resume,
        )
        result = run_historical_estimation(run_config)
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": result.status,
                "processed_observation_count": result.processed_observation_count,
                "prediction_record_count": result.prediction_record_count,
                "failed_observation_count": result.failed_observation_count,
                "output_dir": str(result.output_dir),
                "report": str(result.report_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
