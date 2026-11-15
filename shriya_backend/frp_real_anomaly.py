
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).resolve().parent / "data"
THRESHOLD = 3.0
# A region-day must have at least this many detections to count as an anomaly,
# so one or two stray pixels cannot top the list.
MIN_HOTSPOTS = 10

FILES = {
    "MODIS": DATA / "modis_regional_frp_real.csv",
    "VIIRS": DATA / "viirs_regional_frp_real.csv",
}


def robust_z_score(values):
    median = values.median()
    mad = (values - median).abs().median()

    if mad > 0:
        return 0.6745 * (values - median) / mad

    std = values.std()

    if std > 0:
        return (values - values.mean()) / std

    return pd.Series(0.0, index=values.index)


def detect_anomalies(sensor, path):
    print(f"\nDetecting anomalies for {sensor}...")

    df = pd.read_csv(path, parse_dates=["acq_date"])

    all_dates = pd.date_range(
        df["acq_date"].min(),
        df["acq_date"].max(),
        freq="D",
    )

    results = []

    regions = df.groupby(["lat_bin", "lon_bin"])

    for (lat, lon), region in regions:
        region = (
            region.set_index("acq_date")
            .reindex(all_dates)
            .rename_axis("acq_date")
            .reset_index()
        )

        # No detection in a region on a date means zero observed FRP.
        for column in ["total_frp", "hotspot_count", "max_frp"]:
            region[column] = region[column].fillna(0)

        region["frp_z_score"] = robust_z_score(
            region["total_frp"]
        )

        region["count_z_score"] = robust_z_score(
            region["hotspot_count"]
        )

        region["frp_anomaly"] = (
            region["frp_z_score"] >= THRESHOLD
        )

        region["count_anomaly"] = (
            region["count_z_score"] >= THRESHOLD
        )

        region["is_anomaly"] = (
            (region["frp_anomaly"] | region["count_anomaly"])
            & (region["hotspot_count"] >= MIN_HOTSPOTS)
            & (region["total_frp"] > 0)
        )

        region["lat_bin"] = lat
        region["lon_bin"] = lon
        region["sensor"] = sensor

        anomalies = region[region["is_anomaly"]].copy()

        results.append(anomalies)

    if results:
        output = pd.concat(results, ignore_index=True)
    else:
        output = pd.DataFrame()

    output_path = DATA / f"{sensor.lower()}_anomalies_real.csv"
    output.to_csv(output_path, index=False)

    print(f"{sensor} anomalies detected: {len(output):,}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    for sensor, path in FILES.items():
        detect_anomalies(sensor, path)

    print("\nReal FRP anomaly detection completed.")
