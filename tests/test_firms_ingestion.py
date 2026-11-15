from pathlib import Path

import pandas as pd
import pytest

from spatial_ml.data import (
    clean_firms_records,
    load_firms_csv,
    load_modis,
    load_viirs,
    normalize_firms_schema,
    quality_control_summary,
    temporal_range_summary,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_load_modis_csv_normalizes_schema_and_sensor() -> None:
    records = load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv")

    assert len(records) == 6
    assert records["sensor"].eq("MODIS").all()
    assert {
        "latitude",
        "longitude",
        "date",
        "time",
        "datetime",
        "sensor",
        "satellite",
        "confidence",
        "frp",
        "daynight",
    }.issubset(records.columns)


def test_load_viirs_csv_normalizes_aliases_and_sensor() -> None:
    records = load_viirs(FIXTURE_DIR / "viirs_sample_synthetic.csv")

    assert records["sensor"].eq("VIIRS").all()
    assert records.loc[0, "daynight"] == "D"
    assert records.loc[0, "datetime"] == pd.Timestamp("2012-01-02 00:00")


def test_explicit_sensor_argument_overrides_source_sensor() -> None:
    source = pd.DataFrame(
        {
            "LAT": ["10"],
            "LON": ["20"],
            "ACQ_DATE": ["2020-01-02"],
            "ACQ_TIME": ["5"],
            "satellite": ["test-platform"],
            "instrument": ["test-instrument"],
        }
    )

    normalized = normalize_firms_schema(source, "VIIRS")

    assert normalized.loc[0, "sensor"] == "VIIRS"
    assert normalized.loc[0, "time"] == "0005"
    assert normalized.loc[0, "datetime"] == pd.Timestamp("2020-01-02 00:05")


def test_malformed_datetime_is_retained_until_cleaning_then_excluded() -> None:
    records = load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv")

    assert records["datetime"].isna().sum() == 2
    cleaned = clean_firms_records(records)
    assert cleaned["datetime"].notna().all()
    assert len(cleaned) == 2


def test_invalid_coordinates_are_removed() -> None:
    modis = clean_firms_records(load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv"))
    viirs = clean_firms_records(load_viirs(FIXTURE_DIR / "viirs_sample_synthetic.csv"))

    assert modis["latitude"].between(-90, 90).all()
    assert viirs["longitude"].between(-180, 180).all()
    assert len(modis) == 2
    assert len(viirs) == 3


def test_duplicate_records_are_deduplicated_by_observation_key() -> None:
    records = load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv")
    cleaned = clean_firms_records(records)

    assert len(records) - len(cleaned) == 4
    assert not cleaned.duplicated(
        subset=[
            "sensor",
            "latitude",
            "longitude",
            "datetime",
            "satellite",
            "instrument",
        ]
    ).any()


def test_missing_and_invalid_frp_do_not_remove_records() -> None:
    records = load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv")
    cleaned = clean_firms_records(records)

    assert len(cleaned) == 2
    assert cleaned["frp"].isna().sum() == 1


def test_invalid_confidence_is_flagged_but_original_value_is_preserved() -> None:
    records = load_viirs(FIXTURE_DIR / "viirs_sample_synthetic.csv")
    cleaned = clean_firms_records(records)

    invalid = cleaned.loc[cleaned["confidence"] == "999"].iloc[0]
    assert invalid["confidence"] == "999"
    assert not invalid["confidence_valid"]
    assert cleaned["confidence_valid"].sum() == 2


def test_temporal_range_summary_reports_observed_ranges_and_overlap() -> None:
    modis = clean_firms_records(load_modis(FIXTURE_DIR / "modis_sample_synthetic.csv"))
    viirs = clean_firms_records(load_viirs(FIXTURE_DIR / "viirs_sample_synthetic.csv"))

    summary = temporal_range_summary(modis, viirs)

    assert summary["MODIS"]["earliest_datetime"] == pd.Timestamp("2001-06-15 00:05")
    assert summary["VIIRS"]["latest_datetime"] == pd.Timestamp("2012-01-04 23:59")
    assert summary["overlap"]["overlaps"] is False
    shared_time = temporal_range_summary(modis, modis.iloc[[0]])
    assert shared_time["overlap"]["overlaps"] is True


def test_qc_summary_reports_counts_ranges_missingness_and_sensor() -> None:
    raw = load_viirs(FIXTURE_DIR / "viirs_sample_synthetic.csv")
    cleaned = clean_firms_records(raw)

    summary = quality_control_summary(raw, cleaned)

    assert summary["raw_records"] == 4
    assert summary["valid_records"] == 3
    assert summary["removed_records"] == 1
    assert summary["percentage_retained"] == 75.0
    assert summary["sensor"] == "VIIRS"
    assert summary["duplicate_count"] == 0
    assert summary["invalid_confidence_count"] == 1
    assert summary["invalid_frp_count"] == 1
    assert summary["unique_satellites"] == ["NOAA-20", "SUOMI-NPP"]
    assert summary["day_night_counts"] == {"D": 2, "N": 1}


def test_missing_file_and_invalid_sensor_have_clear_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="FIRMS CSV file does not exist"):
        load_firms_csv(tmp_path / "missing.csv", "MODIS")
    with pytest.raises(ValueError, match="expected MODIS or VIIRS"):
        load_firms_csv(FIXTURE_DIR / "modis_sample_synthetic.csv", "ASTER")
