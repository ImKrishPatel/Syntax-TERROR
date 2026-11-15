import gzip
import csv
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from spatial_ml.data.archive_ingest import (
    audit_firms_archives,
    iter_firms_zip_chunks,
)
from spatial_ml.data.cleaning import clean_firms_records
from spatial_ml.pipeline.overlap import generate_observation_ids

_HEADER = [
    "latitude",
    "longitude",
    "brightness",
    "scan",
    "track",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
    "version",
    "bright_t31",
    "frp",
    "daynight",
    "type",
]


def _row(
    *,
    latitude: str = "10.0",
    longitude: str = "20.0",
    brightness: str = "325.5",
    date: str = "2017-11-14",
    time: str = "0005",
    satellite: str = "Terra",
    instrument: str = "MODIS",
    confidence: str = "85",
) -> dict[str, str]:
    return {
        "latitude": latitude,
        "longitude": longitude,
        "brightness": brightness,
        "scan": "1.0",
        "track": "1.0",
        "acq_date": date,
        "acq_time": time,
        "satellite": satellite,
        "instrument": instrument,
        "confidence": confidence,
        "version": "6.03",
        "bright_t31": "300.0",
        "frp": "12.5",
        "daynight": "D",
        "type": "0",
    }


def _write_archive(
    path: Path,
    rows: list[dict[str, str]],
    *,
    member: str = "fire_archive_M-C61.csv",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=_HEADER)
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, text.getvalue())
        archive.writestr("Readme.txt", "Synthetic test fixture.")
    return path


def test_streaming_zip_chunks_normalize_and_keep_source_columns(tmp_path: Path) -> None:
    archive_path = _write_archive(
        tmp_path / "DL_FIRE_M-C61_demo.zip",
        [
            _row(time="5"),
            _row(
                date="2017-11-15",
                time="0042",
                satellite="Aqua",
            ),
            _row(date="2017-11-16", time="2359"),
        ],
    )

    chunks = list(iter_firms_zip_chunks(archive_path, chunksize=2))

    assert [len(chunk) for chunk in chunks] == [2, 1]
    records = pd.concat(chunks, ignore_index=True)
    assert records["sensor"].eq("MODIS").all()
    assert records["time"].tolist() == ["0005", "0042", "2359"]
    assert records["datetime"].iloc[0] == pd.Timestamp("2017-11-14 00:05")
    assert records["satellite"].tolist() == ["Terra", "Aqua", "Terra"]
    assert records["brightness"].tolist() == ["325.5", "325.5", "325.5"]
    assert records["source_archive"].eq(archive_path.name).all()
    assert records["source_file"].eq("fire_archive_M-C61.csv").all()
    assert records["source_row_number"].tolist() == [2, 3, 4]
    assert records["source_record_fingerprint"].str.len().eq(32).all()


def test_viirs_categorical_confidence_is_preserved(tmp_path: Path) -> None:
    archive_path = _write_archive(
        tmp_path / "DL_FIRE_SV-C2_demo.zip",
        [
            _row(
                satellite="SNPP",
                instrument="SNPP",
                confidence="n",
                date="2018-01-01",
                time="0007",
            ),
            _row(
                satellite="SNPP",
                instrument="SNPP",
                confidence="h",
                date="2018-01-01",
                time="0008",
            ),
        ],
        member="fire_archive_SV-C2.csv",
    )

    records = pd.concat(
        iter_firms_zip_chunks(archive_path, chunksize=1), ignore_index=True
    )

    assert records["sensor"].eq("VIIRS").all()
    assert records["satellite"].eq("SNPP").all()
    assert records["instrument"].eq("SNPP").all()
    assert records["confidence"].tolist() == ["n", "h"]


