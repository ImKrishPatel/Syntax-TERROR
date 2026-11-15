import pandas as pd
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Calibration equation from our FRP harmonization model
INTERCEPT = -0.07322
SLOPE = 0.81842


def normalize_frp(df):
    """Convert VIIRS FRP to the MODIS-equivalent scale."""
    df = df.copy()

    df["normalized_frp"] = (
        INTERCEPT + SLOPE * df["frp_viirs"]
    ).clip(lower=0)

    return df


def classify_intensity(df):
    """Classify fire observations using normalized FRP."""
    df = df.copy()

    bins = [-float("inf"), 10, 50, 100, float("inf")]
    categories = ["Low", "Moderate", "High", "Extreme"]

    df["fire_intensity"] = pd.cut(
        df["normalized_frp"],
        bins=bins,
        labels=categories,
        right=False
    )

    return df

def calculate_frp_summary(df):
    """Calculate summary statistics for normalized FRP."""
    return {
        "mean_frp_mw": round(df["normalized_frp"].mean(), 2),
        "maximum_frp_mw": round(df["normalized_frp"].max(), 2),
        "total_observed_frp_mw": round(df["normalized_frp"].sum(), 2),
        "observation_count": int(len(df))
    }


def main():
    input_file = DATA_DIR / "matched_frp.csv"

    df = pd.read_csv(input_file)

    df = normalize_frp(df)
    df = classify_intensity(df)

    output_file = DATA_DIR / "frp_features.csv"
    df.to_csv(output_file, index=False)

    summary = calculate_frp_summary(df)

    summary_file = DATA_DIR / "frp_summary.json"
    with open(summary_file, "w") as file:
        json.dump(summary, file, indent=4)

    print("\nFRP Feature Processing Complete")
    print("--------------------------------")
    print(f"Observations processed: {len(df)}")
    print(f"Mean FRP: {summary['mean_frp_mw']} MW")
    print(f"Maximum FRP: {summary['maximum_frp_mw']} MW")
    print(f"Low intensity: {(df['fire_intensity'] == 'Low').sum()}")
    print(f"Moderate intensity: {(df['fire_intensity'] == 'Moderate').sum()}")
    print(f"High intensity: {(df['fire_intensity'] == 'High').sum()}")
    print(f"Extreme intensity: {(df['fire_intensity'] == 'Extreme').sum()}")

    print("\nFeature files saved successfully.")


if __name__ == "__main__":
    main()