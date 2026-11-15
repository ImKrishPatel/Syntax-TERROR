from datetime import date
import json
import csv
import io
from pathlib import Path
import zipfile

import pandas as pd

from spatial_ml.pipeline.full_run import _lock_run_folder, run_all_years
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


_MODIS_ROW = {
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
_VIIRS_ROW = {
    **_MODIS_ROW,
    "latitude": "10.0001",
    "satellite": "SNPP",
    "instrument": "VIIRS",
    "confidence": "h",
    "version": "2.0",
}


def _modis_zip(folder: Path, code: str, acquisition_date: str) -> Path:
    path = folder / f"DL_FIRE_M-C61_{code}.zip"
    _write_firms_zip(
        path,
        {**_MODIS_ROW, "acq_date": acquisition_date},
        f"fire_archive_M-C61_{code}.csv",
    )
    return path


def _viirs_zip(folder: Path, code: str, acquisition_date: str) -> Path:
    path = folder / f"DL_FIRE_SV-C2_{code}.zip"
    _write_firms_zip(
        path,
        {**_VIIRS_ROW, "acq_date": acquisition_date},
        f"fire_archive_SV-C2_{code}.csv",
    )
    return path


def test_real_pipeline_streams_yearly_archives_to_m3_and_m4_partitions(
    tmp_path: Path,
) -> None:
    # Request numbers are deliberately out of order: the pipeline has to sort
    # the archives by the dates inside them.
    years = {
        "2017-11-14": ("900500", "900501"),
        "2018-11-14": ("900300", "900301"),
        "2019-11-14": ("900400", "900401"),
        "2020-11-14": ("900100", "900101"),
        "2021-11-14": ("900200", "900201"),
    }
    archives = []
    for acquisition_date, (modis_code, viirs_code) in reversed(years.items()):
        archives.append(_modis_zip(tmp_path, modis_code, acquisition_date))
        archives.append(_viirs_zip(tmp_path, viirs_code, acquisition_date))

    manifest = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )

    assert manifest["status"] == "complete"
    assert manifest["completed_months"] == [
        "2017-11",
        "2018-11",
        "2019-11",
        "2020-11",
        "2021-11",
    ]
    assert manifest["summary"]["modis_observation_count"] == 5
    assert manifest["summary"]["viirs_observation_count"] == 5
    assert manifest["summary"]["matched_modis_count"] == 5
    assert manifest["summary"]["candidate_relationship_count"] == 5
    assert manifest["summary"]["m4_sample_count"] == 5
    assert manifest["summary"]["m4_target_cell_count"] == 45
    assert manifest["summary"]["m4_positive_cell_count"] == 5
    assert manifest["unpaired_days"]["modis_only_count"] == 0
    assert manifest["unpaired_days"]["viirs_only_count"] == 0
    month = tmp_path / "overlap" / "2017-11"
    pairs = pd.read_csv(month / "matched_pairs.csv.gz")
    candidates = pd.read_csv(month / "candidate_relationships.csv.gz")
    assert pairs.loc[0, "modis_source_archive"] == "DL_FIRE_M-C61_900500.zip"
    assert pairs.loc[0, "viirs_source_archive"] == "DL_FIRE_SV-C2_900501.zip"
    assert len(candidates) == 1
    sample = next(load_training_sample_shards([month / "training_samples.npz"]))
    assert sample.target_grid.sum() == 1
    assert sample.metadata["modis_source_archive"] == "DL_FIRE_M-C61_900500.zip"

    resumed = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )
    assert resumed["summary"]["m4_sample_count"] == 5


def test_archive_folder_is_expanded_and_unsupported_sensors_are_skipped(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    (raw / "modis").mkdir(parents=True)
    (raw / "viirs").mkdir()
    _modis_zip(raw / "modis", "900001", "2017-11-14")
    _viirs_zip(raw / "viirs", "900002", "2017-11-14")
    noaa20 = raw / "viirs" / "DL_FIRE_J1V-C2_900003.zip"
    _write_firms_zip(noaa20, _VIIRS_ROW, "fire_archive_J1V-C2_900003.csv")

    manifest = run_real_m3_m4(
        [raw],
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )

    used = [item["name"] for item in manifest["archive_signature"]]
    assert used == ["DL_FIRE_M-C61_900001.zip", "DL_FIRE_SV-C2_900002.zip"]
    assert manifest["summary"]["m4_sample_count"] == 1


def test_modis_before_viirs_and_single_sensor_days_are_not_matched(
    tmp_path: Path,
) -> None:
    archives = [
        # MODIS-only years: nothing to match, so these are left out.
        _modis_zip(tmp_path, "900010", "2010-11-14"),
        _modis_zip(tmp_path, "900011", "2011-11-14"),
        _modis_zip(tmp_path, "900012", "2012-11-14"),
        _viirs_zip(tmp_path, "900020", "2012-11-14"),
        # A year where VIIRS was not recording on the MODIS date.
        _modis_zip(tmp_path, "900013", "2013-11-14"),
        _viirs_zip(tmp_path, "900021", "2013-11-20"),
    ]

    manifest = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )

    used = [item["name"] for item in manifest["archive_signature"]]
    assert "DL_FIRE_M-C61_900010.zip" not in used
    assert "DL_FIRE_M-C61_900011.zip" not in used
    assert "DL_FIRE_M-C61_900012.zip" in used
    assert manifest["completed_months"] == ["2012-11"]
    assert manifest["summary"]["m4_sample_count"] == 1
    assert manifest["unpaired_days"]["modis_only"] == ["2013-11-14"]
    assert manifest["unpaired_days"]["viirs_only"] == ["2013-11-20"]


