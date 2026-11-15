
import pandas as pd
import numpy as np
import duckdb
import zipfile
import pyarrow as pa
import pyarrow.parquet as pq

from pathlib import Path
import gc
import time


# =====================================================
# 1. CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parent

RAW_DIR = BASE_DIR / "data" / "raw"
TEMP_DIR = BASE_DIR / "data" / "intermediate"
OUTPUT_DIR = BASE_DIR / "data" / "processed"

GRID_SIZE = 0.25

# Number of CSV rows processed at a time.
CHUNK_SIZE = 100_000

# None means process the entire geographical dataset.
# Otherwise use: (min_lon, max_lon, min_lat, max_lat)
REGION_BBOX = None

# Example for Alberta:
# REGION_BBOX = (-120, -110, 49, 60)

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# =====================================================
# 2. IDENTIFY SATELLITE TYPE
# =====================================================

def identify_sensor(filename):

    name = filename.upper()

    if "M-C61" in name:
        return "MODIS"

    elif "SV-C2" in name:
        return "VIIRS"

    else:
        raise ValueError(
            f"Cannot identify satellite type: {filename}"
        )


# =====================================================
# 3. PROCESS ONE ZIP FILE
# =====================================================

def process_zip(zip_path):

    print("\n" + "=" * 60)
    print(f"PROCESSING ZIP: {zip_path.name}")
    print("=" * 60)

    sensor = identify_sensor(zip_path.name)

    output_file = TEMP_DIR / (
        zip_path.stem + "_processed.parquet"
    )

    # Remove old output before reprocessing.
    if output_file.exists():
        output_file.unlink()

    total_rows = 0
    valid_rows = 0
    writer = None

    min_date = None
    max_date = None

    start_time = time.time()

    try:

        # Open ZIP without extracting it.
        with zipfile.ZipFile(zip_path, "r") as archive:

            csv_files = [
                name for name in archive.namelist()
                if name.lower().endswith(".csv")
                and not name.startswith("__MACOSX/")
            ]

            if not csv_files:
                raise ValueError(
                    f"No CSV files found inside {zip_path.name}"
                )

            print(f"Satellite: {sensor}")
            print(f"CSV files found: {len(csv_files)}")

            for csv_file in csv_files:

                print(f"\nReading: {csv_file}")

                with archive.open(csv_file) as file:

                    reader = pd.read_csv(
                        file,
                        chunksize=CHUNK_SIZE,
                        low_memory=False
                    )

                    for chunk_number, chunk in enumerate(reader):

                        total_rows += len(chunk)

                        # Standardize column names.
                        chunk.columns = [
                            col.strip().lower()
                            for col in chunk.columns
                        ]

                        # Required FIRMS columns.
                        required = [
                            "latitude",
                            "longitude",
                            "acq_date"
                        ]

                        missing = [
                            col for col in required
                            if col not in chunk.columns
                        ]

                        if missing:
                            raise ValueError(
                                f"Missing columns {missing} "
                                f"in {csv_file}"
                            )

                        # FRP is optional for ingestion.
                        if "frp" not in chunk.columns:
                            chunk["frp"] = np.nan

                        # Convert coordinates.
                        chunk["latitude"] = pd.to_numeric(
                            chunk["latitude"],
                            errors="coerce"
                        )

                        chunk["longitude"] = pd.to_numeric(
                            chunk["longitude"],
                            errors="coerce"
                        )

                        # Remove invalid coordinates.
                        chunk = chunk.dropna(
                            subset=[
                                "latitude",
                                "longitude"
                            ]
                        )

                        chunk = chunk[
                            chunk["latitude"].between(-90, 90)
                            &
                            chunk["longitude"].between(-180, 180)
                        ]

                        # Optional geographical filtering.
                        if REGION_BBOX is not None:

                            min_lon, max_lon, min_lat, max_lat = (
                                REGION_BBOX
                            )

                            chunk = chunk[
                                chunk["longitude"].between(
                                    min_lon, max_lon
                                )
                                &
                                chunk["latitude"].between(
                                    min_lat, max_lat
                                )
                            ]

                        # Parse acquisition date.
                        chunk["acq_date"] = pd.to_datetime(
                            chunk["acq_date"],
                            errors="coerce"
                        ).dt.normalize()

                        chunk = chunk.dropna(
                            subset=["acq_date"]
                        )

                        if chunk.empty:
                            continue

                        # Track date range.
                        chunk_min = chunk["acq_date"].min()
                        chunk_max = chunk["acq_date"].max()

                        if min_date is None or chunk_min < min_date:
                            min_date = chunk_min

                        if max_date is None or chunk_max > max_date:
                            max_date = chunk_max

                        valid_rows += len(chunk)

                        # Convert FRP to numeric.
                        chunk["frp"] = pd.to_numeric(
                            chunk["frp"],
                            errors="coerce"
                        )

                        # ---------------------------------
                        # SPATIAL GRID CREATION
                        # ---------------------------------

                        chunk["grid_lat"] = (
                            np.floor(
                                chunk["latitude"] / GRID_SIZE
                            ) * GRID_SIZE
                        )

                        chunk["grid_lon"] = (
                            np.floor(
                                chunk["longitude"] / GRID_SIZE
                            ) * GRID_SIZE
                        )

                        # ---------------------------------
                        # DAILY AGGREGATION
                        # ---------------------------------

                        aggregated = (
                            chunk.groupby(
                                [
                                    "grid_lat",
                                    "grid_lon",
                                    "acq_date"
                                ],
                                observed=True
                            )
                            .agg(
                                hotspot_count=(
                                    "latitude", "size"
                                ),
                                frp_sum=(
                                    "frp", "sum"
                                ),
                                frp_count=(
                                    "frp", "count"
                                )
                            )
                            .reset_index()
                        )

                        aggregated["sensor"] = sensor

                        # ---------------------------------
                        # SAVE CHUNK TO PARQUET
                        # ---------------------------------

                        table = pa.Table.from_pandas(
                            aggregated,
                            preserve_index=False
                        )

                        if writer is None:

                            writer = pq.ParquetWriter(
                                output_file,
                                table.schema,
                                compression="zstd"
                            )

                        writer.write_table(table)

                        print(
                            f"Chunk {chunk_number + 1}: "
                            f"{len(chunk):,} rows processed"
                        )

                        del chunk
                        del aggregated
                        del table

                        gc.collect()

        if writer is not None:
            writer.close()
            writer = None

        else:
            print("WARNING: No valid records found.")
            return

        elapsed = time.time() - start_time

        print("\nZIP PROCESSING COMPLETE")
        print(f"Rows read: {total_rows:,}")
        print(f"Valid rows: {valid_rows:,}")
        print(f"Date range: {min_date} to {max_date}")
        print(f"Processing time: {elapsed:.1f} seconds")
        print(f"Saved to: {output_file}")

    except Exception:

        if writer is not None:
            writer.close()

        raise


