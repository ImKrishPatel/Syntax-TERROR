import csv
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from spatial_ml.data import TrainingSample
from spatial_ml.evaluation.calibration import ProbabilityCalibrator
from spatial_ml.features.environmental import RasterFeatureSource
from spatial_ml.models import ProbabilisticSpatialEnsemble
from spatial_ml.pipeline.historical import (
    HistoricalRunConfig,
    OUTPUT_COLUMNS,
    _make_feature_input,
    load_historical_artifacts,
    run_historical_estimation,
)

torch.set_num_threads(1)


def _training_samples() -> tuple[TrainingSample, ...]:
    samples = []
    for index in range(3):
        target = np.zeros((2, 2), dtype=np.float32)
        target.flat[index] = 1.0
        samples.append(
            TrainingSample(
                modis_id=f"training-{index}",
                modis_features={
                    "frp": float(index + 1),
                    "confidence": float(70 + index * 5),
                },
                environmental_features=np.full(
                    (2, 2, 1), 100.0 + index * 10, dtype=np.float32
                ),
                environmental_feature_names=("elevation_m",),
                target_grid=target,
                target_observations=pd.DataFrame(),
                metadata={
                    "modis_id": f"training-{index}",
                    "observation_datetime": f"2020-06-0{index + 1}T12:00:00",
                    "modis_latitude": 35.0 + index * 0.1,
                    "modis_longitude": -100.0 + index * 0.1,
                    "modis_satellite": "Terra",
                    "modis_instrument": "MODIS",
                    "target_data_status": "observed",
                    "target_representation": (
                        "binary_viirs_point_observation_occupancy"
                    ),
                    "grid": {
                        "height": 2,
                        "width": 2,
                        "resolution_m": 375.0,
                    },
                },
            )
        )
    return tuple(samples)


@pytest.fixture
def model_artifact(tmp_path):
    model = ProbabilisticSpatialEnsemble(
        grid_height=2,
        grid_width=2,
        hidden_channels=2,
        conv_blocks=1,
        ensemble_size=2,
        epochs=1,
        batch_size=2,
        random_seed=17,
    ).fit(_training_samples())
    artifact = model.save(tmp_path / "model" / "spatial.pt")
    return model, artifact


def _write_firms(path, rows) -> None:
    columns = [
        "id",
        "latitude",
        "longitude",
        "acq_date",
        "acq_time",
        "satellite",
        "instrument",
        "confidence",
        "frp",
    ]
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _record(
    source_id: str,
    date: str = "2000-11-02",
    satellite: str = "Terra",
    frp: float = 200.0,
) -> tuple:
    return (
        source_id,
        35.0,
        -100.0,
        date,
        "1200",
        satellite,
        "MODIS",
        "high",
        frp,
    )


def _run_config(input_path, artifact, output, **kwargs):
    return HistoricalRunConfig(
        modis_csv=input_path,
        model_artifact=artifact,
        output_dir=output,
        allow_exploratory=True,
        batch_size=kwargs.pop("batch_size", 2),
        **kwargs,
    )


