
import numpy as np
import pandas as pd
from pathlib import Path

# Reproducible random data
rng = np.random.default_rng(42)

# Number of simulated fire observations
N = 1000

# Generate global coordinates
latitude = rng.uniform(-60, 75, N)
longitude = rng.uniform(-180, 180, N)

# Generate dates between 2012 and 2026
start_date = pd.Timestamp("2012-01-20")
end_date = pd.Timestamp("2026-11-14")

random_days = rng.integers(
    0, (end_date - start_date).days + 1, N
)

dates = start_date + pd.to_timedelta(
    random_days, unit="D"
)

# Generate valid acquisition times (HHMM)
minutes = rng.integers(0, 1440, N)
times = [
    f"{m // 60:02d}{m % 60:02d}"
    for m in minutes
]

# Simulate underlying fire radiative power (MW)
true_frp = rng.lognormal(
    mean=2.0,
    sigma=1.0,
    size=N
)

# Artificial sensor measurements for testing
modis_frp = (
    true_frp * 0.90 + rng.normal(0, 1.5, N)
)

viirs_frp = (
    true_frp * 1.10 + rng.normal(0, 1.5, N)
)

# FRP cannot be negative
modis_frp = np.maximum(modis_frp, 0)
viirs_frp = np.maximum(viirs_frp, 0)

# MODIS dataset
modis = pd.DataFrame({
    "mock_pair_id": range(N),
    "latitude": latitude,
    "longitude": longitude,
    "acq_date": dates.strftime("%Y-%m-%d"),
    "acq_time": times,
    "satellite": rng.choice(["Terra", "Aqua"], N),
    "instrument": "MODIS",
    "frp": modis_frp.round(3),
})

# VIIRS dataset
viirs = pd.DataFrame({
    "mock_pair_id": range(N),
    "latitude": latitude + rng.normal(0, 0.005, N),
    "longitude": longitude + rng.normal(0, 0.005, N),
    "acq_date": dates.strftime("%Y-%m-%d"),
    "acq_time": times,
    "satellite": "Suomi-NPP",
    "instrument": "VIIRS",
    "frp": viirs_frp.round(3),
})

# Save generated datasets
output_dir = Path(__file__).parent / "data"
output_dir.mkdir(parents=True, exist_ok=True)

modis.to_csv(output_dir / "modis_mock.csv", index=False)
viirs.to_csv(output_dir / "viirs_mock.csv", index=False)

print("Mock data generated successfully!")
print(f"MODIS records: {len(modis)}")
print(f"VIIRS records: {len(viirs)}")
print(f"Output directory: {output_dir}")
