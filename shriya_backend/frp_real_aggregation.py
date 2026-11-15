
from pathlib import Path
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
CHUNK_SIZE = 500_000
GRID_SIZE = 10

FILES = {
    "MODIS": DATA / "modis_real_cleaned.csv.gz",
    "VIIRS": DATA / "viirs_real_cleaned.csv.gz",
}


def process_sensor(sensor, path):
    print(f"\nProcessing {sensor}...")

    daily_parts = []
    regional_parts = []

    for chunk_number, chunk in enumerate(
        pd.read_csv(
            path,
            compression="gzip",
            chunksize=CHUNK_SIZE,
            usecols=[
                "acq_date", "latitude", "longitude", "frp", "type"
            ],
        ),
        start=1,
    ):
        chunk["acq_date"] = pd.to_datetime(
            chunk["acq_date"], errors="coerce"
        )
        chunk = chunk.dropna(
            subset=["acq_date", "latitude", "longitude", "frp"]
        )

        # Keep vegetation fires only (FIRMS type 0). Types 1-3 are volcanoes,
        # gas flares/industry and offshore platforms, and zero-FRP detections
        # are not usable fire energy. Without this, a couple of 0 MW detections
        # near the South Pole showed up as the strongest "anomalies".
        if "type" in chunk:
            chunk = chunk[chunk["type"].fillna(0).astype(int) == 0]
        chunk = chunk[chunk["frp"] > 0]

        # Assign detections to 10-degree geographic grid cells.
        chunk["lat_bin"] = (
            (chunk["latitude"] // GRID_SIZE) * GRID_SIZE
        ).astype(int)

        chunk["lon_bin"] = (
            (chunk["longitude"] // GRID_SIZE) * GRID_SIZE
        ).astype(int)

        # Daily global summary.
        daily = chunk.groupby("acq_date").agg(
            total_frp=("frp", "sum"),
            hotspot_count=("frp", "size"),
            mean_frp=("frp", "mean"),
            max_frp=("frp", "max"),
        ).reset_index()

        daily_parts.append(daily)

        # Daily regional summary.
        regional = chunk.groupby(
            ["acq_date", "lat_bin", "lon_bin"]
        ).agg(
            total_frp=("frp", "sum"),
            hotspot_count=("frp", "size"),
            mean_frp=("frp", "mean"),
            max_frp=("frp", "max"),
        ).reset_index()

        regional_parts.append(regional)

        print(f"Processed chunk {chunk_number}")

    daily_result = (
        pd.concat(daily_parts)
        .groupby("acq_date")
        .agg(
            total_frp=("total_frp", "sum"),
            hotspot_count=("hotspot_count", "sum"),
            mean_frp=("total_frp", "sum"),
            max_frp=("max_frp", "max"),
        )
        .reset_index()
    )

    daily_result["mean_frp"] = (
        daily_result["total_frp"] /
        daily_result["hotspot_count"]
    )

    regional_result = (
        pd.concat(regional_parts)
        .groupby(["acq_date", "lat_bin", "lon_bin"])
        .agg(
            total_frp=("total_frp", "sum"),
            hotspot_count=("hotspot_count", "sum"),
            max_frp=("max_frp", "max"),
        )
        .reset_index()
    )

    regional_result["mean_frp"] = (
        regional_result["total_frp"] /
        regional_result["hotspot_count"]
    )

    daily_result["sensor"] = sensor
    regional_result["sensor"] = sensor

    daily_result.to_csv(
        DATA / f"{sensor.lower()}_daily_frp_real.csv",
        index=False,
    )

    regional_result.to_csv(
        DATA / f"{sensor.lower()}_regional_frp_real.csv",
        index=False,
    )

    print(f"{sensor} daily summaries:", len(daily_result))
    print(f"{sensor} regional summaries:", len(regional_result))


if __name__ == "__main__":
    for sensor, path in FILES.items():
        process_sensor(sensor, path)

    print("\nReal FRP aggregation completed.")
