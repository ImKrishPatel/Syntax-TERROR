from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
import torch

from spatial_ml.data import TrainingSample, split_samples_by_parent
from spatial_ml.evaluation import evaluate_probability_grids
from spatial_ml.models import (
    ProbabilisticSpatialEnsemble,
    summarize_ensemble_probabilities,
    training_sample_to_spatial_tensor,
)

torch.set_num_threads(1)


def _sample(
    modis_id: str,
    positive_cell: int,
    shape: tuple[int, int] = (2, 3),
    missing_environment: bool = False,
) -> TrainingSample:
    height, width = shape
    environment = np.arange(height * width * 2, dtype=np.float32).reshape(
        height, width, 2
    )
    if missing_environment:
        environment[0, 0, 0] = np.nan
    target = np.zeros(shape, dtype=np.float32)
    target.flat[positive_cell % target.size] = 1.0
    return TrainingSample(
        modis_id=modis_id,
        modis_features={
            "frp": 4.0 + positive_cell,
            "confidence": "high" if positive_cell % 2 else "80",
        },
        environmental_features=environment,
        environmental_feature_names=("elevation_m", "land_cover"),
        target_grid=target,
        target_observations=pd.DataFrame(),
        metadata={
            "modis_id": modis_id,
            "observation_datetime": f"2020-06-0{positive_cell + 1}T12:00:00",
            "source_sensors": ["MODIS", "VIIRS"],
            "target_data_status": "observed",
            "target_representation": "binary_viirs_point_observation_occupancy",
            "grid": {
                "height": height,
                "width": width,
                "resolution_m": 100.0,
                "row_order": "north_to_south",
                "column_order": "west_to_east",
            },
        },
    )


def _fit_model(
    samples: tuple[TrainingSample, ...],
) -> ProbabilisticSpatialEnsemble:
    return ProbabilisticSpatialEnsemble(
        grid_height=2,
        grid_width=3,
        hidden_channels=4,
        conv_blocks=1,
        ensemble_size=2,
        epochs=2,
        batch_size=2,
        random_seed=13,
    ).fit(samples)


def test_tensor_conversion_preserves_grid_cell_and_channel_order() -> None:
    sample = _sample("m0", 0)

    tensor, feature_names = training_sample_to_spatial_tensor(sample)

    assert tensor.shape == (9, 2, 3)
    assert feature_names[5:7] == (
        "cell_x_offset_m",
        "cell_y_offset_m",
    )
    np.testing.assert_array_equal(tensor[5, 0], [-100.0, 0.0, 100.0])
    np.testing.assert_array_equal(tensor[6, 0], [50.0, 50.0, 50.0])
    np.testing.assert_array_equal(tensor[6, 1], [-50.0, -50.0, -50.0])
    np.testing.assert_array_equal(
        tensor[7:],
        sample.environmental_features.transpose(2, 0, 1),
    )


def test_spatial_ensemble_predicts_probability_grid_and_metadata() -> None:
    samples = tuple(_sample(f"m{index}", index) for index in range(3))
    model = _fit_model(samples)

    prediction = model.predict(samples[0])

    assert prediction.probability.shape == (2, 3)
    assert prediction.uncertainty_std.shape == (2, 3)
    assert np.isfinite(prediction.probability).all()
    assert np.all((prediction.probability >= 0) & (prediction.probability <= 1))
    assert np.isfinite(prediction.uncertainty_std).all()
    assert np.all(prediction.uncertainty_std >= 0)
    assert prediction.metadata["target_definition"] == "observed_viirs_occupancy"
    assert prediction.metadata["target_data_status"] == "model_estimate"
    assert prediction.metadata["grid"]["row_order"] == "north_to_south"
    assert len(prediction.metadata["input_channel_names"]) > len(
        prediction.metadata["input_feature_names"]
    )


def test_default_configured_target_grid_runs_without_changing_m4_defaults() -> None:
    samples = tuple(
        _sample(f"default{index}", index, shape=(3, 3))
        for index in range(3)
    )
    model = ProbabilisticSpatialEnsemble(
        hidden_channels=2,
        conv_blocks=1,
        ensemble_size=2,
        epochs=1,
        batch_size=2,
        random_seed=2,
    ).fit(samples)

    assert model.grid_height == 3
    assert model.grid_width == 3
    assert model.predict(samples[0]).probability.shape == (3, 3)


def test_missing_features_are_imputed_and_marked_using_training_data() -> None:
    samples = (
        _sample("m0", 0, missing_environment=True),
        _sample("m1", 1),
        _sample("m2", 2),
    )
    model = _fit_model(samples)

    assert "missing_elevation_m" in model.input_channel_names
    prediction = model.predict(samples[0])
    assert np.isfinite(prediction.probability).all()


def test_prediction_does_not_use_target_values_or_status() -> None:
    samples = tuple(_sample(f"m{index}", index) for index in range(3))
    model = _fit_model(samples)
    sample = samples[0]
    changed_target = replace(
        sample,
        target_grid=np.zeros_like(sample.target_grid),
        metadata={**sample.metadata, "target_data_status": "not_available"},
    )

    original_tensor, _ = training_sample_to_spatial_tensor(sample)
    changed_tensor, _ = training_sample_to_spatial_tensor(changed_target)
    np.testing.assert_array_equal(original_tensor, changed_tensor)
    np.testing.assert_array_equal(
        model.predict(sample).probability,
        model.predict(changed_target).probability,
    )