def test_near_real_time_member_is_ignored(tmp_path: Path) -> None:
    modis = _modis_zip(tmp_path, "900030", "2025-11-14")
    viirs = _viirs_zip(tmp_path, "900031", "2025-11-14")
    nrt_header = [name for name in _FIRMS_HEADER if name != "type"]
    for path, row, sensor_tag in (
        (modis, _MODIS_ROW, "M-C61"),
        (viirs, _VIIRS_ROW, "SV-C2"),
    ):
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=nrt_header)
        writer.writeheader()
        nrt_row = {key: value for key, value in row.items() if key != "type"}
        writer.writerow({**nrt_row, "acq_date": "2026-09-01"})
        code = path.stem.rsplit("_", 1)[1]
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(f"fire_nrt_{sensor_tag}_{code}.csv", buffer.getvalue())

    manifest = run_real_m3_m4(
        [modis, viirs],
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap",
        chunksize=2,
    )

    assert manifest["completed_months"] == ["2025-11"]
    assert manifest["summary"]["modis_observation_count"] == 1
    assert manifest["summary"]["viirs_observation_count"] == 1


def test_single_year_run_only_matches_that_calendar_year(tmp_path: Path) -> None:
    archives = [
        _modis_zip(tmp_path, "900040", "2017-11-14"),
        _viirs_zip(tmp_path, "900041", "2017-11-14"),
        _modis_zip(tmp_path, "900042", "2018-11-14"),
        _viirs_zip(tmp_path, "900043", "2018-11-14"),
    ]

    manifest = run_real_m3_m4(
        archives,
        processed_dir=tmp_path / "processed",
        output_dir=tmp_path / "overlap" / "year_2018",
        chunksize=2,
        year=2018,
    )

    assert manifest["completed_months"] == ["2018-11"]
    assert manifest["configuration"]["year"] == 2018
    assert manifest["summary"]["m4_sample_count"] == 1


def test_all_years_runner_writes_one_folder_per_year(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    _modis_zip(raw, "900050", "2017-11-14")
    _viirs_zip(raw, "900051", "2017-11-14")
    _modis_zip(raw, "900052", "2018-11-14")
    _viirs_zip(raw, "900053", "2018-11-14")
    overlap = tmp_path / "overlap"

    failed = run_all_years(
        [raw], processed_dir=tmp_path / "processed", output_dir=overlap, workers=2
    )

    assert failed == []
    shards = sorted(overlap.glob("*/????-??/training_samples.npz"))
    assert [path.parent.name for path in shards] == ["2017-11", "2018-11"]
    assert (overlap / "year_2019" / "processing_manifest.json").is_file()
    assert (overlap / "logs" / "year_2017.log").is_file()

    # Starting it again must not redo or break finished years.
    assert run_all_years(
        [raw], processed_dir=tmp_path / "processed", output_dir=overlap, workers=2
    ) == []


def test_second_copy_of_the_full_run_is_refused(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    _modis_zip(raw, "900060", "2017-11-14")
    _viirs_zip(raw, "900061", "2017-11-14")
    overlap = tmp_path / "overlap"
    holder = _lock_run_folder(overlap)
    assert holder is not None
    try:
        try:
            run_all_years([raw], processed_dir=tmp_path / "processed", output_dir=overlap)
        except RuntimeError as error:
            assert "already running" in str(error)
        else:
            raise AssertionError("a second copy was allowed to run")
    finally:
        holder.close()
    # Once the first copy is gone, the folder can be used again.
    assert run_all_years(
        [raw], processed_dir=tmp_path / "processed", output_dir=overlap
    ) == []


def test_unrecorded_month_folder_is_set_aside_and_redone(tmp_path: Path) -> None:
    archives = [
        _modis_zip(tmp_path, "900070", "2017-11-14"),
        _viirs_zip(tmp_path, "900071", "2017-11-14"),
    ]
    overlap = tmp_path / "overlap"
    run_real_m3_m4(archives, processed_dir=tmp_path / "processed", output_dir=overlap)
    # Pretend the run was stopped after saving the month but before
    # recording it as finished.
    manifest_path = overlap / "processing_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["completed_months"] = []
    manifest["month_outputs"] = {}
    manifest["month_summaries"] = {}
    manifest["status"] = "in_progress"
    manifest_path.write_text(json.dumps(manifest))

    resumed = run_real_m3_m4(
        archives, processed_dir=tmp_path / "processed", output_dir=overlap
    )

    assert resumed["completed_months"] == ["2017-11"]
    assert resumed["summary"]["m4_sample_count"] == 1
    assert len(list(overlap.glob(".unfinished-2017-11-*"))) == 1