def test_historical_dates_satellites_schema_provenance_and_shift(
    tmp_path, model_artifact
) -> None:
    _, artifact = model_artifact
    source = tmp_path / "modis.csv"
    _write_firms(
        source,
        [
            _record("terra", "2000-11-02", "Terra"),
            _record("aqua-too-early", "2001-01-02", "Aqua"),
            _record("aqua", "2002-07-02", "Aqua"),
            _record("outside-history", "2012-01-01", "Terra"),
        ],
    )

    result = run_historical_estimation(
        _run_config(source, artifact, tmp_path / "out")
    )

    assert result.status == "partial"
    assert result.processed_observation_count == 2
    assert result.prediction_record_count == 8
    report = json.loads(result.report_path.read_text())
    assert report["validation_status"] == "exploratory_unvalidated"
    assert report["prediction_summaries"]["uncertainty_estimate"]["count"] == 8
    assert report["quality_control"]["filtered_outside_date_range_count"] == 1
    assert report["quality_control"]["before_satellite_start_count"] == 1
    assert report["distribution_shift"]["historical_satellite_counts"] == {
        "Terra": 1,
        "Aqua": 1,
    }

    rows = []
    for batch_file in sorted(
        (result.output_dir / "batches").glob("predictions-*.csv")
    ):
        with batch_file.open() as stream:
            rows.extend(csv.DictReader(stream))
    assert len(rows) == 8
    assert {row["source_sensor"] for row in rows} == {"MODIS"}
    assert {row["satellite"] for row in rows} == {"Terra", "Aqua"}
    assert {row["source_observation_id"] for row in rows} == {"terra", "aqua"}
    assert {row["estimated"] for row in rows} == {"True"}
    assert {row["reference_observation_available"] for row in rows} == {"False"}
    assert {row["uncertainty_type"] for row in rows} == {
        "model_disagreement_proxy"
    }
    assert all(34 <= float(row["cell_center_latitude"]) <= 36 for row in rows)
    assert all(
        row["outside_training_date_range"] == "True" for row in rows
    )
    assert any(
        "modis_frp" in json.loads(row["outside_training_support_features"])
        for row in rows
    )
    assert tuple(rows[0]) == OUTPUT_COLUMNS


def test_missing_environment_uses_saved_preprocessing_and_reports_missing_channel(
    tmp_path, model_artifact
) -> None:
    model, artifact = model_artifact
    source = tmp_path / "modis.csv"
    _write_firms(source, [_record("missing-environment")])
    output = tmp_path / "out"

    result = run_historical_estimation(_run_config(source, artifact, output))

    batch = pd.read_csv(output / "batches" / "predictions-000000.csv")
    assert batch["raw_estimated_detection_probability"].between(0, 1).all()
    assert all(
        json.loads(value) == ["elevation_m"]
        for value in batch["missing_feature_channels"]
    )
    assert result.prediction_record_count == model.grid_height * model.grid_width
    expected_input, _ = _make_feature_input(
        pd.Series(
            {
                "modis_id": "expected",
                "latitude": 35.0,
                "longitude": -100.0,
                "datetime": pd.Timestamp("2000-11-02T12:00:00"),
                "frp": 200.0,
                "confidence": "high",
                "satellite": "Terra",
                "instrument": "MODIS",
            }
        ),
        model,
        (),
        375.0,
    )
    expected = model.predict(expected_input)
    np.testing.assert_allclose(
        batch["raw_estimated_detection_probability"],
        expected.probability.reshape(-1),
        atol=1e-6,
    )
    loaded, _, _ = load_historical_artifacts(
        _run_config(source, artifact, output / "other")
    )
    assert loaded.preprocessing_summary() == model.preprocessing_summary()


def test_unexpected_environmental_channel_is_rejected(model_artifact) -> None:
    model, _ = model_artifact
    row = pd.Series(
        {
            "modis_id": "historical",
            "latitude": 35.0,
            "longitude": -100.0,
            "datetime": pd.Timestamp("2001-01-01T12:00:00"),
            "frp": 1.0,
            "confidence": "high",
            "satellite": "Terra",
            "instrument": "MODIS",
        }
    )

    with pytest.raises(ValueError, match="not part of this model schema"):
        _make_feature_input(
            row,
            model,
            (RasterFeatureSource("untrained_feature", "unused.tif"),),
            375.0,
        )


