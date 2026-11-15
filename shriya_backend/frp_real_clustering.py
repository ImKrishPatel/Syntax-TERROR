
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

DATA = Path(__file__).resolve().parent / "data"

EARTH_RADIUS_KM = 6371.0
CLUSTER_DISTANCE_KM = 1600
MIN_REGIONS = 2

FILES = {
    "MODIS": DATA / "modis_anomalies_real.csv",
    "VIIRS": DATA / "viirs_anomalies_real.csv",
}


def cluster_sensor(sensor, path):
    print(f"\nClustering {sensor} regional anomalies...")

    df = pd.read_csv(path, parse_dates=["acq_date"])

    if df.empty:
        print("No anomalies available.")
        return

    df = df[df["is_anomaly"] == True].copy()

    # Use the centre of each 10-degree grid cell.
    df["latitude"] = df["lat_bin"] + 5
    df["longitude"] = df["lon_bin"] + 5

    cluster_distance = CLUSTER_DISTANCE_KM / EARTH_RADIUS_KM
    output = []

    for date, daily in df.groupby("acq_date"):
        if len(daily) < MIN_REGIONS:
            continue

        coordinates = np.radians(
            daily[["latitude", "longitude"]].to_numpy()
        )

        model = DBSCAN(
            eps=cluster_distance,
            min_samples=MIN_REGIONS,
            metric="haversine",
        )

        daily["cluster_id"] = model.fit_predict(coordinates)

        # -1 represents isolated regions, not a cluster.
        clustered = daily[daily["cluster_id"] != -1]

        for cluster_id, cluster in clustered.groupby("cluster_id"):
            output.append({
                "date": date,
                "sensor": sensor,
                "cluster_id": int(cluster_id),
                "region_count": len(cluster),
                "latitude": cluster["latitude"].mean(),
                "longitude": cluster["longitude"].mean(),
                "total_frp": cluster["total_frp"].sum(),
                "total_hotspots": cluster["hotspot_count"].sum(),
                "max_frp": cluster["max_frp"].max(),
            })

    result = pd.DataFrame(output)

    output_path = DATA / f"{sensor.lower()}_regional_clusters_real.csv"
    result.to_csv(output_path, index=False)

    print(f"Clusters found: {len(result):,}")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    for sensor, path in FILES.items():
        cluster_sensor(sensor, path)

    print("\nReal regional clustering completed.")
