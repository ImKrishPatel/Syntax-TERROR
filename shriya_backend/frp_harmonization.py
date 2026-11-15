
import pandas as pd
import numpy as np
import json

from pathlib import Path
from scipy.spatial import cKDTree

from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    mean_squared_error,
    mean_absolute_error,
    r2_score
)

# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Matching configuration
MAX_DISTANCE_KM = 1.0
MAX_TIME_DIFFERENCE_MIN = 60
EARTH_RADIUS_KM = 6371.0


def load_cleaned_data():
    """Load cleaned MODIS and VIIRS datasets."""

    modis_path = DATA_DIR / "modis_cleaned.csv"
    viirs_path = DATA_DIR / "viirs_cleaned.csv"

    if not modis_path.exists() or not viirs_path.exists():
        raise FileNotFoundError(
            "Cleaned MODIS or VIIRS data not found."
        )

    modis = pd.read_csv(modis_path)
    viirs = pd.read_csv(viirs_path)

    print("MODIS records:", len(modis))
    print("VIIRS records:", len(viirs))

    return modis, viirs


def prepare_data(modis, viirs):
    """Prepare datasets for comparison."""

    modis = modis.copy()
    viirs = viirs.copy()

    for df in [modis, viirs]:
        df["acq_date"] = pd.to_datetime(
            df["acq_date"], errors="coerce"
        )

        for column in ["latitude", "longitude", "frp"]:
            df[column] = pd.to_numeric(
                df[column], errors="coerce"
            )

        df["acq_time"] = (
            df["acq_time"].astype(str).str.zfill(4)
        )

    return modis, viirs


def time_to_minutes(value):
    """Convert FIRMS HHMM time to minutes after midnight."""

    try:
        value = str(value).strip().zfill(4)

        hour = int(value[:-2])
        minute = int(value[-2:])

        if hour > 23 or minute > 59:
            return np.nan

        return hour * 60 + minute

    except (ValueError, TypeError):
        return np.nan


def to_xyz(df):
    """Convert latitude and longitude to 3D sphere coordinates."""

    lat = np.radians(df["latitude"].to_numpy())
    lon = np.radians(df["longitude"].to_numpy())

    return np.column_stack((
        np.cos(lat) * np.cos(lon),
        np.cos(lat) * np.sin(lon),
        np.sin(lat)
    ))


