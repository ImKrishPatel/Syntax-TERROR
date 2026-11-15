from pathlib import Path
import json

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = DATA_DIR / "frp_matching_temp"

MODIS_FILE = DATA_DIR / "modis_real_cleaned.csv.gz"
VIIRS_FILE = DATA_DIR / "viirs_real_cleaned.csv.gz"

# Processing configuration
CHUNK_SIZE = 250_000
MAX_DISTANCE_KM = 1.0
MAX_TIME_MINUTES = 60

EARTH_RADIUS_KM = 6371.0

# Columns needed for matching
REQUIRED_COLUMNS = [
    "acq_date",
    "acq_time",
    "latitude",
    "longitude",
    "frp",
]
def partition_sensor_data(input_file, sensor_name):
    """Read a cleaned dataset in chunks and partition it by date."""

    sensor_dir = TEMP_DIR / sensor_name
    sensor_dir.mkdir(parents=True, exist_ok=True)

    # Clear old daily partitions before a fresh run.
    for old_file in sensor_dir.glob("*.csv"):
        old_file.unlink()

    print(f"\nPartitioning {sensor_name}...")

    total_read = 0
    total_saved = 0

    for chunk_number, chunk in enumerate(
        pd.read_csv(
            input_file,
            compression="gzip",
            chunksize=CHUNK_SIZE,
            usecols=REQUIRED_COLUMNS,
            dtype={"acq_time": "string"},
        ),
        start=1,
    ):
        total_read += len(chunk)

        chunk["acq_date"] = pd.to_datetime(
            chunk["acq_date"],
            errors="coerce",
        )

        chunk["acq_time"] = (
            chunk["acq_time"]
            .fillna("")
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
            .str.zfill(4)
        )

        valid_time = chunk["acq_time"].str.fullmatch(
            r"(?:[01]\d|2[0-3])[0-5]\d"
        )

        for column in ["latitude", "longitude", "frp"]:
            chunk[column] = pd.to_numeric(
                chunk[column],
                errors="coerce",
            )

        valid = (
            chunk["acq_date"].notna()
            & valid_time
            & chunk["latitude"].between(-90, 90)
            & chunk["longitude"].between(-180, 180)
            & chunk["frp"].notna()
            & np.isfinite(chunk["frp"])
            & chunk["frp"].ge(0)
        )

        chunk = chunk.loc[valid].copy()

        if chunk.empty:
            continue

        chunk["timestamp"] = pd.to_datetime(
            chunk["acq_date"].dt.strftime("%Y-%m-%d")
            + " "
            + chunk["acq_time"].str[:2]
            + ":"
            + chunk["acq_time"].str[2:],
            errors="coerce",
        )

        chunk = chunk.dropna(subset=["timestamp"])

        for date, daily_data in chunk.groupby(
            chunk["timestamp"].dt.strftime("%Y-%m-%d")
        ):
            output_file = sensor_dir / f"{date}.csv"

            daily_data[
                ["latitude", "longitude", "frp", "timestamp"]
            ].to_csv(
                output_file,
                mode="a",
                header=not output_file.exists(),
                index=False,
            )

            total_saved += len(daily_data)

        print(
            f"  Chunks: {chunk_number} | "
            f"Read: {total_read:,} | "
            f"Saved: {total_saved:,}"
        )

    print(f"\nFinished {sensor_name}: {total_saved:,} rows")

