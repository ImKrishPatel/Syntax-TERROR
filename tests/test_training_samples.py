from pathlib import Path

import numpy as np
import pandas as pd

from spatial_ml.data import (
    TrainingSample,
    build_training_sample,
    clean_firms_records,
    load_modis,
    load_viirs,
)
from spatial_ml.features import EnvironmentalFeatureSet
from spatial_ml.pipeline import generate_overlap_dataset

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_overlap_sample_inputs() -> tuple[
    pd.DataFrame, pd.DataFrame
]:
    modis = clean_firms_records(
        load_modis(FIXTURE_DIR / "overlap_modis_synthetic.csv")
    )
    viirs = clean_firms_records(
        load_viirs(FIXTURE_DIR / "overlap_viirs_synthetic.csv")
    )
    return generate_overlap_dataset(modis, viirs)


def test_sample_combines_modis_features_environment_and_observed_target() -> None:
    pairs, candidates = _load_overlap_sample_inputs()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[candidates["modis_id"] == modis["modis_id"]]
    environment = EnvironmentalFeatureSet(
        values=np.full((3, 3, 2), 5.0, dtype=np.float32),
        feature_names=("elevation_m", "land_cover"),
        metadata={"sources": [{"name": "synthetic"}]},
    )

    sample = build_training_sample(modis, viirs, environment)

    assert sample.modis_id == modis["modis_id"]
    assert sample.modis_features == {"frp": 10.0, "confidence": "80"}
    assert sample.environmental_features.shape == (3, 3, 2)
    assert sample.environmental_feature_names == ("elevation_m", "land_cover")
    assert sample.target_grid.shape == (3, 3)
    assert sample.target_grid.sum() == 2
    assert sample.metadata["source_sensors"] == ["MODIS", "VIIRS"]
    assert sample.metadata["target_sensor"] == "VIIRS"
    assert sample.metadata["target_data_status"] == "observed"
    assert sample.metadata["target_representation"] == (
        "binary_viirs_point_observation_occupancy"
    )
    assert sample.metadata["viirs_ids"] == viirs["viirs_id"].tolist()


def test_unmatched_modis_sample_is_preserved_with_empty_target() -> None:
    pairs, candidates = _load_overlap_sample_inputs()
    modis = pairs.loc[~pairs["match_valid"]].iloc[0]
    viirs = candidates.loc[candidates["modis_id"] == modis["modis_id"]]

    sample = build_training_sample(modis, viirs)

    assert not sample.target_grid.any()
    assert sample.environmental_features.shape == (3, 3, 0)
    assert sample.target_observations.empty
    assert sample.metadata["target_observation_count"] == 0
    assert sample.metadata["target_in_grid_observation_count"] == 0


def test_training_sample_npz_round_trip_is_pickle_free(tmp_path: Path) -> None:
    pairs, candidates = _load_overlap_sample_inputs()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[candidates["modis_id"] == modis["modis_id"]]
    environmental = EnvironmentalFeatureSet(
        values=np.ones((3, 3, 1), dtype=np.float32),
        feature_names=("elevation_m",),
        metadata={"sources": [{"name": "synthetic_elevation"}]},
    )
    sample = build_training_sample(modis, viirs, environmental)
    destination = sample.save(tmp_path / "serialized" / "sample.npz")

    restored = TrainingSample.load(destination)

    assert restored.modis_id == sample.modis_id
    assert restored.metadata == sample.metadata
    assert restored.modis_features == sample.modis_features
    assert restored.environmental_feature_names == ("elevation_m",)
    np.testing.assert_array_equal(
        restored.environmental_features, sample.environmental_features
    )
    np.testing.assert_array_equal(restored.target_grid, sample.target_grid)
    assert restored.target_observations["viirs_id"].tolist() == (
        sample.target_observations["viirs_id"].tolist()
    )
    assert restored.target_observations["target_row"].tolist() == (
        sample.target_observations["target_row"].tolist()
    )
