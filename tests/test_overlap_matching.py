from pathlib import Path

import pandas as pd
import pytest

from spatial_ml.data import clean_firms_records, load_modis, load_viirs
from spatial_ml.pipeline import (
    generate_observation_ids,
    generate_overlap_dataset,
    get_overlap_period,
    save_overlap_tables,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_synthetic_overlap() -> tuple[pd.DataFrame, pd.DataFrame]:
    modis = clean_firms_records(
        load_modis(FIXTURE_DIR / "overlap_modis_synthetic.csv")
    )
    viirs = clean_firms_records(
        load_viirs(FIXTURE_DIR / "overlap_viirs_synthetic.csv")
    )
    return modis, viirs


def test_overlap_period_uses_actual_input_datetime_ranges() -> None:
    modis, viirs = _load_synthetic_overlap()

    overlap = get_overlap_period(modis, viirs)

    assert overlap.overlap_start == pd.Timestamp("2020-06-01 12:00")
    assert overlap.overlap_end == pd.Timestamp("2020-06-02 12:00")


def test_non_overlapping_datasets_raise_a_clear_error() -> None:
    modis, viirs = _load_synthetic_overlap()
    viirs["datetime"] += pd.Timedelta(days=30)

    with pytest.raises(ValueError, match="datetime ranges do not overlap"):
        get_overlap_period(modis, viirs)


def test_default_matching_is_same_day_and_within_configured_tolerance() -> None:
    modis, viirs = _load_synthetic_overlap()

    pairs, candidates = generate_overlap_dataset(modis, viirs)

    assert len(pairs) == 4
    assert len(candidates) == 3
    assert (
        candidates["modis_datetime"].dt.date
        == candidates["viirs_datetime"].dt.date
    ).all()
    assert candidates["time_difference_minutes"].le(30).all()
    assert not candidates["viirs_datetime"].eq(
        pd.Timestamp("2020-06-01 12:45")
    ).any()
    assert not candidates["viirs_datetime"].eq(
        pd.Timestamp("2020-06-02 12:01")
    ).any()


def test_nearest_match_and_candidate_count_are_preserved() -> None:
    modis, viirs = _load_synthetic_overlap()

    pairs, candidates = generate_overlap_dataset(modis, viirs)
    origin_pair = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]
    origin_candidates = candidates.loc[
        candidates["modis_id"] == origin_pair["modis_id"]
    ]

    assert origin_pair["viirs_candidate_count"] == 2
    assert origin_pair["match_valid"]
    assert origin_pair["match_quality"] == "valid"
    assert origin_pair["viirs_id"] == origin_candidates.iloc[0]["viirs_id"]
    assert origin_pair["viirs_datetime"] == pd.Timestamp("2020-06-01 12:20")
    assert origin_pair["spatial_distance_m"] == pytest.approx(111.0, abs=1.0)
    assert origin_pair["temporal_difference_minutes"] == 20.0


def test_spatial_radius_excludes_distant_candidate_and_is_configurable() -> None:
    modis, viirs = _load_synthetic_overlap()

    _, default_candidates = generate_overlap_dataset(modis, viirs)
    _, smaller_radius_candidates = generate_overlap_dataset(
        modis, viirs, spatial_radius_m=150.0
    )

    assert len(default_candidates) == 3
    assert len(smaller_radius_candidates) == 1
    assert smaller_radius_candidates["distance_m"].max() < 150.0


def test_time_tolerance_can_be_configured() -> None:
    modis, viirs = _load_synthetic_overlap()

    pairs, candidates = generate_overlap_dataset(
        modis, viirs, time_tolerance_minutes=10
    )
    origin_pair = pairs.loc[pairs["modis_latitude"] == 40.0].iloc[0]

    assert origin_pair["viirs_candidate_count"] == 1
    assert origin_pair["viirs_datetime"] == pd.Timestamp("2020-06-01 12:05")
    assert candidates["time_difference_minutes"].le(10).all()


def test_distance_uses_geodesic_meters_and_handles_antimeridian() -> None:
    modis, viirs = _load_synthetic_overlap()

    pairs, _ = generate_overlap_dataset(modis, viirs)
    dateline_pair = pairs.loc[pairs["modis_latitude"] == 0.0].iloc[0]

    assert dateline_pair["match_valid"]
    assert 600.0 < dateline_pair["spatial_distance_m"] < 800.0
    assert dateline_pair["delta_x_m"] > 0
    assert abs(dateline_pair["delta_y_m"]) < 1.0


def test_relative_coordinates_use_east_north_convention() -> None:
    modis, viirs = _load_synthetic_overlap()

    _, candidates = generate_overlap_dataset(modis, viirs)
    north_candidate = candidates.loc[
        (candidates["modis_latitude"] == 40.0)
        & (candidates["viirs_datetime"] == pd.Timestamp("2020-06-01 12:05"))
    ].iloc[0]

    assert abs(north_candidate["delta_x_m"]) < 1.0
    assert north_candidate["delta_y_m"] > 0


def test_unmatched_modis_observations_remain_in_pair_table() -> None:
    modis, viirs = _load_synthetic_overlap()

    pairs, _ = generate_overlap_dataset(modis, viirs)
    unmatched = pairs.loc[~pairs["match_valid"]]

    assert len(unmatched) == 2
    assert unmatched["viirs_candidate_count"].eq(0).all()
    assert unmatched["match_quality"].eq("invalid").all()
    assert unmatched["viirs_id"].isna().all()


def test_observation_ids_are_stable_across_ordering() -> None:
    modis, _ = _load_synthetic_overlap()

    original_ids = generate_observation_ids(modis, "MODIS")
    reordered_ids = generate_observation_ids(modis.iloc[::-1], "MODIS")

    assert original_ids.tolist() == reordered_ids.iloc[::-1].tolist()
    assert original_ids.tolist() == generate_observation_ids(
        modis, "MODIS"
    ).tolist()


def test_overlap_output_is_deterministic() -> None:
    modis, viirs = _load_synthetic_overlap()

    first_pairs, first_candidates = generate_overlap_dataset(modis, viirs)
    second_pairs, second_candidates = generate_overlap_dataset(
        modis.iloc[::-1], viirs.iloc[::-1]
    )

    pd.testing.assert_frame_equal(first_pairs, second_pairs)
    pd.testing.assert_frame_equal(first_candidates, second_candidates)


def test_overlap_tables_can_be_saved_explicitly(tmp_path: Path) -> None:
    modis, viirs = _load_synthetic_overlap()
    pairs, candidates = generate_overlap_dataset(modis, viirs)

    pair_path, candidate_path = save_overlap_tables(
        pairs, candidates, tmp_path / "overlap"
    )

    assert pair_path.name == "modis_viirs_pairs.csv"
    assert candidate_path.name == "modis_viirs_candidates.csv"
    assert len(pd.read_csv(pair_path)) == len(pairs)
    assert len(pd.read_csv(candidate_path)) == len(candidates)
