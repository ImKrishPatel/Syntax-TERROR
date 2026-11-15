"""Deterministic cleaning and quality summaries for normalized FIRMS records."""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_LOGGER = logging.getLogger(__name__)
_DUPLICATE_KEY = (
    "sensor",
    "latitude",
    "longitude",
    "datetime",
    "satellite",
    "instrument",
)
_CONFIDENCE_LABELS = {"l", "n", "h", "low", "nominal", "high"}
_SOURCE_DUPLICATE_KEY = ("sensor", "source_record_fingerprint")


def _confidence_is_valid(value: object) -> bool:
    if value is None or pd.isna(value):
        return True
    text = str(value).strip().lower()
    if text in _CONFIDENCE_LABELS:
        return True
    try:
        numeric_value = float(text)
    except ValueError:
        return False
    return math.isfinite(numeric_value) and 0 <= numeric_value <= 100


def _require_schema(records: pd.DataFrame) -> None:
    required = {
        "latitude",
        "longitude",
        "datetime",
        "sensor",
        "satellite",
        "instrument",
        "confidence",
        "frp",
        "daynight",
    }
    missing = sorted(required.difference(records.columns))
    if missing:
        raise ValueError(
            "Expected normalized FIRMS records; missing columns: "
            + ", ".join(missing)
        )


def _duplicate_columns(records: pd.DataFrame) -> List[str]:
    if "source_record_fingerprint" in records:
        return [
            column for column in _SOURCE_DUPLICATE_KEY if column in records
        ]
    return [column for column in _DUPLICATE_KEY if column in records]


def clean_firms_records(records: pd.DataFrame) -> pd.DataFrame:
    """Drop invalid coordinates/timestamps and exact identifying duplicates.

    Rows with missing or invalid FRP are retained with ``frp`` set to missing.
    Confidence is never used as a filter: its source value is preserved and
    ``confidence_valid`` flags malformed non-missing values.
    """
    _require_schema(records)
    cleaned = records.copy()

    latitude = pd.to_numeric(cleaned["latitude"], errors="coerce")
    longitude = pd.to_numeric(cleaned["longitude"], errors="coerce")
    valid_coordinates = (
        latitude.notna()
        & longitude.notna()
        & latitude.ge(-90)
        & latitude.le(90)
        & longitude.ge(-180)
        & longitude.le(180)
    )
    invalid_coordinate_count = int((~valid_coordinates).sum())
    cleaned = cleaned.loc[valid_coordinates].copy()
    cleaned["latitude"] = latitude.loc[valid_coordinates]
    cleaned["longitude"] = longitude.loc[valid_coordinates]
    if invalid_coordinate_count:
        _LOGGER.info(
            "Removed %d FIRMS records with missing or out-of-range coordinates.",
            invalid_coordinate_count,
        )

    datetimes = pd.to_datetime(cleaned["datetime"], errors="coerce")
    valid_datetimes = datetimes.notna()
    invalid_datetime_count = int((~valid_datetimes).sum())
    cleaned = cleaned.loc[valid_datetimes].copy()
    cleaned["datetime"] = datetimes.loc[valid_datetimes]
    if invalid_datetime_count:
        _LOGGER.info(
            "Removed %d FIRMS records with missing or malformed date/time.",
            invalid_datetime_count,
        )

    raw_frp = cleaned["frp"]
    numeric_frp = pd.to_numeric(raw_frp, errors="coerce")
    missing_frp = raw_frp.isna() | raw_frp.astype(str).str.strip().eq("")
    valid_frp = numeric_frp.notna() & np.isfinite(numeric_frp) & numeric_frp.ge(0)
    invalid_frp_count = int((~missing_frp & ~valid_frp).sum())
    cleaned["frp"] = numeric_frp.where(valid_frp)
    if invalid_frp_count:
        _LOGGER.info(
            "Set %d invalid/non-numeric FIRMS FRP values to missing.",
            invalid_frp_count,
        )

    confidence_valid = cleaned["confidence"].map(_confidence_is_valid)
    confidence_valid = confidence_valid.astype(bool)
    cleaned["confidence_valid"] = confidence_valid
    invalid_confidence_count = int((~confidence_valid).sum())
    if invalid_confidence_count:
        _LOGGER.info(
            "Flagged %d invalid FIRMS confidence values; records were retained.",
            invalid_confidence_count,
        )

    duplicate_columns = _duplicate_columns(cleaned)
    duplicate_count = int(cleaned.duplicated(subset=duplicate_columns).sum())
    cleaned = cleaned.drop_duplicates(subset=duplicate_columns, keep="first")
    if duplicate_count:
        _LOGGER.info("Removed %d duplicate FIRMS records.", duplicate_count)

    return cleaned.reset_index(drop=True)


