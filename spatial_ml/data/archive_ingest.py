"""Memory-bounded FIRMS ZIP ingestion and archive-quality auditing."""

import argparse
import gzip
import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from spatial_ml.data.cleaning import clean_firms_records
from spatial_ml.data.firms import normalize_firms_schema

_REQUIRED_SOURCE_COLUMNS = (
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
)
_EXPECTED_PERIODS: Dict[str, Tuple[str, str, str]] = {
    "849514": ("MODIS", "2016-11-14", "2017-11-14"),
    "849541": ("MODIS", "2017-11-14", "2018-11-14"),
    "849531": ("MODIS", "2018-11-14", "2019-11-14"),
    "849545": ("MODIS", "2019-11-14", "2020-11-14"),
    "849584": ("MODIS", "2020-11-14", "2021-11-14"),
    "849515": ("VIIRS", "2016-11-14", "2017-11-14"),
    "849542": ("VIIRS", "2017-11-14", "2018-11-14"),
    "849532": ("VIIRS", "2018-11-14", "2019-11-14"),
    "849546": ("VIIRS", "2019-11-14", "2020-11-14"),
    "849585": ("VIIRS", "2020-11-14", "2021-11-14"),
}
_ALIASES = {
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "long"),
    "date": ("date", "acq_date", "acquisition_date"),
    "time": ("time", "acq_time", "acquisition_time"),
    "satellite": ("satellite", "sat", "platform"),
    "instrument": ("instrument", "sensor_instrument"),
    "confidence": ("confidence", "conf"),
    "frp": ("frp", "fire_radiative_power"),
}


def _infer_sensor(archive_path: Path, csv_member: str) -> str:
    identity = f"{archive_path.name} {csv_member}".upper()
    if "SV-C2" in identity:
        return "VIIRS"
    if "M-C61" in identity:
        return "MODIS"
    raise ValueError(
        f"Cannot infer MODIS/VIIRS from archive name or CSV member: "
        f"{archive_path.name!r}, {csv_member!r}"
    )


def _csv_member(archive_path: Path) -> str:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                member
                for member in archive.namelist()
                if member.lower().endswith(".csv")
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Cannot open FIRMS ZIP archive {archive_path}: {exc}") from exc
    if len(members) > 1:
        # The newest FIRMS requests ship two CSVs: the standard-quality
        # "fire_archive" file and a "fire_nrt" file with the last few months
        # of near-real-time detections. Only the standard file is used.
        members = [
            member
            for member in members
            if not Path(member).name.lower().startswith("fire_nrt_")
        ]
    if len(members) != 1:
        raise ValueError(
            f"Expected exactly one standard CSV in {archive_path.name}; "
            f"found {len(members)}."
        )
    return members[0]


def _read_source_header(archive_path: Path, member: str) -> List[str]:
    with zipfile.ZipFile(archive_path) as archive, archive.open(member) as stream:
        return [str(column) for column in pd.read_csv(stream, nrows=0).columns]


def _record_fingerprints(raw: pd.DataFrame) -> pd.Series:
    forward = pd.util.hash_pandas_object(raw, index=False).to_numpy(
        dtype=np.uint64
    )
    reverse = pd.util.hash_pandas_object(
        raw.iloc[:, ::-1], index=False
    ).to_numpy(dtype=np.uint64)
    return pd.Series(
        [f"{left:016x}{right:016x}" for left, right in zip(forward, reverse)],
        index=raw.index,
        dtype="string",
    )


