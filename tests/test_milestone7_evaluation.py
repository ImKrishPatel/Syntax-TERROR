import json

import numpy as np
import pandas as pd
import pytest
import torch

from spatial_ml.data import TrainingSample
from spatial_ml.evaluation import (
    create_evaluation_split,
    evaluate_uncertainty,
    fit_calibrator_on_validation,
    spatial_block_id,
)
from spatial_ml.evaluation.comparison import (
    compare_models,
    write_evaluation_artifacts,
)
from spatial_ml.models import ProbabilisticRandomForest, ProbabilisticSpatialEnsemble

torch.set_num_threads(1)


def _sample(index: int, year: int, longitude: float = -110.0) -> TrainingSample:
    target = np.zeros((2, 2), dtype=np.float32)
    target.flat[index % target.size] = 1.0
    environment = np.asarray(
        [
            [[100.0 + index, 1.0], [120.0 + index, 1.0]],
            [[140.0 + index, 2.0], [160.0 + index, 2.0]],
        ],
        dtype=np.float32,
    )
    sample_id = f"modis-{year}-{index}"
    return TrainingSample(
        modis_id=sample_id,
        modis_features={"frp": float(index + 1), "confidence": 80.0},
        environmental_features=environment,
        environmental_feature_names=("elevation_m", "land_cover"),
        target_grid=target,
        target_observations=pd.DataFrame(),
        metadata={
            "modis_id": sample_id,
            "observation_datetime": f"{year}-06-{index + 1:02d}T12:00:00",
            "modis_latitude": 35.0,
            "modis_longitude": longitude,
            "target_data_status": "observed",
            "target_representation": "binary_viirs_point_observation_occupancy",
            "grid": {"height": 2, "width": 2, "resolution_m": 375.0},
        },
    )


def _temporal_split():
    samples = [
        _sample(index, year)
        for year in (2020, 2021, 2022)
        for index in range(1, 4)
    ]
    return create_evaluation_split(
        samples,
        "temporal",
        train_end="2020-12-31",
        validation_end="2021-12-31",
    )


def _fitted_models(split):
    forest = ProbabilisticRandomForest(n_estimators=8, random_state=5).fit(
        split.train
    )
    cnn = ProbabilisticSpatialEnsemble(
        grid_height=2,
        grid_width=2,
        hidden_channels=3,
        conv_blocks=1,
        ensemble_size=2,
        epochs=2,
        batch_size=2,
        random_seed=5,
    ).fit(split.train)
    return forest, cnn


def test_temporal_split_keeps_parent_samples_disjoint_and_reproducible() -> None:
    split = _temporal_split()
    repeated = _temporal_split()

    assert split.strategy == "temporal"
    assert [sample.modis_id for sample in split.train] == [
        sample.modis_id for sample in repeated.train
    ]
    assert max(
        pd.Timestamp(sample.metadata["observation_datetime"])
        for sample in split.train
    ) <= pd.Timestamp("2020-12-31 23:59:59.999999999")
    assert {
        sample.modis_id for sample in split.train
    }.isdisjoint(sample.modis_id for sample in split.validation)
    assert {
        sample.modis_id for sample in split.validation
    }.isdisjoint(sample.modis_id for sample in split.test)


def test_spatial_split_keeps_spatial_blocks_disjoint() -> None:
    samples = [
        _sample(index, 2020, longitude=-120.0 + index * 10.0)
        for index in range(9)
    ]

    split = create_evaluation_split(
        samples,
        "spatial",
        block_size_m=100_000.0,
        random_seed=11,
    )

    partition_blocks = {
        name: set(values) for name, values in split.partition_blocks.items()
    }
    assert partition_blocks["train"].isdisjoint(partition_blocks["validation"])
    assert partition_blocks["train"].isdisjoint(partition_blocks["test"])
    assert partition_blocks["validation"].isdisjoint(partition_blocks["test"])
    assert {
        spatial_block_id(sample, 100_000.0) for sample in split.test
    } == partition_blocks["test"]


def test_combined_split_drops_samples_outside_time_block_intersections() -> None:
    locations = [
        _sample(index, 2020, longitude=-120.0 + index * 10.0)
        for index in range(9)
    ]
    spatial = create_evaluation_split(
        locations,
        "spatial",
        block_size_m=100_000.0,
        random_seed=17,
    )
    block_partition = {
        block: name
        for name, blocks in spatial.partition_blocks.items()
        for block in blocks
    }
    year_for_partition = {"train": 2020, "validation": 2021, "test": 2022}
    crossed_year = {"train": 2021, "validation": 2022, "test": 2020}
    combined_samples = []
    for index, location in enumerate(locations):
        block = spatial_block_id(location, 100_000.0)
        owner = block_partition[block]
        for year in (year_for_partition[owner], crossed_year[owner]):
            combined_samples.append(
                _sample(index + (year - 2020) * 10, year, location.metadata["modis_longitude"])
            )

    split = create_evaluation_split(
        combined_samples,
        "combined",
        train_end="2020",
        validation_end="2021",
        block_size_m=100_000.0,
        random_seed=17,
    )

    assert len(split.dropped_parent_ids) == 9
    assert split.train and split.validation and split.test
    for name in ("train", "validation", "test"):
        expected_blocks = set(split.partition_blocks[name])
        assert {
            spatial_block_id(sample, 100_000.0)
            for sample in getattr(split, name)
        } == expected_blocks


