"""Memory-bounded M3 matching and M4 sample generation for FIRMS archives."""

import argparse
import gzip
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import date
from itertools import zip_longest
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from spatial_ml import config
from spatial_ml.data.archive_ingest import audit_firms_archives
from spatial_ml.data.samples import TrainingSample, build_training_sample
from spatial_ml.pipeline.overlap import generate_overlap_dataset

_ARCHIVE_ORDER = {
    "MODIS": ("849514", "849541", "849531", "849545", "849584"),
    "VIIRS": ("849515", "849542", "849532", "849546", "849585"),
}
_DISTANCE_EDGES = np.arange(0.0, 1000.0 + 50.0, 50.0)
_TIME_EDGES = np.arange(0.0, 30.0 + 1.0, 1.0)
_REQUIRED_NORMALIZED_FIELDS = {
    "latitude",
    "longitude",
    "date",
    "datetime",
    "sensor",
    "satellite",
    "instrument",
    "confidence",
    "frp",
    "source_archive",
    "source_file",
    "source_row_number",
    "source_record_fingerprint",
}
_SENTINEL = object()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, indent=2, allow_nan=False, default=str)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _archive_sensor(path: Path) -> str:
    upper = path.name.upper()
    if "M-C61" in upper:
        return "MODIS"
    if "SV-C2" in upper:
        return "VIIRS"
    raise ValueError(f"Cannot determine FIRMS sensor from archive {path.name!r}.")


def _archive_code(path: Path) -> str:
    match = re.search(r"_(\d{6})\.zip$", path.name)
    if match is None:
        raise ValueError(f"Archive name has no six-digit code: {path.name!r}.")
    return match.group(1)


def _ordered_archives(archive_paths: Sequence[str | Path]) -> Dict[str, List[Path]]:
    grouped: Dict[str, Dict[str, Path]] = {"MODIS": {}, "VIIRS": {}}
    for value in archive_paths:
        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(f"FIRMS archive does not exist: {path}")
        sensor = _archive_sensor(path)
        code = _archive_code(path)
        if code in grouped[sensor]:
            raise ValueError(f"Duplicate {sensor} archive code {code}.")
        grouped[sensor][code] = path

    ordered = {}
    for sensor, codes in _ARCHIVE_ORDER.items():
        if set(grouped[sensor]) != set(codes):
            raise ValueError(
                f"Expected archive codes {codes} for {sensor}; found "
                f"{tuple(sorted(grouped[sensor]))}."
            )
        ordered[sensor] = [grouped[sensor][code] for code in codes]
    return ordered