def iter_firms_zip_chunks(
    archive_path: str | Path,
    sensor: Optional[str] = None,
    *,
    chunksize: int = 100_000,
) -> Iterator[pd.DataFrame]:
    """Yield normalized source-preserving chunks from a CSV inside one ZIP.

    The ZIP is opened read-only and its CSV member is streamed; no archive
    member is extracted. Returned chunks retain unknown NASA source columns.
    """
    path = Path(archive_path)
    if not path.is_file():
        raise FileNotFoundError(f"FIRMS ZIP archive does not exist: {path}")
    if isinstance(chunksize, bool) or chunksize < 1:
        raise ValueError("chunksize must be a positive integer.")
    member = _csv_member(path)
    normalized_sensor = (
        sensor.strip().upper() if sensor is not None else _infer_sensor(path, member)
    )
    if normalized_sensor not in {"MODIS", "VIIRS"}:
        raise ValueError("sensor must be MODIS or VIIRS.")

    try:
        with zipfile.ZipFile(path) as archive, archive.open(member) as csv_stream:
            reader = pd.read_csv(
                csv_stream,
                dtype=str,
                chunksize=chunksize,
                on_bad_lines="error",
            )
            source_row_number = 2
            for raw in reader:
                fingerprints = _record_fingerprints(raw)
                normalized = normalize_firms_schema(raw, normalized_sensor)
                row_count = len(normalized)
                normalized["source_archive"] = path.name
                normalized["source_file"] = member
                normalized["source_row_number"] = np.arange(
                    source_row_number, source_row_number + row_count, dtype=np.int64
                )
                normalized["source_record_fingerprint"] = fingerprints
                source_row_number += row_count
                yield normalized
    except (pd.errors.EmptyDataError, pd.errors.ParserError, zipfile.BadZipFile) as exc:
        raise ValueError(
            f"Could not stream FIRMS CSV from {path.name}: {exc}"
        ) from exc


def _find_source_column(columns: Sequence[str], canonical_name: str) -> Optional[str]:
    by_name = {str(column).strip().lower(): column for column in columns}
    aliases = _ALIASES.get(canonical_name, (canonical_name,))
    return next((by_name[name] for name in aliases if name in by_name), None)


def _missing_mask(values: pd.Series) -> pd.Series:
    return values.isna() | values.eq("").fillna(False)