def test_validation_calibration_is_loaded_and_exported(
    tmp_path, model_artifact
) -> None:
    model, artifact = model_artifact
    source = tmp_path / "modis.csv"
    _write_firms(source, [_record("calibrated")])
    calibrator = ProbabilityCalibrator(
        method="platt",
        parameters={"slope": 1.0, "intercept": 0.0},
        provenance={
            "fit_partition": "validation",
            "validation_parent_ids": ["validation-parent"],
        },
    )
    calibration_path = calibrator.save(tmp_path / "calibration.json")
    report_path = tmp_path / "m7.json"
    report_path.write_text(
        json.dumps(
            {
                "model_artifacts": {
                    "spatial_ensemble": {
                        "sha256": hashlib.sha256(
                            artifact.read_bytes()
                        ).hexdigest()
                    }
                },
                "calibration_artifacts": {
                    "spatial_ensemble": {
                        "sha256": hashlib.sha256(
                            calibration_path.read_bytes()
                        ).hexdigest()
                    }
                },
                "models": {
                    "spatial_ensemble": {
                        "calibration": {
                            "status": "available",
                            "method": "platt",
                            "validation_parent_ids": ["validation-parent"],
                        }
                    }
                },
            }
        )
    )
    config = _run_config(
        source,
        artifact,
        tmp_path / "out",
        calibration_artifact=calibration_path,
        m7_evaluation_report=report_path,
    )

    _, loaded_calibrator, provenance = load_historical_artifacts(config)
    assert loaded_calibrator is not None
    assert provenance["calibration"]["status"] == "loaded_validation_fitted"
    result = run_historical_estimation(config)
    predictions = pd.read_csv(
        result.output_dir / "batches" / "predictions-000000.csv"
    )
    np.testing.assert_allclose(
        predictions["raw_estimated_detection_probability"],
        predictions["calibrated_estimated_detection_probability"],
        atol=1e-6,
    )
    assert predictions["calibration_version"].eq("1.0.0").all()
    assert model.training_sample_ids


def test_calibration_overlapping_model_training_is_rejected(
    tmp_path, model_artifact
) -> None:
    model, artifact = model_artifact
    calibration_path = ProbabilityCalibrator(
        method="platt",
        parameters={"slope": 1.0, "intercept": 0.0},
        provenance={
            "fit_partition": "validation",
            "validation_parent_ids": [model.training_sample_ids[0]],
        },
    ).save(tmp_path / "overlapping-calibration.json")
    config = _run_config(
        tmp_path / "input.csv",
        artifact,
        tmp_path / "out",
        calibration_artifact=calibration_path,
    )

    with pytest.raises(ValueError, match="overlaps"):
        load_historical_artifacts(config)


def test_duplicate_records_and_resume_replay_are_deterministic(
    tmp_path, model_artifact
) -> None:
    _, artifact = model_artifact
    source = tmp_path / "duplicates.csv"
    _write_firms(source, [_record("duplicate-a"), _record("duplicate-b")])
    output = tmp_path / "out"
    config = _run_config(source, artifact, output, batch_size=1)

    first = run_historical_estimation(config)
    replay = run_historical_estimation(
        _run_config(source, artifact, output, batch_size=1, resume=True)
    )

    assert first.processed_observation_count == 1
    assert first.failed_observation_count == 1
    assert replay.processed_observation_count == first.processed_observation_count
    assert replay.prediction_record_count == first.prediction_record_count
    failures = pd.read_csv(output / "failed_observations.csv")
    assert failures["source_reason_code"].eq("duplicate_source_record").all()


def test_invalid_records_are_reported_and_geographic_subset_is_applied(
    tmp_path, model_artifact
) -> None:
    _, artifact = model_artifact
    source = tmp_path / "quality.csv"
    invalid_coordinate = list(_record("bad-coordinate"))
    invalid_coordinate[1] = 100.0
    invalid_timestamp = list(_record("bad-timestamp"))
    invalid_timestamp[3] = "not-a-date"
    outside_bbox = list(_record("outside-bbox"))
    outside_bbox[1] = 45.0
    _write_firms(
        source,
        [
            invalid_coordinate,
            invalid_timestamp,
            outside_bbox,
            _record("inside-bbox"),
        ],
    )

    result = run_historical_estimation(
        _run_config(
            source,
            artifact,
            tmp_path / "out",
            bbox=(-101.0, 34.0, -99.0, 36.0),
        )
    )

    report = json.loads(result.report_path.read_text())
    assert result.processed_observation_count == 1
    assert result.failed_observation_count == 2
    assert report["quality_control"]["invalid_coordinate_count"] == 1
    assert report["quality_control"]["invalid_timestamp_count"] == 1
    assert report["quality_control"]["filtered_outside_geographic_subset_count"] == 1


