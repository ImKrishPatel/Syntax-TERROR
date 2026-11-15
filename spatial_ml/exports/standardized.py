"""Validated CSV and GeoJSON exports for observed and estimated fire data."""

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping
from shapely.ops import split

from spatial_ml import config
from spatial_ml.data import clean_firms_records, load_modis, load_viirs
from spatial_ml.data.target_grid import LocalTargetGrid
from spatial_ml.pipeline import generate_observation_ids, generate_overlap_dataset

EXPORT_FILENAMES = {
    "modis": "modis_observations.csv",
    "viirs": "viirs_observations.csv",
    "matched": "modis_viirs_matched.csv",
    "probabilities": "fine_scale_fire_probability.csv",
}

OBSERVATION_COLUMNS = (
    "observation_id",
    "original_source_identifier",
    "sensor",
    "latitude",
    "longitude",
    "acquisition_date",
    "acquisition_time",
    "datetime",
    "satellite",
    "instrument",
    "confidence",
    "confidence_source_value",
    "confidence_valid",
    "frp",
    "frp_source_value",
    "frp_units",
    "daynight",
    "source_file",
    "source_file_sha256",
    "source_product",
    "source_version",
    "ingestion_timestamp",
    "frp_cleaning_flag",
    "estimated",
    "reference_observation_available",
    "source_fields_json",
)

MATCHED_COLUMNS = (
    "modis_observation_id",
    "viirs_observation_id",
    "modis_latitude",
    "modis_longitude",
    "viirs_latitude",
    "viirs_longitude",
    "modis_datetime",
    "viirs_datetime",
    "spatial_matching_distance_m",
    "temporal_difference_seconds",
    "match_status",
    "selected_nearest_match",
    "viirs_candidate_count",
    "matching_method",
    "spatial_radius_m",
    "temporal_tolerance_minutes",
    "modis_source_file",
    "viirs_source_file",
    "modis_original_source_identifier",
    "viirs_original_source_identifier",
    "modis_satellite",
    "modis_instrument",
    "viirs_satellite",
    "viirs_instrument",
    "modis_source_file_sha256",
    "viirs_source_file_sha256",
    "estimated",
)

PROBABILITY_COLUMNS = (
    "parent_modis_id",
    "cell_id",
    "observation_datetime",
    "cell_center_latitude",
    "cell_center_longitude",
    "cell_boundary_wgs84",
    "raw_estimated_probability",
    "calibrated_probability",
    "uncertainty_value",
    "uncertainty_type",
    "uncertainty_method",
    "satellite",
    "instrument",
    "model_name",
    "model_version",
    "preprocessing_version",
    "calibration_version",
    "calibration_method",
    "source_sensor",
    "source_file_name",
    "source_file_sha256",
    "source_row_number",
    "source_observation_id",
    "source_latitude",
    "source_longitude",
    "source_timestamp",
    "spatial_resolution_m",
    "crs",
    "grid_crs",
    "estimated",
    "reference_observation_available",
    "source_provenance_json",
)

_CELL_ID = re.compile(r"^r(?P<row>\d+)_c(?P<column>\d+)$")
_SOURCE_ID_ALIASES = (
    "id",
    "source_id",
    "source_observation_id",
    "observation_id",
    "fire_id",
    "detection_id",
)
_PRODUCT_ALIASES = ("source_product", "product", "product_name")
_VERSION_ALIASES = ("source_version", "version", "product_version")
_FRP_UNIT_ALIASES = ("frp_units", "frp_unit", "frp_units_of_measure")


class ExportValidationError(ValueError):
    """Raised when an export cannot satisfy its published schema/QC rules."""


def _column_by_alias(frame: pd.DataFrame, aliases: Sequence[str]) -> Optional[str]:
    mapping = {
        str(column).strip().lstrip("\ufeff").lower(): column
        for column in frame.columns
    }
    return next((mapping[name] for name in aliases if name in mapping), None)


