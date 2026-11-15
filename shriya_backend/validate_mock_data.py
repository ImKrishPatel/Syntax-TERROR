
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def validate_dataset(sensor):
    input_file = DATA_DIR / f"realistic_{sensor}_mock.csv"

    df = pd.read_csv(input_file)

    original_count = len(df)

    # Convert required measurement columns to numeric.
    for column in ["latitude", "longitude", "frp"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # Count data-quality problems before cleaning.
    missing_values = df[
        ["latitude", "longitude", "frp"]
    ].isna().any(axis=1).sum()

    invalid_coordinates = (
        ~df["latitude"].between(-90, 90)
        | ~df["longitude"].between(-180, 180)
    ).sum()

    negative_frp = (df["frp"] < 0).sum()

    duplicates = df.duplicated(
        subset=["latitude", "longitude", "acq_date", "acq_time"]
    ).sum()

    # Remove incomplete and invalid observations.
    cleaned = df.dropna(
        subset=["latitude", "longitude", "frp", "acq_date", "acq_time"]
    ).copy()

    cleaned = cleaned[
        cleaned["latitude"].between(-90, 90)
        & cleaned["longitude"].between(-180, 180)
        & (cleaned["frp"] >= 0)
    ]

    cleaned = cleaned.drop_duplicates(
        subset=["latitude", "longitude", "acq_date", "acq_time"]
    )

    output_file = DATA_DIR / f"realistic_{sensor}_cleaned.csv"
    cleaned.to_csv(output_file, index=False)

    print(f"\n{sensor.upper()} validation")
    print("-------------------------")
    print(f"Original observations: {original_count}")
    print(f"Rows with missing required values: {missing_values}")
    print(f"Invalid coordinate rows: {invalid_coordinates}")
    print(f"Negative FRP rows: {negative_frp}")
    print(f"Duplicate rows: {duplicates}")
    print(f"Valid observations retained: {len(cleaned)}")
    print(f"Rows removed: {original_count - len(cleaned)}")


def main():
    validate_dataset("modis")
    validate_dataset("viirs")

    print("\nValidation completed.")
    print("Cleaned datasets saved successfully.")


if __name__ == "__main__":
    main()
