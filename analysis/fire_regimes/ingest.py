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


def load_firms_raw(paths, res=0.5, min_modis_conf=30, viirs_from=2012):
    """Read FIRMS archive CSVs into a point table (one row per detection).

    Filters: low confidence, and static sources / volcanoes / offshore via the
    "type" column when it exists. Archive files without "type" are handled
    later by the persistent-heat filter in features.py.
    Returns columns: cell_id, lat, lon, date, frp, is_night, is_estimated.
    """
    frames = []
    for p in paths:
        df = pd.read_csv(p, low_memory=False)
        df.columns = [c.lower() for c in df.columns]
        if "type" in df.columns:
            df = df[df["type"].isin(FIRMS_KEEP_TYPES)]
        conf = df["confidence"]
        if pd.api.types.is_numeric_dtype(conf):          # MODIS: 0-100
            df = df[conf >= min_modis_conf]
        else:                                             # VIIRS: l / n / h
            df = df[conf.astype(str).str.lower().isin(VIIRS_KEEP_CONF)]
        instr = str(df.get("instrument", pd.Series(["?"])).iloc[0]).upper() if len(df) else "?"
        frames.append(pd.DataFrame({
            "lat": df["latitude"].astype(float),
            "lon": df["longitude"].astype(float),
            "date": pd.to_datetime(df["acq_date"]),
            "frp": df["frp"].astype(float),
            "is_night": df.get("daynight", "D").astype(str).str.upper().eq("N"),
            "sensor": instr,
        }))
    pts = pd.concat(frames, ignore_index=True)
    pts["lat"], pts["lon"] = snap(pts["lat"], res), snap(pts["lon"], res)
    pts["cell_id"] = cell_id(pts["lat"], pts["lon"])
    # MODIS-only years count as "estimated" for the trust flags, matching the app
    pts["is_estimated"] = pts["date"].dt.year < viirs_from
    return pts


def points_to_monthly(pts):
    g = pts.assign(year=pts["date"].dt.year, month=pts["date"].dt.month,
                   count=1.0, est_count=pts["is_estimated"].astype(float))
    return (g.groupby(["cell_id", "lat", "lon", "year", "month"], as_index=False)
             [["count", "frp", "est_count"]].sum())
