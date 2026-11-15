
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

RANDOM_SEED = 42
rng = np.random.default_rng(RANDOM_SEED)


def create_fire_cluster(center_lat, center_lon, count, date, pair_start):
    """Generate nearby synthetic fire observations."""

    observations = []

    for i in range(count):
        latitude = center_lat + rng.normal(0, 0.025)
        longitude = center_lon + rng.normal(0, 0.025)

        true_frp = rng.uniform(5, 150)

        observations.append({
            "mock_pair_id": pair_start + i,
            "latitude": latitude,
            "longitude": longitude,
            "acq_date": date,
            "acq_time": f"{rng.integers(0, 2400):04d}",
            "true_frp": true_frp
        })

    return observations


def create_isolated_hotspots(date, pair_start, count):
    """Generate isolated observations far from the clusters."""

    observations = []

    for i in range(count):
        observations.append({
            "mock_pair_id": pair_start + i,
            "latitude": rng.uniform(-55, 65),
            "longitude": rng.uniform(-170, 170),
            "acq_date": date,
            "acq_time": f"{rng.integers(0, 2400):04d}",
            "true_frp": rng.uniform(2, 100)
        })

    return observations


def create_sensor_datasets(observations):
    """Simulate different MODIS and VIIRS FRP measurements."""

    modis_records = []
    viirs_records = []

    for obs in observations:
        true_frp = obs["true_frp"]

        modis_frp = max(
            0,
            true_frp * 0.90 + rng.normal(0, 2)
        )

        viirs_frp = max(
            0,
            true_frp * 1.10 + rng.normal(0, 2)
        )

        common = {
            "mock_pair_id": obs["mock_pair_id"],
            "latitude": obs["latitude"],
            "longitude": obs["longitude"],
            "acq_date": obs["acq_date"],
            "acq_time": obs["acq_time"]
        }

        modis_records.append({
            **common,
            "satellite": "Terra",
            "instrument": "MODIS",
            "frp": round(modis_frp, 3)
        })

        viirs_records.append({
            **common,
            "satellite": "Suomi-NPP",
            "instrument": "VIIRS",
            "frp": round(viirs_frp, 3)
        })

    return pd.DataFrame(modis_records), pd.DataFrame(viirs_records)


def main():
    observations = []
    pair_id = 1

    # Multiple fire clusters on different dates.
    scenarios = [
        (55.0, -115.0, "2026-07-01", 25),
        (35.0,  100.0, "2026-07-01", 20),
        (-25.0, 135.0, "2026-07-02", 30),
        (10.0,   20.0, "2026-07-02", 18),
        (55.0, -115.0, "2026-07-03", 22),
        (35.0,  100.0, "2026-07-03", 24)
    ]

    for lat, lon, date, count in scenarios:
        cluster = create_fire_cluster(
            lat, lon, count, date, pair_id
        )
        observations.extend(cluster)
        pair_id += count

    # Add isolated hotspots.
    isolated = create_isolated_hotspots(
        "2026-07-01", pair_id, 15
    )
    observations.extend(isolated)

    # Add some additional observations with missing FRP.
    for obs in observations[:5]:
        obs["true_frp"] = np.nan

    modis, viirs = create_sensor_datasets(observations)

    # Add missing FRP values to test data validation.
    modis.loc[0, "frp"] = np.nan
    viirs.loc[1, "frp"] = np.nan

    # Add invalid coordinates to test validation.
    modis.loc[2, "latitude"] = 95.0

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    modis.to_csv(
        DATA_DIR / "realistic_modis_mock.csv",
        index=False
    )

    viirs.to_csv(
        DATA_DIR / "realistic_viirs_mock.csv",
        index=False
    )

    print("\nRealistic Mock Data Generated")
    print("--------------------------------")
    print(f"MODIS observations: {len(modis)}")
    print(f"VIIRS observations: {len(viirs)}")
    print(f"Acquisition dates: {len(set(o['acq_date'] for o in observations))}")
    print("Known fire clusters: 6")
    print(f"Isolated hotspots: {len(isolated)}")
    print("Missing FRP values and invalid coordinates included.")
    print("\nNew mock datasets saved successfully.")


if __name__ == "__main__":
    main()
