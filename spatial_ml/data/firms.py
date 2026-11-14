"""Local CSV loading and schema normalization for FIRMS observations."""

from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

SUPPORTED_SENSORS = {"MODIS", "VIIRS"}

_COLUMN_ALIASES: Dict[str, List[str]] = {
    "latitude": ["latitude", "lat"],
    "longitude": ["longitude", "lon", "long"],
    "date": ["date", "acq_date", "acquisition_date"],
    "time": ["time", "acq_time", "acquisition_time"],
    "datetime": ["datetime", "acq_datetime", "acquisition_datetime", "timestamp"],
    "satellite": ["satellite", "sat", "platform"],
    "instrument": ["instrument", "sensor_instrument"],
    "confidence": ["confidence", "conf"],
    "frp": ["frp", "fire_radiative_power"],
    "daynight": ["daynight", "day_night", "day_night_flag"],
}
_STANDARD_COLUMNS = (
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
)


def _validate_sensor(sensor: str) -> str:
    normalized_sensor = sensor.strip().upper()
    if normalized_sensor not in SUPPORTED_SENSORS:
        raise ValueError(
            f"Unsupported sensor {sensor!r}; expected MODIS or VIIRS."
        )
    return normalized_sensor


def _column_series(frame: pd.DataFrame, aliases: List[str]) -> pd.Series:
    columns_by_name = {
        str(column).strip().lstrip("\ufeff").lower(): column
        for column in frame.columns
    }
    matches = [
        columns_by_name[alias]
        for alias in aliases
        if alias in columns_by_name
    ]
    if not matches:
        return pd.Series(pd.NA, index=frame.index, dtype="object")

    values = frame[matches[0]].copy()
    for column in matches[1:]:
        values = values.combine_first(frame[column])
    return values


def _parse_date(value: object) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return pd.NaT
    if isinstance(value, (date, datetime, pd.Timestamp)):
        parsed = pd.Timestamp(value)
    else:
        text = str(value).strip()
        if not text:
            return pd.NaT
        parsed = pd.NaT
        for date_format in (
            "%Y-%m-%d",
            "%Y%m%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%Y/%m/%d",
            "%d-%b-%Y",
        ):
            try:
                parsed = pd.Timestamp(datetime.strptime(text, date_format))
                break
            except ValueError:
                continue
        if pd.isna(parsed):
            parsed = pd.to_datetime(text, errors="coerce")

    if pd.isna(parsed):
        return pd.NaT
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed.normalize()


def _parse_time(value: object) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, time):
        hour, minute = value.hour, value.minute
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        if ":" in text:
            try:
                parsed_time = datetime.strptime(text, "%H:%M")
            except ValueError:
                try:
                    parsed_time = datetime.strptime(text, "%H:%M:%S")
                except ValueError:
                    return None
            hour, minute = parsed_time.hour, parsed_time.minute
        elif text.isdigit() and len(text) <= 4:
            padded = text.zfill(4)
            hour, minute = int(padded[:2]), int(padded[2:])
        else:
            return None

    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}{minute:02d}"


def _parse_datetime(value: object) -> pd.Timestamp:
    if value is None or pd.isna(value):
        return pd.NaT
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT
    if pd.isna(parsed):
        return pd.NaT
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return parsed


def normalize_firms_schema(frame: pd.DataFrame, sensor: str) -> pd.DataFrame:
    """Normalize FIRMS column names while retaining source columns and rows."""
    normalized_sensor = _validate_sensor(sensor)
    result = frame.copy()

    for canonical, aliases in _COLUMN_ALIASES.items():
        result[canonical] = _column_series(frame, aliases)

    direct_datetimes = result["datetime"].map(_parse_datetime)
    source_dates = result["date"].map(_parse_date)
    source_times = result["time"].map(_parse_time)

    missing_dates = source_dates.isna()
    source_dates.loc[missing_dates] = direct_datetimes.loc[missing_dates].map(
        lambda value: value.normalize() if pd.notna(value) else pd.NaT
    )
    missing_times = result["time"].isna()
    source_times.loc[missing_times] = direct_datetimes.loc[missing_times].map(
        lambda value: value.strftime("%H%M") if pd.notna(value) else None
    )

    normalized_datetimes = []
    for position, (parsed_date, parsed_time, direct_datetime) in enumerate(
        zip(source_dates, source_times, direct_datetimes)
    ):
        if (
            pd.notna(parsed_date)
            and parsed_time is not None
            and not pd.isna(parsed_time)
        ):
            normalized_datetimes.append(
                pd.Timestamp(
                    datetime.combine(
                        parsed_date.date(),
                        datetime.strptime(parsed_time, "%H%M").time(),
                    )
                )
            )
        elif pd.isna(result["date"].iloc[position]) and pd.isna(
            result["time"].iloc[position]
        ):
            normalized_datetimes.append(direct_datetime)
        else:
            normalized_datetimes.append(pd.NaT)

    result["latitude"] = pd.to_numeric(result["latitude"], errors="coerce")
    result["longitude"] = pd.to_numeric(result["longitude"], errors="coerce")
    result["date"] = source_dates
    result["time"] = pd.Series(source_times, index=result.index, dtype="object")
    result["datetime"] = pd.to_datetime(normalized_datetimes, errors="coerce")
    result["sensor"] = normalized_sensor

    for column in _STANDARD_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA
    return result


def load_firms_csv(path: Union[str, Path], sensor: str) -> pd.DataFrame:
    """Read and normalize a local FIRMS CSV without applying cleaning rules."""
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"FIRMS CSV file does not exist: {csv_path}")
    try:
        raw_records = pd.read_csv(csv_path, dtype=str)
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise ValueError(f"Could not read FIRMS CSV file {csv_path}: {exc}") from exc
    return normalize_firms_schema(raw_records, sensor)


def load_modis(path: Union[str, Path]) -> pd.DataFrame:
    """Load a local MODIS FIRMS CSV."""
    return load_firms_csv(path, "MODIS")


def load_viirs(path: Union[str, Path]) -> pd.DataFrame:
    """Load a local VIIRS FIRMS CSV."""
    return load_firms_csv(path, "VIIRS")