def _duplicate_count(records: pd.DataFrame) -> int:
    columns = _duplicate_columns(records)
    if not columns:
        return 0
    return int(records.duplicated(subset=columns).sum())


def _range(values: pd.Series) -> Optional[Tuple[Any, Any]]:
    non_missing = values.dropna()
    if non_missing.empty:
        return None
    return non_missing.min(), non_missing.max()


def quality_control_summary(
    raw_records: pd.DataFrame,
    cleaned_records: pd.DataFrame,
    sensor: Optional[str] = None,
) -> Dict[str, Any]:
    """Return record counts, ranges, missingness, duplicates, and sensor stats."""
    _require_schema(raw_records)
    _require_schema(cleaned_records)

    clean_latitude = pd.to_numeric(cleaned_records["latitude"], errors="coerce")
    clean_longitude = pd.to_numeric(cleaned_records["longitude"], errors="coerce")
    datetime_values = pd.to_datetime(cleaned_records["datetime"], errors="coerce")
    sensor_values = cleaned_records["sensor"].dropna().astype(str).unique()
    sensor_name = sensor or (
        sensor_values[0] if len(sensor_values) == 1 else None
    )
    satellites = cleaned_records["satellite"].dropna().astype(str).unique()
    daynight_counts = (
        cleaned_records["daynight"].dropna().astype(str).value_counts().to_dict()
    )
    raw_frp = raw_records["frp"]
    numeric_raw_frp = pd.to_numeric(raw_frp, errors="coerce")
    missing_raw_frp = raw_frp.isna() | raw_frp.astype(str).str.strip().eq("")
    invalid_frp_count = int(
        (
            ~missing_raw_frp
            & (
                numeric_raw_frp.isna()
                | ~np.isfinite(numeric_raw_frp)
                | numeric_raw_frp.lt(0)
            )
        ).sum()
    )
    invalid_confidence_count = int(
        (~raw_records["confidence"].map(_confidence_is_valid)).sum()
    )
    raw_count = len(raw_records)
    raw_latitude = pd.to_numeric(raw_records["latitude"], errors="coerce")
    raw_longitude = pd.to_numeric(raw_records["longitude"], errors="coerce")
    raw_datetimes = pd.to_datetime(raw_records["datetime"], errors="coerce")

    return {
        "raw_records": raw_count,
        "valid_records": len(cleaned_records),
        "removed_records": raw_count - len(cleaned_records),
        "percentage_retained": (
            100.0 * len(cleaned_records) / raw_count if raw_count else 0.0
        ),
        "raw_latitude_range": _range(raw_latitude),
        "raw_longitude_range": _range(raw_longitude),
        "raw_date_range": _range(raw_datetimes),
        "latitude_range": _range(clean_latitude),
        "longitude_range": _range(clean_longitude),
        "date_range": _range(datetime_values),
        "raw_missing_frp_count": int(missing_raw_frp.sum()),
        "missing_frp_count": int(cleaned_records["frp"].isna().sum()),
        "raw_missing_confidence_count": int(raw_records["confidence"].isna().sum()),
        "missing_confidence_count": int(cleaned_records["confidence"].isna().sum()),
        "invalid_frp_count": invalid_frp_count,
        "invalid_confidence_count": invalid_confidence_count,
        "duplicate_count": _duplicate_count(raw_records),
        "sensor": sensor_name,
        "unique_satellites": sorted(satellites.tolist()),
        "day_night_counts": daynight_counts,
    }


def _datetime_bounds(
    records: pd.DataFrame,
) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    if "datetime" not in records:
        return None, None
    datetimes = pd.to_datetime(records["datetime"], errors="coerce").dropna()
    if datetimes.empty:
        return None, None
    return datetimes.min(), datetimes.max()


def temporal_range_summary(
    modis_records: pd.DataFrame, viirs_records: pd.DataFrame
) -> Dict[str, Any]:
    """Report observed MODIS/VIIRS datetime ranges and their temporal overlap."""
    modis_earliest, modis_latest = _datetime_bounds(modis_records)
    viirs_earliest, viirs_latest = _datetime_bounds(viirs_records)
    has_ranges = all(
        value is not None
        for value in (modis_earliest, modis_latest, viirs_earliest, viirs_latest)
    )
    overlap_start = max(modis_earliest, viirs_earliest) if has_ranges else None
    overlap_end = min(modis_latest, viirs_latest) if has_ranges else None
    overlaps = bool(has_ranges and overlap_start <= overlap_end)
    return {
        "MODIS": {
            "earliest_datetime": modis_earliest,
            "latest_datetime": modis_latest,
        },
        "VIIRS": {
            "earliest_datetime": viirs_earliest,
            "latest_datetime": viirs_latest,
        },
        "overlap": {
            "overlaps": overlaps,
            "earliest_datetime": overlap_start if overlaps else None,
            "latest_datetime": overlap_end if overlaps else None,
        },
    }
