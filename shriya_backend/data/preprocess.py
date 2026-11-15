
import pandas as pd
from pathlib import Path

# Project paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR

def load_modis_data():
    """Load the mock MODIS dataset."""

    file_path = DATA_DIR / "modis_mock.csv"

    if not file_path.exists():
        raise FileNotFoundError(
            f"MODIS dataset not found: {file_path}"
        )

    df = pd.read_csv(file_path)

    print("MODIS data loaded successfully")
    print("Original shape:", df.shape)

    return df


def clean_modis_data(df):
    print("Cleaning MODIS data...")

    required_columns = [
        "latitude",
        "longitude",
        "acq_date",
        "frp"
    ]

    for column in required_columns:
        if column not in df.columns:
            raise ValueError(f"Missing required column: {column}")

    # Convert numeric columns
    for column in ["latitude", "longitude", "frp"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # Remove missing essential values
    df = df.dropna(subset=required_columns)

    # Validate geographic coordinates
    df = df[
        df["latitude"].between(-90, 90) &
        df["longitude"].between(-180, 180)
    ]

    # FRP cannot be negative
    df = df[df["frp"] >= 0]

    # Parse acquisition date
    df["acq_date"] = pd.to_datetime(
        df["acq_date"],
        errors="coerce"
    )

    df = df.dropna(subset=["acq_date"])

    # Remove exact duplicate records
    df = df.drop_duplicates()

    df = df.reset_index(drop=True)

    print("Cleaning completed!")
    print("Cleaned records:", len(df))

    return df


def main():
    df = load_modis_data()
    cleaned_df = clean_modis_data(df)

    output_path = DATA_DIR / "modis_cleaned.csv"
    cleaned_df.to_csv(output_path, index=False)

    print("Cleaned MODIS data saved:", output_path)


if __name__ == "__main__":
    main()