def test_training_rejects_estimated_targets() -> None:
    sample = _sample("m0", 0)
    estimated = replace(
        sample,
        metadata={**sample.metadata, "target_data_status": "model_estimate"},
    )

    with pytest.raises(ValueError, match="estimated/non-observed targets"):
        _fit_model((estimated,))


def test_training_rejects_different_target_representations() -> None:
    sample = _sample("m0", 0)
    incompatible_target = replace(
        sample,
        metadata={**sample.metadata, "target_representation": "other_target"},
    )

    with pytest.raises(ValueError, match="does not use the observed VIIRS"):
        _fit_model((incompatible_target,))


def test_grid_shape_must_match_configured_architecture() -> None:
    model = _fit_model(tuple(_sample(f"m{index}", index) for index in range(3)))
    incompatible = _sample("wide", 0, shape=(3, 3))

    with pytest.raises(ValueError, match="does not match configured"):
        model.predict(incompatible)


def test_parent_samples_are_kept_whole_across_partitions() -> None:
    samples = tuple(_sample(f"m{index}", index) for index in range(4))
    duplicate_parent = replace(samples[0], modis_features={"frp": 10, "confidence": "low"})

    split = split_samples_by_parent(
        (*samples, duplicate_parent),
        validation_fraction=0.25,
        test_fraction=0.25,
        random_seed=4,
    )
    partitions = (split.train, split.validation, split.test)
    parent_partitions = [
        {sample.modis_id for sample in partition} for partition in partitions
    ]

    assert all(partition for partition in partitions)
    assert not (parent_partitions[0] & parent_partitions[1])
    assert not (parent_partitions[0] & parent_partitions[2])
    assert not (parent_partitions[1] & parent_partitions[2])
    sample_zero_partitions = [
        index
        for index, partition in enumerate(partitions)
        if any(sample.modis_id == samples[0].modis_id for sample in partition)
    ]
    assert len(sample_zero_partitions) == 1
    assert split.train[0].metadata["observation_datetime"] is not None


def test_identical_ensemble_members_have_zero_disagreement() -> None:
    identical = np.full((3, 2, 3), 0.25, dtype=np.float32)

    estimate = summarize_ensemble_probabilities(identical)

    np.testing.assert_array_equal(estimate.probability, 0.25)
    np.testing.assert_array_equal(estimate.uncertainty_std, 0.0)


def test_ensemble_spread_requires_multiple_members() -> None:
    with pytest.raises(ValueError, match="at least two"):
        ProbabilisticSpatialEnsemble(ensemble_size=1)
    with pytest.raises(ValueError, match="at least two members"):
        summarize_ensemble_probabilities(np.full((1, 2, 3), 0.5))


def test_spatial_model_save_load_preserves_predictions(tmp_path) -> None:
    samples = tuple(_sample(f"m{index}", index) for index in range(3))
    model = _fit_model(samples)
    expected = model.predict(samples[0])

    artifact = model.save(tmp_path / "model" / "spatial.pt")
    restored = ProbabilisticSpatialEnsemble.load(artifact)
    actual = restored.predict(samples[0])

    np.testing.assert_allclose(actual.probability, expected.probability, atol=1e-7)
    np.testing.assert_allclose(
        actual.uncertainty_std, expected.uncertainty_std, atol=1e-7
    )
    assert restored.input_channel_names == model.input_channel_names
    assert restored.training_sample_ids == model.training_sample_ids
    assert restored.member_seeds == model.member_seeds


def test_fixed_seed_produces_repeatable_ensemble_predictions() -> None:
    samples = tuple(_sample(f"m{index}", index) for index in range(3))

    first = _fit_model(samples).predict(samples[0])
    second = _fit_model(samples).predict(samples[0])

    np.testing.assert_array_equal(first.probability, second.probability)
    np.testing.assert_array_equal(first.uncertainty_std, second.uncertainty_std)


def test_probability_metrics_include_calibration_and_ranking_scores() -> None:
    targets = np.array([[0, 1], [0, 1]], dtype=np.float32)
    probabilities = np.array([[0.1, 0.9], [0.4, 0.6]], dtype=np.float32)

    result = evaluate_probability_grids(targets, probabilities, n_bins=4)

    assert result.brier_score == pytest.approx(0.085)
    assert result.log_loss > 0
    assert result.roc_auc == pytest.approx(1.0)
    assert result.pr_auc == pytest.approx(1.0)
    assert result.reliability["count"].sum() == targets.size
    assert result.metric_status["roc_auc"] == "defined"


def test_single_class_metrics_are_reported_as_undefined() -> None:
    targets = np.zeros((2, 2), dtype=np.float32)
    probabilities = np.full((2, 2), 0.1, dtype=np.float32)

    result = evaluate_probability_grids(targets, probabilities)

    assert np.isfinite(result.brier_score)
    assert np.isfinite(result.log_loss)
    assert result.roc_auc is None
    assert result.pr_auc is None
    assert "both target classes" in result.metric_status["roc_auc"]