def audit_firms_archives(
    archive_paths: Sequence[str | Path],
    *,
    chunksize: int = 100_000,
    report_path: Optional[str | Path] = None,
    processed_output_dir: Optional[str | Path] = None,
    overwrite_report: bool = False,
    progress_callback: Optional[Callable[[Mapping[str, object]], None]] = None,
) -> Dict[str, object]:
    """Audit multiple FIRMS ZIP archives without collecting all rows in RAM."""
    if not archive_paths:
        raise ValueError("Provide at least one FIRMS ZIP archive.")
    paths = [Path(path) for path in archive_paths]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"FIRMS ZIP archive does not exist: {path}")
    destination = Path(report_path) if report_path is not None else None
    if destination is not None and destination.exists() and not overwrite_report:
        raise FileExistsError(
            f"Audit report already exists; choose another path or pass "
            f"overwrite_report=True: {destination}"
        )
    processed_dir = (
        Path(processed_output_dir) if processed_output_dir is not None else None
    )
    processed_targets: Dict[Path, Tuple[Path, str, str]] = {}
    if processed_dir is not None:
        for path in paths:
            member = _csv_member(path)
            sensor = _infer_sensor(path, member)
            target = (
                processed_dir
                / sensor.lower()
                / f"{path.stem}.normalized.csv.gz"
            )
            if target in processed_targets:
                raise ValueError(f"Multiple archives map to the same output: {target}")
            if target.exists():
                raise FileExistsError(
                    f"Processed output already exists; choose a new output directory "
                    f"or remove it after review: {target}"
                )
            processed_targets[target] = (path, member, sensor)

    archive_reports: List[Dict[str, object]] = []
    processed_temp_dir = None
    staged_outputs: Dict[Path, Path] = {}
    if processed_dir is not None:
        processed_dir.parent.mkdir(parents=True, exist_ok=True)
        processed_temp_dir = tempfile.TemporaryDirectory(
            prefix=f".{processed_dir.name}.staging-",
            dir=processed_dir.parent,
        )
        staged_root = Path(processed_temp_dir.name)
        staged_outputs = {
            target: staged_root / target.relative_to(processed_dir)
            for target in processed_targets
        }

    previous_boundaries: Dict[str, Tuple[str, set[str]]] = {}
    try:
        for path in paths:
            member = _csv_member(path)
            sensor = _infer_sensor(path, member)
            archive_code_match = re.search(r"_(\d{6})\.zip$", path.name)
            archive_code = (
                archive_code_match.group(1) if archive_code_match else None
            )
            expected = _EXPECTED_PERIODS.get(archive_code or "")
            if expected and expected[0] != sensor:
                raise ValueError(
                    f"Archive {path.name} is labeled {sensor}, but its known "
                    f"archive code is expected to be {expected[0]}."
                )

            source_header = _read_source_header(path, member)
            source_columns = {
                name: _find_source_column(source_header, name)
                for name in (
                    "satellite",
                    "instrument",
                    "confidence",
                )
            }
            prior_boundary = previous_boundaries.get(sensor)
            archive_report: Dict[str, object] = {
                "archive": path.name,
                "sensor": sensor,
                "csv_member": member,
                "archive_size_bytes": path.stat().st_size,
                "raw_rows": 0,
                "retained_rows": 0,
                "rejected_rows": 0,
                "invalid_coordinate_rows": 0,
                "invalid_date_rows": 0,
                "invalid_time_rows": 0,
                "invalid_datetime_rows": 0,
                "invalid_frp_rows": 0,
                "invalid_confidence_rows": 0,
                "duplicate_rows_within_archive": 0,
                "duplicate_rows_from_prior_archives": 0,
                "missing_value_counts": {},
                "satellite_distribution": {},
                "instrument_distribution": {},
                "confidence_distribution": {},
                "distinct_acquisition_dates": 0,
                "earliest_acquisition_date": None,
                "latest_acquisition_date": None,
                "latitude_range": None,
                "longitude_range": None,
                "latitude_hemisphere_counts": {"north": 0, "south": 0},
                "longitude_hemisphere_counts": {"east": 0, "west": 0},
                "normalized_schema_available": True,
                "acquisition_dates_monotonic": True,
            }
            target_output = next(
                (
                    target
                    for target, (source_path, _, _) in processed_targets.items()
                    if source_path == path
                ),
                None,
            )
            staged_output = (
                staged_outputs[target_output] if target_output is not None else None
            )
            if staged_output is not None:
                staged_output.parent.mkdir(parents=True, exist_ok=True)
                archive_report["processed_output"] = str(target_output)

            missing_counts: Counter[str] = Counter()
            satellites: Counter[str] = Counter()
            instruments: Counter[str] = Counter()
            confidence_values: Counter[str] = Counter()
            dates_seen: set[str] = set()
            latitude_min = latitude_max = None
            longitude_min = longitude_max = None
            row_count = 0
            active_date: Optional[str] = None
            active_seen: set[str] = set()
            active_archive_seen: set[str] = set()
            active_prior_archive: set[str] = set()
            cross_archive_counted: set[str] = set()
            last_seen_date: Optional[str] = None
            output_stream = (
                gzip.open(
                    staged_output,
                    "wt",
                    encoding="utf-8",
                    newline="",
                    compresslevel=1,
                )
                if staged_output is not None
                else None
            )
            output_header_written = False
            try:
                for chunk in iter_firms_zip_chunks(
                    path, sensor, chunksize=chunksize
                ):
                    if "raw_source_columns" not in archive_report:
                        archive_report["raw_source_columns"] = source_header
                        normalized_header = {
                            str(name).strip().lower() for name in source_header
                        }
                        archive_report["missing_source_columns"] = [
                            column
                            for column in _REQUIRED_SOURCE_COLUMNS
                            if column.lower() not in normalized_header
                        ]

                    raw_rows = len(chunk)
                    row_count += raw_rows
                    archive_report["raw_rows"] = row_count
                    for column in source_header:
                        missing_counts[column] += int(
                            _missing_mask(chunk[column]).sum()
                        )

                    for canonical, counter in (
                        ("satellite", satellites),
                        ("instrument", instruments),
                        ("confidence", confidence_values),
                    ):
                        column = source_columns[canonical]
                        if column is not None:
                            values = (
                                chunk[column].dropna().astype(str).str.strip()
                            )
                            counter.update(values.value_counts().to_dict())

                    latitude = pd.to_numeric(chunk["latitude"], errors="coerce")
                    longitude = pd.to_numeric(chunk["longitude"], errors="coerce")
                    valid_coordinates = (
                        np.isfinite(latitude)
                        & np.isfinite(longitude)
                        & latitude.between(-90, 90)
                        & longitude.between(-180, 180)
                    )
                    archive_report["invalid_coordinate_rows"] = int(
                        archive_report["invalid_coordinate_rows"]
                    ) + int((~valid_coordinates).sum())

                    archive_report["invalid_date_rows"] = int(
                        archive_report["invalid_date_rows"]
                    ) + int(chunk["date"].isna().sum())
                    archive_report["invalid_time_rows"] = int(
                        archive_report["invalid_time_rows"]
                    ) + int(chunk["time"].isna().sum())
                    archive_report["invalid_datetime_rows"] = int(
                        archive_report["invalid_datetime_rows"]
                    ) + int(chunk["datetime"].isna().sum())

                    valid_dates = chunk["date"].dropna().dt.strftime("%Y-%m-%d")
                    unique_dates = valid_dates.unique()
                    dates_seen.update(unique_dates.tolist())
                    if len(unique_dates):
                        earliest = unique_dates.min()
                        latest = unique_dates.max()
                        previous_earliest = archive_report[
                            "earliest_acquisition_date"
                        ]
                        previous_latest = archive_report[
                            "latest_acquisition_date"
                        ]
                        if previous_earliest is None or earliest < previous_earliest:
                            archive_report["earliest_acquisition_date"] = earliest
                        if previous_latest is None or latest > previous_latest:
                            archive_report["latest_acquisition_date"] = latest

                    valid_latitudes = latitude.loc[valid_coordinates]
                    valid_longitudes = longitude.loc[valid_coordinates]
                    if not valid_latitudes.empty:
                        chunk_lat_min = float(valid_latitudes.min())
                        chunk_lat_max = float(valid_latitudes.max())
                        chunk_lon_min = float(valid_longitudes.min())
                        chunk_lon_max = float(valid_longitudes.max())
                        latitude_min = (
                            chunk_lat_min
                            if latitude_min is None
                            else min(latitude_min, chunk_lat_min)
                        )
                        latitude_max = (
                            chunk_lat_max
                            if latitude_max is None
                            else max(latitude_max, chunk_lat_max)
                        )
                        longitude_min = (
                            chunk_lon_min
                            if longitude_min is None
                            else min(longitude_min, chunk_lon_min)
                        )
                        longitude_max = (
                            chunk_lon_max
                            if longitude_max is None
                            else max(longitude_max, chunk_lon_max)
                        )
                        archive_report["latitude_hemisphere_counts"]["north"] += int(
                            valid_latitudes.ge(0).sum()
                        )
                        archive_report["latitude_hemisphere_counts"]["south"] += int(
                            valid_latitudes.lt(0).sum()
                        )
                        archive_report["longitude_hemisphere_counts"]["east"] += int(
                            valid_longitudes.ge(0).sum()
                        )
                        archive_report["longitude_hemisphere_counts"]["west"] += int(
                            valid_longitudes.lt(0).sum()
                        )

                    numeric_frp = pd.to_numeric(chunk["frp"], errors="coerce")
                    missing_frp = _missing_mask(chunk["frp"])
                    valid_frp = (
                        numeric_frp.notna()
                        & np.isfinite(numeric_frp)
                        & numeric_frp.ge(0)
                    )
                    archive_report["invalid_frp_rows"] = int(
                        archive_report["invalid_frp_rows"]
                    ) + int((~missing_frp & ~valid_frp).sum())

                    confidence_text = (
                        chunk["confidence"]
                        .astype("string")
                        .str.strip()
                        .str.lower()
                    )
                    categorical = confidence_text.isin(
                        ["l", "n", "h", "low", "nominal", "high"]
                    )
                    numeric_confidence = pd.to_numeric(
                        chunk["confidence"], errors="coerce"
                    )
                    confidence_missing = _missing_mask(chunk["confidence"])
                    valid_confidence = categorical | (
                        numeric_confidence.notna()
                        & np.isfinite(numeric_confidence)
                        & numeric_confidence.between(0, 100)
                    )
                    archive_report["invalid_confidence_rows"] = int(
                        archive_report["invalid_confidence_rows"]
                    ) + int((~confidence_missing & ~valid_confidence).sum())

                    cleaned = clean_firms_records(chunk)
                    cleaned_by_date = {
                        str(day): day_records
                        for day, day_records in cleaned.groupby(
                            cleaned["date"].dt.strftime("%Y-%m-%d"),
                            sort=False,
                        )
                    }
                    archive_report["normalized_schema_available"] = bool(
                        archive_report["normalized_schema_available"]
                        and {
                            "latitude",
                            "longitude",
                            "date",
                            "time",
                            "datetime",
                            "sensor",
                            "satellite",
                            "instrument",
                            "confidence",
                            "frp",
                            "daynight",
                        }.issubset(cleaned.columns)
                    )

                    valid_for_duplicates = chunk.loc[
                        valid_coordinates & chunk["datetime"].notna()
                    ].copy()
                    valid_for_duplicates["_audit_date"] = (
                        valid_for_duplicates["date"].dt.strftime("%Y-%m-%d")
                    )
                    dates_in_chunk = valid_for_duplicates["_audit_date"]
                    if not dates_in_chunk.is_monotonic_increasing or (
                        last_seen_date is not None
                        and not dates_in_chunk.empty
                        and dates_in_chunk.iloc[0] < last_seen_date
                    ):
                        archive_report["acquisition_dates_monotonic"] = False
                        raise ValueError(
                            f"{path.name} is not ordered by acquisition date. "
                            "Use a date-sorted source before bounded duplicate "
                            "auditing; no report or processed files were written."
                        )

                    for day, day_group in valid_for_duplicates.groupby(
                        "_audit_date", sort=False
                    ):
                        if day != active_date:
                            active_date = str(day)
                            active_archive_seen = set()
                            active_prior_archive = (
                                set(prior_boundary[1])
                                if prior_boundary is not None
                                and prior_boundary[0] == active_date
                                else set()
                            )
                            active_seen = set(active_prior_archive)
                            cross_archive_counted = set()

                        fingerprint_counts = day_group[
                            "source_record_fingerprint"
                        ].value_counts()
                        unique_fingerprints = set(
                            fingerprint_counts.index.astype(str)
                        )
                        archive_report["duplicate_rows_within_archive"] = int(
                            archive_report["duplicate_rows_within_archive"]
                        ) + len(day_group) - len(unique_fingerprints)
                        cross_matches = (
                            unique_fingerprints
                            & active_prior_archive
                            - cross_archive_counted
                        )
                        same_archive_matches = (
                            unique_fingerprints & active_archive_seen
                        )
                        archive_report["duplicate_rows_within_archive"] = int(
                            archive_report["duplicate_rows_within_archive"]
                        ) + len(same_archive_matches)
                        archive_report["duplicate_rows_from_prior_archives"] = int(
                            archive_report["duplicate_rows_from_prior_archives"]
                        ) + len(cross_matches)

                        cleaned_day = cleaned_by_date.get(
                            str(day), cleaned.iloc[0:0]
                        )
                        cleaned_fingerprints = cleaned_day[
                            "source_record_fingerprint"
                        ].astype(str)
                        prior_duplicate_mask = cleaned_fingerprints.isin(active_seen)
                        archive_report["retained_rows"] = int(
                            archive_report["retained_rows"]
                        ) + int((~prior_duplicate_mask).sum())
                        if output_stream is not None:
                            cleaned_day.loc[~prior_duplicate_mask].to_csv(
                                output_stream,
                                index=False,
                                header=not output_header_written,
                            )
                            output_header_written = True

                        active_archive_seen.update(unique_fingerprints)
                        active_seen.update(unique_fingerprints)
                        cross_archive_counted.update(cross_matches)
                        last_seen_date = active_date

                if row_count == 0:
                    raise ValueError(f"No data rows found in {path.name}.")
                if output_stream is not None and not output_header_written:
                    cleaned.iloc[0:0].to_csv(
                        output_stream, index=False, header=True
                    )
            finally:
                if output_stream is not None:
                    output_stream.close()

            archive_report["distinct_acquisition_dates"] = len(dates_seen)
            archive_report["missing_value_counts"] = dict(missing_counts)
            archive_report["satellite_distribution"] = dict(satellites)
            archive_report["instrument_distribution"] = dict(instruments)
            archive_report["confidence_distribution"] = dict(confidence_values)
            archive_report["duplicate_rows"] = (
                int(archive_report["duplicate_rows_within_archive"])
                + int(archive_report["duplicate_rows_from_prior_archives"])
            )
            archive_report["rejected_rows"] = (
                row_count - int(archive_report["retained_rows"])
            )
            archive_report["latitude_range"] = (
                [latitude_min, latitude_max] if latitude_min is not None else None
            )
            archive_report["longitude_range"] = (
                [longitude_min, longitude_max]
                if longitude_min is not None
                else None
            )
            archive_report["duplicate_fingerprint"] = (
                "128-bit paired pandas row hashes over all original source columns"
            )
            if expected:
                _, expected_start, expected_end = expected
                archive_report["expected_period"] = [expected_start, expected_end]
                archive_report["expected_date_coverage_matches"] = (
                    archive_report["earliest_acquisition_date"] == expected_start
                    and archive_report["latest_acquisition_date"] == expected_end
                    and archive_report["distinct_acquisition_dates"] in (366, 367)
                )
            archive_reports.append(archive_report)
            if active_date is not None:
                previous_boundaries[sensor] = (
                    active_date,
                    set(active_seen),
                )
            if progress_callback is not None:
                progress_callback(archive_report)

        if processed_dir is not None:
            processed_dir.mkdir(parents=True, exist_ok=True)
            for target, staged in staged_outputs.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(
                        f"Processed output appeared during ingestion; refusing "
                        f"to overwrite it: {target}"
                    )
                os.replace(staged, target)

        result: Dict[str, object] = {
            "report_schema_version": "1.0.0",
            "audit_method": (
                "ZIP members streamed in pandas chunks; source rows normalized "
                "and passed through the M2 cleaner; exact duplicate fingerprints "
                "are tracked for one sorted acquisition date at a time and "
                "across matching archive boundary dates."
            ),
            "chunk_size": chunksize,
            "archive_count": len(archive_reports),
            "total_raw_rows": sum(
                int(item["raw_rows"]) for item in archive_reports
            ),
            "total_retained_rows": sum(
                int(item["retained_rows"]) for item in archive_reports
            ),
            "total_rejected_rows": sum(
                int(item["rejected_rows"]) for item in archive_reports
            ),
            "team_requested_end_date": "2026-07-31",
            "latest_available_acquisition_date": max(
                (
                    str(item["latest_acquisition_date"])
                    for item in archive_reports
                    if item["latest_acquisition_date"] is not None
                ),
                default=None,
            ),
            "archives": archive_reports,
        }

        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(result, stream, indent=2, sort_keys=True)
                stream.write("\n")
            try:
                if destination.exists() and not overwrite_report:
                    raise FileExistsError(
                        f"Audit report already exists: {destination}"
                    )
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
        return result
    finally:
        if processed_temp_dir is not None:
            processed_temp_dir.cleanup()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stream FIRMS CSVs directly from ZIP archives and write an audit "
            "report. No archive is extracted or modified."
        )
    )
    parser.add_argument(
        "--archives",
        nargs="+",
        type=Path,
        required=True,
        help="MODIS M-C61 and/or SNPP VIIRS SV-C2 ZIP archive paths",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=100_000, help="Rows per memory-bounded chunk"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("results/firms_archive_audit.json"),
        help="New JSON report path; an existing report is not overwritten",
    )
    parser.add_argument(
        "--processed-output-dir",
        type=Path,
        help=(
            "Optional directory for per-archive cleaned normalized CSV.GZ "
            "intermediates; existing files are never overwritten"
        ),
    )
    parser.add_argument(
        "--overwrite-report",
        action="store_true",
        help="Replace the named JSON report if it already exists",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _build_parser().parse_args(argv)
    try:
        report = audit_firms_archives(
            arguments.archives,
            chunksize=arguments.chunk_size,
            report_path=arguments.report,
            processed_output_dir=arguments.processed_output_dir,
            overwrite_report=arguments.overwrite_report,
            progress_callback=lambda item: print(
                f"{item['archive']}: {item['raw_rows']:,} rows; "
                f"{item['retained_rows']:,} retained; "
                f"{item['duplicate_rows']:,} exact duplicates"
            ),
        )
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as exc:
        _build_parser().error(str(exc))
    print(
        f"Audited {report['archive_count']} archive(s): "
        f"{report['total_raw_rows']:,} rows; "
        f"{report['total_retained_rows']:,} retained; "
        f"{report['total_rejected_rows']:,} rejected."
    )
    print(f"Report: {arguments.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