def _archive_signature(archives: Mapping[str, Sequence[Path]]) -> List[Dict[str, object]]:
    return [
        {
            "sensor": sensor,
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for sensor in ("MODIS", "VIIRS")
        for path in archives[sensor]
    ]


def _normalized_paths(
    archives: Mapping[str, Sequence[Path]], processed_dir: Path
) -> Dict[str, List[Path]]:
    paths = {
        sensor: [
            processed_dir
            / sensor.lower()
            / f"{archive.stem}.normalized.csv.gz"
            for archive in archives[sensor]
        ]
        for sensor in ("MODIS", "VIIRS")
    }
    return paths


def _prepare_normalized_archives(
    archives: Mapping[str, Sequence[Path]],
    processed_dir: Path,
    chunksize: int,
) -> Path:
    outputs = _normalized_paths(archives, processed_dir)
    report_path = processed_dir / "archive_audit.json"
    all_outputs_exist = all(path.is_file() for paths in outputs.values() for path in paths)
    if all_outputs_exist and report_path.is_file():
        with report_path.open(encoding="utf-8") as stream:
            report = json.load(stream)
        reported_names = {item["archive"] for item in report.get("archives", [])}
        expected_names = {
            path.name for paths in archives.values() for path in paths
        }
        if reported_names != expected_names:
            raise ValueError(
                f"Existing M2 audit report in {processed_dir} does not match "
                "the requested FIRMS archives."
            )
        return report_path

    existing_outputs = [
        path for paths in outputs.values() for path in paths if path.exists()
    ]
    if existing_outputs or report_path.exists():
        raise FileExistsError(
            "The normalized archive directory contains a partial or unrelated "
            f"run; inspect it before proceeding: {processed_dir}"
        )
    required_free_bytes = 10 * 1024**3
    available = shutil.disk_usage(processed_dir.parent).free
    if available < required_free_bytes:
        raise OSError(
            f"At least 10 GiB of free disk is required before writing normalized "
            f"archives; only {available / 1024**3:.1f} GiB is available."
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    audit_firms_archives(
        [path for sensor in ("MODIS", "VIIRS") for path in archives[sensor]],
        chunksize=chunksize,
        report_path=report_path,
        processed_output_dir=processed_dir,
        progress_callback=lambda item: print(
            f"M2 {item['archive']}: {item['raw_rows']:,} source rows, "
            f"{item['retained_rows']:,} retained, "
            f"{item['duplicate_rows']:,} duplicate fingerprints"
        ),
    )
    if not all(path.is_file() for paths in outputs.values() for path in paths):
        raise RuntimeError("M2 completed without producing all normalized archives.")
    return report_path


def iter_normalized_days(
    paths: Sequence[str | Path],
    sensor: str,
    *,
    chunksize: int = 100_000,
) -> Iterator[Tuple[date, pd.DataFrame]]:
    """Yield one full acquisition date across consecutive archive boundaries."""
    current_day: Optional[date] = None
    current_frames: List[pd.DataFrame] = []
    last_day: Optional[date] = None

    for path_value in paths:
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(f"Normalized FIRMS archive does not exist: {path}")
        reader = pd.read_csv(path, compression="gzip", chunksize=chunksize)
        for chunk in reader:
            missing = _REQUIRED_NORMALIZED_FIELDS - set(chunk.columns)
            if missing:
                raise ValueError(
                    f"{path.name} is missing normalized fields: {sorted(missing)}"
                )
            if chunk["sensor"].isna().any() or not chunk["sensor"].eq(sensor).all():
                raise ValueError(f"{path.name} contains rows not labeled {sensor}.")
            parsed_dates = pd.to_datetime(chunk["date"], errors="coerce")
            if parsed_dates.isna().any():
                raise ValueError(f"{path.name} contains an invalid normalized date.")
            if not parsed_dates.is_monotonic_increasing:
                raise ValueError(
                    f"{path.name} is not ordered by acquisition date; "
                    "daily partitioning would be unsafe."
                )
            chunk = chunk.copy()
            chunk["_partition_date"] = parsed_dates.dt.date
            for day, group in chunk.groupby("_partition_date", sort=False):
                selected_day = day
                if last_day is not None and selected_day < last_day:
                    raise ValueError(
                        f"{path.name} moves backward from {last_day} to {selected_day}."
                    )
                if current_day is None:
                    current_day = selected_day
                elif selected_day != current_day:
                    yield current_day, _combine_day(sensor, current_frames)
                    current_day = selected_day
                    current_frames = []
                group = group.drop(columns="_partition_date")
                current_frames.append(group)
                last_day = selected_day

    if current_day is not None:
        yield current_day, _combine_day(sensor, current_frames)


def _combine_day(sensor: str, frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    day_records = pd.concat(frames, ignore_index=True)
    day_records["datetime"] = pd.to_datetime(day_records["datetime"], errors="coerce")
    if day_records["datetime"].isna().any():
        raise ValueError(f"{sensor} daily partition contains invalid datetimes.")
    if not day_records["datetime"].dt.date.eq(
        day_records["date"].map(
            lambda value: pd.Timestamp(value).date()
        )
    ).all():
        raise ValueError(f"{sensor} daily partition has inconsistent date/datetime.")
    fingerprints = day_records["source_record_fingerprint"].astype("string")
    if fingerprints.isna().any() or fingerprints.str.strip().eq("").any():
        raise ValueError(f"{sensor} daily partition has missing source fingerprints.")
    return day_records.drop_duplicates(
        subset=["source_record_fingerprint"], keep="first"
    ).reset_index(drop=True)


def _jsonable_sample_metadata(sample: TrainingSample) -> str:
    payload = {
        "modis_id": sample.modis_id,
        "modis_features": sample.modis_features,
        "metadata": sample.metadata,
    }

    def convert(value: object) -> object:
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, np.generic):
            return convert(value.item())
        if isinstance(value, (pd.Timestamp, date)):
            return value.isoformat()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, Mapping):
            return {str(key): convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        return value

    return json.dumps(convert(payload), allow_nan=False, separators=(",", ":"))


def load_training_sample_shards(
    paths: Iterable[str | Path],
) -> Iterator[TrainingSample]:
    """Load M4 samples lazily from pickle-free monthly NPZ partitions."""
    for path_value in paths:
        path = Path(path_value)
        with np.load(path, allow_pickle=False) as shard:
            identifiers = shard["modis_ids"].astype(str)
            targets = shard["target_grids"]
            metadata_json = shard["metadata_json"].astype(str)
            if len(identifiers) != len(targets) or len(targets) != len(metadata_json):
                raise ValueError(f"M4 shard arrays do not align in {path}.")
            for identifier, target, metadata_text in zip(
                identifiers, targets, metadata_json
            ):
                payload = json.loads(metadata_text)
                metadata = payload["metadata"]
                yield TrainingSample(
                    modis_id=str(identifier),
                    modis_features=payload["modis_features"],
                    environmental_features=np.empty((3, 3, 0), dtype=np.float32),
                    environmental_feature_names=(),
                    target_grid=target.astype(np.float32),
                    target_observations=pd.DataFrame(),
                    metadata=metadata,
                )


def _sample_shard_payload(
    samples: Sequence[TrainingSample],
) -> Dict[str, np.ndarray]:
    if samples:
        target_grids = np.stack(
            [sample.target_grid for sample in samples]
        ).astype(np.uint8)
    else:
        target_grids = np.empty(
            (0, config.TARGET_GRID_HEIGHT, config.TARGET_GRID_WIDTH),
            dtype=np.uint8,
        )
    return {
        "modis_ids": np.asarray(
            [sample.modis_id for sample in samples], dtype=str
        ),
        "target_grids": target_grids,
        "metadata_json": np.asarray(
            [_jsonable_sample_metadata(sample) for sample in samples], dtype=str
        ),
    }


def _write_sample_shard(path: Path, samples: Sequence[TrainingSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        np.savez_compressed(stream, **_sample_shard_payload(samples))


def _new_month_summary(year_month: str) -> Dict[str, object]:
    return {
        "year_month": year_month,
        "day_count": 0,
        "modis_observation_count": 0,
        "viirs_observation_count": 0,
        "matched_modis_count": 0,
        "unmatched_modis_count": 0,
        "candidate_relationship_count": 0,
        "candidate_distance_histogram_50m": [0] * (len(_DISTANCE_EDGES) - 1),
        "candidate_time_histogram_1m": [0] * (len(_TIME_EDGES) - 1),
        "matched_distance_min_m": None,
        "matched_distance_max_m": None,
        "matched_distance_sum_m": 0.0,
        "matched_time_min_minutes": None,
        "matched_time_max_minutes": None,
        "matched_time_sum_minutes": 0.0,
        "regional_counts_10deg": {},
        "m4_candidate_parent_count": 0,
        "m4_positive_parent_count": 0,
        "m4_out_of_grid_only_parent_count": 0,
        "m4_target_cell_count": 0,
        "m4_positive_cell_count": 0,
        "m4_negative_cell_count": 0,
        "m4_missing_frp_count": 0,
        "m4_missing_confidence_count": 0,
        "m4_samples": [],
    }


def _add_histogram(
    summary: Dict[str, object],
    name: str,
    values: pd.Series,
    edges: np.ndarray,
) -> None:
    histogram = np.histogram(
        pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float),
        bins=edges,
    )[0]
    old = np.asarray(summary[name], dtype=np.int64)
    summary[name] = (old + histogram).tolist()


def _update_month_summary(
    summary: Dict[str, object],
    day: date,
    modis: pd.DataFrame,
    viirs: pd.DataFrame,
    pairs: pd.DataFrame,
    candidates: pd.DataFrame,
) -> None:
    summary["day_count"] = int(summary["day_count"]) + 1
    summary["modis_observation_count"] = (
        int(summary["modis_observation_count"]) + len(modis)
    )
    summary["viirs_observation_count"] = (
        int(summary["viirs_observation_count"]) + len(viirs)
    )
    matched_mask = pairs["match_valid"].fillna(False).astype(bool)
    matched = pairs.loc[matched_mask]
    summary["matched_modis_count"] = (
        int(summary["matched_modis_count"]) + len(matched)
    )
    summary["unmatched_modis_count"] = (
        int(summary["unmatched_modis_count"]) + len(pairs) - len(matched)
    )
    summary["candidate_relationship_count"] = (
        int(summary["candidate_relationship_count"]) + len(candidates)
    )
    _add_histogram(
        summary,
        "candidate_distance_histogram_50m",
        candidates["distance_m"] if not candidates.empty else pd.Series(dtype=float),
        _DISTANCE_EDGES,
    )
    _add_histogram(
        summary,
        "candidate_time_histogram_1m",
        (
            candidates["time_difference_minutes"]
            if not candidates.empty
            else pd.Series(dtype=float)
        ),
        _TIME_EDGES,
    )
    for column, min_name, max_name, sum_name in (
        (
            "spatial_distance_m",
            "matched_distance_min_m",
            "matched_distance_max_m",
            "matched_distance_sum_m",
        ),
        (
            "temporal_difference_minutes",
            "matched_time_min_minutes",
            "matched_time_max_minutes",
            "matched_time_sum_minutes",
        ),
    ):
        values = pd.to_numeric(matched[column], errors="coerce").dropna()
        if not values.empty:
            previous_min = summary[min_name]
            previous_max = summary[max_name]
            summary[min_name] = (
                float(values.min())
                if previous_min is None
                else min(float(previous_min), float(values.min()))
            )
            summary[max_name] = (
                float(values.max())
                if previous_max is None
                else max(float(previous_max), float(values.max()))
            )
            summary[sum_name] = float(summary[sum_name]) + float(values.sum())

    if not pairs.empty:
        latitudes = pd.to_numeric(pairs["modis_latitude"], errors="coerce")
        longitudes = pd.to_numeric(pairs["modis_longitude"], errors="coerce")
        latitude_bins = np.minimum(
            np.floor((latitudes.to_numpy(dtype=float) + 90.0) / 10.0).astype(int),
            17,
        )
        longitude_bins = np.minimum(
            np.floor((longitudes.to_numpy(dtype=float) + 180.0) / 10.0).astype(int),
            35,
        )
        regions = pd.DataFrame(
            {
                "region": [
                    f"lat{lat:02d}_lon{lon:02d}"
                    for lat, lon in zip(latitude_bins, longitude_bins)
                ],
                "matched": matched_mask.to_numpy(dtype=bool),
            }
        )
        regional = summary["regional_counts_10deg"]
        for region, group in regions.groupby("region", sort=True):
            current = regional.setdefault(
                region,
                {"modis_observations": 0, "matched": 0, "unmatched": 0},
            )
            matched_count = int(group["matched"].sum())
            current["modis_observations"] += len(group)
            current["matched"] += matched_count
            current["unmatched"] += len(group) - matched_count

    by_parent = {
        str(parent_id): group.reset_index(drop=True)
        for parent_id, group in candidates.groupby("modis_id", sort=False)
    }
    summary["m4_candidate_parent_count"] = (
        int(summary["m4_candidate_parent_count"]) + len(by_parent)
    )
    pair_rows = {
        str(row["modis_id"]): row
        for row in pairs.to_dict(orient="records")
    }
    samples = summary["m4_samples"]
    for parent_id, candidate_rows in by_parent.items():
        sample = build_training_sample(
            pair_rows[parent_id],
            candidate_rows,
        )
        if not np.any(sample.target_grid == 1):
            summary["m4_out_of_grid_only_parent_count"] = (
                int(summary["m4_out_of_grid_only_parent_count"]) + 1
            )
            continue
        metadata = {
            **sample.metadata,
            "modis_source_archive": pair_rows[parent_id].get("modis_source_archive"),
            "modis_source_file": pair_rows[parent_id].get("modis_source_file"),
            "modis_source_row_number": pair_rows[parent_id].get(
                "modis_source_row_number"
            ),
            "modis_source_record_fingerprint": pair_rows[parent_id].get(
                "modis_source_record_fingerprint"
            ),
            "environmental_data_status": "unavailable_no_local_rasters",
        }
        sample = TrainingSample(
            modis_id=sample.modis_id,
            modis_features=sample.modis_features,
            environmental_features=sample.environmental_features,
            environmental_feature_names=sample.environmental_feature_names,
            target_grid=sample.target_grid,
            target_observations=pd.DataFrame(),
            metadata=metadata,
        )
        samples.append(sample)
        summary["m4_positive_parent_count"] = (
            int(summary["m4_positive_parent_count"]) + 1
        )
        positive = int(np.count_nonzero(sample.target_grid == 1))
        cells = int(sample.target_grid.size)
        summary["m4_target_cell_count"] = (
            int(summary["m4_target_cell_count"]) + cells
        )
        summary["m4_positive_cell_count"] = (
            int(summary["m4_positive_cell_count"]) + positive
        )
        summary["m4_negative_cell_count"] = (
            int(summary["m4_negative_cell_count"]) + cells - positive
        )
        summary["m4_missing_frp_count"] = (
            int(summary["m4_missing_frp_count"])
            + int(pd.isna(sample.modis_features.get("frp")))
        )
        summary["m4_missing_confidence_count"] = (
            int(summary["m4_missing_confidence_count"])
            + int(pd.isna(sample.modis_features.get("confidence")))
        )


class _MonthWriters:
    def __init__(self, output_dir: Path, year_month: str) -> None:
        self.output_dir = output_dir
        self.year_month = year_month
        self.partition_dir = output_dir / year_month
        self.staging = tempfile.TemporaryDirectory(
            prefix=".m3-m4-staging-", dir=output_dir
        )
        staging_dir = Path(self.staging.name)
        self.pairs_temporary = staging_dir / "matched_pairs.csv.gz"
        self.candidates_temporary = staging_dir / "candidate_relationships.csv.gz"
        self.pairs_stream = gzip.open(
            self.pairs_temporary, "wt", encoding="utf-8", newline="", compresslevel=1
        )
        self.candidates_stream = gzip.open(
            self.candidates_temporary,
            "wt",
            encoding="utf-8",
            newline="",
            compresslevel=1,
        )
        self.pairs_header = True
        self.candidates_header = True

    def write(self, pairs: pd.DataFrame, candidates: pd.DataFrame) -> None:
        pairs.to_csv(
            self.pairs_stream,
            index=False,
            header=self.pairs_header,
        )
        candidates.to_csv(
            self.candidates_stream,
            index=False,
            header=self.candidates_header,
        )
        self.pairs_header = False
        self.candidates_header = False

    def finish(self, samples: Sequence[TrainingSample]) -> Dict[str, str]:
        self.pairs_stream.close()
        self.candidates_stream.close()
        sample_temporary = Path(self.staging.name) / "training_samples.npz"
        _write_sample_shard(sample_temporary, samples)
        if self.partition_dir.exists():
            raise FileExistsError(
                f"Refusing to overwrite completed or untracked M3/M4 data: "
                f"{self.partition_dir}"
            )
        os.replace(self.staging.name, self.partition_dir)
        self.staging.cleanup()
        return {
            "matched_pairs": str(self.partition_dir / "matched_pairs.csv.gz"),
            "candidate_relationships": str(
                self.partition_dir / "candidate_relationships.csv.gz"
            ),
            "training_samples": str(self.partition_dir / "training_samples.npz"),
        }

    def abort(self) -> None:
        for stream in (self.pairs_stream, self.candidates_stream):
            if not stream.closed:
                stream.close()
        self.staging.cleanup()


def _summarize_month(summary: Dict[str, object]) -> Dict[str, object]:
    samples = summary.pop("m4_samples")
    count = len(samples)
    if count:
        frp_values = pd.to_numeric(
            pd.Series([sample.modis_features.get("frp") for sample in samples]),
            errors="coerce",
        )
        confidence_values = pd.to_numeric(
            pd.Series(
                [
                    sample.modis_features.get("confidence")
                    for sample in samples
                ]
            ),
            errors="coerce",
        )
        coordinates = [
            (
                float(sample.metadata["modis_latitude"]),
                float(sample.metadata["modis_longitude"]),
            )
            for sample in samples
        ]
        timestamps = [
            pd.Timestamp(sample.metadata["observation_datetime"])
            for sample in samples
        ]
        summary["m4_feature_summary"] = {
            "environmental_channels": [],
            "environmental_data_status": "unavailable_no_local_rasters",
            "frp": {
                "missing_count": int(frp_values.isna().sum()),
                "minimum": (
                    float(frp_values.min()) if frp_values.notna().any() else None
                ),
                "median": (
                    float(frp_values.median())
                    if frp_values.notna().any()
                    else None
                ),
                "maximum": (
                    float(frp_values.max()) if frp_values.notna().any() else None
                ),
            },
            "modis_confidence_numeric": {
                "missing_count": int(confidence_values.isna().sum()),
                "minimum": (
                    float(confidence_values.min())
                    if confidence_values.notna().any()
                    else None
                ),
                "median": (
                    float(confidence_values.median())
                    if confidence_values.notna().any()
                    else None
                ),
                "maximum": (
                    float(confidence_values.max())
                    if confidence_values.notna().any()
                    else None
                ),
            },
            "latitude_bounds": [
                min(value[0] for value in coordinates),
                max(value[0] for value in coordinates),
            ],
            "longitude_bounds": [
                min(value[1] for value in coordinates),
                max(value[1] for value in coordinates),
            ],
            "observation_date_range": [
                min(timestamps).isoformat(),
                max(timestamps).isoformat(),
            ],
        }
    else:
        summary["m4_feature_summary"] = {
            "environmental_channels": [],
            "environmental_data_status": "unavailable_no_local_rasters",
            "status": "no eligible in-grid positive samples",
        }
    summary["m4_sample_count"] = count
    summary["m4_positive_cell_prevalence"] = (
        int(summary["m4_positive_cell_count"])
        / int(summary["m4_target_cell_count"])
        if int(summary["m4_target_cell_count"])
        else None
    )
    return summary


def _aggregate_summaries(
    month_summaries: Mapping[str, Mapping[str, object]],
) -> Dict[str, object]:
    aggregate: Dict[str, object] = {
        "months": len(month_summaries),
        "modis_observation_count": 0,
        "viirs_observation_count": 0,
        "matched_modis_count": 0,
        "unmatched_modis_count": 0,
        "candidate_relationship_count": 0,
        "candidate_distance_histogram_50m": [0] * (len(_DISTANCE_EDGES) - 1),
        "candidate_time_histogram_1m": [0] * (len(_TIME_EDGES) - 1),
        "matched_distance_min_m": None,
        "matched_distance_max_m": None,
        "matched_distance_sum_m": 0.0,
        "matched_time_min_minutes": None,
        "matched_time_max_minutes": None,
        "matched_time_sum_minutes": 0.0,
        "m4_candidate_parent_count": 0,
        "m4_positive_parent_count": 0,
        "m4_out_of_grid_only_parent_count": 0,
        "m4_sample_count": 0,
        "m4_target_cell_count": 0,
        "m4_positive_cell_count": 0,
        "m4_negative_cell_count": 0,
        "m4_missing_frp_count": 0,
        "m4_missing_confidence_count": 0,
    }
    yearly: Dict[str, Dict[str, int]] = {}
    regional: Dict[str, Dict[str, int]] = {}
    for key, summary in sorted(month_summaries.items()):
        year = key[:4]
        year_summary = yearly.setdefault(
            year,
            {
                "modis_observation_count": 0,
                "matched_modis_count": 0,
                "unmatched_modis_count": 0,
                "candidate_relationship_count": 0,
                "m4_sample_count": 0,
            },
        )
        for name in (
            "modis_observation_count",
            "viirs_observation_count",
            "matched_modis_count",
            "unmatched_modis_count",
            "candidate_relationship_count",
            "m4_candidate_parent_count",
            "m4_positive_parent_count",
            "m4_out_of_grid_only_parent_count",
            "m4_sample_count",
            "m4_target_cell_count",
            "m4_positive_cell_count",
            "m4_negative_cell_count",
            "m4_missing_frp_count",
            "m4_missing_confidence_count",
        ):
            aggregate[name] = int(aggregate[name]) + int(summary[name])
            if name in year_summary:
                year_summary[name] += int(summary[name])
        for histogram_name in (
            "candidate_distance_histogram_50m",
            "candidate_time_histogram_1m",
        ):
            aggregate[histogram_name] = (
                np.asarray(aggregate[histogram_name], dtype=np.int64)
                + np.asarray(summary[histogram_name], dtype=np.int64)
            ).tolist()
        for name in (
            "matched_distance_min_m",
            "matched_distance_max_m",
            "matched_time_min_minutes",
            "matched_time_max_minutes",
        ):
            value = summary[name]
            if value is not None:
                existing = aggregate[name]
                use_min = name.endswith("_min_m") or name.endswith("_min_minutes")
                aggregate[name] = (
                    float(value)
                    if existing is None
                    else (min(float(existing), float(value)) if use_min else max(float(existing), float(value)))
                )
        for name in ("matched_distance_sum_m", "matched_time_sum_minutes"):
            aggregate[name] = float(aggregate[name]) + float(summary[name])
        for region, values in summary["regional_counts_10deg"].items():
            current = regional.setdefault(
                region,
                {"modis_observations": 0, "matched": 0, "unmatched": 0},
            )
            for name in current:
                current[name] += int(values[name])

    aggregate["yearly"] = yearly
    aggregate["regional_10deg"] = regional
    aggregate["m4_positive_cell_prevalence"] = (
        aggregate["m4_positive_cell_count"] / aggregate["m4_target_cell_count"]
        if aggregate["m4_target_cell_count"]
        else None
    )
    matched_count = aggregate["matched_modis_count"]
    aggregate["matched_distance_mean_m"] = (
        aggregate["matched_distance_sum_m"] / matched_count if matched_count else None
    )
    aggregate["matched_time_mean_minutes"] = (
        aggregate["matched_time_sum_minutes"] / matched_count
        if matched_count
        else None
    )
    return aggregate


def run_real_m3_m4(
    archive_paths: Sequence[str | Path],
    *,
    processed_dir: str | Path = config.PROCESSED_DATA_DIR / "firms_real_2016_2021",
    output_dir: str | Path = config.OVERLAP_DATA_DIR / "real_2016_2021",
    chunksize: int = 100_000,
    time_tolerance_minutes: float = config.DEFAULT_TIME_TOLERANCE_MINUTES,
    spatial_radius_m: float = config.SPATIAL_MATCH_RADIUS_M,
) -> Dict[str, object]:
    """Run M2 normalization, then resumable monthly M3 matching and M4 samples."""
    archives = _ordered_archives(archive_paths)
    if isinstance(chunksize, bool) or chunksize < 1:
        raise ValueError("chunksize must be a positive integer.")
    if not math.isfinite(time_tolerance_minutes) or time_tolerance_minutes < 0:
        raise ValueError("time_tolerance_minutes must be finite and non-negative.")
    if not math.isfinite(spatial_radius_m) or spatial_radius_m < 0:
        raise ValueError("spatial_radius_m must be finite and non-negative.")

    processed = Path(processed_dir)
    report_path = _prepare_normalized_archives(archives, processed, chunksize)
    normalized = _normalized_paths(archives, processed)
    destination = Path(output_dir)
    manifest_path = destination / "processing_manifest.json"
    signature = _archive_signature(archives)
    run_configuration = {
        "chunk_size": chunksize,
        "time_tolerance_minutes": float(time_tolerance_minutes),
        "spatial_radius_m": float(spatial_radius_m),
        "target_grid": {
            "resolution_m": config.TARGET_GRID_RESOLUTION_M,
            "width": config.TARGET_GRID_WIDTH,
            "height": config.TARGET_GRID_HEIGHT,
            "projection": config.TARGET_GRID_PROJECTION,
        },
        "environmental_features": [],
        "environmental_data_status": "unavailable_no_local_rasters",
        "region_summary_definition": "10-degree latitude/longitude bins",
    }
    if destination.exists():
        if not manifest_path.is_file():
            raise FileExistsError(
                f"M3/M4 output directory already exists without a run manifest: "
                f"{destination}"
            )
        with manifest_path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("archive_signature") != signature or manifest.get(
            "configuration"
        ) != run_configuration:
            raise ValueError(
                "Existing M3/M4 run manifest does not match the requested inputs "
                "or configuration."
            )
        completed = manifest.get("completed_months", [])
        for month in completed:
            for key, relative in manifest["month_outputs"][month].items():
                if not (destination / relative).is_file():
                    raise FileNotFoundError(
                        f"Completed partition {month} is missing {key}: "
                        f"{destination / relative}"
                    )
    else:
        destination.mkdir(parents=True, exist_ok=False)
        manifest = {
            "manifest_version": "1.0.0",
            "status": "in_progress",
            "archive_signature": signature,
            "m2_audit_report": str(report_path),
            "normalized_archives": {
                sensor: [str(path) for path in paths]
                for sensor, paths in normalized.items()
            },
            "configuration": run_configuration,
            "completed_months": [],
            "month_outputs": {},
            "month_summaries": {},
        }
        _atomic_json(manifest_path, manifest)

    completed_months = set(manifest["completed_months"])
    modis_days = iter_normalized_days(
        normalized["MODIS"], "MODIS", chunksize=chunksize
    )
    viirs_days = iter_normalized_days(
        normalized["VIIRS"], "VIIRS", chunksize=chunksize
    )
    iterators = zip_longest(modis_days, viirs_days, fillvalue=_SENTINEL)
    current_month: Optional[str] = None
    current_summary: Optional[Dict[str, object]] = None
    current_writers: Optional[_MonthWriters] = None
    try:
        for modis_item, viirs_item in iterators:
            if modis_item is _SENTINEL or viirs_item is _SENTINEL:
                raise ValueError(
                    "MODIS and VIIRS archives do not cover the same acquisition dates."
                )
            modis_day, modis_records = modis_item
            viirs_day, viirs_records = viirs_item
            if modis_day != viirs_day:
                raise ValueError(
                    f"Sensor date coverage differs: MODIS {modis_day}, "
                    f"VIIRS {viirs_day}."
                )
            month = modis_day.strftime("%Y-%m")
            if month in completed_months:
                continue
            if month != current_month:
                if current_month is not None:
                    if current_writers is None or current_summary is None:
                        raise RuntimeError("Monthly M3/M4 partition state is incomplete.")
                    output_paths = current_writers.finish(
                        current_summary["m4_samples"]
                    )
                    summary = _summarize_month(current_summary)
                    manifest["month_outputs"][current_month] = {
                        key: str(Path(value).relative_to(destination))
                        for key, value in output_paths.items()
                    }
                    manifest["month_summaries"][current_month] = summary
                    manifest["completed_months"].append(current_month)
                    manifest["completed_months"].sort()
                    _atomic_json(manifest_path, manifest)
                    print(
                        f"M3/M4 {current_month}: "
                        f"{summary['modis_observation_count']:,} MODIS, "
                        f"{summary['matched_modis_count']:,} matched, "
                        f"{summary['candidate_relationship_count']:,} candidates, "
                        f"{summary['m4_sample_count']:,} eligible samples"
                    )

                current_month = month
                month_dir = destination / month
                if month_dir.exists() and any(month_dir.iterdir()):
                    raise FileExistsError(
                        f"Untracked or partial monthly output exists: {month_dir}"
                    )
                current_summary = _new_month_summary(month)
                current_writers = _MonthWriters(destination, month)

            if current_summary is None or current_writers is None:
                raise RuntimeError("Monthly M3/M4 partition was not initialized.")
            pairs, candidates = generate_overlap_dataset(
                modis_records,
                viirs_records,
                time_tolerance_minutes=time_tolerance_minutes,
                spatial_radius_m=spatial_radius_m,
            )
            current_writers.write(pairs, candidates)
            _update_month_summary(
                current_summary,
                modis_day,
                modis_records,
                viirs_records,
                pairs,
                candidates,
            )

        if current_month is not None:
            if current_writers is None or current_summary is None:
                raise RuntimeError("Monthly M3/M4 partition state is incomplete.")
            output_paths = current_writers.finish(current_summary["m4_samples"])
            summary = _summarize_month(current_summary)
            manifest["month_outputs"][current_month] = {
                key: str(Path(value).relative_to(destination))
                for key, value in output_paths.items()
            }
            manifest["month_summaries"][current_month] = summary
            manifest["completed_months"].append(current_month)
            manifest["completed_months"].sort()
            _atomic_json(manifest_path, manifest)
            print(
                f"M3/M4 {current_month}: "
                f"{summary['modis_observation_count']:,} MODIS, "
                f"{summary['matched_modis_count']:,} matched, "
                f"{summary['candidate_relationship_count']:,} candidates, "
                f"{summary['m4_sample_count']:,} eligible samples"
            )
    except Exception:
        if current_writers is not None:
            current_writers.abort()
        raise

    expected_months = set(manifest["month_summaries"])
    if not expected_months:
        raise ValueError("No matching dates were processed from the archives.")
    manifest["status"] = "complete"
    manifest["summary"] = _aggregate_summaries(manifest["month_summaries"])
    _atomic_json(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run chunked M2 ingestion, daily M3 matching, and M4 target-sample "
            "generation on local FIRMS ZIP archives. No data are downloaded."
        )
    )
    parser.add_argument("--archives", nargs="+", required=True, type=Path)
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=config.PROCESSED_DATA_DIR / "firms_real_2016_2021",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=config.OVERLAP_DATA_DIR / "real_2016_2021",
    )
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument(
        "--time-tolerance-minutes",
        type=float,
        default=config.DEFAULT_TIME_TOLERANCE_MINUTES,
    )
    parser.add_argument(
        "--spatial-radius-m",
        type=float,
        default=config.SPATIAL_MATCH_RADIUS_M,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = run_real_m3_m4(
            arguments.archives,
            processed_dir=arguments.processed_dir,
            output_dir=arguments.output_dir,
            chunksize=arguments.chunk_size,
            time_tolerance_minutes=arguments.time_tolerance_minutes,
            spatial_radius_m=arguments.spatial_radius_m,
        )
    except (FileNotFoundError, FileExistsError, OSError, ValueError, RuntimeError) as exc:
        _parser().error(str(exc))
    summary_path = arguments.output_dir / "processing_manifest.json"
    print(f"Status: {manifest['status']}")
    print(f"Manifest: {summary_path}")
    print(json.dumps(manifest["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
