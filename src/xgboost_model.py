"""
NASA Space Apps 2026 - fire data processing + 1-30 day fire forecast.

Stage 1  read FIRMS zips -> clean, filtered hotspots per 0.25 deg cell per day
Stage 2  combine every archive with DuckDB (MODIS and VIIRS kept apart)
Stage 3  build forecast features (recent fire + NASA POWER weather/drought + season)
Stage 4  XGBoost forecast of the next 7 / 30 days; each year is tested by a model
         that never saw it, write public/data/forecast.json

Run (from the repo root):
    python src/xgboost_model.py                      # all stages
    python src/xgboost_model.py --skip-processing    # reuse stage 1/2 output
Inputs:
    src/data/raw/DL_FIRE_*.zip        FIRMS archive downloads (MODIS M-C61, VIIRS SV-C2)
    src/data/raw/POWER_Point_Daily_*.csv   NASA POWER daily point file
        (T2M, RH2M, WS2M, PRECTOTCORR, GWETROOT, GWETTOP)
"""

import argparse
import gc
import json
import re
import time
import zipfile
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


# =====================================================
# 1. CONFIGURATION
# =====================================================

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent

RAW_DIR = BASE_DIR / "data" / "raw"
TEMP_DIR = BASE_DIR / "data" / "intermediate"
OUTPUT_DIR = BASE_DIR / "data" / "processed"
APP_FORECAST = REPO_DIR / "public" / "data" / "forecast.json"

GRID_SIZE = 0.25
CHUNK_SIZE = 200_000

# None = keep the whole world in stage 1/2.
# Otherwise (min_lon, max_lon, min_lat, max_lat), e.g. Alberta (-120, -110, 49, 60)
REGION_BBOX = None

# Detection filters
MODIS_MIN_CONFIDENCE = 30          # MODIS confidence is 0-100; < 30 is "low"
VIIRS_DROP_CONFIDENCE = {"l"}      # VIIRS confidence is l / n / h
KEEP_TYPES = {0}                   # 0 = vegetation fire; 1 volcano, 2 static land source, 3 offshore

# Forecast settings
FORECAST_HALF_HEIGHT = 2.0         # degrees of latitude either side of the weather point
FORECAST_HALF_WIDTH = 3.5          # degrees of longitude either side
WINDOWS = (7, 30)                  # forecast totals for the next 7 and next 30 days
QUANTILES = (0.05, 0.5, 0.95)      # low / expected / high shown in the app
FIRE_SEASON = range(4, 11)         # April-October: the months the test scores use

USECOLS = ["latitude", "longitude", "acq_date", "acq_time", "satellite",
           "confidence", "frp", "daynight", "type"]


# =====================================================
# 2. STAGE 1 - READ ZIP FILES
# =====================================================

def identify_sensor(filename):
    name = filename.upper()
    if "M-C61" in name:
        return "MODIS"
    if "SV-C2" in name or "J1V-C2" in name or "J2V-C2" in name:
        return "VIIRS"
    raise ValueError(f"Cannot identify satellite type: {filename}")


def clean_chunk(chunk, sensor, after_date=None):
    """Filter one CSV chunk. Returns (clean chunk, counts of what was dropped)."""
    dropped = {}
    chunk.columns = [c.strip().lower() for c in chunk.columns]

    for col in ("latitude", "longitude", "frp"):
        chunk[col] = pd.to_numeric(chunk.get(col), errors="coerce")
    chunk["acq_date"] = pd.to_datetime(chunk["acq_date"], errors="coerce").dt.normalize()
    n = len(chunk)
    chunk = chunk.dropna(subset=["latitude", "longitude", "acq_date"])
    chunk = chunk[chunk["latitude"].between(-90, 90) & chunk["longitude"].between(-180, 180)]
    dropped["bad_rows"] = n - len(chunk)

    if REGION_BBOX is not None:
        min_lon, max_lon, min_lat, max_lat = REGION_BBOX
        chunk = chunk[chunk["longitude"].between(min_lon, max_lon)
                      & chunk["latitude"].between(min_lat, max_lat)]

    # NRT files repeat days that the archive file already has: keep only newer days.
    if after_date is not None:
        n = len(chunk)
        chunk = chunk[chunk["acq_date"] > after_date]
        dropped["overlap_with_archive"] = n - len(chunk)

    # Low-confidence detections
    n = len(chunk)
    if sensor == "MODIS":
        conf = pd.to_numeric(chunk["confidence"], errors="coerce")
        chunk = chunk[conf >= MODIS_MIN_CONFIDENCE]
    else:
        conf = chunk["confidence"].astype(str).str.strip().str.lower()
        chunk = chunk[~conf.isin(VIIRS_DROP_CONFIDENCE)]
    dropped["low_confidence"] = n - len(chunk)

    # Gas flares, volcanoes, other static heat (archive files only have "type")
    if "type" in chunk.columns:
        n = len(chunk)
        t = pd.to_numeric(chunk["type"], errors="coerce")
        chunk = chunk[t.isin(KEEP_TYPES)]
        dropped["not_vegetation_fire"] = n - len(chunk)

    # Same satellite pass, same pixel, listed twice
    n = len(chunk)
    chunk = chunk.drop_duplicates(subset=["latitude", "longitude", "acq_date", "acq_time", "satellite"])
    dropped["duplicates"] = n - len(chunk)
    return chunk, dropped


