
import pandas as pd
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def load_viirs_data():
    """Load VIIRS hotspot dataset."""

    file_path = DATA_DIR / "viirs_mock.csv"

    if not file_path.exists():
        raise FileNotFoundError(
            f"VIIRS dataset not found: {file_path}"
        )

    df = pd.read_csv(file_path)

    print("VIIRS data loaded successfully")
    print("Original shape:", df.shape)

    return df


def clean_viirs_data(df):
    """Clean VIIRS fire detection records."""

    print("Cleaning VIIRS data...")

    required_columns = [
        "latitude",
        "longitude",
        "acq_date",
        "frp"
    ]

    for column in required_columns:
        if column not in df.columns:
            raise ValueError(
                f"Missing required column: {column}"
            )

    # Convert numeric fields
    for column in ["latitude", "longitude", "frp"]:
        df[column] = pd.to_numeric(
            df[column], errors="coerce"
        )

    # Remove records missing essential values
    df = df.dropna(subset=required_columns)

    # Validate geographic coordinates
    df = df[
        df["latitude"].between(-90, 90) &
        df["longitude"].between(-180, 180)
    ]

    # FRP cannot be negative
    df = df[df["frp"] >= 0]

    # Parse acquisition dates
    df["acq_date"] = pd.to_datetime(
        df["acq_date"], errors="coerce"
    )

    df = df.dropna(subset=["acq_date"])

    # Preserve confidence as categorical or numeric
    if "confidence" in df.columns:
        df["confidence"] = df["confidence"].astype("string")

    # Remove exact duplicate records
    df = df.drop_duplicates()

    df = df.reset_index(drop=True)

    print("Cleaning completed!")
    print("Cleaned records:", len(df))

    return df


def main():
    df = load_viirs_data()

    cleaned_df = clean_viirs_data(df)

    output_path = DATA_DIR / "viirs_cleaned.csv"
    cleaned_df.to_csv(output_path, index=False)

    print("Cleaned VIIRS data saved:", output_path)


if __name__ == "__main__":
    main()
