
from pathlib import Path
import pandas as pd

DATA = Path("shriya_backend/data")

modis = pd.read_csv(
    DATA / "modis_daily_frp_real.csv",
    parse_dates=["acq_date"],
)

viirs = pd.read_csv(
    DATA / "viirs_daily_frp_real.csv",
    parse_dates=["acq_date"],
)

combined = modis.merge(
    viirs,
    on="acq_date",
    suffixes=("_modis", "_viirs"),
)

# Only use dates with substantial observations from both sensors.
combined = combined[
    (combined["hotspot_count_modis"] >= 5000)
    & (combined["hotspot_count_viirs"] >= 20000)
].copy()

combined["year"] = combined["acq_date"].dt.year
combined["month"] = combined["acq_date"].dt.month

# Divide the year into four seasons.
combined["season"] = combined["month"].map({
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Autumn", 10: "Autumn", 11: "Autumn",
})

# Select the strongest observation date from each year-season.
selected = (
    combined.sort_values("smaller_sensor_count"
                         if "smaller_sensor_count" in combined.columns
                         else "hotspot_count_modis",
                         ascending=False)
)

selected = selected.drop_duplicates(
    subset=["year", "season"]
)

# Keep a maximum of 12 dates, distributed across years.
selected = selected.sort_values(
    ["year", "month"]
).head(12)

output = selected[[
    "acq_date",
    "hotspot_count_modis",
    "hotspot_count_viirs",
    "year",
    "season",
]]

output.to_csv(
    DATA / "selected_calibration_dates.csv",
    index=False,
)

print("\nSelected calibration dates:")
print(output.to_string(index=False))

print("\nSaved selected_calibration_dates.csv")
