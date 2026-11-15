"""Step 1: data ingestion.

Two inputs are supported:

* the app's grid file (public/data/hotspots.csv, see DATA_FORMAT.md): monthly
  counts + FRP per 0.5 degree cell. This is the default so regimes always match
  what the map shows.
* raw FIRMS archive CSVs (MODIS and/or VIIRS). These add day/night and daily
  features that the monthly grid cannot give.

Both end up as the same monthly table:
    cell_id, lat, lon, year, month, count, frp, est_count
"""
import numpy as np
import pandas as pd

# FIRMS "type" column: 0 vegetation fire, 1 active volcano, 2 other static land
# source (gas flares, industry), 3 offshore. Only 0 is kept.
FIRMS_KEEP_TYPES = {0}
VIIRS_KEEP_CONF = {"n", "h", "nominal", "high"}


def snap(v, res):
    """Cell centre for a coordinate on a res-degree grid."""
    return np.floor(np.asarray(v, dtype=float) / res) * res + res / 2


def cell_id(lat, lon):
    return [f"{a:g}_{b:g}" for a, b in zip(lat, lon)]


def load_grid_csv(path):
    """Read hotspots.csv (DATA_FORMAT.md section 1) into the monthly table."""
    df = pd.read_csv(path)
    need = {"cell_id", "lat", "lon", "date", "hotspot_count", "frp_mw"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    d = pd.to_datetime(df["date"])
    est = df.get("is_estimated", False)
    if not isinstance(est, bool):
        est = est.astype(str).str.lower().eq("true")
    out = pd.DataFrame({
        "cell_id": df["cell_id"].astype(str),
        "lat": df["lat"].astype(float),
        "lon": df["lon"].astype(float),
        "year": d.dt.year,
        "month": d.dt.month,
        "count": df["hotspot_count"].astype(float),
        "frp": df["frp_mw"].astype(float),
    })
    out["est_count"] = np.where(est, out["count"], 0.0)
    # several rows per cell-month are allowed (e.g. weekly data): sum them
    return out.groupby(["cell_id", "lat", "lon", "year", "month"], as_index=False).sum()


def _csv_sources(paths):
    """Yield (name, open file) for every CSV, including CSVs inside FIRMS zips."""
    import zipfile
    for p in sorted(map(str, paths)):
        if p.lower().endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if n.lower().endswith(".csv"):
                        with z.open(n) as fh:
                            yield f"{p}:{n}", fh
        else:
            with open(p, "rb") as fh:
                yield p, fh


def load_firms_raw(paths, res=0.5, min_modis_conf=30, viirs_from=2012, sensor=None, chunksize=2_000_000):
    """Stream FIRMS archive CSVs / zips into a cell-day table.

    Never holds the raw points in memory: each chunk is filtered (confidence,
    FIRMS "type"), snapped to the grid and summed per cell and day.
    Files from overlapping requests (e.g. 2015-16 and 2016-21) would double
    count, so any day already covered by an earlier file is skipped.
    sensor: "MODIS" or "VIIRS" to keep one instrument (mixing both double counts).
    Returns columns: cell_id, lat, lon, date, count, frp, night, est_count.
    """
    cols = ["latitude", "longitude", "acq_date", "frp", "confidence", "daynight", "type", "instrument"]
    covered, parts, seen_instr = set(), [], set()
    for name, fh in _csv_sources(paths):
        file_dates, n_in, n_kept = set(), 0, 0
        for ch in pd.read_csv(fh, usecols=lambda c: c.lower() in cols, chunksize=chunksize, low_memory=False):
            ch.columns = [c.lower() for c in ch.columns]
            n_in += len(ch)
            instr = ch["instrument"].astype(str).str.upper() if "instrument" in ch else pd.Series("?", index=ch.index)
            seen_instr |= set(instr.unique())
            if sensor:
                ch, instr = ch[instr.eq(sensor.upper())], instr[instr.eq(sensor.upper())]
            if "type" in ch:
                ch = ch[ch["type"].isin(FIRMS_KEEP_TYPES)]
            conf = ch["confidence"]
            num = pd.to_numeric(conf, errors="coerce")
            keep = num.ge(min_modis_conf) | conf.astype(str).str.lower().isin(VIIRS_KEEP_CONF)
            ch = ch[keep]
            file_dates |= set(ch["acq_date"].unique())
            ch = ch[~ch["acq_date"].isin(covered)]
            if ch.empty:
                continue
            lat, lon = snap(ch["latitude"], res), snap(ch["longitude"], res)
            d = pd.DataFrame({"lat": lat, "lon": lon, "date": ch["acq_date"].to_numpy(),
                              "count": 1.0, "frp": ch["frp"].astype(float).to_numpy(),
                              "night": ch["daynight"].astype(str).str.upper().eq("N").to_numpy().astype(float)})
            n_kept += len(d)
            parts.append(d.groupby(["lat", "lon", "date"], as_index=False).sum())
        covered |= file_dates
        print(f"    {name.split('/')[-1]}: {n_in:,} rows, {n_kept:,} kept")
    if len({i for i in seen_instr if i in ("MODIS", "VIIRS")}) > 1 and not sensor:
        raise ValueError("Files mix MODIS and VIIRS; pass --sensor MODIS or --sensor VIIRS to avoid double counting")
    day = pd.concat(parts).groupby(["lat", "lon", "date"], as_index=False).sum()
    day["date"] = pd.to_datetime(day["date"])
    day["cell_id"] = cell_id(day["lat"], day["lon"])
    # A MODIS-only record is one sensor throughout: nothing is "estimated" and
    # 2012 is not a sensor change. Mixed/VIIRS records flag pre-2012 like the app.
    only_modis = sensor == "MODIS" or seen_instr <= {"MODIS", "?"}
    day["est_count"] = 0.0 if only_modis else np.where(day["date"].dt.year < viirs_from, day["count"], 0.0)
    return day


def points_to_monthly(day):
    g = day.assign(year=day["date"].dt.year, month=day["date"].dt.month)
    return (g.groupby(["cell_id", "lat", "lon", "year", "month"], as_index=False)
             [["count", "frp", "est_count"]].sum())
