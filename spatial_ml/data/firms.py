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


def _parse_date_series(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="coerce")
    unresolved = values.notna() & parsed.isna()
    if unresolved.any():
        parsed.loc[unresolved] = values.loc[unresolved].map(_parse_date)
    if parsed.dt.tz is not None:
        parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
    return parsed.dt.normalize()


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


def _parse_time_series(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.strip()
    numeric = text.str.fullmatch(r"\d{1,4}(?:\.0)?", na=False)
    digits = text.loc[numeric].str.replace(r"\.0$", "", regex=True).str.zfill(4)
    hours = pd.to_numeric(digits.str[:2], errors="coerce")
    minutes = pd.to_numeric(digits.str[2:], errors="coerce")
    valid_numeric = hours.le(23) & minutes.le(59)
    parsed = pd.Series(pd.NA, index=values.index, dtype="object")
    parsed.loc[digits.index[valid_numeric]] = digits.loc[valid_numeric]

    fallback = values.loc[text.notna() & ~numeric]
    if not fallback.empty:
        parsed.loc[fallback.index] = fallback.map(_parse_time)
    return parsed


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

    direct_datetimes = pd.to_datetime(
        result["datetime"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    unresolved_datetimes = result["datetime"].notna() & direct_datetimes.isna()
    if unresolved_datetimes.any():
        direct_datetimes.loc[unresolved_datetimes] = result.loc[
            unresolved_datetimes, "datetime"
        ].map(_parse_datetime)
    source_dates = _parse_date_series(result["date"])
    source_times = _parse_time_series(result["time"])

    missing_dates = source_dates.isna()
    source_dates.loc[missing_dates] = direct_datetimes.loc[missing_dates].map(
        lambda value: value.normalize() if pd.notna(value) else pd.NaT
    )
    missing_times = result["time"].isna()
    source_times.loc[missing_times] = direct_datetimes.loc[missing_times].map(
        lambda value: value.strftime("%H%M") if pd.notna(value) else None
    )

    has_date_and_time = source_dates.notna() & source_times.notna()
    normalized_datetimes = pd.Series(
        pd.NaT, index=result.index, dtype="datetime64[ns]"
    )
    if has_date_and_time.any():
        date_time_strings = (
            source_dates.loc[has_date_and_time].dt.strftime("%Y-%m-%d")
            + " "
            + source_times.loc[has_date_and_time].astype(str)
        )
        normalized_datetimes.loc[has_date_and_time] = pd.to_datetime(
            date_time_strings,
            format="%Y-%m-%d %H%M",
            errors="coerce",
        )

    result["latitude"] = pd.to_numeric(result["latitude"], errors="coerce")
    result["longitude"] = pd.to_numeric(result["longitude"], errors="coerce")
    result["date"] = source_dates
    result["time"] = pd.Series(source_times, index=result.index, dtype="object")
    result["datetime"] = normalized_datetimes
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