def test_empty_input_writes_schema_and_out_of_range_model_output_is_reported(
    tmp_path, model_artifact, monkeypatch
) -> None:
    _, artifact = model_artifact
    empty_source = tmp_path / "empty.csv"
    _write_firms(empty_source, [])
    empty_result = run_historical_estimation(
        _run_config(empty_source, artifact, tmp_path / "empty-out")
    )
    assert empty_result.status == "empty"
    assert empty_result.prediction_record_count == 0
    assert pd.read_csv(
        empty_result.output_dir / "batches" / "predictions-000000.csv"
    ).empty

    source = tmp_path / "invalid-model.csv"
    _write_firms(source, [_record("bad-probability")])
    monkeypatch.setattr(
        ProbabilisticSpatialEnsemble,
        "predict",
        lambda self, sample: SimpleNamespace(
            probability=np.full((2, 2), 1.2),
            uncertainty_std=np.zeros((2, 2)),
            metadata={},
        ),
    )
    result = run_historical_estimation(
        _run_config(source, artifact, tmp_path / "bad-output")
    )
    quality = json.loads(
        (result.output_dir / "quality_control.json").read_text()
    )
    assert quality["invalid_probability_output_count"] == 1
    assert quality["probability_range_check"] == "not_applicable_no_predictions"
    failures = pd.read_csv(result.output_dir / "failed_observations.csv")
    assert failures["source_reason_code"].eq("invalid_probability_output").all()


def test_incomplete_csv_preserves_completed_batches_and_reports_incomplete_status(
    tmp_path, model_artifact
) -> None:
    _, artifact = model_artifact
    source = tmp_path / "incomplete.csv"
    source.write_text(
        "id,latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp\n"
        "valid,35,-100,2000-11-02,1200,Terra,MODIS,high,10\n"
        "malformed,row,has,too,many,fields,for,this,csv,record\n"
    )

    result = run_historical_estimation(
        _run_config(source, artifact, tmp_path / "out", batch_size=1)
    )

    assert result.status == "incomplete"
    assert result.processed_observation_count == 1
    report = json.loads(result.report_path.read_text())
    assert report["quality_control"]["counts"]["fatal_input_error_count"] == 1
    assert "could not be fully parsed" in report["warnings"][0]


def test_model_requires_explicit_exploratory_permission(
    tmp_path, model_artifact
) -> None:
    _, artifact = model_artifact
    with pytest.raises(ValueError, match="allow_exploratory=True"):
        load_historical_artifacts(
            HistoricalRunConfig(
                modis_csv=tmp_path / "modis.csv",
                model_artifact=artifact,
                output_dir=tmp_path / "out",
            )
        )


def test_real_held_out_comparison_cannot_be_bypassed_by_exploratory_flag(
    tmp_path, model_artifact
) -> None:
    model, artifact = model_artifact
    identical_metrics = {
        "brier_score": 0.2,
        "log_loss": 0.5,
        "expected_calibration_error": 0.1,
    }
    report_path = tmp_path / "m7.json"
    report_path.write_text(
        json.dumps(
            {
                "evaluation_evidence": {
                    "classification": "real_observed_firms",
                    "attested_by": "data reviewer",
                },
                "comparison_population": {"identical_for_models": True},
                "model_artifacts": {
                    "spatial_ensemble": {
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()
                    }
                },
                "training_population": {
                    "model_fit_parent_sample_ids": list(
                        model.training_sample_ids
                    )
                },
                "model_configuration": {
                    "feature_names": list(model.feature_names),
                    "spatial_ensemble": {
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
                    },
                },
                "models": {
                    "random_forest": {"test_raw": identical_metrics},
                    "spatial_ensemble": {"test_raw": identical_metrics},
                },
            }
        )
    )

    with pytest.raises(ValueError, match="does not support the selected model"):
        load_historical_artifacts(
            _run_config(
                tmp_path / "modis.csv",
                artifact,
                tmp_path / "out",
                m7_evaluation_report=report_path,
            )
        )