def haversine_km(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two coordinates in km.
    """

    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    c = 2 * np.arcsin(np.sqrt(a))

    return EARTH_RADIUS_KM * c


def get_overlapping_dates():
    """
    Find MODIS dates that have VIIRS observations
    on the same date, previous date, or next date.
    """

    modis_dir = TEMP_DIR / "MODIS"
    viirs_dir = TEMP_DIR / "VIIRS"

    modis_dates = {
        path.stem for path in modis_dir.glob("*.csv")
    }

    viirs_dates = {
        path.stem for path in viirs_dir.glob("*.csv")
    }

    process_dates = []

    for date in sorted(modis_dates):
        current_date = pd.Timestamp(date)

        possible_viirs_dates = {
            (current_date + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in [-1, 0, 1]
        }

        if possible_viirs_dates.intersection(viirs_dates):
            process_dates.append(date)

    print("\nDate overlap report")
    print("-------------------")
    print("MODIS dates:", len(modis_dates))
    print("VIIRS dates:", len(viirs_dates))
    print("Dates eligible for matching:", len(process_dates))

    if process_dates:
        print("First date:", process_dates[0])
        print("Last date:", process_dates[-1])

    return process_dates

def to_xyz(coordinates):
    """Convert latitude and longitude to 3D spherical coordinates."""

    lat = coordinates[:, 0]
    lon = coordinates[:, 1]

    return np.column_stack([
        np.cos(lat) * np.cos(lon),
        np.cos(lat) * np.sin(lon),
        np.sin(lat),
    ])
def load_date_window(sensor_name, target_date):
    """Load daily partitions and assign stable observation IDs."""

    sensor_dir = TEMP_DIR / sensor_name
    target = pd.Timestamp(target_date)

    dates_to_load = [
        (target - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        target.strftime("%Y-%m-%d"),
        (target + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
    ]

    parts = []

    for date in dates_to_load:
        daily_file = sensor_dir / f"{date}.csv"

        if not daily_file.exists():
            continue

        daily_data = pd.read_csv(
            daily_file,
            parse_dates=["timestamp"],
        )

        # Stable ID based on sensor, date and row position.
        daily_data["observation_id"] = [
            f"{sensor_name}_{date}_{i}"
            for i in range(len(daily_data))
        ]

        parts.append(daily_data)

    if not parts:
        return pd.DataFrame(columns=[
            "latitude",
            "longitude",
            "frp",
            "timestamp",
            "observation_id",
        ])

    return pd.concat(parts, ignore_index=True)


def process_matching_date(target_date, used_viirs):
    """Match one MODIS date against a three-day VIIRS window."""

    print(f"\nProcessing {target_date}...")

    modis = load_date_window("MODIS", target_date)
    viirs = load_date_window("VIIRS", target_date)

    target = pd.Timestamp(target_date).date()

    modis = modis[
        modis["timestamp"].dt.date == target
    ].copy()

    if modis.empty or viirs.empty:
        print("No observations available.")
        return pd.DataFrame()

    print(f"MODIS observations: {len(modis):,}")
    print(f"VIIRS observations: {len(viirs):,}")

    matches = match_observations(
        modis,
        viirs,
        target_date,
        used_viirs,
    )

    if not matches.empty:
        used_viirs.update(
            matches["viirs_observation_id"].tolist()
        )

    print(f"Matched pairs: {len(matches):,}")

    return matches

def match_observations(modis, viirs, target_date, used_viirs=None):
    """
    Match MODIS and VIIRS observations using spatial and
    temporal thresholds, with global VIIRS duplicate protection.
    """

    if used_viirs is None:
        used_viirs = set()

    if modis.empty or viirs.empty:
        return pd.DataFrame()

    modis = modis.reset_index(drop=True)
    viirs = viirs.reset_index(drop=True)

    modis_coordinates = np.radians(
        modis[["latitude", "longitude"]].to_numpy()
    )

    viirs_coordinates = np.radians(
        viirs[["latitude", "longitude"]].to_numpy()
    )

    tree = cKDTree(to_xyz(viirs_coordinates))

    radius = 2 * np.sin(
        MAX_DISTANCE_KM / (2 * EARTH_RADIUS_KM)
    )

    nearby = tree.query_ball_point(
        to_xyz(modis_coordinates),
        r=radius,
    )

    candidates = []

    for mi, viirs_indices in enumerate(nearby):
        m = modis.iloc[mi]

        for vi in viirs_indices:
            v = viirs.iloc[vi]

            # Skip VIIRS observations already matched on another date.
            if v["observation_id"] in used_viirs:
                continue

            time_difference = abs(
                (m["timestamp"] - v["timestamp"]).total_seconds()
            ) / 60

            if time_difference > MAX_TIME_MINUTES:
                continue

            distance = haversine_km(
                m["latitude"],
                m["longitude"],
                v["latitude"],
                v["longitude"],
            )

            candidates.append((
                distance,
                time_difference,
                mi,
                vi,
            ))

    candidates.sort(key=lambda item: (item[0], item[1]))

    matched_modis = set()
    matched_viirs = set()
    matches = []

    for distance, time_difference, mi, vi in candidates:
        if mi in matched_modis or vi in matched_viirs:
            continue

        m = modis.iloc[mi]
        v = viirs.iloc[vi]

        matched_modis.add(mi)
        matched_viirs.add(vi)

        matches.append({
            "date": target_date,
            "modis_observation_id": m["observation_id"],
            "viirs_observation_id": v["observation_id"],
            "timestamp_modis": m["timestamp"],
            "timestamp_viirs": v["timestamp"],
            "latitude_modis": m["latitude"],
            "longitude_modis": m["longitude"],
            "latitude_viirs": v["latitude"],
            "longitude_viirs": v["longitude"],
            "frp_modis": m["frp"],
            "frp_viirs": v["frp"],
            "distance_km": distance,
            "time_difference_minutes": time_difference,
        })

    return pd.DataFrame(matches)


def main():
    """Match MODIS and VIIRS detections on the selected calibration dates.

    python shriya_backend/frp_multi_date_matching.py          # dates from select_calibration_dates.py
    python shriya_backend/frp_multi_date_matching.py --all    # every date both sensors cover
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="match every overlapping date")
    args = parser.parse_args()

    # Split the cleaned files into one CSV per day (once).
    for sensor, source in (("MODIS", MODIS_FILE), ("VIIRS", VIIRS_FILE)):
        if not (TEMP_DIR / sensor).exists() or not any((TEMP_DIR / sensor).glob("*.csv")):
            partition_sensor_data(source, sensor)

    if args.all:
        test_dates = get_overlapping_dates()
    else:
        selected = DATA_DIR / "selected_calibration_dates.csv"
        if not selected.exists():
            raise FileNotFoundError(f"{selected} (run select_calibration_dates.py first, or use --all)")
        test_dates = sorted(pd.read_csv(selected)["acq_date"].astype(str))

    test_dates = [d for d in test_dates if (TEMP_DIR / "MODIS" / f"{d}.csv").exists()
                  and (TEMP_DIR / "VIIRS" / f"{d}.csv").exists()]
    if not test_dates:
        raise ValueError("None of the requested dates are covered by both sensors.")

    # These are the files the API serves (/frp/matches, /frp/matching-diagnostics).
    output_path = DATA_DIR / "real_frp_matches_multi_date.csv"
    diagnostics_path = DATA_DIR / "real_frp_matching_diagnostics.json"

    if output_path.exists():
        output_path.unlink()

    used_viirs = set()
    daily_diagnostics = []

    total_modis = 0
    total_matches = 0

    # Process only the five selected dates.
    for target_date in test_dates:

        matches = process_matching_date(
            target_date,
            used_viirs,
        )

        modis_file = TEMP_DIR / "MODIS" / f"{target_date}.csv"

        with open(modis_file, "r") as file:
            daily_modis_count = sum(1 for _ in file) - 1

        daily_match_count = len(matches)

        total_modis += daily_modis_count
        total_matches += daily_match_count

        if not matches.empty:
            matches.to_csv(
                output_path,
                mode="a",
                header=not output_path.exists(),
                index=False,
            )

        daily_diagnostics.append({
            "date": target_date,
            "modis_observations": daily_modis_count,
            "matched_pairs": daily_match_count,
            "unmatched_modis": daily_modis_count - daily_match_count,
        })

        # Retain VIIRS IDs that may be needed by the next date.
        used_viirs = {
            observation_id
            for observation_id in used_viirs
            if observation_id.split("_", 2)[1] >= target_date
        }

        print(
            f"Progress: {target_date} | "
            f"Total matches: {total_matches:,}"
        )

    diagnostics = {
        "test_mode": not args.all,
        "dates_processed": len(test_dates),
        "first_date": test_dates[0],
        "last_date": test_dates[-1],
        "total_modis_observations": total_modis,
        "total_matched_pairs": total_matches,
        "total_unmatched_modis": total_modis - total_matches,
        "match_rate_percent": (
            round(total_matches / total_modis * 100, 2)
            if total_modis else 0
        ),
        "maximum_distance_km": MAX_DISTANCE_KM,
        "maximum_time_difference_minutes": MAX_TIME_MINUTES,
        "daily_results": daily_diagnostics,
    }

    with open(diagnostics_path, "w") as file:
        json.dump(diagnostics, file, indent=4)

    print("\nFRP MATCHING COMPLETED")
    print("------------------------------------")
    print("Dates processed:", len(test_dates))
    print("MODIS observations:", f"{total_modis:,}")
    print("Matched pairs:", f"{total_matches:,}")
    print("Unmatched MODIS:", f"{total_modis - total_matches:,}")
    print("Match rate:", diagnostics["match_rate_percent"], "%")
    print("Output:", output_path)
    print("Diagnostics:", diagnostics_path)


if __name__ == "__main__":
    main()
