
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    r2_score,
    mean_absolute_error,
    mean_squared_error,
)

DATA = Path("shriya_backend/data")

matches = pd.read_csv(
    DATA / "real_frp_matches_2024-08-10.csv"
)

# Keep positive FRP pairs for this comparison.
df = matches[
    (matches["frp_modis"] > 0)
    & (matches["frp_viirs"] > 0)
].copy()

X = df[["frp_viirs"]].to_numpy()
y = df["frp_modis"].to_numpy()

# Model 1: Ordinary linear regression.
linear_model = LinearRegression()
linear_model.fit(X, y)

linear_predictions = linear_model.predict(X)

# Model 2: Log-transformed linear regression.
X_log = np.log1p(X)
y_log = np.log1p(y)

log_model = LinearRegression()
log_model.fit(X_log, y_log)

log_predictions = np.expm1(
    log_model.predict(X_log)
)

def report(name, actual, predicted):
    print(f"\n{name}")
    print("R-squared:", round(r2_score(actual, predicted), 4))
    print("MAE:", round(mean_absolute_error(actual, predicted), 2))
    print(
        "RMSE:",
        round(np.sqrt(mean_squared_error(actual, predicted)), 2),
    )


print("Positive matched pairs:", len(df))

report("ORDINARY LINEAR MODEL", y, linear_predictions)
report("LOG-TRANSFORMED MODEL", y, log_predictions)

print("\nLOG MODEL COEFFICIENTS")
print("Intercept:", round(log_model.intercept_, 5))
print("Slope:", round(log_model.coef_[0], 5))

# Save the fitted pilot coefficients.
results = pd.DataFrame([{
    "date": "2024-08-10",
    "model": "log_linear",
    "intercept": log_model.intercept_,
    "slope": log_model.coef_[0],
    "positive_pairs": len(df),
    "status": "exploratory_one_day_fit",
}])

results.to_csv(
    DATA / "real_frp_calibration_pilot.csv",
    index=False,
)

print("\nSaved real_frp_calibration_pilot.csv")
print("\nCalibration comparison completed.")