def test_archive_audit_counts_exact_duplicates_not_shared_coordinates(
    tmp_path: Path,
) -> None:
    first = _row()
    distinct_same_time_and_location = _row(brightness="326.0", time="0005")
    first_archive = _write_archive(
        tmp_path / "DL_FIRE_M-C61_first.zip",
        [first, first, distinct_same_time_and_location],
    )
    second_archive = _write_archive(
        tmp_path / "DL_FIRE_M-C61_second.zip",
        [first, _row(brightness="327.0", time="0005")],
    )
    report_path = tmp_path / "audit.json"

    report = audit_firms_archives(
        [first_archive, second_archive],
        chunksize=1,
        report_path=report_path,
    )

    first_result, second_result = report["archives"]
    assert report["total_raw_rows"] == 5
    assert report["total_retained_rows"] == 3
    assert report["total_rejected_rows"] == 2
    assert first_result["duplicate_rows_within_archive"] == 1
    assert first_result["duplicate_rows_from_prior_archives"] == 0
    assert second_result["duplicate_rows_from_prior_archives"] == 1
    assert second_result["retained_rows"] == 1
    assert json.loads(report_path.read_text())["archive_count"] == 2


def test_malformed_fields_are_counted_and_cleaning_retains_valid_record(
    tmp_path: Path,
) -> None:
    archive_path = _write_archive(
        tmp_path / "DL_FIRE_M-C61_invalid.zip",
        [
            _row(time="5", confidence=""),
            _row(latitude="91"),
            _row(date="not-a-date"),
            _row(time="2461"),
        ],
    )

    report = audit_firms_archives([archive_path], chunksize=2)
    result = report["archives"][0]

    assert result["invalid_coordinate_rows"] == 1
    assert result["invalid_date_rows"] == 1
    assert result["invalid_time_rows"] == 1
    assert result["invalid_datetime_rows"] == 2
    assert result["retained_rows"] == 1
    assert result["rejected_rows"] == 3
    assert result["missing_value_counts"]["confidence"] == 1


def test_source_fingerprint_preserves_distinct_same_pixel_observations() -> None:
    records = pd.DataFrame(
        {
            "latitude": [10.0, 10.0],
            "longitude": [20.0, 20.0],
            "datetime": pd.to_datetime(
                ["2017-11-14 00:05", "2017-11-14 00:05"]
            ),
            "sensor": ["MODIS", "MODIS"],
            "satellite": ["Terra", "Terra"],
            "instrument": ["MODIS", "MODIS"],
            "confidence": ["85", "85"],
            "frp": ["12.5", "13.0"],
            "daynight": ["D", "D"],
            "source_record_fingerprint": ["source-row-a", "source-row-b"],
        }
    )

    cleaned = clean_firms_records(records)
    identifiers = generate_observation_ids(cleaned, "MODIS")

    assert len(cleaned) == 2
    assert identifiers.nunique() == 2


def test_existing_audit_report_is_not_overwritten(tmp_path: Path) -> None:
    archive_path = _write_archive(
        tmp_path / "DL_FIRE_M-C61_existing.zip",
        [_row()],
    )
    report_path = tmp_path / "audit.json"
    report_path.write_text("keep this")

    with pytest.raises(FileExistsError, match="already exists"):
        audit_firms_archives(
            [archive_path], report_path=report_path, chunksize=1
        )

    assert report_path.read_text() == "keep this"


def test_optional_processed_intermediate_is_cleaned_and_not_overwritten(
    tmp_path: Path,
) -> None:
    archive_path = _write_archive(
        tmp_path / "DL_FIRE_M-C61_intermediate.zip",
        [_row(), _row(), _row(brightness="326.0")],
    )
    output_dir = tmp_path / "processed"

    report = audit_firms_archives(
        [archive_path],
        chunksize=1,
        processed_output_dir=output_dir,
    )
    output_path = Path(report["archives"][0]["processed_output"])
    with gzip.open(output_path, "rt", encoding="utf-8") as stream:
        processed = pd.read_csv(stream, dtype=str)

    assert len(processed) == 2
    assert processed["sensor"].eq("MODIS").all()
    assert processed["source_archive"].eq(archive_path.name).all()
    with pytest.raises(FileExistsError, match="already exists"):
        audit_firms_archives(
            [archive_path],
            chunksize=1,
            processed_output_dir=output_dir,
        )