def _json_value(value: object) -> object:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _source_field_json(row: Mapping[str, object], source_columns: Sequence[str]) -> str:
    return json.dumps(
        {str(column): _json_value(row.get(column)) for column in source_columns},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _normalize_filter_date(value: Optional[str], name: str) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid ISO date.") from exc
    if pd.isna(parsed):
        raise ValueError(f"{name} must be a valid ISO date.")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed.normalize()


def _date_mask(
    values: pd.Series,
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.Series:
    start = _normalize_filter_date(start_date, "start_date")
    end = _normalize_filter_date(end_date, "end_date")
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must be on or before end_date.")
    dates = pd.to_datetime(values, errors="coerce", utc=True).dt.tz_localize(None)
    mask = dates.notna()
    if start is not None:
        mask &= dates >= start
    if end is not None:
        mask &= dates < end + pd.Timedelta(days=1)
    return mask


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_observations(
    source_path: str | Path,
    sensor: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Load, normalize, clean, and date-filter a local FIRMS sensor CSV."""
    path = Path(source_path)
    normalized_sensor = sensor.strip().upper()
    if normalized_sensor not in {"MODIS", "VIIRS"}:
        raise ValueError("sensor must be MODIS or VIIRS.")
    raw = load_modis(path) if normalized_sensor == "MODIS" else load_viirs(path)
    source_sha256 = _path_sha256(path)
    source_columns = list(raw.columns)
    raw["source_file"] = raw.get("source_file", path.name)
    raw["source_file_sha256"] = source_sha256
    raw["original_source_identifier"] = (
        raw[_column_by_alias(raw, _SOURCE_ID_ALIASES)]
        if _column_by_alias(raw, _SOURCE_ID_ALIASES) is not None
        else pd.NA
    )
    raw["confidence_source_value"] = raw["confidence"].astype("object")
    raw["frp_source_value"] = raw["frp"].astype("object")
    product_column = _column_by_alias(raw, _PRODUCT_ALIASES)
    version_column = _column_by_alias(raw, _VERSION_ALIASES)
    units_column = _column_by_alias(raw, _FRP_UNIT_ALIASES)
    raw["source_product"] = (
        raw[product_column] if product_column is not None else pd.NA
    )
    raw["source_version"] = (
        raw[version_column] if version_column is not None else pd.NA
    )
    raw["frp_units"] = raw[units_column] if units_column is not None else pd.NA
    raw["ingestion_timestamp"] = datetime.now(timezone.utc).isoformat()

    frp_numeric = pd.to_numeric(raw["frp"], errors="coerce")
    frp_was_present = raw["frp"].notna() & raw["frp"].astype(str).str.strip().ne("")
    raw["frp_cleaning_flag"] = np.where(
        ~frp_was_present,
        "missing_source",
        np.where(
            frp_numeric.isna() | ~np.isfinite(frp_numeric) | frp_numeric.lt(0),
            "invalid_set_missing",
            "valid",
        ),
    )
    if raw.empty:
        cleaned = raw.copy()
        cleaned["confidence_valid"] = pd.Series(dtype=bool)
    else:
        cleaned = clean_firms_records(raw)
    cleaned["observation_id"] = generate_observation_ids(
        cleaned.reset_index(drop=True), normalized_sensor
    ).to_numpy()
    cleaned["acquisition_date"] = pd.to_datetime(
        cleaned["datetime"], errors="coerce", utc=True
    ).dt.strftime("%Y-%m-%d")
    cleaned["acquisition_time"] = pd.to_datetime(
        cleaned["datetime"], errors="coerce", utc=True
    ).dt.strftime("%H%M")
    cleaned["datetime"] = pd.to_datetime(
        cleaned["datetime"], errors="coerce", utc=True
    ).dt.tz_convert(None)
    cleaned["estimated"] = False
    cleaned["reference_observation_available"] = ""
    cleaned["source_fields_json"] = [
        _source_field_json(row, source_columns)
        for row in cleaned[source_columns].to_dict(orient="records")
    ]
    full_clean_count = len(cleaned)
    if start_date is not None or end_date is not None:
        cleaned = cleaned.loc[
            _date_mask(cleaned["datetime"], start_date, end_date)
        ].copy()
    source_info = {
        "source_file": str(path),
        "source_sha256": source_sha256,
        "source_record_count": len(raw),
        "cleaned_record_count_before_date_filter": full_clean_count,
        "export_record_count": len(cleaned),
        "removed_by_cleaning_count": len(raw) - full_clean_count,
        "source_columns": source_columns,
    }
    return cleaned.reset_index(drop=True), source_info


def _validate_observations(records: pd.DataFrame, sensor: str) -> Dict[str, object]:
    required = {
        "observation_id",
        "sensor",
        "latitude",
        "longitude",
        "datetime",
        "estimated",
        "source_file",
    }
    missing = required - set(records.columns)
    if missing:
        raise ExportValidationError(
            f"{sensor} export is missing required columns: {sorted(missing)}"
        )
    if records["observation_id"].isna().any() or records["observation_id"].astype(str).str.strip().eq("").any():
        raise ExportValidationError(f"{sensor} export contains missing observation IDs.")
    if records["observation_id"].duplicated().any():
        raise ExportValidationError(f"{sensor} export contains duplicate observation IDs.")
    latitude = pd.to_numeric(records["latitude"], errors="coerce")
    longitude = pd.to_numeric(records["longitude"], errors="coerce")
    if (
        latitude.isna().any()
        or longitude.isna().any()
        or not np.isfinite(latitude).all()
        or not np.isfinite(longitude).all()
        or not latitude.between(-90, 90).all()
        or not longitude.between(-180, 180).all()
    ):
        raise ExportValidationError(f"{sensor} export contains invalid coordinates.")
    if pd.to_datetime(records["datetime"], errors="coerce", utc=True).isna().any():
        raise ExportValidationError(f"{sensor} export contains invalid datetimes.")
    if not records["sensor"].eq(sensor).all():
        raise ExportValidationError(f"{sensor} export has an incompatible sensor label.")
    if _parse_boolean(records["estimated"], "estimated").any():
        raise ExportValidationError(
            "Original satellite observations cannot be labelled as model estimates."
        )
    return _coverage_summary(records, "latitude", "longitude", "datetime")


def _coverage_summary(
    records: pd.DataFrame, latitude_column: str, longitude_column: str, date_column: str
) -> Dict[str, object]:
    if records.empty:
        return {
            "date_start": None,
            "date_end": None,
            "geographic_bounds": None,
        }
    dates = pd.to_datetime(records[date_column], errors="coerce", utc=True)
    latitudes = pd.to_numeric(records[latitude_column], errors="coerce")
    longitudes = pd.to_numeric(records[longitude_column], errors="coerce")
    return {
        "date_start": dates.min().isoformat(),
        "date_end": dates.max().isoformat(),
        "geographic_bounds": {
            "west": float(longitudes.min()),
            "south": float(latitudes.min()),
            "east": float(longitudes.max()),
            "north": float(latitudes.max()),
        },
    }


def _atomic_csv(path: Path, records: pd.DataFrame, columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    records.reindex(columns=columns).to_csv(temporary, index=False)
    os.replace(temporary, path)


def _export_observations(
    records: pd.DataFrame,
    output_path: str | Path,
    sensor: str,
    source_info: Mapping[str, object],
    *,
    validate_only: bool,
) -> Dict[str, object]:
    coverage = _validate_observations(records, sensor)
    unknown_columns = [
        column
        for column in records.columns
        if column not in OBSERVATION_COLUMNS and column != "observation_id"
    ]
    columns = list(OBSERVATION_COLUMNS) + unknown_columns
    result = {
        "filename": Path(output_path).name,
        "row_count": len(records),
        "columns": columns,
        "date_range": {
            "start": coverage["date_start"],
            "end": coverage["date_end"],
        },
        "geographic_coverage": coverage["geographic_bounds"],
        "source_files": sorted(
            {str(source_info["source_file"])}
            | {
                str(value)
                for value in records["source_file"].dropna()
                if str(value).strip()
            }
        ),
        "source_file_sha256": source_info.get("source_sha256"),
        "crs": "EPSG:4326",
        "warnings": (
            ["No observations remain after cleaning and date filtering."]
            if records.empty
            else []
        )
        + (
            [
                f"{source_info['removed_by_cleaning_count']} invalid or duplicate "
                "source row(s) were removed by the existing FIRMS cleaner."
            ]
            if source_info.get("removed_by_cleaning_count", 0)
            else []
        ),
        "validation": {"status": "passed", "checks": ["ids", "coordinates", "datetime", "observed_not_estimated"]},
    }
    if not validate_only:
        _atomic_csv(Path(output_path), records, columns)
    return result


def export_modis_observations(
    records: pd.DataFrame,
    output_path: str | Path,
    source_info: Mapping[str, object],
    *,
    validate_only: bool = False,
) -> Dict[str, object]:
    """Export cleaned observed MODIS records, retaining source fields."""
    return _export_observations(
        records, output_path, "MODIS", source_info, validate_only=validate_only
    )


def export_viirs_observations(
    records: pd.DataFrame,
    output_path: str | Path,
    source_info: Mapping[str, object],
    *,
    validate_only: bool = False,
) -> Dict[str, object]:
    """Export cleaned observed VIIRS records, retaining source fields."""
    return _export_observations(
        records, output_path, "VIIRS", source_info, validate_only=validate_only
    )


def _source_lookup(records: pd.DataFrame, sensor: str) -> Dict[str, Mapping[str, object]]:
    identifiers = generate_observation_ids(records.reset_index(drop=True), sensor)
    result = {}
    for identifier, row in zip(identifiers, records.to_dict(orient="records")):
        result[str(identifier)] = row
    return result


def _match_row(
    modis_id: object,
    viirs_id: object,
    modis_row: Optional[Mapping[str, object]],
    viirs_row: Optional[Mapping[str, object]],
    *,
    distance_m: object,
    temporal_seconds: object,
    status: str,
    selected: bool,
    candidate_count: int,
    spatial_radius_m: float,
    temporal_tolerance_minutes: float,
) -> Dict[str, object]:
    return {
        "modis_observation_id": _json_value(modis_id),
        "viirs_observation_id": _json_value(viirs_id),
        "modis_latitude": _json_value(modis_row.get("latitude") if modis_row else None),
        "modis_longitude": _json_value(modis_row.get("longitude") if modis_row else None),
        "viirs_latitude": _json_value(viirs_row.get("latitude") if viirs_row else None),
        "viirs_longitude": _json_value(viirs_row.get("longitude") if viirs_row else None),
        "modis_datetime": _json_value(modis_row.get("datetime") if modis_row else None),
        "viirs_datetime": _json_value(viirs_row.get("datetime") if viirs_row else None),
        "spatial_matching_distance_m": _json_value(distance_m),
        "temporal_difference_seconds": _json_value(temporal_seconds),
        "match_status": status,
        "selected_nearest_match": selected,
        "viirs_candidate_count": candidate_count,
        "matching_method": "same_UTC_date_WGS84_geodesic_nearest_candidates",
        "spatial_radius_m": spatial_radius_m,
        "temporal_tolerance_minutes": temporal_tolerance_minutes,
        "modis_source_file": _json_value(modis_row.get("source_file") if modis_row else None),
        "viirs_source_file": _json_value(viirs_row.get("source_file") if viirs_row else None),
        "modis_original_source_identifier": _json_value(
            modis_row.get("original_source_identifier") if modis_row else None
        ),
        "viirs_original_source_identifier": _json_value(
            viirs_row.get("original_source_identifier") if viirs_row else None
        ),
        "modis_satellite": _json_value(
            modis_row.get("satellite") if modis_row else None
        ),
        "modis_instrument": _json_value(
            modis_row.get("instrument") if modis_row else None
        ),
        "viirs_satellite": _json_value(
            viirs_row.get("satellite") if viirs_row else None
        ),
        "viirs_instrument": _json_value(
            viirs_row.get("instrument") if viirs_row else None
        ),
        "modis_source_file_sha256": _json_value(
            modis_row.get("source_file_sha256") if modis_row else None
        ),
        "viirs_source_file_sha256": _json_value(
            viirs_row.get("source_file_sha256") if viirs_row else None
        ),
        "estimated": False,
    }


def export_matched_observations(
    modis_records: pd.DataFrame,
    viirs_records: pd.DataFrame,
    output_path: str | Path,
    *,
    spatial_radius_m: Optional[float] = None,
    temporal_tolerance_minutes: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source_files: Sequence[str | Path] = (),
    validate_only: bool = False,
) -> Dict[str, object]:
    """Export every candidate relationship plus unmatched MODIS observations."""
    radius = config.SPATIAL_MATCH_RADIUS_M if spatial_radius_m is None else float(spatial_radius_m)
    tolerance = (
        config.DEFAULT_TIME_TOLERANCE_MINUTES
        if temporal_tolerance_minutes is None
        else float(temporal_tolerance_minutes)
    )
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("spatial_radius_m must be finite and non-negative.")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("temporal_tolerance_minutes must be finite and non-negative.")
    _validate_observations(modis_records, "MODIS")
    _validate_observations(viirs_records, "VIIRS")
    modis = modis_records.loc[
        _date_mask(modis_records["datetime"], start_date, end_date)
    ].copy()
    viirs = viirs_records.loc[
        _date_mask(viirs_records["datetime"], start_date, end_date)
    ].copy()
    modis_lookup = _source_lookup(modis, "MODIS")
    viirs_lookup = _source_lookup(viirs, "VIIRS")
    rows: List[Dict[str, object]] = []
    warnings: List[str] = []
    if modis.empty:
        warnings.append("No MODIS observations remain in the requested date range.")
    elif viirs.empty:
        warnings.append(
            "No VIIRS observations remain in the requested date range; MODIS "
            "records are exported as unmatched."
        )
        for identifier in modis_lookup:
            row = modis_lookup[identifier]
            rows.append(
                _match_row(
                    identifier,
                    None,
                    row,
                    None,
                    distance_m=None,
                    temporal_seconds=None,
                    status="unmatched_no_viirs_input",
                    selected=False,
                    candidate_count=0,
                    spatial_radius_m=radius,
                    temporal_tolerance_minutes=tolerance,
                )
            )
    else:
        try:
            pairs, candidates = generate_overlap_dataset(
                modis,
                viirs,
                time_tolerance_minutes=tolerance,
                spatial_radius_m=radius,
            )
        except ValueError as exc:
            if "datetime ranges do not overlap" not in str(exc):
                raise
            warnings.append(
                "The MODIS and VIIRS date ranges do not intersect; MODIS "
                "records are exported as unmatched."
            )
            pairs = pd.DataFrame()
            candidates = pd.DataFrame()

        selected_pairs = {
            (str(pair["modis_id"]), str(pair["viirs_id"]))
            for pair in pairs.to_dict(orient="records")
            if pd.notna(pair.get("viirs_id"))
        }
        candidate_counts = (
            candidates.groupby("modis_id").size().to_dict()
            if not candidates.empty
            else {}
        )
        for candidate in candidates.to_dict(orient="records"):
            modis_id = str(candidate["modis_id"])
            viirs_id = str(candidate["viirs_id"])
            rows.append(
                _match_row(
                    modis_id,
                    viirs_id,
                    modis_lookup.get(modis_id),
                    viirs_lookup.get(viirs_id),
                    distance_m=candidate["distance_m"],
                    temporal_seconds=float(candidate["time_difference_minutes"]) * 60.0,
                    status="candidate_match",
                    selected=(modis_id, viirs_id) in selected_pairs,
                    candidate_count=int(candidate_counts.get(modis_id, 0)),
                    spatial_radius_m=radius,
                    temporal_tolerance_minutes=tolerance,
                )
            )
        matched_modis_ids = {
            str(candidate["modis_id"])
            for candidate in candidates.to_dict(orient="records")
        }
        pair_by_id = {
            str(pair["modis_id"]): pair
            for pair in pairs.to_dict(orient="records")
        }
        for modis_id, modis_row in modis_lookup.items():
            if modis_id in matched_modis_ids:
                continue
            pair = pair_by_id.get(modis_id)
            status = (
                "unmatched"
                if pair is not None
                else "outside_observed_overlap_period"
            )
            rows.append(
                _match_row(
                    modis_id,
                    None,
                    modis_row,
                    None,
                    distance_m=None,
                    temporal_seconds=None,
                    status=status,
                    selected=False,
                    candidate_count=0,
                    spatial_radius_m=radius,
                    temporal_tolerance_minutes=tolerance,
                )
            )

    output = pd.DataFrame(rows, columns=MATCHED_COLUMNS)
    key_columns = ["modis_observation_id", "viirs_observation_id", "modis_datetime"]
    if not output.empty and output.duplicated(key_columns).any():
        raise ExportValidationError("Matched export contains duplicate relationship keys.")
    matched_mask = output["match_status"].eq("candidate_match")
    if output.loc[matched_mask, ["modis_observation_id", "viirs_observation_id"]].isna().any().any():
        raise ExportValidationError("Candidate matches must contain both observation IDs.")
    distances = pd.to_numeric(output["spatial_matching_distance_m"], errors="coerce")
    time_differences = pd.to_numeric(output["temporal_difference_seconds"], errors="coerce")
    if (
        distances[matched_mask].isna().any()
        or time_differences[matched_mask].isna().any()
        or distances[matched_mask].lt(0).any()
        or time_differences[matched_mask].lt(0).any()
    ):
        raise ExportValidationError("Matched distances and time differences must be non-negative.")
    summary = _coverage_summary(output, "modis_latitude", "modis_longitude", "modis_datetime")
    result = {
        "filename": Path(output_path).name,
        "row_count": len(output),
        "columns": list(MATCHED_COLUMNS),
        "date_range": {"start": summary["date_start"], "end": summary["date_end"]},
        "geographic_coverage": summary["geographic_bounds"],
        "source_files": sorted(
            {
                *(str(item) for item in source_files),
                *(
                    str(value)
                    for column in ("modis_source_file", "viirs_source_file")
                    for value in output[column].dropna()
                    if str(value)
                ),
            }
        ),
        "crs": "EPSG:4326",
        "warnings": warnings + (["No match rows were produced."] if output.empty else []),
        "validation": {
            "status": "passed",
            "checks": ["candidate_ids", "nonnegative_distance_time", "unique_relationship_key"],
        },
    }
    if not validate_only:
        _atomic_csv(Path(output_path), output, MATCHED_COLUMNS)
    return result


def _read_probability_input(path: Path) -> Tuple[pd.DataFrame, List[str]]:
    if not path.exists():
        raise FileNotFoundError(f"M8 probability input does not exist: {path}")
    files = (
        sorted(path.glob("predictions-*.csv"))
        if path.is_dir()
        else [path]
    )
    if not files:
        raise FileNotFoundError(
            f"No M8 prediction batch CSV files were found in: {path}"
        )
    frames = []
    for file_path in files:
        try:
            frames.append(pd.read_csv(file_path, dtype=str))
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise ValueError(f"Could not read M8 probability input {file_path}: {exc}") from exc
    return pd.concat(frames, ignore_index=True), [str(item) for item in files]


def _parse_boolean(values: pd.Series, name: str) -> pd.Series:
    normalized = values.astype("string").str.strip().str.lower()
    parsed = normalized.map({"true": True, "false": False, "1": True, "0": False})
    if parsed.isna().any():
        raise ExportValidationError(f"{name} contains missing or invalid boolean values.")
    return parsed.astype(bool)


def _cell_indices_and_geometry(
    row: Mapping[str, object],
    *,
    width: int,
    height: int,
    resolution_m: float,
) -> Tuple[List[List[float]], str]:
    match = _CELL_ID.fullmatch(str(row["cell_id"]))
    if match is None:
        raise ExportValidationError(
            "GeoJSON requires stable M8 target cell IDs in rNN_cNN form."
        )
    grid_row = int(match.group("row"))
    grid_column = int(match.group("column"))
    if not (0 <= grid_row < height and 0 <= grid_column < width):
        raise ExportValidationError("Target cell ID is outside its inferred grid dimensions.")
    grid = LocalTargetGrid(
        float(row["source_latitude"]),
        float(row["source_longitude"]),
        resolution_m=resolution_m,
        width=width,
        height=height,
    )
    expected_x, expected_y = grid.cell_center_local(grid_row, grid_column)
    center_x, center_y = grid.geographic_to_local(
        float(row["cell_center_longitude"]),
        float(row["cell_center_latitude"]),
    )
    if not (
        math.isclose(float(center_x), expected_x, abs_tol=0.1)
        and math.isclose(float(center_y), expected_y, abs_tol=0.1)
    ):
        raise ExportValidationError(
            "Cell centers do not match regular grid geometry; refusing to invent boundaries."
        )
    half = resolution_m / 2.0
    local_corners = [
        (expected_x - half, expected_y + half),
        (expected_x + half, expected_y + half),
        (expected_x + half, expected_y - half),
        (expected_x - half, expected_y - half),
        (expected_x - half, expected_y + half),
    ]
    longitudes, latitudes = grid.local_to_geographic(
        np.asarray([point[0] for point in local_corners]),
        np.asarray([point[1] for point in local_corners]),
    )
    coordinates = [
        [float(longitude), float(latitude)]
        for longitude, latitude in zip(longitudes, latitudes)
    ]
    polygon = Polygon(coordinates)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ExportValidationError("Derived GeoJSON target cell polygon is invalid.")
    return coordinates, grid.crs.to_string()


def _geojson_cell_geometry(coordinates: Sequence[Sequence[float]]) -> Dict[str, object]:
    unwrapped = [[float(coordinates[0][0]), float(coordinates[0][1])]]
    for longitude, latitude in coordinates[1:-1]:
        adjusted = float(longitude)
        previous = unwrapped[-1][0]
        while adjusted - previous > 180:
            adjusted -= 360
        while adjusted - previous < -180:
            adjusted += 360
        unwrapped.append([adjusted, float(latitude)])
    closing_longitude = float(coordinates[0][0])
    while closing_longitude - unwrapped[-1][0] > 180:
        closing_longitude -= 360
    while closing_longitude - unwrapped[-1][0] < -180:
        closing_longitude += 360
    unwrapped.append([closing_longitude, float(coordinates[0][1])])
    polygon = Polygon(unwrapped)
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ExportValidationError("Unwrapped target cell polygon is invalid.")

    pieces = [polygon]
    minimum_x, _, maximum_x, _ = polygon.bounds
    first_boundary = math.floor((minimum_x - 180) / 360)
    last_boundary = math.ceil((maximum_x - 180) / 360)
    for boundary_index in range(first_boundary, last_boundary + 1):
        boundary_x = 180 + 360 * boundary_index
        next_pieces = []
        for piece in pieces:
            if piece.bounds[0] < boundary_x < piece.bounds[2]:
                divided = split(
                    piece,
                    LineString(((boundary_x, -90), (boundary_x, 90))),
                )
                next_pieces.extend(
                    part for part in divided.geoms if part.geom_type == "Polygon"
                )
            else:
                next_pieces.append(piece)
        pieces = next_pieces

    canonical_pieces = []
    for piece in pieces:
        shift = -360 * math.floor((piece.centroid.x + 180) / 360)
        canonical = translate(piece, xoff=shift)
        if not canonical.is_valid or canonical.area <= 0:
            raise ExportValidationError("Antimeridian-safe target cell polygon is invalid.")
        canonical_pieces.append(canonical)
    geometry = (
        canonical_pieces[0]
        if len(canonical_pieces) == 1
        else MultiPolygon(canonical_pieces)
    )
    if not geometry.is_valid:
        raise ExportValidationError("GeoJSON target cell geometry is invalid.")
    return mapping(geometry)


def _validated_probabilities(
    path: Path,
    *,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[pd.DataFrame, List[str]]:
    records, source_files = _read_probability_input(path)
    required_aliases = {
        "parent_modis_id": ("parent_modis_id", "parent_modis_observation_id"),
        "cell_id": ("cell_id", "target_cell_id"),
        "observation_datetime": ("observation_datetime",),
        "cell_center_latitude": ("cell_center_latitude",),
        "cell_center_longitude": ("cell_center_longitude",),
        "raw_estimated_probability": (
            "raw_estimated_probability",
            "raw_estimated_detection_probability",
        ),
        "calibrated_probability": (
            "calibrated_probability",
            "calibrated_estimated_detection_probability",
        ),
        "uncertainty_value": ("uncertainty_value", "uncertainty_estimate"),
        "uncertainty_type": ("uncertainty_type",),
        "uncertainty_method": ("uncertainty_method",),
        "satellite": ("satellite",),
        "instrument": ("instrument",),
        "model_name": ("model_name",),
        "model_version": ("model_version",),
        "preprocessing_version": ("preprocessing_version",),
        "estimated": ("estimated",),
        "reference_observation_available": ("reference_observation_available",),
    }
    aliases = {}
    for destination, choices in required_aliases.items():
        source_column = _column_by_alias(records, choices)
        if source_column is None and destination != "calibrated_probability":
            raise ExportValidationError(
                f"M8 probability input lacks required field {destination!r}."
            )
        aliases[destination] = source_column
    for destination, source_column in aliases.items():
        if source_column is None:
            records[destination] = pd.NA
        elif source_column != destination:
            records[destination] = records[source_column]
    records["estimated"] = _parse_boolean(records["estimated"], "estimated")
    records["reference_observation_available"] = _parse_boolean(
        records["reference_observation_available"],
        "reference_observation_available",
    )
    if not records["estimated"].all():
        raise ExportValidationError("Fine-scale probability rows must be model estimates.")
    if records["reference_observation_available"].any():
        raise ExportValidationError(
            "Historical estimated rows must have reference_observation_available=false."
        )
    for column in (
        "parent_modis_id",
        "cell_id",
        "observation_datetime",
        "model_name",
        "model_version",
        "preprocessing_version",
    ):
        if records[column].isna().any() or records[column].astype(str).str.strip().eq("").any():
            raise ExportValidationError(f"Fine-scale estimates contain missing {column}.")
    if records["cell_id"].astype(str).str.strip().eq("").any():
        raise ExportValidationError("Fine-scale estimates contain empty cell IDs.")
    dates = pd.to_datetime(records["observation_datetime"], errors="coerce", utc=True)
    if dates.isna().any():
        raise ExportValidationError("Fine-scale estimates contain invalid datetimes.")
    records["observation_datetime"] = dates.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for column in ("cell_center_latitude", "cell_center_longitude"):
        records[column] = pd.to_numeric(records[column], errors="coerce")
    if (
        records["cell_center_latitude"].isna().any()
        or records["cell_center_longitude"].isna().any()
        or not records["cell_center_latitude"].between(-90, 90).all()
        or not records["cell_center_longitude"].between(-180, 180).all()
    ):
        raise ExportValidationError("Fine-scale estimates contain invalid coordinates.")
    for column in ("raw_estimated_probability", "calibrated_probability"):
        records[column] = pd.to_numeric(records[column], errors="coerce")
        present = records[column].notna()
        if (
            not np.isfinite(records.loc[present, column]).all()
            or not records.loc[present, column].between(0, 1).all()
        ):
            raise ExportValidationError(f"{column} must be finite and in [0, 1].")
    if records["raw_estimated_probability"].isna().any():
        raise ExportValidationError(
            "raw_estimated_probability must be present for every estimate."
        )
    records["uncertainty_value"] = pd.to_numeric(
        records["uncertainty_value"], errors="coerce"
    )
    if (
        records["uncertainty_value"].isna().any()
        or not np.isfinite(records["uncertainty_value"]).all()
        or records["uncertainty_value"].lt(0).any()
    ):
        raise ExportValidationError("Uncertainty values must be finite and nonnegative.")
    key = ["parent_modis_id", "cell_id", "observation_datetime"]
    if records.duplicated(key).any():
        raise ExportValidationError("Fine-scale estimate export contains duplicate row keys.")
    if start_date is not None or end_date is not None:
        records = records.loc[
            _date_mask(records["observation_datetime"], start_date, end_date)
        ].copy()
    if "source_latitude" not in records:
        records["source_latitude"] = pd.NA
    if "source_longitude" not in records:
        records["source_longitude"] = pd.NA
    if "grid_resolution_m" in records:
        records["spatial_resolution_m"] = pd.to_numeric(
            records["grid_resolution_m"], errors="coerce"
        )
    elif "spatial_resolution_m" not in records:
        records["spatial_resolution_m"] = pd.NA
    records["spatial_resolution_m"] = pd.to_numeric(
        records["spatial_resolution_m"], errors="coerce"
    )
    if (
        records["spatial_resolution_m"].isna().any()
        or not np.isfinite(records["spatial_resolution_m"]).all()
        or records["spatial_resolution_m"].le(0).any()
    ):
        raise ExportValidationError(
            "Fine-scale estimates require a finite positive spatial resolution."
        )
    for column in (
        "source_file_name",
        "source_file_sha256",
        "source_row_number",
        "source_observation_id",
        "source_latitude",
        "source_longitude",
        "source_timestamp",
        "instrument",
        "satellite",
        "calibration_version",
        "calibration_method",
    ):
        if column not in records:
            records[column] = pd.NA
    return records.reset_index(drop=True), source_files


def _probability_source_json(row: Mapping[str, object]) -> str:
    include = (
        "source_sensor",
        "source_file_name",
        "source_file_sha256",
        "source_row_number",
        "source_observation_id",
        "source_latitude",
        "source_longitude",
        "source_timestamp",
    )
    return json.dumps(
        {name: _json_value(row.get(name)) for name in include},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def export_fine_scale_probabilities(
    probability_input: str | Path,
    output_path: str | Path,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    geojson_path: Optional[str | Path] = None,
    validate_only: bool = False,
) -> Dict[str, object]:
    """Standardize existing M8 prediction CSVs; does not run inference."""
    records, source_files = _validated_probabilities(
        Path(probability_input), start_date=start_date, end_date=end_date
    )
    source_mapping = {
        "parent_modis_id": "parent_modis_id",
        "cell_id": "cell_id",
        "observation_datetime": "observation_datetime",
        "cell_center_latitude": "cell_center_latitude",
        "cell_center_longitude": "cell_center_longitude",
        "raw_estimated_probability": "raw_estimated_probability",
        "calibrated_probability": "calibrated_probability",
        "uncertainty_value": "uncertainty_value",
        "uncertainty_type": "uncertainty_type",
        "uncertainty_method": "uncertainty_method",
        "satellite": "satellite",
        "instrument": "instrument",
        "model_name": "model_name",
        "model_version": "model_version",
        "preprocessing_version": "preprocessing_version",
        "calibration_version": "calibration_version",
        "calibration_method": "calibration_method",
        "source_sensor": "source_sensor",
        "source_file_name": "source_file_name",
        "source_file_sha256": "source_file_sha256",
        "source_row_number": "source_row_number",
        "source_observation_id": "source_observation_id",
        "source_latitude": "source_latitude",
        "source_longitude": "source_longitude",
        "source_timestamp": "source_timestamp",
        "spatial_resolution_m": "spatial_resolution_m",
        "estimated": "estimated",
        "reference_observation_available": "reference_observation_available",
    }
    output = records.rename(columns=source_mapping).copy()
    output["crs"] = "EPSG:4326"
    output["grid_crs"] = "local_aeqd"
    output["cell_boundary_wgs84"] = ""
    output["source_provenance_json"] = [
        _probability_source_json(row)
        for row in output.to_dict(orient="records")
    ]
    geojson_features = []
    geometry_warnings = []
    if not output.empty:
        parsed_cells = []
        for cell_id in output["cell_id"].astype(str):
            match = _CELL_ID.fullmatch(cell_id)
            if match is None:
                raise ExportValidationError(
                    "GeoJSON export requires cell IDs in M8 rNN_cNN form."
                )
            parsed_cells.append(
                (int(match.group("row")), int(match.group("column")))
            )
        if parsed_cells:
            height = max(row for row, _ in parsed_cells) + 1
            width = max(column for _, column in parsed_cells) + 1
            parent_key = ["parent_modis_id", "observation_datetime"]
            for key, group in output.groupby(parent_key, sort=False, dropna=False):
                group_cells = [
                    parsed_cells[index] for index in group.index.to_list()
                ]
                if len(set(group_cells)) != height * width:
                    raise ExportValidationError(
                        "GeoJSON requires a complete, non-duplicated regular target "
                        "grid for each parent observation."
                    )
            for index, row in output.iterrows():
                resolution = pd.to_numeric(
                    pd.Series([row["spatial_resolution_m"]]), errors="coerce"
                ).iloc[0]
                if pd.isna(resolution) or not math.isfinite(float(resolution)) or resolution <= 0:
                    raise ExportValidationError(
                        "GeoJSON cell boundaries require a finite positive spatial resolution."
                    )
                if pd.isna(row["source_latitude"]) or pd.isna(row["source_longitude"]):
                    raise ExportValidationError(
                        "Cell boundaries require the source MODIS coordinates."
                    )
                try:
                    source_latitude = float(row["source_latitude"])
                    source_longitude = float(row["source_longitude"])
                except (TypeError, ValueError) as exc:
                    raise ExportValidationError(
                        "Cell boundaries require valid source MODIS coordinates."
                    ) from exc
                if (
                    not math.isfinite(source_latitude)
                    or not math.isfinite(source_longitude)
                    or not -90 <= source_latitude <= 90
                    or not -180 <= source_longitude <= 180
                ):
                    raise ExportValidationError(
                        "Cell boundaries require valid source MODIS coordinates."
                    )
                coordinates, local_crs = _cell_indices_and_geometry(
                    row,
                    width=width,
                    height=height,
                    resolution_m=float(resolution),
                )
                output.at[index, "cell_boundary_wgs84"] = json.dumps(coordinates)
                output.at[index, "grid_crs"] = local_crs
                properties = {
                    "cell_id": row["cell_id"],
                    "parent_modis_id": row["parent_modis_id"],
                    "observation_datetime": pd.Timestamp(
                        row["observation_datetime"]
                    ).isoformat(),
                    "estimated_probability": float(row["raw_estimated_probability"]),
                    "calibrated_probability": (
                        None
                        if pd.isna(row["calibrated_probability"])
                        else float(row["calibrated_probability"])
                    ),
                    "uncertainty_value": float(row["uncertainty_value"]),
                    "uncertainty_type": row["uncertainty_type"],
                    "uncertainty_method": row["uncertainty_method"],
                    "model_name": row["model_name"],
                    "model_version": row["model_version"],
                    "preprocessing_version": row["preprocessing_version"],
                    "calibration_version": _json_value(row["calibration_version"]),
                    "satellite": _json_value(row["satellite"]),
                    "instrument": _json_value(row["instrument"]),
                    "source_provenance": json.loads(row["source_provenance_json"]),
                    "spatial_resolution_m": float(resolution),
                    "grid_crs": local_crs,
                    "crs": "EPSG:4326",
                    "estimated": True,
                    "reference_observation_available": False,
                }
                polygon = Polygon(coordinates)
                if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
                    raise ExportValidationError("GeoJSON target polygon is invalid.")
                geojson_features.append(
                    {
                        "type": "Feature",
                        "geometry": _geojson_cell_geometry(coordinates),
                        "properties": properties,
                    }
                )
    elif geojson_path is not None:
        geometry_warnings.append("Empty estimate input produced an empty GeoJSON collection.")

    summary = _coverage_summary(
        output,
        "cell_center_latitude",
        "cell_center_longitude",
        "observation_datetime",
    )
    result = {
        "filename": Path(output_path).name,
        "row_count": len(output),
        "columns": list(PROBABILITY_COLUMNS),
        "date_range": {"start": summary["date_start"], "end": summary["date_end"]},
        "geographic_coverage": summary["geographic_bounds"],
        "source_files": source_files,
        "source_file_sha256": {
            str(file_path): _path_sha256(Path(file_path))
            for file_path in source_files
        },
        "geojson_filename": (
            Path(geojson_path).name if geojson_path is not None else None
        ),
        "crs": "EPSG:4326",
        "warnings": geometry_warnings
        + (["No probability records remain after date filtering."] if output.empty else []),
        "validation": {
            "status": "passed",
            "checks": [
                "unique_parent_cell_datetime_key",
                "probability_range",
                "finite_nonnegative_uncertainty",
                "historical_estimate_flags",
                "valid_coordinates",
            ],
        },
    }
    if not validate_only:
        _atomic_csv(Path(output_path), output, PROBABILITY_COLUMNS)
        if geojson_path is not None:
            _write_geojson(Path(geojson_path), geojson_features)
    return result


def _write_geojson(path: Path, features: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "type": "FeatureCollection",
                "name": path.stem,
                "features": list(features),
            },
            stream,
            indent=2,
            allow_nan=False,
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_export_manifest(
    output_dir: str | Path,
    products: Mapping[str, Mapping[str, object]],
    *,
    source_inputs: Optional[Mapping[str, object]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    validate_only: bool = False,
) -> Path:
    """Write a machine-readable run manifest after product exports succeed."""
    destination = Path(output_dir) / "export_manifest.json"
    payload = {
        "export_schema_version": config.EXPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_requested": {
            "start_date": start_date,
            "end_date": end_date,
            "default_start_date": config.TEAM_EXPORT_START_DATE,
            "default_end_date": config.TEAM_EXPORT_END_DATE,
        },
        "source_inputs": dict(source_inputs or {}),
        "crs": "EPSG:4326",
        "products": dict(products),
        "validation_only": validate_only,
    }
    if not validate_only:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    return destination
