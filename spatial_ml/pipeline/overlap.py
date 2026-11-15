"""Temporal and spatial matching for cleaned MODIS and VIIRS observations."""

import hashlib
import json
import math
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

import numpy as np
import pandas as pd
from pyproj import Geod
from scipy.spatial import cKDTree

from spatial_ml import config

_OBSERVATION_FIELDS = (
    "latitude",
    "longitude",
    "datetime",
    "frp",
    "confidence",
    "satellite",
    "instrument",
)
_IDENTITY_FIELDS = (
    "datetime",
    "latitude",
    "longitude",
    "satellite",
    "instrument",
)
_SOURCE_PROVENANCE_FIELDS = (
    "source_archive",
    "source_file",
    "source_row_number",
    "source_record_fingerprint",
)
_REQUIRED_COLUMNS = {"sensor", *_OBSERVATION_FIELDS}
_WGS84 = Geod(ellps="WGS84")
_MIN_WGS84_CURVATURE_RADIUS_M = 6_335_439.327


class OverlapPeriod(NamedTuple):
    """Inclusive intersection of the observed MODIS and VIIRS time ranges."""

    overlap_start: pd.Timestamp
    overlap_end: pd.Timestamp


def _datetime_values(records: pd.DataFrame, label: str) -> pd.Series:
    if "datetime" not in records:
        raise ValueError(f"{label} records must contain a 'datetime' column.")
    values = pd.to_datetime(records["datetime"], errors="coerce", utc=True)
    values = values.dt.tz_localize(None)
    if values.isna().any():
        raise ValueError(
            f"{label} records contain missing or invalid datetimes; "
            "clean them before matching."
        )
    return values


def get_overlap_period(
    modis_records: pd.DataFrame, viirs_records: pd.DataFrame
) -> OverlapPeriod:
    """Return observed time-range intersection, or raise if none can be found."""
    if modis_records.empty or viirs_records.empty:
        raise ValueError("Cannot determine overlap from an empty sensor dataset.")

    modis_times = _datetime_values(modis_records, "MODIS")
    viirs_times = _datetime_values(viirs_records, "VIIRS")
    overlap_start = max(modis_times.min(), viirs_times.min())
    overlap_end = min(modis_times.max(), viirs_times.max())
    if overlap_start > overlap_end:
        raise ValueError(
            "MODIS and VIIRS datetime ranges do not overlap "
            f"(MODIS: {modis_times.min()} to {modis_times.max()}; "
            f"VIIRS: {viirs_times.min()} to {viirs_times.max()})."
        )
    return OverlapPeriod(overlap_start, overlap_end)


def _identity_value(field: str, value: object) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    if field in {"latitude", "longitude"}:
        return format(float(value), ".17g")
    if field == "datetime":
        return pd.Timestamp(value).isoformat()
    return str(value).strip()


def generate_observation_ids(
    records: pd.DataFrame, sensor: str
) -> pd.Series:
    """Create reproducible sensor-prefixed SHA-256 IDs from FIRMS identity fields."""
    normalized_sensor = sensor.strip().upper()
    if normalized_sensor not in {"MODIS", "VIIRS"}:
        raise ValueError(f"Unsupported sensor {sensor!r}; expected MODIS or VIIRS.")
    missing = sorted(set(_IDENTITY_FIELDS).difference(records.columns))
    if missing:
        raise ValueError(
            "Cannot generate observation IDs; missing columns: "
            + ", ".join(missing)
        )

    identity_fields = _IDENTITY_FIELDS
    if "source_record_fingerprint" in records.columns:
        identity_fields = (*identity_fields, "source_record_fingerprint")

    ids = []
    for _, record in records.iterrows():
        identity = [
            normalized_sensor,
            *(
                _identity_value(field, record[field])
                for field in identity_fields
            ),
        ]
        encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        ids.append(f"{normalized_sensor.lower()}_{digest}")
    return pd.Series(ids, index=records.index, dtype="string")


def _prepare_observations(records: pd.DataFrame, sensor: str) -> pd.DataFrame:
    missing = sorted(_REQUIRED_COLUMNS.difference(records.columns))
    if missing:
        raise ValueError(
            f"{sensor} input must use the normalized FIRMS schema; missing "
            "columns: "
            + ", ".join(missing)
        )
    if records.empty:
        return records.copy().reset_index(drop=True)

    prepared = records.copy().reset_index(drop=True)
    sensor_values = prepared["sensor"]
    if sensor_values.isna().any() or sensor_values.ne(sensor).any():
        raise ValueError(
            f"{sensor} input must have {sensor!r} in every 'sensor' value."
        )

    prepared["datetime"] = _datetime_values(prepared, sensor)
    for coordinate, minimum, maximum in (
        ("latitude", -90.0, 90.0),
        ("longitude", -180.0, 180.0),
    ):
        prepared[coordinate] = pd.to_numeric(
            prepared[coordinate], errors="coerce"
        )
        if (
            prepared[coordinate].isna().any()
            or not np.isfinite(prepared[coordinate]).all()
            or not prepared[coordinate].between(minimum, maximum).all()
        ):
            raise ValueError(
                f"{sensor} input contains invalid {coordinate} values; "
                "clean it before matching."
            )

    prepared["observation_id"] = generate_observation_ids(prepared, sensor)
    if prepared["observation_id"].duplicated().any():
        raise ValueError(
            f"{sensor} input contains duplicate observation IDs; "
            "remove duplicate records before matching."
        )
    return prepared


