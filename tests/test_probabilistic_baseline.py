from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from spatial_ml.data import (
    TrainingSample,
    build_training_sample,
    clean_firms_records,
    load_modis,
    load_viirs,
)
from spatial_ml.features import EnvironmentalFeatureSet
from spatial_ml.models import ProbabilisticRandomForest
from spatial_ml.pipeline import generate_overlap_dataset

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _training_samples() -> tuple[TrainingSample, TrainingSample]:
    modis = clean_firms_records(
        load_modis(FIXTURE_DIR / "overlap_modis_synthetic.csv")
    )
    viirs = clean_firms_records(
        load_viirs(FIXTURE_DIR / "overlap_viirs_synthetic.csv")
    )
    pairs, candidates = generate_overlap_dataset(modis, viirs)
    matched = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    matched_candidates = candidates.loc[
        candidates["modis_id"] == matched["modis_id"]
    ]
    environmental = EnvironmentalFeatureSet(
        values=np.array(
            [
                [[1.0], [2.0], [3.0]],
                [[4.0], [np.nan], [6.0]],
                [[7.0], [8.0], [9.0]],
            ],
            dtype=np.float32,
        ),
        feature_names=("elevation_m",),
        metadata={"sources": [{"name": "synthetic_elevation"}]},
    )
    positive_sample = build_training_sample(
        matched, matched_candidates, environmental
    )

    unmatched = pairs.loc[~pairs["match_valid"]].iloc[0]
    unmatched_candidates = candidates.loc[
        candidates["modis_id"] == unmatched["modis_id"]
    ]
    empty_sample = build_training_sample(unmatched, unmatched_candidates)
    return positive_sample, empty_sample


def test_random_forest_predicts_probability_grid_and_tree_spread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample, empty_sample = _training_samples()
    model = ProbabilisticRandomForest(n_estimators=31, random_state=7)

    model.fit([sample, empty_sample])
    prediction = model.predict(sample)

    assert model.training_sample_ids == (sample.modis_id,)
    assert model.excluded_sample_ids == (empty_sample.modis_id,)
    assert sample.modis_id not in model.excluded_sample_ids
    assert "Excluded 1 sample(s)" in caplog.text
    assert prediction.probability.shape == sample.target_grid.shape
    assert prediction.uncertainty_std.shape == sample.target_grid.shape
    assert np.isfinite(prediction.probability).all()
    assert np.all((prediction.probability >= 0) & (prediction.probability <= 1))
    assert np.isfinite(prediction.uncertainty_std).all()
    assert np.all(prediction.uncertainty_std >= 0)
    assert prediction.metadata["target_data_status"] == "model_estimate"
    assert prediction.metadata["grid"] == sample.metadata["grid"]
    assert "FINE-SCALE FIRE-DETECTION PROBABILITY + UNCERTAINTY" in (
        prediction.metadata["product_type"]
    )
    assert "not a confidence interval" in prediction.metadata["uncertainty_method"]


def test_prediction_does_not_depend_on_target_values_or_status() -> None:
    sample, _ = _training_samples()
    model = ProbabilisticRandomForest(n_estimators=17, random_state=11).fit(
        [sample]
    )
    no_target = replace(
        sample,
        target_grid=np.zeros_like(sample.target_grid),
        metadata={**sample.metadata, "target_data_status": "not_available"},
    )

    prediction_with_label = model.predict(sample)
    prediction_without_label = model.predict(no_target)

    np.testing.assert_array_equal(
        prediction_with_label.probability,
        prediction_without_label.probability,
    )
    np.testing.assert_array_equal(
        prediction_with_label.uncertainty_std,
        prediction_without_label.uncertainty_std,
    )


def test_empty_evidence_is_excluded_and_reported() -> None:
    _, empty_sample = _training_samples()
    model = ProbabilisticRandomForest(n_estimators=5)

    with pytest.raises(
        ValueError, match="No training samples contain in-grid observed"
    ):
        model.fit([empty_sample])


def test_training_rejects_estimated_targets() -> None:
    sample, _ = _training_samples()
    estimated_target = replace(
        sample,
        metadata={**sample.metadata, "target_data_status": "model_estimate"},
    )

    with pytest.raises(ValueError, match="must have observed VIIRS targets"):
        ProbabilisticRandomForest(n_estimators=5).fit([estimated_target])


def test_training_requires_both_observed_classes() -> None:
    sample, _ = _training_samples()
    all_positive = replace(sample, target_grid=np.ones_like(sample.target_grid))

    with pytest.raises(ValueError, match="requires both observed-positive"):
        ProbabilisticRandomForest(n_estimators=5).fit([all_positive])


def test_prediction_before_fit_has_clear_error() -> None:
    sample, _ = _training_samples()

    with pytest.raises(RuntimeError, match="must be fitted"):
        ProbabilisticRandomForest().predict(sample)


def test_random_forest_requires_multiple_trees_for_uncertainty() -> None:
    with pytest.raises(ValueError, match="at least two"):
        ProbabilisticRandomForest(n_estimators=1)


def test_random_forest_artifact_round_trip_preserves_predictions_and_provenance(
    tmp_path: Path,
) -> None:
    sample, _ = _training_samples()
    model = ProbabilisticRandomForest(n_estimators=7, random_state=23).fit([sample])
    expected = model.predict(sample)

    artifact = model.save(tmp_path / "forest.joblib")
    restored = ProbabilisticRandomForest.load(artifact)
    actual = restored.predict(sample)

    np.testing.assert_array_equal(actual.probability, expected.probability)
    np.testing.assert_array_equal(actual.uncertainty_std, expected.uncertainty_std)
    assert restored.training_sample_ids == model.training_sample_ids
    assert restored.feature_names == model.feature_names
    assert restored.training_cell_count == model.training_cell_count
    assert restored.feature_importances()
    with pytest.raises(FileExistsError, match="overwrite"):
        model.save(artifact)
