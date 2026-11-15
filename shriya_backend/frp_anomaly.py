import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

Z_SCORE_THRESHOLD = 2.0


def load_data():
    file_path = DATA_DIR / "frp_features.csv"
    df = pd.read_csv(file_path)

    return df


def detect_frp_anomalies(df):
    """Detect unusually high FRP observations using z-scores."""

    df = df.copy()

    mean_frp = df["normalized_frp"].mean()
    std_frp = df["normalized_frp"].std()

    if pd.isna(std_frp) or std_frp == 0:
        df["z_score"] = 0.0
        df["is_anomaly"] = False
        df["anomaly_severity"] = "Normal"
        return df

    df["z_score"] = (
        df["normalized_frp"] - mean_frp
    ) / std_frp

    df["is_anomaly"] = (
        df["z_score"] >= Z_SCORE_THRESHOLD
    )

    df["anomaly_severity"] = np.select(
        [
            df["z_score"] >= 3.0,
            df["z_score"] >= 2.5,
            df["z_score"] >= Z_SCORE_THRESHOLD
        ],
        [
            "Extreme",
            "High",
            "Moderate"
        ],
        default="Normal"
    )

    return df


def detect_regional_anomalies(df):
    """Detect unusually high FRP within geographic grid cells."""

    df = df.copy()

    df["latitude_band"] = (
        (df["latitude_viirs"] // 10) * 10
    ).astype(int)

    df["longitude_band"] = (
        (df["longitude_viirs"] // 10) * 10
    ).astype(int)

    regional = df.groupby(
        ["latitude_band", "longitude_band"],
        observed=True
    ).agg(
        hotspot_count=("normalized_frp", "count"),
        average_frp=("normalized_frp", "mean"),
        maximum_frp=("normalized_frp", "max")
    ).reset_index()

    regional_mean = regional["average_frp"].mean()
    regional_std = regional["average_frp"].std()

    if pd.isna(regional_std) or regional_std == 0:
        regional["regional_z_score"] = 0.0
    else:
        regional["regional_z_score"] = (
            regional["average_frp"] - regional_mean
        ) / regional_std

    regional["regional_anomaly"] = (
        regional["regional_z_score"] >= Z_SCORE_THRESHOLD
    )

    return regional


def main():
    df = load_data()

    observations = detect_frp_anomalies(df)
    regional = detect_regional_anomalies(observations)

    observations.to_csv(
        DATA_DIR / "frp_anomalies.csv",
        index=False
    )

    regional.to_csv(
        DATA_DIR / "regional_anomalies.csv",
        index=False
    )

    anomalies = observations[
        observations["is_anomaly"]
    ]

    regional_anomalies = regional[
        regional["regional_anomaly"]
    ]

    print("\nFRP Anomaly Detection Complete")
    print("--------------------------------")
    print(f"Observations analyzed: {len(observations)}")
    print(f"FRP anomalies detected: {len(anomalies)}")
    print(f"Regional anomalies detected: {len(regional_anomalies)}")

    print("\nAnomaly severity:")
    print(
        observations["anomaly_severity"]
        .value_counts()
        .to_string()
    )

    print("\nAnomaly files saved successfully.")


if __name__ == "__main__":
    main()