def _unit_sphere_coordinates(
    latitudes: np.ndarray, longitudes: np.ndarray
) -> np.ndarray:
    latitude_radians = np.radians(latitudes)
    longitude_radians = np.radians(longitudes)
    cos_latitude = np.cos(latitude_radians)
    return np.column_stack(
        (
            cos_latitude * np.cos(longitude_radians),
            cos_latitude * np.sin(longitude_radians),
            np.sin(latitude_radians),
        )
    )


def _candidate_search_radius(radius_m: float) -> float:
    """Return a conservative unit-sphere chord radius for cKDTree queries."""
    central_angle = min(radius_m / _MIN_WGS84_CURVATURE_RADIUS_M, math.pi)
    return 2.0 * math.sin(central_angle / 2.0)


def _local_aeqd_coordinates(
    azimuth_degrees: float, distance_m: float
) -> Tuple[float, float]:
    """Convert geodesic azimuth/distance to local AEQD east/north meters."""
    azimuth_radians = math.radians(azimuth_degrees)
    return (
        distance_m * math.sin(azimuth_radians),
        distance_m * math.cos(azimuth_radians),
    )


def _empty_pair_record(modis_record: pd.Series) -> Dict[str, object]:
    record: Dict[str, object] = {
        "modis_id": modis_record["observation_id"],
        "viirs_id": pd.NA,
    }
    for field in _OBSERVATION_FIELDS:
        record[f"modis_{field}"] = modis_record[field]
        record[f"viirs_{field}"] = pd.NA
    for field in _SOURCE_PROVENANCE_FIELDS:
        record[f"modis_{field}"] = modis_record.get(field, pd.NA)
        record[f"viirs_{field}"] = pd.NA
    record.update(
        {
            "spatial_distance_m": pd.NA,
            "temporal_difference_minutes": pd.NA,
            "viirs_candidate_count": 0,
            "delta_x_m": pd.NA,
            "delta_y_m": pd.NA,
            "match_quality": "invalid",
            "match_valid": False,
        }
    )
    return record


def _candidate_record(
    modis_record: pd.Series,
    viirs_record: pd.Series,
    distance_m: float,
    time_difference_minutes: float,
    delta_x_m: float,
    delta_y_m: float,
) -> Dict[str, object]:
    record: Dict[str, object] = {
        "modis_id": modis_record["observation_id"],
        "viirs_id": viirs_record["observation_id"],
    }
    for field in _OBSERVATION_FIELDS:
        record[f"modis_{field}"] = modis_record[field]
        record[f"viirs_{field}"] = viirs_record[field]
    for field in _SOURCE_PROVENANCE_FIELDS:
        record[f"modis_{field}"] = modis_record.get(field, pd.NA)
        record[f"viirs_{field}"] = viirs_record.get(field, pd.NA)
    record.update(
        {
            "distance_m": distance_m,
            "time_difference_minutes": time_difference_minutes,
            "delta_x_m": delta_x_m,
            "delta_y_m": delta_y_m,
            "valid": True,
        }
    )
    return record


def _pair_columns() -> List[str]:
    return [
        "modis_id",
        "viirs_id",
        *(f"modis_{field}" for field in _OBSERVATION_FIELDS),
        *(f"viirs_{field}" for field in _OBSERVATION_FIELDS),
        *(f"modis_{field}" for field in _SOURCE_PROVENANCE_FIELDS),
        *(f"viirs_{field}" for field in _SOURCE_PROVENANCE_FIELDS),
        "spatial_distance_m",
        "temporal_difference_minutes",
        "viirs_candidate_count",
        "delta_x_m",
        "delta_y_m",
        "match_quality",
        "match_valid",
    ]


def _candidate_columns() -> List[str]:
    return [
        "modis_id",
        "viirs_id",
        *(f"modis_{field}" for field in _OBSERVATION_FIELDS),
        *(f"viirs_{field}" for field in _OBSERVATION_FIELDS),
        *(f"modis_{field}" for field in _SOURCE_PROVENANCE_FIELDS),
        *(f"viirs_{field}" for field in _SOURCE_PROVENANCE_FIELDS),
        "distance_m",
        "time_difference_minutes",
        "delta_x_m",
        "delta_y_m",
        "valid",
    ]


