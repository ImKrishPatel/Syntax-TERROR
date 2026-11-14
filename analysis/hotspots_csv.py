"""Build the app's public/data/hotspots.csv (DATA_FORMAT.md section 1) from FIRMS.

    python -m analysis.hotspots_csv --firms data/*.zip --sensor MODIS --res 2 --start 2006-01 --out public/data/hotspots.csv
    python -m analysis.hotspots_csv --cache cellday.pkl --res 2 ...     # reuse fire_regimes' cell-day table

One row per grid cell per month with fire. Columns: cell_id, lat, lon, date,
hotspot_count, frp_mw (is_estimated is left out: every row is a real observation).
The grid is coarse on purpose: the browser loads the whole file, and a global
record at 0.5 degrees would be ~170 MB.
"""
import argparse

import numpy as np
import pandas as pd

from .fire_regimes import ingest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--firms", nargs="*")
    ap.add_argument("--sensor", choices=["MODIS", "VIIRS"])
    ap.add_argument("--cache", help="cell-day table saved by fire_regimes.run --cache")
    ap.add_argument("--res", type=float, default=2.0, help="grid size in degrees (app CONFIG.cellSize must match)")
    ap.add_argument("--start", default=None, help="first month to keep, YYYY-MM")
    ap.add_argument("--out", default="public/data/hotspots.csv")
    a = ap.parse_args(argv)

    day = pd.read_pickle(a.cache) if a.cache else ingest.load_firms_raw(a.firms, 0.5, sensor=a.sensor)
    if a.start:
        day = day[day["date"] >= pd.Timestamp(a.start + "-01")]
    lat, lon = ingest.snap(day["lat"], a.res), ingest.snap(day["lon"], a.res)
    m = (day.assign(lat=lat, lon=lon, date=day["date"].dt.to_period("M").dt.to_timestamp())
            .groupby(["lat", "lon", "date"], as_index=False)[["count", "frp"]].sum())
    # short ids keep the file small; any unique id works (DATA_FORMAT.md)
    m["cell_id"] = (m["lat"].map("{:g}".format) + "_" + m["lon"].map("{:g}".format))
    out = pd.DataFrame({
        "cell_id": m["cell_id"], "lat": m["lat"].map("{:g}".format), "lon": m["lon"].map("{:g}".format),
        "date": m["date"].dt.strftime("%Y-%m-%d"),
        "hotspot_count": m["count"].astype(int), "frp_mw": np.round(m["frp"]).astype(int),
    }).sort_values(["date", "cell_id"])
    out.to_csv(a.out, index=False, lineterminator="\n")
    print(f"{a.out}: {len(out):,} rows, {out['cell_id'].nunique():,} cells, "
          f"{out['date'].min()} to {out['date'].max()}")


if __name__ == "__main__":
    main()
