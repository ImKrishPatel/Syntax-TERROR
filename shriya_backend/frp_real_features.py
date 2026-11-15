
from pathlib import Path
import json

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

INPUT_FILE = DATA_DIR / "viirs_real_cleaned.csv.gz"
OUTPUT_FILE = DATA_DIR / "viirs_calibrated_features_real.csv.gz"
SUMMARY_FILE = DATA_DIR / "viirs_calibrated_features_summary.json"

CHUNK_SIZE = 250_000

# Latest multi-date chronological calibration:
# VIIRS FRP -> estimated MODIS-equivalent FRP
INTERCEPT = 23.7925246440714
SLOPE = 1.8719126746211914

BINS = [-np.inf, 10, 50, 100, np.inf]
LABELS = ["Low", "Moderate", "High", "Extreme"]


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(INPUT_FILE)

    # Never silently overwrite previous results.
    if OUTPUT_FILE.exists() or SUMMARY_FILE.exists():
        raise FileExistsError(
            "Output already exists. Rename or back it up before rerunning."
        )

    total_rows = 0
    total_frp = 0.0
    total_estimated_frp = 0.0
    intensity_counts = {label: 0 for label in LABELS}
    first_chunk = True

    for chunk in pd.read_csv(INPUT_FILE, chunksize=CHUNK_SIZE):
        chunk["frp"] = pd.to_numeric(
            chunk["frp"], errors="coerce"
        )

        chunk = chunk.dropna(subset=["frp"]).copy()
        chunk = chunk[np.isfinite(chunk["frp"])].copy()
        chunk = chunk[chunk["frp"] >= 0].copy()

        if chunk.empty:
            continue

        # Preserve original VIIRS FRP.
        chunk["estimated_modis_frp_mw"] = (
            INTERCEPT + SLOPE * chunk["frp"]
        ).clip(lower=0)

        # Compatibility alias for existing feature calculations.
        chunk["normalized_frp"] = chunk["estimated_modis_frp_mw"]

        chunk["fire_intensity"] = pd.cut(
            chunk["estimated_modis_frp_mw"],
            bins=BINS,
            labels=LABELS,
            right=False
        )

        # Write compressed output incrementally.
        chunk.to_csv(
            OUTPUT_FILE,
            index=False,
            mode="wt" if first_chunk else "at",
            header=first_chunk,
            compression="gzip"
        )

        first_chunk = False
        total_rows += len(chunk)
        total_frp += chunk["frp"].sum()
        total_estimated_frp += chunk[
            "estimated_modis_frp_mw"
        ].sum()

        counts = chunk["fire_intensity"].value_counts()
        for label in LABELS:
            intensity_counts[label] += int(counts.get(label, 0))

        print(f"Processed {total_rows:,} observations")

    if total_rows == 0:
        raise ValueError("No valid VIIRS FRP observations found.")

    summary = {
        "source": "NASA VIIRS real cleaned observations",
        "calibration_direction": "VIIRS_FRP_to_MODIS_FRP",
        "calibration_status": "preliminary_temporal_holdout",
        "intercept": INTERCEPT,
        "slope": SLOPE,
        "observations_processed": total_rows,
        "original_viirs_total_frp_mw": float(total_frp),
        "estimated_modis_equivalent_total_frp_mw": float(
            total_estimated_frp
        ),
        "intensity_counts": intensity_counts,
        "output_file": OUTPUT_FILE.name
    }

    with open(SUMMARY_FILE, "w") as file:
        json.dump(summary, file, indent=4)

    print("\nReal VIIRS feature generation complete.")
    print(f"Observations: {total_rows:,}")
    print(f"Output: {OUTPUT_FILE.name}")
    print(f"Summary: {SUMMARY_FILE.name}")


if __name__ == "__main__":
    main()
