import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def load_data():
    file_path = DATA_DIR / "frp_features.csv"
    df = pd.read_csv(file_path)

    df["acq_date_viirs"] = pd.to_datetime(df["acq_date_viirs"])

    return df


def daily_aggregation(df):
    """Summarize fire activity for each day."""

    daily = df.groupby("acq_date_viirs").agg(
        hotspot_count=("normalized_frp", "count"),
        average_frp=("normalized_frp", "mean"),
        maximum_frp=("normalized_frp", "max"),
        total_observed_frp=("normalized_frp", "sum")
    ).reset_index()

    return daily


def regional_aggregation(df):
    """Group observations into approximate geographic grid cells."""

    df = df.copy()

    df["latitude_band"] = (
        (df["latitude_viirs"] // 10) * 10
    ).astype(int)

    df["longitude_band"] = (
        (df["longitude_viirs"] // 10) * 10
    ).astype(int)

    regional = df.groupby(
        ["latitude_band", "longitude_band"],
        observed=True
    ).agg(
        hotspot_count=("normalized_frp", "count"),
        average_frp=("normalized_frp", "mean"),
        maximum_frp=("normalized_frp", "max"),
        total_observed_frp=("normalized_frp", "sum")
    ).reset_index()

    return regional


def main():
    df = load_data()

    daily = daily_aggregation(df)
    regional = regional_aggregation(df)

    daily.to_csv(DATA_DIR / "daily_frp_summary.csv", index=False)
    regional.to_csv(DATA_DIR / "regional_frp_summary.csv", index=False)

    print("\nFire Aggregation Complete")
    print("-------------------------")
    print(f"Observations processed: {len(df)}")
    print(f"Days represented: {len(daily)}")
    print(f"Geographic grid cells: {len(regional)}")

    print("\nTop 5 grid cells by average FRP:")
    print(
        regional.sort_values(
            "average_frp", ascending=False
        ).head(5).to_string(index=False)
    )

    print("\nAggregation files saved successfully.")


if __name__ == "__main__":
    main()