def aggregate_chunk(chunk, sensor):
    chunk = chunk.assign(
        grid_lat=np.floor(chunk["latitude"] / GRID_SIZE) * GRID_SIZE,
        grid_lon=np.floor(chunk["longitude"] / GRID_SIZE) * GRID_SIZE,
        is_night=(chunk["daynight"].astype(str).str.upper() == "N").astype(int),
    )
    out = (chunk.groupby(["grid_lat", "grid_lon", "acq_date"])
           .agg(hotspot_count=("latitude", "size"),
                night_count=("is_night", "sum"),
                frp_sum=("frp", "sum"),
                frp_count=("frp", "count"))
           .reset_index())
    out["sensor"] = sensor
    return out


def process_csv(archive, csv_name, sensor, writer_holder, output_file, totals, after_date=None):
    max_date = None
    with archive.open(csv_name) as file:
        reader = pd.read_csv(file, chunksize=CHUNK_SIZE, low_memory=False,
                             usecols=lambda c: c.strip().lower() in USECOLS)
        for chunk in reader:
            totals["read"] += len(chunk)
            chunk, dropped = clean_chunk(chunk, sensor, after_date)
            for key, value in dropped.items():
                totals[key] = totals.get(key, 0) + value
            if chunk.empty:
                continue
            totals["kept"] += len(chunk)
            d = chunk["acq_date"].max()
            max_date = d if max_date is None or d > max_date else max_date

            table = pa.Table.from_pandas(aggregate_chunk(chunk, sensor), preserve_index=False)
            if writer_holder[0] is None:
                writer_holder[0] = pq.ParquetWriter(output_file, table.schema, compression="zstd")
            writer_holder[0].write_table(table)
            del chunk, table
            gc.collect()
    return max_date


