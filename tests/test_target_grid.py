from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from spatial_ml.data import (
    LocalTargetGrid,
    build_viirs_target_grid,
    clean_firms_records,
    load_modis,
    load_viirs,
)
from spatial_ml.pipeline import generate_overlap_dataset

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _overlap_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    modis = clean_firms_records(
        load_modis(FIXTURE_DIR / "overlap_modis_synthetic.csv")
    )
    viirs = clean_firms_records(
        load_viirs(FIXTURE_DIR / "overlap_viirs_synthetic.csv")
    )
    pairs, candidates = generate_overlap_dataset(modis, viirs)
    return pairs, candidates, modis


def test_default_grid_dimensions_resolution_and_center() -> None:
    grid = LocalTargetGrid(40.0, -120.0)

    assert grid.shape == (3, 3)
    assert grid.resolution_m == 375
    assert grid.cell_for_local(0.0, 0.0) == (1, 1)
    assert grid.cell_center_local(1, 1) == (0.0, 0.0)
    assert grid.cell_for_geographic(-120.0, 40.0) == (1, 1)


def test_local_aeqd_metadata_preserves_the_crs_definition() -> None:
    grid = LocalTargetGrid(40.0, -120.0)

    assert grid.metadata["target_crs"] == grid.crs.to_string()


def test_grid_dimensions_and_resolution_are_configurable() -> None:
    grid = LocalTargetGrid(
        40.0, -120.0, resolution_m=250.0, width=4, height=2
    )

    assert grid.shape == (2, 4)
    assert grid.resolution_m == 250.0
    assert grid.bounds_local == (-500.0, -250.0, 500.0, 250.0)


def test_local_coordinates_round_trip_in_meters() -> None:
    grid = LocalTargetGrid(40.0, -120.0)

    longitude, latitude = grid.local_to_geographic(120.0, -75.0)
    x_m, y_m = grid.geographic_to_local(longitude, latitude)

    assert float(x_m) == pytest.approx(120.0, abs=1e-5)
    assert float(y_m) == pytest.approx(-75.0, abs=1e-5)


def test_grid_boundaries_have_stable_row_and_column_indices() -> None:
    grid = LocalTargetGrid(0.0, 0.0)
    left, bottom, right, top = grid.bounds_local

    assert grid.cell_for_local(left, 0.0) == (1, 0)
    assert grid.cell_for_local(np.nextafter(right, -np.inf), 0.0) == (1, 2)
    assert grid.cell_for_local(right, 0.0) is None
    assert grid.cell_for_local(0.0, top) == (0, 1)
    assert grid.cell_for_local(0.0, bottom) is None


def test_custom_projected_crs_is_supported() -> None:
    grid = LocalTargetGrid(
        40.0,
        -120.0,
        projection_strategy="custom",
        target_crs="EPSG:32611",
    )

    assert grid.crs.to_epsg() == 32611
    assert grid.cell_for_geographic(-120.0, 40.0) == (1, 1)


def test_viirs_candidates_are_mapped_and_source_metadata_is_preserved() -> None:
    pairs, candidates, _ = _overlap_data()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[candidates["modis_id"] == modis["modis_id"]]

    result = build_viirs_target_grid(modis, viirs)

    assert result.values.shape == (3, 3)
    assert result.values.sum() == 2
    assert result.observations["in_grid"].all()
    assert result.observations["viirs_id"].notna().all()
    assert result.observations["viirs_frp"].notna().all()
    assert result.observations["target_row"].notna().all()
    assert result.observations["target_col"].notna().all()


def test_multiple_viirs_points_in_one_cell_aggregate_as_binary_occupancy() -> None:
    pairs, candidates, _ = _overlap_data()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[
        candidates["modis_id"] == modis["modis_id"]
    ].iloc[:2].copy()
    viirs["delta_x_m"] = [20.0, 30.0]
    viirs["delta_y_m"] = [10.0, 15.0]

    result = build_viirs_target_grid(modis, viirs)

    assert len(result.observations) == 2
    assert result.observations["target_row"].nunique() == 1
    assert result.observations["target_col"].nunique() == 1
    assert result.values.sum() == 1
    assert set(np.unique(result.values)) <= {0.0, 1.0}


def test_empty_viirs_input_returns_an_empty_grid_without_dropping_sample() -> None:
    pairs, candidates, _ = _overlap_data()
    modis = pairs.loc[~pairs["match_valid"]].iloc[0]
    empty_candidates = candidates.iloc[:0].copy()

    result = build_viirs_target_grid(modis, empty_candidates)

    assert result.values.shape == (3, 3)
    assert not result.values.any()
    assert result.observations.empty


def test_out_of_grid_viirs_point_is_retained_and_flagged() -> None:
    pairs, candidates, _ = _overlap_data()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[
        candidates["modis_id"] == modis["modis_id"]
    ].iloc[:1].copy()
    right = LocalTargetGrid(40.0, -120.0).bounds_local[2]
    viirs.loc[:, "delta_x_m"] = right
    viirs.loc[:, "delta_y_m"] = 0.0

    result = build_viirs_target_grid(modis, viirs)

    assert len(result.observations) == 1
    assert not result.observations.loc[0, "in_grid"]
    assert pd.isna(result.observations.loc[0, "target_row"])
    assert not result.values.any()


def test_target_mapping_is_deterministic() -> None:
    pairs, candidates, _ = _overlap_data()
    modis = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    viirs = candidates.loc[candidates["modis_id"] == modis["modis_id"]]

    first = build_viirs_target_grid(modis, viirs)
    second = build_viirs_target_grid(modis, viirs.iloc[::-1])

    np.testing.assert_array_equal(first.values, second.values)
    pd.testing.assert_frame_equal(
        first.observations.sort_values("viirs_id").reset_index(drop=True),
        second.observations.sort_values("viirs_id").reset_index(drop=True),
    )