def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in kilometres."""

    lat1, lon1, lat2, lon2 = map(
        np.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    return (
        2 * EARTH_RADIUS_KM
        * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    )


def match_observations(modis, viirs):
    """
    Match observations by date, spatial distance and time.

    Each MODIS and VIIRS observation can only be used once.
    """

    modis = modis.copy().reset_index(drop=True)
    viirs = viirs.copy().reset_index(drop=True)

    modis["time_minutes"] = (
        modis["acq_time"].apply(time_to_minutes)
    )

    viirs["time_minutes"] = (
        viirs["acq_time"].apply(time_to_minutes)
    )

    required = [
        "latitude", "longitude",
        "acq_date", "time_minutes", "frp"
    ]

    modis = modis.dropna(subset=required)
    viirs = viirs.dropna(subset=required)

    max_angle = MAX_DISTANCE_KM / EARTH_RADIUS_KM
    max_chord = 2 * np.sin(max_angle / 2)

    matched_pairs = []
    used_viirs = set()

    for date in sorted(modis["acq_date"].unique()):

        modis_day = modis[
            modis["acq_date"] == date
        ]

        viirs_day = viirs[
            viirs["acq_date"] == date
        ]

        if viirs_day.empty:
            continue

        viirs_indices = viirs_day.index.to_numpy()

        tree = cKDTree(to_xyz(viirs_day))
        candidates = []

        for m_idx, m_row in modis_day.iterrows():

            point = to_xyz(
                pd.DataFrame([m_row])
            )[0]

            nearby = tree.query_ball_point(
                point, r=max_chord
            )

            for position in nearby:

                v_idx = viirs_indices[position]

                if v_idx in used_viirs:
                    continue

                v_row = viirs.loc[v_idx]

                time_difference = abs(
                    m_row["time_minutes"]
                    - v_row["time_minutes"]
                )

                if time_difference > MAX_TIME_DIFFERENCE_MIN:
                    continue

                distance = haversine_distance(
                    m_row["latitude"],
                    m_row["longitude"],
                    v_row["latitude"],
                    v_row["longitude"]
                )

                candidates.append((
                    distance,
                    time_difference,
                    m_idx,
                    v_idx
                ))

        # Prefer closest spatial and temporal pairs
        candidates.sort(
            key=lambda item: (item[0], item[1])
        )

        used_modis = set()

        for distance, time_diff, m_idx, v_idx in candidates:

            if m_idx in used_modis or v_idx in used_viirs:
                continue

            m_row = modis.loc[m_idx].to_dict()
            v_row = viirs.loc[v_idx].to_dict()

            pair = {
                **{
                    f"{key}_modis": value
                    for key, value in m_row.items()
                },
                **{
                    f"{key}_viirs": value
                    for key, value in v_row.items()
                },
                "distance_km": distance,
                "time_difference_min": time_diff
            }

            matched_pairs.append(pair)

            used_modis.add(m_idx)
            used_viirs.add(v_idx)

    matched = pd.DataFrame(matched_pairs)

    print("\nSpatial-temporal matching results")
    print("---------------------------------")
    print("Matched observations:", len(matched))
    print("Maximum distance:", MAX_DISTANCE_KM, "km")
    print("Maximum time difference:",
          MAX_TIME_DIFFERENCE_MIN, "minutes")

    return matched


def calibrate_frp(matched):
    """Train and evaluate FRP calibration."""

    if len(matched) < 20:
        raise ValueError(
            "At least 20 matched observations are required."
        )

    X = matched[["frp_viirs"]]
    y = matched["frp_modis"]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.20,
        random_state=42
    )

    print("\nDataset split")
    print("Training records:", len(X_train))
    print("Testing records:", len(X_test))

    model = LinearRegression()
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)

    r2 = r2_score(y_test, predictions)

    rmse = np.sqrt(
        mean_squared_error(y_test, predictions)
    )

    mae = mean_absolute_error(
        y_test, predictions
    )

    intercept = float(model.intercept_)
    slope = float(model.coef_[0])

    print("\nFRP Calibration Results")
    print("-----------------------")
    print(f"Intercept: {intercept:.5f}")
    print(f"Slope: {slope:.5f}")
    print(f"Test R-squared: {r2:.5f}")
    print(f"Test RMSE: {rmse:.5f} MW")
    print(f"Test MAE: {mae:.5f} MW")

    # Save test predictions
    test_results = X_test.copy()
    test_results["actual_modis_frp"] = y_test.values
    test_results["predicted_modis_frp"] = predictions

    test_results["residual"] = (
        test_results["actual_modis_frp"]
        - test_results["predicted_modis_frp"]
    )

    test_results.to_csv(
        DATA_DIR / "frp_test_predictions.csv",
        index=False
    )

    # Save calibration results
    results = {
        "intercept": intercept,
        "slope": slope,
        "equation": (
            f"MODIS_FRP = {intercept:.5f} "
            f"+ {slope:.5f} * VIIRS_FRP"
        ),
        "test_r2": float(r2),
        "test_rmse_mw": float(rmse),
        "test_mae_mw": float(mae),
        "training_records": len(X_train),
        "testing_records": len(X_test),
        "total_matched_records": len(matched),
        "data_type": "synthetic_prototype"
    }

    with open(
        DATA_DIR / "frp_calibration_results.json",
        "w"
    ) as file:
        json.dump(results, file, indent=4)

    print("\nCalibration results saved.")
    print("Test predictions saved.")

    return model


def main():
    """Run the complete FRP harmonization pipeline."""

    modis, viirs = load_cleaned_data()

    modis, viirs = prepare_data(modis, viirs)

    matched = match_observations(modis, viirs)

    if len(matched) < 20:
        print("Insufficient matches for calibration.")
        return

    matched.to_csv(
        DATA_DIR / "matched_frp.csv",
        index=False
    )

    print("\nMatched data saved.")

    calibrate_frp(matched)


if __name__ == "__main__":
    main()