@pytest.mark.parametrize("method", ("platt", "isotonic"))
def test_validation_calibration_tracks_only_validation_parents(
    tmp_path, method: str
) -> None:
    targets = [
        np.asarray([[1, 0], [0, 0]], dtype=np.uint8),
        np.asarray([[0, 1], [0, 0]], dtype=np.uint8),
        np.asarray([[0, 0], [1, 0]], dtype=np.uint8),
    ]
    probabilities = [
        np.asarray([[0.7, 0.2], [0.3, 0.1]], dtype=float),
        np.asarray([[0.2, 0.8], [0.4, 0.1]], dtype=float),
        np.asarray([[0.2, 0.3], [0.9, 0.4]], dtype=float),
    ]
    attempt = fit_calibrator_on_validation(
        targets,
        probabilities,
        ["validation-a", "validation-b", "validation-c"],
        method=method,
        min_cells=1,
        min_per_class=1,
    )

    assert attempt.status == "available"
    assert attempt.calibrator is not None
    assert attempt.calibrator.provenance["fit_partition"] == "validation"
    assert attempt.calibrator.provenance["validation_parent_ids"] == [
        "validation-a",
        "validation-b",
        "validation-c",
    ]
    artifact = attempt.calibrator.save(tmp_path / "calibrator.json")
    loaded = type(attempt.calibrator).load(artifact)
    np.testing.assert_allclose(
        loaded.apply(np.asarray([0.25, 0.75])),
        attempt.calibrator.apply(np.asarray([0.25, 0.75])),
    )


def test_calibration_reports_insufficient_parent_support() -> None:
    attempt = fit_calibrator_on_validation(
        [np.asarray([[1, 0]], dtype=np.uint8)],
        [np.asarray([[0.8, 0.2]])],
        ["one-parent"],
        min_cells=1,
        min_per_class=1,
    )

    assert attempt.status == "unavailable"
    assert "parent samples" in (attempt.reason or "")


def test_uncertainty_diagnostics_sort_by_uncertainty_and_report_counts() -> None:
    result = evaluate_uncertainty(
        np.asarray([[1, 0], [0, 1]]),
        np.asarray([[0.8, 0.1], [0.4, 0.7]]),
        np.asarray([[0.4, 0.1], [0.3, 0.2]]),
        n_quantiles=2,
    )

    assert result["cell_count"] == 4
    low, high = result["uncertainty_quantile_errors"]
    assert low["mean_uncertainty"] < high["mean_uncertainty"]
    assert result["risk_coverage"][0]["retained_cell_count"] == 1
    assert result["risk_coverage"][-1]["retained_cell_count"] == 4


def test_model_comparison_uses_identical_test_samples_and_writes_artifacts(
    tmp_path,
) -> None:
    split = _temporal_split()
    forest, cnn = _fitted_models(split)

    evaluation = compare_models(
        split,
        forest,
        cnn,
        min_calibration_cells=1,
        min_calibration_per_class=1,
        min_calibration_parents=2,
    )
    artifacts = write_evaluation_artifacts(evaluation, tmp_path)

    assert evaluation.sample_ids == tuple(
        sample.modis_id for sample in split.test
    )
    assert evaluation.report["comparison_population"]["identical_for_models"]
    assert set(evaluation.raw_probabilities) == {"random_forest", "spatial_ensemble"}
    assert evaluation.report["models"]["random_forest"]["calibration"][
        "validation_parent_ids"
    ] == [sample.modis_id for sample in split.validation]
    assert artifacts["metrics"].is_file()
    report = json.loads(artifacts["metrics"].read_text(encoding="utf-8"))
    assert report["product_label"].startswith("MODEL-ESTIMATED")
    assert artifacts["predictions"].is_file()
    with np.load(artifacts["predictions"], allow_pickle=False) as archive:
        assert "random_forest_raw_probability" in archive
        assert "spatial_ensemble_raw_probability" in archive
        assert "target_grids" in archive
    assert artifacts["reliability"].is_file()


def test_model_comparison_rejects_models_fit_on_different_parents() -> None:
    split = _temporal_split()
    forest, cnn = _fitted_models(split)
    cnn.training_sample_ids = cnn.training_sample_ids[:-1]

    with pytest.raises(ValueError, match="same nonempty parent-sample population"):
        compare_models(split, forest, cnn)