def generate_overlap_dataset(
    modis_records: pd.DataFrame,
    viirs_records: pd.DataFrame,
    time_tolerance_minutes: Optional[float] = None,
    spatial_radius_m: Optional[float] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build deterministic nearest-pair and all-valid-candidate tables.

    Inputs must be normalized, cleaned FIRMS records. Temporal candidates must
    be on the same acquisition date and within the configured absolute time
    tolerance. A 3D unit-sphere cKDTree narrows geographic candidates globally;
    WGS84 geodesic distance then enforces the radius exactly.
    """
    tolerance = (
        config.DEFAULT_TIME_TOLERANCE_MINUTES
        if time_tolerance_minutes is None
        else float(time_tolerance_minutes)
    )
    radius = (
        config.SPATIAL_MATCH_RADIUS_M
        if spatial_radius_m is None
        else float(spatial_radius_m)
    )
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("time_tolerance_minutes must be a finite non-negative value.")
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("spatial_radius_m must be a finite non-negative value.")

    modis = _prepare_observations(modis_records, "MODIS")
    viirs = _prepare_observations(viirs_records, "VIIRS")
    period = get_overlap_period(modis, viirs)
    first_overlap_date = period.overlap_start.date()
    last_overlap_date = period.overlap_end.date()
    modis = modis.loc[
        modis["datetime"].dt.date.between(first_overlap_date, last_overlap_date)
    ].reset_index(drop=True)
    viirs = viirs.loc[
        viirs["datetime"].dt.date.between(first_overlap_date, last_overlap_date)
    ].reset_index(drop=True)

    viirs_by_day = {}
    if not viirs.empty:
        for day, day_records in viirs.groupby(viirs["datetime"].dt.date, sort=True):
            positions = day_records.index.to_numpy(dtype=int)
            unit_coordinates = _unit_sphere_coordinates(
                viirs.loc[positions, "latitude"].to_numpy(dtype=float),
                viirs.loc[positions, "longitude"].to_numpy(dtype=float),
            )
            viirs_by_day[day] = (positions, cKDTree(unit_coordinates))

    pair_rows: List[Dict[str, object]] = []
    candidate_rows: List[Dict[str, object]] = []
    search_radius = _candidate_search_radius(radius)
    for _, modis_record in modis.iterrows():
        pair = _empty_pair_record(modis_record)
        day_index = viirs_by_day.get(modis_record["datetime"].date())
        matches = []
        if day_index is not None:
            positions, tree = day_index
            modis_unit = _unit_sphere_coordinates(
                np.asarray([float(modis_record["latitude"])]),
                np.asarray([float(modis_record["longitude"])]),
            )[0]
            local_candidates = tree.query_ball_point(
                modis_unit, search_radius, eps=0.0
            )
            for local_index in local_candidates:
                viirs_record = viirs.loc[positions[local_index]]
                time_difference = abs(
                    (
                        viirs_record["datetime"] - modis_record["datetime"]
                    ).total_seconds()
                ) / 60.0
                if time_difference > tolerance:
                    continue

                azimuth, _, distance_m = _WGS84.inv(
                    float(modis_record["longitude"]),
                    float(modis_record["latitude"]),
                    float(viirs_record["longitude"]),
                    float(viirs_record["latitude"]),
                )
                if distance_m > radius:
                    continue

                delta_x_m, delta_y_m = _local_aeqd_coordinates(
                    azimuth, distance_m
                )
                matches.append(
                    _candidate_record(
                        modis_record,
                        viirs_record,
                        float(distance_m),
                        float(time_difference),
                        float(delta_x_m),
                        float(delta_y_m),
                    )
                )

        matches.sort(
            key=lambda match: (
                match["distance_m"],
                match["time_difference_minutes"],
                match["viirs_id"],
            )
        )
        pair["viirs_candidate_count"] = len(matches)
        if matches:
            nearest = matches[0]
            pair["viirs_id"] = nearest["viirs_id"]
            for field in _OBSERVATION_FIELDS:
                pair[f"viirs_{field}"] = nearest[f"viirs_{field}"]
            for field in _SOURCE_PROVENANCE_FIELDS:
                pair[f"viirs_{field}"] = nearest[f"viirs_{field}"]
            pair["spatial_distance_m"] = nearest["distance_m"]
            pair["temporal_difference_minutes"] = nearest[
                "time_difference_minutes"
            ]
            pair["delta_x_m"] = nearest["delta_x_m"]
            pair["delta_y_m"] = nearest["delta_y_m"]
            pair["match_quality"] = "valid"
            pair["match_valid"] = True
            candidate_rows.extend(matches)
        pair_rows.append(pair)

    pairs = pd.DataFrame(pair_rows, columns=_pair_columns())
    candidates = pd.DataFrame(candidate_rows, columns=_candidate_columns())
    if not pairs.empty:
        pairs = pairs.sort_values("modis_id", kind="mergesort").reset_index(drop=True)
    if not candidates.empty:
        candidates = candidates.sort_values(
            ["modis_id", "distance_m", "viirs_id"], kind="mergesort"
        ).reset_index(drop=True)
    return pairs, candidates


def save_overlap_tables(
    pairs: pd.DataFrame,
    candidates: pd.DataFrame,
    output_dir: Union[str, Path] = config.OVERLAP_DATA_DIR,
) -> Tuple[Path, Path]:
    """Explicitly save overlap tables as CSV under the requested directory."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    pair_path = destination / "modis_viirs_pairs.csv"
    candidate_path = destination / "modis_viirs_candidates.csv"
    pairs.to_csv(pair_path, index=False)
    candidates.to_csv(candidate_path, index=False)
    return pair_path, candidate_path
