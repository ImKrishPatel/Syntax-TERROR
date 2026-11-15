
from pathlib import Path
import json

import numpy as np
import pandas as pd

from sklearn.linear_model import LinearRegression
from sklearn.metrics import (
    r2_score,
    mean_absolute_error,
    mean_squared_error,
)

DATA = Path(__file__).resolve().parent / "data"
MATCH_FILE = DATA / "real_frp_matches_multi_date.csv"

CHUNK_SIZE = 250_000
# Grid used by the web app (CONFIG.cellSize). Per-pixel FRP pairs are noisy
# (different pixel sizes, view angles and overpass times); summed per grid cell
# and day, VIIRS predicts MODIS far better, and that is the scale the app shows.
GRID_DEGREES = 2.0


def main():
    print("MULTI-DATE FRP CALIBRATION")
    print("-------------------------")

    # First pass: identify the available dates.
    print("\nReading available dates...")

    all_dates = set()

    for chunk in pd.read_csv(
        MATCH_FILE,
        usecols=["date"],
        chunksize=CHUNK_SIZE,
    ):
        all_dates.update(chunk["date"].dropna().unique())

    dates = sorted(all_dates)

    if len(dates) < 2:
        raise ValueError("Not enough dates for calibration.")

    # Hold out the latest 20% of dates for testing.
    cutoff_index = int(len(dates) * 0.8)
    cutoff_date = dates[cutoff_index]

    print("Available dates:", len(dates))
    print("Training dates:", dates[0], "to", dates[cutoff_index - 1])
    print("Testing dates:", cutoff_date, "to", dates[-1])

    # Second pass: collect positive FRP pairs.
    print("\nReading matched FRP observations...")

    train_x = []
    train_y = []
    test_x = []
    test_y = []

    train_count = 0
    test_count = 0
    cell_parts = []

    for chunk in pd.read_csv(
        MATCH_FILE,
        usecols=["date", "latitude_modis", "longitude_modis", "frp_modis", "frp_viirs"],
        chunksize=CHUNK_SIZE,
    ):
        chunk["frp_modis"] = pd.to_numeric(
            chunk["frp_modis"], errors="coerce"
        )
        chunk["frp_viirs"] = pd.to_numeric(
            chunk["frp_viirs"], errors="coerce"
        )

        chunk = chunk[
            np.isfinite(chunk["frp_modis"])
            & np.isfinite(chunk["frp_viirs"])
            & (chunk["frp_modis"] > 0)
            & (chunk["frp_viirs"] > 0)
        ]

        cell_parts.append(
            chunk.assign(
                lat_cell=(chunk["latitude_modis"] // GRID_DEGREES).astype(int),
                lon_cell=(chunk["longitude_modis"] // GRID_DEGREES).astype(int),
            ).groupby(["date", "lat_cell", "lon_cell"])[["frp_modis", "frp_viirs"]].sum()
        )

        training = chunk[chunk["date"] < cutoff_date]
        testing = chunk[chunk["date"] >= cutoff_date]

        if not training.empty:
            train_x.append(training["frp_viirs"].to_numpy())
            train_y.append(training["frp_modis"].to_numpy())
            train_count += len(training)

        if not testing.empty:
            test_x.append(testing["frp_viirs"].to_numpy())
            test_y.append(testing["frp_modis"].to_numpy())
            test_count += len(testing)

    if train_count == 0 or test_count == 0:
        raise ValueError("Training or testing data is empty.")

    X_train = np.concatenate(train_x).reshape(-1, 1)
    y_train = np.concatenate(train_y)

    X_test = np.concatenate(test_x).reshape(-1, 1)
    y_test = np.concatenate(test_y)

    print("\nPositive training pairs:", train_count)
    print("Positive testing pairs:", test_count)

    # Ordinary linear regression.
    linear_model = LinearRegression()
    linear_model.fit(X_train, y_train)

    linear_predictions = linear_model.predict(X_test)

    # Log-transformed linear regression.
    log_model = LinearRegression()
    log_model.fit(np.log1p(X_train), np.log1p(y_train))

    log_predictions = np.expm1(
        log_model.predict(np.log1p(X_test))
    )

    def evaluate(name, actual, predicted):
        results = {
            "r2": float(r2_score(actual, predicted)),
            "mae": float(mean_absolute_error(actual, predicted)),
            "rmse": float(
                np.sqrt(mean_squared_error(actual, predicted))
            ),
        }

        print(f"\n{name}")
        print("R-squared:", round(results["r2"], 5))
        print("MAE:", round(results["mae"], 3))
        print("RMSE:", round(results["rmse"], 3))

        return results

    linear_results = evaluate(
        "ORDINARY LINEAR MODEL - TEST DATA",
        y_test,
        linear_predictions,
    )

    log_results = evaluate(
        "LOG LINEAR MODEL - TEST DATA",
        y_test,
        log_predictions,
    )

    # Grid-scale model: matched FRP summed per grid cell and day.
    cells = pd.concat(cell_parts).groupby(level=[0, 1, 2]).sum().reset_index()
    cell_train = cells[cells["date"] < cutoff_date]
    cell_test = cells[cells["date"] >= cutoff_date]
    grid_model = LinearRegression()
    grid_model.fit(cell_train[["frp_viirs"]], cell_train["frp_modis"])
    grid_results = evaluate(
        f"GRID MODEL ({GRID_DEGREES:g} DEGREE CELL-DAY SUMS) - TEST DATA",
        cell_test["frp_modis"],
        grid_model.predict(cell_test[["frp_viirs"]]),
    )

    # Save the models' coefficients and test metrics.
    results = {
        "calibration_direction": "VIIRS_FRP_to_MODIS_FRP",
        "target": "MODIS FRP (MW)",
        "predictor": "VIIRS FRP (MW)",
        "split_method": "chronological_date_split",
        "training_end_date": dates[cutoff_index - 1],
        "testing_start_date": cutoff_date,
        "testing_end_date": dates[-1],
        "training_dates": cutoff_index,
        "testing_dates": len(dates) - cutoff_index,
        "positive_training_pairs": train_count,
        "positive_testing_pairs": test_count,
        "ordinary_linear": {
            "intercept": float(linear_model.intercept_),
            "slope": float(linear_model.coef_[0]),
            "test_metrics": linear_results,
        },
        "log_linear": {
            "intercept": float(log_model.intercept_),
            "slope": float(log_model.coef_[0]),
            "test_metrics": log_results,
        },
        "grid_cell_day": {
            "grid_degrees": GRID_DEGREES,
            "intercept": float(grid_model.intercept_),
            "slope": float(grid_model.coef_[0]),
            "training_cells": int(len(cell_train)),
            "testing_cells": int(len(cell_test)),
            "test_metrics": grid_results,
        },
        "status": "preliminary_temporal_holdout_evaluation",
    }

    output_json = DATA / "frp_multi_date_calibration_results.json"

    with open(output_json, "w") as file:
        json.dump(results, file, indent=4)

    # Save a small prediction sample, not millions of rows.
    prediction_sample = pd.DataFrame({
        "actual_modis_frp": y_test[:10000],
        "viirs_frp": X_test[:10000, 0],
        "predicted_modis_linear": linear_predictions[:10000],
        "predicted_modis_log": log_predictions[:10000],
    })

    prediction_sample.to_csv(
        DATA / "frp_multi_date_calibration_predictions_sample.csv",
        index=False,
    )

    print("\nCALIBRATION COMPLETED")
    print("Results:", output_json)
    print("Prediction sample saved.")
    print("\nNote: This is a temporal holdout evaluation,")
    print("not yet independent scientific validation.")


if __name__ == "__main__":
    main()
