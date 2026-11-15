
from pathlib import Path
from zipfile import ZipFile
import pandas as pd
import numpy as np
import csv

# Project paths
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "data" / "raw_nasa"
OUTPUT_DIR = BASE_DIR / "data"

CHUNK_SIZE = 100_000

# Keep common fields from both satellite datasets.
KEEP_COLUMNS = [
    "latitude",
    "longitude",
    "brightness",
    "scan",
    "track",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
    "version",
    "bright_t31",
    "frp",
    "daynight",
    "type",
]

NUMERIC_COLUMNS = [
    "latitude",
    "longitude",
    "brightness",
    "scan",
    "track",
    "bright_t31",
    "frp",
]

def process_sensor(sensor_code, output_name):
    zip_files = sorted(RAW_DIR.glob(f"DL_FIRE_{sensor_code}_*.zip"))
    output_path = OUTPUT_DIR / output_name

    if not zip_files:
        print(f"No ZIP files found for {sensor_code}")
        return

    if output_path.exists():
        output_path.unlink()

    total_read = 0
    total_saved = 0
    first_chunk = True

    print(f"\nProcessing {sensor_code}...")

    for zip_path in zip_files:
        print(f"\nReading: {zip_path.name}")

        with ZipFile(zip_path) as archive:
            csv_files = [
                name for name in archive.namelist()
                if Path(name).name.startswith("fire_archive_")
                and name.lower().endswith(".csv")
            ]

            for csv_name in csv_files:
                print(f"  CSV: {csv_name}")

                with archive.open(csv_name) as raw_file:
                    import io

                    text_file = io.TextIOWrapper(
                        raw_file,
                        encoding="utf-8-sig"
                    )

                    reader = pd.read_csv(
                        text_file,
                        chunksize=CHUNK_SIZE,
                        dtype={
                            "confidence": "string",
                            "acq_time": "string",
                            "satellite": "string",
                            "instrument": "string",
                            "daynight": "string",
                            "type": "string",
                            "version": "string",
                        },
                        usecols=lambda col: col in KEEP_COLUMNS,
                    )

                    for chunk in reader:
                        total_read += len(chunk)

                        # Convert numeric fields safely.
                        for column in NUMERIC_COLUMNS:
                            if column in chunk.columns:
                                chunk[column] = pd.to_numeric(
                                    chunk[column],
                                    errors="coerce"
                                )

                        # Validate acquisition date.
                        chunk["acq_date"] = pd.to_datetime(
                            chunk["acq_date"],
                            errors="coerce"
                        )

                        # Normalize acquisition time to HHMM.
                        chunk["acq_time"] = (
                            chunk["acq_time"]
                            .fillna("")
                            .str.strip()
                            .str.zfill(4)
                        )

                        valid_time = chunk["acq_time"].str.fullmatch(
                            r"(?:[01]\d|2[0-3])[0-5]\d"
                        )

                        # Remove invalid coordinates, dates, times and FRP.
                        valid = (
                            chunk["latitude"].between(-90, 90)
                            & chunk["longitude"].between(-180, 180)
                            & chunk["frp"].notna()
                            & np.isfinite(chunk["frp"])
                            & (chunk["frp"] >= 0)
                            & chunk["acq_date"].notna()
                            & valid_time
                        )

                        cleaned = chunk.loc[valid].copy()

                        # Preserve sensor identity.
                        cleaned["sensor"] = sensor_code

                        # Save date consistently.
                        cleaned["acq_date"] = (
                            cleaned["acq_date"].dt.strftime("%Y-%m-%d")
                        )

                        cleaned.to_csv(
                            output_path,
                            mode="w" if first_chunk else "a",
                            index=False,
                            header=first_chunk,
                            compression="gzip",
                            quoting=csv.QUOTE_MINIMAL,
                        )

                        total_saved += len(cleaned)
                        first_chunk = False

                        print(
                            f"    Read: {total_read:,} | "
                            f"Valid: {total_saved:,}"
                        )

    print(f"\nFinished {sensor_code}")
    print(f"Rows read: {total_read:,}")
    print(f"Rows retained: {total_saved:,}")
    print(f"Output: {output_path}")


def main():
    if not RAW_DIR.exists():
        raise FileNotFoundError(
            f"NASA data folder not found: {RAW_DIR}"
        )

    process_sensor(
        "M-C61",
        "modis_real_cleaned.csv.gz"
    )

    process_sensor(
        "SV-C2",
        "viirs_real_cleaned.csv.gz"
    )

    print("\nNASA historical ingestion completed.")


if __name__ == "__main__":
    main()
