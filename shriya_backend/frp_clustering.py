import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.cluster import DBSCAN

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Clustering settings
EARTH_RADIUS_KM = 6371.0
EPSILON_KM = 25
MIN_SAMPLES = 3


def load_data():
    file_path = DATA_DIR / "frp_features.csv"
    df = pd.read_csv(file_path)

    df = df.dropna(
        subset=["latitude_viirs", "longitude_viirs", "normalized_frp"]
    ).copy()

    return df


def cluster_hotspots(df):
    """Group geographically nearby hotspots using DBSCAN."""

    df = df.copy()

    coordinates = df[
        ["latitude_viirs", "longitude_viirs"]
    ].to_numpy()

    # Convert coordinates to radians for haversine distance.
    coordinates_radians = np.radians(coordinates)

    dbscan = DBSCAN(
        eps=EPSILON_KM / EARTH_RADIUS_KM,
        min_samples=MIN_SAMPLES,
        metric="haversine",
        algorithm="ball_tree"
    )

    df["cluster_id"] = dbscan.fit_predict(coordinates_radians)

    # DBSCAN uses -1 for observations that don't belong to a cluster.
    df["cluster_type"] = np.where(
        df["cluster_id"] == -1,
        "Noise",
        "Cluster"
    )

    return df


def summarize_clusters(df):
    """Calculate statistics for each detected cluster."""

    clustered = df[df["cluster_id"] != -1].copy()

    if clustered.empty:
        return pd.DataFrame(
            columns=[
                "cluster_id",
                "hotspot_count",
                "average_frp",
                "maximum_frp",
                "total_observed_frp"
            ]
        )

    summary = clustered.groupby("cluster_id").agg(
        hotspot_count=("normalized_frp", "count"),
        average_frp=("normalized_frp", "mean"),
        maximum_frp=("normalized_frp", "max"),
        total_observed_frp=("normalized_frp", "sum")
    ).reset_index()

    return summary


def main():
    df = load_data()

    clustered = cluster_hotspots(df)
    summary = summarize_clusters(clustered)

    clustered.to_csv(
        DATA_DIR / "frp_clusters.csv",
        index=False
    )

    summary.to_csv(
        DATA_DIR / "cluster_summary.csv",
        index=False
    )

    noise_count = (clustered["cluster_id"] == -1).sum()

    print("\nDBSCAN Fire Clustering Complete")
    print("--------------------------------")
    print(f"Observations analyzed: {len(clustered)}")
    print(f"Clusters detected: {summary['cluster_id'].nunique()}")
    print(f"Observations in clusters: {len(clustered) - noise_count}")
    print(f"Isolated observations: {noise_count}")

    print("\nLargest clusters:")
    if not summary.empty:
        print(
            summary.sort_values(
                "hotspot_count",
                ascending=False
            ).head(5).to_string(index=False)
        )
    else:
        print("No clusters detected with the current settings.")

    print("\nClustering files saved successfully.")


if __name__ == "__main__":
    main()