def process_zip(zip_path):
    sensor = identify_sensor(zip_path.name)
    output_file = TEMP_DIR / (zip_path.stem + "_processed.parquet")
    if output_file.exists():
        output_file.unlink()

    totals = {"read": 0, "kept": 0}
    writer = [None]
    start = time.time()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [n for n in archive.namelist()
                     if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")]
            if not names:
                raise ValueError(f"No CSV files found inside {zip_path.name}")
            # Archive (science-quality) first, then only the newer days from NRT.
            archive_names = [n for n in names if "nrt" not in n.lower()]
            nrt_names = [n for n in names if "nrt" in n.lower()]
            last_archive_day = None
            for name in archive_names:
                d = process_csv(archive, name, sensor, writer, output_file, totals)
                if d is not None and (last_archive_day is None or d > last_archive_day):
                    last_archive_day = d
            for name in nrt_names:
                process_csv(archive, name, sensor, writer, output_file, totals,
                            after_date=last_archive_day)
    finally:
        if writer[0] is not None:
            writer[0].close()

    print(f"{zip_path.name} ({sensor}): read {totals.pop('read'):,}, "
          f"kept {totals.pop('kept'):,}, dropped {totals} in {time.time() - start:.0f}s")


def process_all_zips():
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    zips = sorted(p for p in RAW_DIR.iterdir() if p.suffix.lower() == ".zip")
    if not zips:
        raise FileNotFoundError(f"No ZIP files found in {RAW_DIR}")
    print(f"Found {len(zips)} ZIP files")
    for z in zips:
        process_zip(z)


# =====================================================
# 3. STAGE 2 - COMBINE (MODIS AND VIIRS KEPT APART)
# =====================================================

def combine_datasets():
    """One row per cell per day. MODIS and VIIRS are separate columns and are never
    added together: both satellites see the same fires, so a sum would count them
    twice, and their FRP is on different scales (Shriya's calibration handles that).
    `hotspots` is the MODIS count, the one sensor that covers every year."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(TEMP_DIR.glob("*_processed.parquet"))
    if not files:
        raise FileNotFoundError("No intermediate Parquet files found.")
    file_sql = "[" + ", ".join("'" + p.resolve().as_posix().replace("'", "''") + "'" for p in files) + "]"
    out = (OUTPUT_DIR / "harmonized_fire_data.parquet").resolve().as_posix()

    def per_sensor(sensor):
        s = f"sensor = '{sensor}'"
        low = sensor.lower()
        return f"""
            SUM(CASE WHEN {s} THEN hotspot_count ELSE 0 END) AS {low}_hotspots,
            SUM(CASE WHEN {s} THEN night_count ELSE 0 END) AS {low}_night_hotspots,
            SUM(CASE WHEN {s} THEN frp_sum END)
              / NULLIF(SUM(CASE WHEN {s} THEN frp_count ELSE 0 END), 0) AS {low}_mean_frp,
            SUM(CASE WHEN {s} THEN frp_sum ELSE 0 END) AS {low}_total_frp"""

    con = duckdb.connect()
    con.execute("SET threads TO 4")
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"SET temp_directory = '{TEMP_DIR.resolve().as_posix()}'")
    try:
        con.execute(f"""
            COPY (
                SELECT grid_lat, grid_lon, CAST(acq_date AS DATE) AS acq_date,
                       {per_sensor('MODIS')},
                       {per_sensor('VIIRS')}
                FROM read_parquet({file_sql})
                GROUP BY grid_lat, grid_lon, CAST(acq_date AS DATE)
            ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
        print(con.execute(f"""
            SELECT COUNT(*) AS cell_days, MIN(acq_date) AS first_date, MAX(acq_date) AS last_date,
                   SUM(modis_hotspots) AS modis, SUM(viirs_hotspots) AS viirs
            FROM read_parquet('{out}')""").fetchdf().to_string(index=False))
    finally:
        con.close()


# =====================================================
# 4. STAGE 3 - FORECAST FEATURES AND TARGETS
# =====================================================

def read_power(path):
    """NASA POWER daily point CSV -> daily DataFrame, plus the point's lat/lon."""
    text = Path(path).read_text()
    lat = float(re.search(r"latitude\s+(-?[\d.]+)", text).group(1))
    lon = float(re.search(r"longitude\s+(-?[\d.]+)", text).group(1))
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("-END HEADER-")) + 1
    w = pd.read_csv(path, skiprows=start).replace(-999.0, np.nan)
    w["date"] = pd.to_datetime(w["YEAR"].astype(str), format="%Y") + pd.to_timedelta(w["DOY"] - 1, unit="D")
    return w.drop(columns=["YEAR", "DOY"]).set_index("date").sort_index(), lat, lon


def region_series(lat, lon):
    """Daily MODIS hotspot total for the forecast box around the weather point."""
    out = (OUTPUT_DIR / "harmonized_fire_data.parquet").resolve().as_posix()
    box = (lat - FORECAST_HALF_HEIGHT, lat + FORECAST_HALF_HEIGHT,
           lon - FORECAST_HALF_WIDTH, lon + FORECAST_HALF_WIDTH)
    con = duckdb.connect()
    try:
        first, last = con.execute(f"SELECT MIN(acq_date), MAX(acq_date) FROM read_parquet('{out}') "
                                  "WHERE modis_hotspots > 0").fetchone()
        df = con.execute(f"""
            SELECT acq_date AS date, SUM(modis_hotspots) AS fires,
                   SUM(modis_night_hotspots) AS night_fires, SUM(modis_total_frp) AS frp
            FROM read_parquet('{out}')
            WHERE grid_lat + {GRID_SIZE / 2} BETWEEN {box[0]} AND {box[1]}
              AND grid_lon + {GRID_SIZE / 2} BETWEEN {box[2]} AND {box[3]}
            GROUP BY acq_date""").fetchdf()
    finally:
        con.close()
    days = pd.date_range(first, last, freq="D")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").reindex(days, fill_value=0).astype(float), box


def doy_climatology(series, years, window=7):
    """Average value for each day of year over the given years, smoothed +-window days."""
    s = series[series.index.year.isin(years)]
    by_doy = s.groupby(np.minimum(s.index.dayofyear, 365)).mean().reindex(range(1, 366)).fillna(0)
    padded = np.concatenate([by_doy.values[-window:], by_doy.values, by_doy.values[:window]])
    smooth = np.convolve(padded, np.ones(2 * window + 1) / (2 * window + 1), mode="valid")
    return pd.Series(smooth, index=range(1, 366))


def build_features(fire, weather, train_years):
    """One row per day t, using only fire and weather known on day t."""
    d = pd.DataFrame(index=fire.index)
    y = fire["fires"]
    d["fires_today"] = y
    for k in (3, 7, 14, 30):
        d[f"fires_last_{k}d"] = y.rolling(k, min_periods=1).sum()
    d["night_share_7d"] = (fire["night_fires"].rolling(7, min_periods=1).sum()
                           / d["fires_last_7d"].replace(0, np.nan)).fillna(0)
    d["frp_per_fire_7d"] = (fire["frp"].rolling(7, min_periods=1).sum()
                            / d["fires_last_7d"].replace(0, np.nan)).fillna(0)
    burning = (y >= 5).astype(int)
    d["days_since_fire"] = burning.groupby((burning == 1).cumsum()).cumcount().clip(upper=365)

    w = weather.reindex(d.index).ffill(limit=3)
    d["temp"] = w["T2M"]
    d["temp_7d"] = w["T2M"].rolling(7, min_periods=3).mean()
    d["humidity"] = w["RH2M"]
    d["humidity_7d"] = w["RH2M"].rolling(7, min_periods=3).mean()
    d["wind"] = w["WS2M"]
    d["rain_7d"] = w["PRECTOTCORR"].rolling(7, min_periods=3).sum()
    d["rain_30d"] = w["PRECTOTCORR"].rolling(30, min_periods=10).sum()
    rain_clim = doy_climatology(w["PRECTOTCORR"].rolling(30, min_periods=10).sum(), train_years)
    d["rain_30d_vs_normal"] = d["rain_30d"] - rain_clim.reindex(np.minimum(d.index.dayofyear, 365)).values
    wet = (w["PRECTOTCORR"] >= 1).astype(int)
    d["days_since_rain"] = wet.groupby((wet == 1).cumsum()).cumcount().clip(upper=60)
    d["soil_root"] = w["GWETROOT"]
    d["soil_top"] = w["GWETTOP"]
    d["soil_root_change_30d"] = w["GWETROOT"] - w["GWETROOT"].shift(30)
    d["hot_dry_windy"] = (d["temp"].clip(lower=0) * (100 - d["humidity"]) / 100 * d["wind"])

    doy = np.minimum(d.index.dayofyear, 365)
    d["doy_sin"] = np.sin(2 * np.pi * doy / 365)
    d["doy_cos"] = np.cos(2 * np.pi * doy / 365)
    return d


def add_target(d, fire, train_years, days):
    """Target = total hotspots over the next `days` days (t+1 .. t+days).
    The model learns the log change from the recent rate, so it scales with whatever is
    burning now (2023 was 4x bigger than any earlier year here, and trees cannot predict
    past the largest value they were trained on)."""
    y = fire["fires"]
    d = d.copy()
    d["target"] = y[::-1].rolling(days).sum()[::-1].shift(-1).reindex(d.index)
    clim = doy_climatology(y, train_years)
    doy = np.minimum(d.index.dayofyear, 365).to_numpy()
    d["usual"] = sum(clim.reindex((doy + k - 1) % 365 + 1).to_numpy() for k in range(1, days + 1))
    recent, span = ("fires_last_30d", 30) if days >= 30 else ("fires_last_7d", 7)
    d["persistence"] = d[recent] / span * days
    d["log_base"] = np.log1p(d["persistence"])
    d["usual_vs_now"] = np.log1p(d["usual"]) - d["log_base"]
    d["change"] = np.log1p(d["target"]) - d["log_base"]
    return d


NOT_FEATURES = {"target", "change", "persistence"}


# =====================================================
# 5. STAGE 4 - TRAIN, TEST, WRITE forecast.json
# =====================================================

def fit_model(train, features):
    """One XGBoost model giving three quantiles (low / expected / high) of the log change."""
    import xgboost as xgb
    model = xgb.XGBRegressor(objective="reg:quantileerror", quantile_alpha=np.array(QUANTILES),
                             n_estimators=400, learning_rate=0.05, max_depth=4, subsample=0.8,
                             colsample_bytree=0.8, min_child_weight=20, tree_method="hist")
    model.fit(train[features], train["change"])
    return model


def predict(model, frame, features):
    q = np.expm1(frame["log_base"].to_numpy()[:, None] + model.predict(frame[features])).clip(min=0)
    return q[:, 1], q[:, 0], q[:, 2]                 # expected, low, high


def leave_one_year_out(fire, weather, years, days):
    """Each year is forecast by a model trained on every other year."""
    results, models = [], {}
    for year in years:
        other = [yr for yr in years if yr != year]
        d = add_target(build_features(fire, weather, other), fire, other, days).dropna(subset=["target"])
        features = [c for c in d.columns if c not in NOT_FEATURES]
        start = pd.Timestamp(year, 1, 1)
        # train rows may not look into the test year, through features or the target window
        leak = (d.index >= start - pd.Timedelta(days=days)) & (d.index < pd.Timestamp(year + 1, 1, 1) + pd.Timedelta(days=30))
        model = fit_model(d[~leak], features)
        test = d[d.index.year == year]
        e, lo, hi = predict(model, test, features)
        results.append(test.assign(expected=e, low=lo, high=hi))
        models[year] = (model, features, d)
    return pd.concat(results), models


def score(t):
    s = t[t.index.month.isin(FIRE_SEASON)]
    mae = lambda c: round(float((s[c] - s["target"]).abs().mean()), 1)
    log_r2 = lambda c: round(float(1 - ((np.log1p(s["target"]) - np.log1p(s[c])) ** 2).sum()
                                   / ((np.log1p(s["target"]) - np.log1p(s["target"]).mean()) ** 2).sum()), 2)
    return {
        "mae_model": mae("expected"), "mae_repeat_recent": mae("persistence"), "mae_usual_for_date": mae("usual"),
        "better_than_repeat_recent_pct": round(100 * (1 - mae("expected") / mae("persistence"))),
        "better_than_usual_pct": round(100 * (1 - mae("expected") / mae("usual"))),
        "log_r2_model": log_r2("expected"), "log_r2_repeat_recent": log_r2("persistence"),
        "log_r2_usual_for_date": log_r2("usual"),
        "share_inside_range": round(float(((s["target"] >= s["low"]) & (s["target"] <= s["high"])).mean()), 2),
    }


def train_and_forecast(power_path):
    weather, lat, lon = read_power(power_path)
    fire, box = region_series(lat, lon)
    y = fire["fires"]
    years = [yr for yr in sorted(set(y.index.year)) if (y.index.year == yr).sum() >= 365
             and yr < y.index[-1].year]                              # full years only
    print(f"Forecast box lat {box[0]:.2f}..{box[1]:.2f}, lon {box[2]:.2f}..{box[3]:.2f}; "
          f"{y.index[0].date()} to {y.index[-1].date()}, {int(y.sum()):,} MODIS hotspots; "
          f"testing {years[0]}-{years[-1]}")

    tests, models, metrics = {}, {}, {}
    for days in WINDOWS:
        tests[days], models[days] = leave_one_year_out(fire, weather, years, days)
        metrics[f"next_{days}_days"] = score(tests[days])
    print(f"\nTEST (Apr-Oct, {years[0]}-{years[-1]}, each year forecast by a model that never saw it)")
    print(pd.DataFrame(metrics).to_string())

    # ---- forecast.json chart: the busiest 30 days of the last three full years ----
    recent = y[y.index.year.isin(years[-3:])]
    made_on = recent.rolling(30).sum().shift(-29).idxmax() - pd.Timedelta(days=1)
    t7, t30 = tests[7], tests[30]
    week_avg = y.rolling(7).mean()
    days = []
    for d in pd.date_range(made_on - pd.Timedelta(days=29), made_on):
        r = t7.loc[d - pd.Timedelta(days=7)]                        # forecast made a week earlier
        days.append({"date": d, "expected": r.expected / 7, "low": r.low / 7, "high": r.high / 7})
    f7, f30 = t7.loc[made_on], t30.loc[made_on]
    for k in range(1, 31):
        if k <= 7:
            e, lo, hi = f7.expected / 7, f7.low / 7, f7.high / 7
        else:
            e, lo, hi = max(f30.expected - f7.expected, 0) / 23, f30.low / 30, f30.high / 30
        days.append({"date": made_on + pd.Timedelta(days=k), "expected": e,
                     "low": min(lo, e), "high": max(hi, e)})
    for d in days:
        d["actual"] = round(float(week_avg.loc[d["date"]]), 1)
        d["date"] = d["date"].strftime("%Y-%m-%d")
        for key in ("expected", "low", "high"):
            d[key] = round(float(d[key]), 1)

    # ---- live forecast from the newest day of data, model trained on every year ----
    live = {"made_on": y.index[-1].strftime("%Y-%m-%d")}
    importance = {}
    for days_ahead in WINDOWS:
        d = add_target(build_features(fire, weather, years), fire, years, days_ahead)
        features = [c for c in d.columns if c not in NOT_FEATURES]
        model = fit_model(d.dropna(subset=["target"]), features)
        e, lo, hi = predict(model, d.iloc[[-1]], features)
        live[f"next_{days_ahead}_days"] = {"expected": round(float(e[0])), "low": round(float(lo[0])),
                                           "high": round(float(hi[0]))}
        if days_ahead == 7:
            importance = (pd.Series(model.get_booster().get_score(importance_type="gain"))
                          .sort_values(ascending=False).head(10).round(2).to_dict())

    forecast = {
        "is_demo": False,
        "region": (f"Northern Alberta ({box[0]:.0f}-{box[1]:.0f}°N, {abs(box[3]):.0f}-{abs(box[2]):.0f}°W), "
                   f"{made_on.year}, a year the model never saw"),
        "made_on": made_on.strftime("%Y-%m-%d"),
        "unit": "MODIS hotspots per day (7-day average)",
        "model": "XGBoost, 5/50/95% quantiles of the next 7-day and 30-day hotspot totals",
        "note": ("Before made_on: each point is the 7-day forecast made a week earlier. After made_on: the "
                 "forecast made on made_on (days 1-7 from the 7-day forecast, days 8-30 from the 30-day one). "
                 "Actual = 7-day average. Inputs: recent fire, NASA POWER weather and soil moisture, season. "
                 f"The model for {made_on.year} was trained on every other year."),
        "weather_point": {"lat": lat, "lon": lon, "source": "NASA POWER (MERRA-2)"},
        "test_metrics": {"years": f"{years[0]}-{years[-1]}", "months": "April-October", **metrics},
        "live": live,
        "top_features": importance,
        "days": days,
    }
    APP_FORECAST.parent.mkdir(parents=True, exist_ok=True)
    APP_FORECAST.write_text(json.dumps(forecast, indent=1, ensure_ascii=False))
    (OUTPUT_DIR / "forecast_metrics.json").write_text(json.dumps(forecast["test_metrics"], indent=1))
    print(f"\nWrote {APP_FORECAST} (made_on {forecast['made_on']})")
    print("Live:", live)
    print("Top features:", list(importance)[:6])


# =====================================================
# 6. MAIN
# =====================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, default=RAW_DIR, help="folder with FIRMS zips and the POWER csv")
    parser.add_argument("--skip-processing", action="store_true", help="reuse stage 1/2 output")
    parser.add_argument("--power", type=Path, help="NASA POWER daily csv (default: first POWER_*.csv in --raw)")
    args = parser.parse_args()
    RAW_DIR = args.raw

    start = time.time()
    if not args.skip_processing:
        process_all_zips()
        combine_datasets()
    power = args.power or next(iter(sorted(RAW_DIR.glob("POWER_*.csv"))), None)
    if power is None:
        raise FileNotFoundError(f"No NASA POWER csv in {RAW_DIR}; pass --power")
    train_and_forecast(power)
    print(f"\nTotal time: {(time.time() - start) / 60:.1f} minutes")
