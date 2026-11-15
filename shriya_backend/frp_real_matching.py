
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)

DATA = Path("shriya_backend/data")

TARGET_DATE = "2024-08-10"
MAX_DISTANCE_KM = 1.0
MAX_TIME_MINUTES = 60
EARTH_RADIUS_KM = 6371.0
CHUNK_SIZE = 500_000


def load_one_day(path):
    """Load observations for one selected date."""

    parts = []

    for chunk in pd.read_csv(
        path,
        compression="gzip",
        chunksize=CHUNK_SIZE,
        usecols=[
            "acq_date",
            "acq_time",
            "latitude",
            "longitude",
            "frp",
        ],
        dtype={"acq_time": "string"},
    ):
        selected = chunk[
            chunk["acq_date"] == TARGET_DATE
        ].copy()

        if selected.empty:
            continue

        selected["acq_time"] = (
            selected["acq_time"]
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(4)
        )

        selected["hour"] = pd.to_numeric(
            selected["acq_time"].str[:2],
            errors="coerce",
        )

        selected["minute"] = pd.to_numeric(
            selected["acq_time"].str[2:],
            errors="coerce",
        )

        selected["time_minutes"] = (
            selected["hour"] * 60 + selected["minute"]
        )

        selected = selected.dropna(
            subset=[
                "latitude",
                "longitude",
                "frp",
                "time_minutes",
            ]
        )

        selected = selected[
            selected["hour"].between(0, 23)
            & selected["minute"].between(0, 59)
            & selected["frp"].ge(0)
        ]

        parts.append(
            selected[
                [
                    "latitude",
                    "longitude",
                    "frp",
                    "time_minutes",
                ]
            ]
        )

    if not parts:
        return pd.DataFrame(
            columns=[
                "latitude",
                "longitude",
                "frp",
                "time_minutes",
            ]
        )

    return pd.concat(parts, ignore_index=True)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = np.radians(
        [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    return 2 * EARTH_RADIUS_KM * np.arcsin(
        np.sqrt(a)
    )


def to_xyz(coordinates):
    lat = coordinates[:, 0]
    lon = coordinates[:, 1]

    return np.column_stack([
        np.cos(lat) * np.cos(lon),
        np.cos(lat) * np.sin(lon),
        np.sin(lat),
    ])


def match_observations(modis, viirs):
    if modis.empty or viirs.empty:
        return pd.DataFrame()

    modis_rad = np.radians(
        modis[["latitude", "longitude"]].to_numpy()
    )

    viirs_rad = np.radians(
        viirs[["latitude", "longitude"]].to_numpy()
    )

    tree = cKDTree(to_xyz(viirs_rad))

    radius = 2 * np.sin(
        MAX_DISTANCE_KM / (2 * EARTH_RADIUS_KM)
    )

    nearby = tree.query_ball_point(
        to_xyz(modis_rad),
        r=radius,
    )

    candidates = []

    for modis_idx, viirs_indices in enumerate(nearby):
        if not viirs_indices:
            continue

        m = modis.iloc[modis_idx]

        for viirs_idx in viirs_indices:
            v = viirs.iloc[viirs_idx]

            time_difference = abs(
                m["time_minutes"] - v["time_minutes"]
            )

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
                modis_idx,
                viirs_idx,
            ))

    candidates.sort(key=lambda x: (x[0], x[1]))

    used_modis = set()
    used_viirs = set()
    matches = []

    for distance, time_difference, mi, vi in candidates:
        if mi in used_modis or vi in used_viirs:
            continue

        used_modis.add(mi)
        used_viirs.add(vi)

        m = modis.iloc[mi]
        v = viirs.iloc[vi]

        matches.append({
            "date": TARGET_DATE,
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


if __name__ == "__main__":
    print("Loading MODIS observations...")

    modis = load_one_day(
        DATA / "modis_real_cleaned.csv.gz"
    )

    print("MODIS observations:", f"{len(modis):,}")

    print("\nLoading VIIRS observations...")

    viirs = load_one_day(
        DATA / "viirs_real_cleaned.csv.gz"
    )

    print("VIIRS observations:", f"{len(viirs):,}")

    print("\nMatching observations...")

    matches = match_observations(modis, viirs)

    output_path = DATA / "real_frp_matches_2024-08-10.csv"
    matches.to_csv(output_path, index=False)

    print("\nMatching results")
    print("Matched pairs:", f"{len(matches):,}")
    print("Saved:", output_path)

    if len(matches) >= 30:
        X = matches[["frp_viirs"]]
        y = matches["frp_modis"]

        model = LinearRegression()
        model.fit(X, y)

        predictions = model.predict(X)

        print("\nExploratory calibration fit")
        print("Intercept:", round(model.intercept_, 5))
        print("Slope:", round(model.coef_[0], 5))
        print("R-squared:", round(r2_score(y, predictions), 5))

        print(
            "MAE:",
            round(mean_absolute_error(y, predictions), 5),
        )

        print(
            "RMSE:",
            round(np.sqrt(mean_squared_error(y, predictions)), 5),
        )

        print("\nWARNING: This is an in-sample, one-day pilot fit.")
    else:
        print("Too few matches for an exploratory fit.")

    print("\nReal MODIS-VIIRS matching pilot completed.")