# =====================================================
# 4. PROCESS ALL ZIP FILES
# =====================================================

def process_all_zips():

    zip_files = sorted(
        path for path in RAW_DIR.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".zip"
    )

    if not zip_files:
        raise FileNotFoundError(
            f"No ZIP files found in {RAW_DIR}"
        )

    print("\nFOUND ZIP FILES:", len(zip_files))

    for index, zip_path in enumerate(zip_files):

        print(
            f"\nARCHIVE {index + 1}/{len(zip_files)}"
        )

        process_zip(zip_path)

    print("\nALL ZIP FILES PROCESSED SUCCESSFULLY!")


# =====================================================
# 5. COMBINE MODIS AND VIIRS USING DUCKDB
# =====================================================

def combine_datasets():

    print("\n" + "=" * 60)
    print("COMBINING MODIS AND VIIRS")
    print("=" * 60)

    parquet_files = sorted(
        TEMP_DIR.glob("*_processed.parquet")
    )

    if not parquet_files:
        raise FileNotFoundError(
            "No intermediate Parquet files found."
        )

    # Pass the explicit file list to DuckDB.
    file_list = [
        str(path.resolve()).replace("\\", "/")
        for path in parquet_files
    ]

    output_file = (
        OUTPUT_DIR / "harmonized_fire_data.parquet"
    ).resolve().as_posix()

    # Escape paths for SQL string literals.
    escaped_files = [
        "'" + path.replace("'", "''") + "'"
        for path in file_list
    ]

    escaped_output = output_file.replace("'", "''")

    con = duckdb.connect()

    con.execute("SET threads TO 4")

    con.execute("SET memory_limit = '4GB'")

    con.execute("SET temp_directory = '"
                + str(TEMP_DIR.resolve()).replace("\\", "/")
                + "'")

    file_sql = "[" + ", ".join(escaped_files) + "]"

    query = f"""
        COPY (

            SELECT

                grid_lat,
                grid_lon,
                CAST(acq_date AS DATE) AS acq_date,

                SUM(hotspot_count)
                    AS daily_hotspots,

                SUM(
                    CASE WHEN sensor = 'MODIS'
                    THEN hotspot_count ELSE 0 END
                ) AS modis_hotspots,

                SUM(
                    CASE WHEN sensor = 'VIIRS'
                    THEN hotspot_count ELSE 0 END
                ) AS viirs_hotspots,

                SUM(frp_sum) /
                    NULLIF(SUM(frp_count), 0)
                    AS mean_frp,

                SUM(
                    CASE WHEN sensor = 'MODIS'
                    THEN frp_sum ELSE 0 END
                ) /
                    NULLIF(
                        SUM(
                            CASE WHEN sensor = 'MODIS'
                            THEN frp_count ELSE 0 END
                        ), 0
                    ) AS modis_mean_frp,

                SUM(
                    CASE WHEN sensor = 'VIIRS'
                    THEN frp_sum ELSE 0 END
                ) /
                    NULLIF(
                        SUM(
                            CASE WHEN sensor = 'VIIRS'
                            THEN frp_count ELSE 0 END
                        ), 0
                    ) AS viirs_mean_frp

            FROM read_parquet({file_sql})

            GROUP BY
                grid_lat,
                grid_lon,
                CAST(acq_date AS DATE)

        )

        TO '{escaped_output}'

        (FORMAT PARQUET, COMPRESSION ZSTD)
    """

    try:

        con.execute(query)

        print("\nHARMONIZATION COMPLETE!")

        print(
            f"Final dataset: {output_file}"
        )

        # Print a small summary.
        summary = con.execute(f"""
            SELECT
                COUNT(*) AS total_records,
                COUNT(DISTINCT acq_date) AS unique_days,
                COUNT(DISTINCT grid_lat || '_' || grid_lon)
                    AS unique_grid_cells,
                MIN(acq_date) AS first_date,
                MAX(acq_date) AS last_date,
                SUM(daily_hotspots) AS total_detections

            FROM read_parquet('{escaped_output}')
        """).fetchdf()

        print("\nDATASET SUMMARY:")
        print(summary.to_string(index=False))

    finally:
        con.close()


# =====================================================
# 6. MAIN EXECUTION
# =====================================================

if __name__ == "__main__":

    start = time.time()

    print("=" * 60)
    print("NASA SPACE APPS - FIRE DATA PROCESSING")
    print("=" * 60)

    # Stage 1: Read all ZIP files.
    process_all_zips()

    # Stage 2: Combine all processed archives.
    combine_datasets()

    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print("PIPELINE FINISHED SUCCESSFULLY!")
    print(f"Total time: {elapsed / 60:.2f} minutes")
    print("=" * 60)
