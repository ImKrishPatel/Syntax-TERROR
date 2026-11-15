from datetime import date
import csv
import io
from pathlib import Path
import zipfile

import pandas as pd

from spatial_ml.pipeline.real_data import (
    iter_normalized_days,
    load_training_sample_shards,
    run_real_m3_m4,
)


_REQUIRED_FIELDS = {
    "latitude": 10.0,
    "longitude": 20.0,
    "sensor": "MODIS",
    "satellite": "Terra",
    "instrument": "MODIS",
    "confidence": "85",
    "frp": "12.5",
    "source_file": "source.csv",
}
_FIRMS_HEADER = [
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
    acquisition_date: str,
    archive: str,
    source_row_number: int,
    fingerprint: str,
) -> dict[str, object]:
    return {
        **_REQUIRED_FIELDS,
        "date": acquisition_date,
        "datetime": f"{acquisition_date} 00:05:00",
        "source_archive": archive,
        "source_row_number": source_row_number,
        "source_record_fingerprint": fingerprint,
    }


def test_daily_reader_merges_archive_boundary_and_only_removes_same_fingerprint(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv.gz"
    second = tmp_path / "second.csv.gz"
    pd.DataFrame(
        [
            _row(
                acquisition_date="2017-11-14",
                archive="first.zip",
                source_row_number=2,
                fingerprint="repeated-row",
            ),
            _row(
                acquisition_date="2017-11-14",
                archive="first.zip",
                source_row_number=3,
                fingerprint="distinct-row",
            ),
        ]
    ).to_csv(first, index=False, compression="gzip")
    pd.DataFrame(
        [
            _row(
                acquisition_date="2017-11-14",
                archive="second.zip",
                source_row_number=2,
                fingerprint="repeated-row",
            ),
            _row(
                acquisition_date="2017-11-15",
                archive="second.zip",
                source_row_number=3,
                fingerprint="next-day",
            ),
        ]
    ).to_csv(second, index=False, compression="gzip")

    days = list(iter_normalized_days((first, second), "MODIS", chunksize=1))

    assert [day for day, _ in days] == [date(2017, 11, 14), date(2017, 11, 15)]
    boundary = days[0][1]
    assert boundary["source_record_fingerprint"].tolist() == [
        "repeated-row",
        "distinct-row",
    ]
    assert boundary.loc[0, "source_archive"] == "first.zip"
    assert len(days[1][1]) == 1


def _write_firms_zip(path: Path, row: dict[str, str], member: str) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_FIRMS_HEADER)
    writer.writeheader()
    writer.writerow(row)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, buffer.getvalue())
        archive.writestr("Readme.txt", "Synthetic integration fixture.")


def test_real_pipeline_streams_ten_archives_to_m3_and_m4_partitions(
    tmp_path: Path,
) -> None:
    modis_row = {
        "latitude": "10",
        "longitude": "20",
        "brightness": "325",
        "scan": "1",
        "track": "1",
        "acq_date": "2017-11-14",
        "acq_time": "0005",
        "satellite": "Terra",
        "instrument": "MODIS",
        "confidence": "85",
        "version": "6.03",
        "bright_t31": "300",
        "frp": "12.5",
        "daynight": "D",
        "type": "0",
    }
    viirs_row = {
        **modis_row,
        "latitude": "10.0001",
        "satellite": "SNPP",
        "instrument": "VIIRS",
        "confidence": "h",
        "version": "2.0",
    }
    archives = []
    for code in ("849514", "849541", "849531", "849545", "849584"):
        path = tmp_path / f"DL_FIRE_M-C61_{code}.zip"
        _write_firms_zip(
            path, modis_row, f"fire_archive_M-C61_{code}.csv"
        )
        archives.append(path)
        code = {
            "849514": "849515",
            "849541": "849542",
            "849531": "849532",
            "849545": "849546",
            "849584": "849585",
        }[code]
        viirs_path = tmp_path / f"DL_FIRE_SV-C2_{code}.zip"
        _write_firms_zip(
            viirs_path, viirs_row, f"fire_archive_SV-C2_{code}.csv"
        )
        archives.append(viirs_path)

    manifest = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )

    assert manifest["status"] == "complete"
    assert manifest["summary"]["modis_observation_count"] == 1
    assert manifest["summary"]["viirs_observation_count"] == 1
    assert manifest["summary"]["matched_modis_count"] == 1
    assert manifest["summary"]["candidate_relationship_count"] == 1
    assert manifest["summary"]["m4_sample_count"] == 1
    assert manifest["summary"]["m4_target_cell_count"] == 9
    assert manifest["summary"]["m4_positive_cell_count"] == 1
    month = tmp_path / "overlap" / "2017-11"
    pairs = pd.read_csv(month / "matched_pairs.csv.gz")
    candidates = pd.read_csv(month / "candidate_relationships.csv.gz")
    assert pairs.loc[0, "modis_source_archive"] == "DL_FIRE_M-C61_849514.zip"
    assert pairs.loc[0, "viirs_source_archive"] == "DL_FIRE_SV-C2_849515.zip"
    assert len(candidates) == 1
    sample = next(load_training_sample_shards([month / "training_samples.npz"]))
    assert sample.target_grid.sum() == 1
    assert sample.metadata["modis_source_archive"] == "DL_FIRE_M-C61_849514.zip"

    resumed = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )
    assert resumed["summary"]["m4_sample_count"] == 1
