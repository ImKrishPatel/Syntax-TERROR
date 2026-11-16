"""Build the harmonized MODIS + VIIRS record for the app (DATA_FORMAT.md section 1).

    python -m analysis.harmonize --viirs data/raw/nasa21-26.zip \
        --modis-grid public/data/hotspots.csv --out public/data/hotspots.csv

What it does
------------
1. Reads VIIRS S-NPP hotspots from FIRMS zips/CSVs (vegetation fires, nominal or
   high confidence) and sums them per 2 degree cell per month, the same grid as
   the MODIS file the app already uses.
2. Where both sensors flew (the overlap months), learns how many VIIRS hotspots
   and how much VIIRS fire energy one MODIS hotspot / one MW of MODIS FRP stands
   for, per cell. Cells with little fire borrow from their 10 degree region and
   then from the whole world (shrinkage), so a quiet cell is not fitted on noise.
3. Writes one record in VIIRS terms:
     * months with real VIIRS data -> the VIIRS numbers, is_estimated = false
     * every other month           -> MODIS converted to VIIRS terms, is_estimated = true
4. Checks itself: fits on the earlier overlap months only and scores the later
   months it never saw, against two baselines (raw MODIS, one global ratio).
   The scores go to public/data/harmonization.json and into validation.json.

Why a ratio and not a bigger model: at 2 degrees per month the two sensors move
together closely; what differs is scale (VIIRS sees ~2-4x more small fires and
reports lower FRP per pixel). A per-cell ratio keeps that difference honest and
easy to explain, and the holdout test shows whether it is enough.
"""
import argparse
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

KEEP_CONF = {"n", "h", "nominal", "high"}
COLS = ["latitude", "longitude", "acq_date", "frp", "confidence", "type", "satellite", "instrument"]


def snap(v, res):
    return np.floor(np.asarray(v, dtype=float) / res) * res + res / 2


def _sources(paths):
    for p in sorted(map(str, paths)):
        if p.lower().endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if n.lower().endswith(".csv"):
                        with z.open(n) as fh:
                            yield f"{Path(p).name}:{n}", fh
        else:
            with open(p, "rb") as fh:
                yield p, fh


def load_viirs_monthly(paths, res=2.0, chunksize=2_000_000):
    """VIIRS S-NPP only (one satellite, so no double counting with NOAA-20/21)."""
    parts, covered = [], set()
    for name, fh in _sources(paths):
        n_in = n_kept = 0
        file_dates = set()
        for ch in pd.read_csv(fh, usecols=lambda c: c.lower() in COLS, chunksize=chunksize, low_memory=False):
            ch.columns = [c.lower() for c in ch.columns]
            n_in += len(ch)
            if "instrument" in ch:
                ch = ch[ch["instrument"].astype(str).str.upper().eq("VIIRS")]
            if "satellite" in ch:  # S-NPP is "N"; NOAA-20 is "1"/"N20", NOAA-21 "N21"
                ch = ch[ch["satellite"].astype(str).str.upper().eq("N")]
            if "type" in ch:
                ch = ch[ch["type"].eq(0)]
            ch = ch[ch["confidence"].astype(str).str.lower().isin(KEEP_CONF)]
            ch = ch[ch["frp"].astype(float) > 0]
            file_dates |= set(ch["acq_date"].unique())
            ch = ch[~ch["acq_date"].isin(covered)]  # overlapping downloads would double count
            if ch.empty:
                continue
            d = pd.DataFrame({
                "lat": snap(ch["latitude"], res), "lon": snap(ch["longitude"], res),
                "date": pd.to_datetime(ch["acq_date"]).dt.to_period("M").dt.to_timestamp(),
                "v_count": 1.0, "v_frp": ch["frp"].astype(float).to_numpy()})
            n_kept += len(d)
            parts.append(d.groupby(["lat", "lon", "date"], as_index=False).sum())
        covered |= file_dates
        print(f"    {name}: {n_in:,} rows, {n_kept:,} VIIRS S-NPP kept")
    if not parts:
        raise SystemExit("No VIIRS S-NPP rows found in the given files")
    return pd.concat(parts).groupby(["lat", "lon", "date"], as_index=False).sum()


def load_modis_grid(path):
    m = pd.read_csv(path)
    return pd.DataFrame({"lat": m["lat"].astype(float), "lon": m["lon"].astype(float),
                         "date": pd.to_datetime(m["date"]),
                         "m_count": m["hotspot_count"].astype(float), "m_frp": m["frp_mw"].astype(float)}
                        ).groupby(["lat", "lon", "date"], as_index=False).sum()


def full_months(viirs):
    """Months with VIIRS data for (nearly) the whole month, judged by global volume."""
    tot = viirs.groupby("date")["v_count"].sum()
    # first/last month of a download are often partial: drop months far below their neighbours
    med = tot.rolling(3, center=True, min_periods=1).median()
    return sorted(tot.index[tot >= 0.5 * med])


def fit_ratios(pair, alpha_count=50.0, alpha_frp=5000.0, region=10.0):
    """Per-cell VIIRS/MODIS ratios with shrinkage: cell -> 10 degree region -> world.

    pair: cell-months in the overlap with m_count, m_frp, v_count, v_frp (zeros filled).
    alpha_*: how much MODIS signal a cell needs before its own ratio dominates.
    """
    g_c = pair["v_count"].sum() / max(pair["m_count"].sum(), 1)
    g_f = pair["v_frp"].sum() / max(pair["m_frp"].sum(), 1)
    p = pair.assign(rlat=snap(pair["lat"], region), rlon=snap(pair["lon"], region))
    reg = p.groupby(["rlat", "rlon"])[["m_count", "m_frp", "v_count", "v_frp"]].sum()
    reg["rc"] = (reg["v_count"] + alpha_count * g_c) / (reg["m_count"] + alpha_count)
    reg["rf"] = (reg["v_frp"] + alpha_frp * g_f) / (reg["m_frp"] + alpha_frp)
    cell = p.groupby(["lat", "lon", "rlat", "rlon"])[["m_count", "m_frp", "v_count", "v_frp"]].sum().reset_index()
    cell = cell.join(reg[["rc", "rf"]], on=["rlat", "rlon"])
    cell["ratio_count"] = (cell["v_count"] + alpha_count * cell["rc"]) / (cell["m_count"] + alpha_count)
    cell["ratio_frp"] = (cell["v_frp"] + alpha_frp * cell["rf"]) / (cell["m_frp"] + alpha_frp)
    return {"global_count": g_c, "global_frp": g_f,
            "region": reg[["rc", "rf"]].reset_index(), "cell": cell[["lat", "lon", "ratio_count", "ratio_frp"]],
            "region_size": region}


def apply_ratios(modis, fit):
    r = fit["region_size"]
    m = modis.assign(rlat=snap(modis["lat"], r), rlon=snap(modis["lon"], r))
    m = m.merge(fit["cell"], on=["lat", "lon"], how="left").merge(fit["region"], on=["rlat", "rlon"], how="left")
    rc = m["ratio_count"].fillna(m["rc"]).fillna(fit["global_count"])
    rf = m["ratio_frp"].fillna(m["rf"]).fillna(fit["global_frp"])
    return m.assign(e_count=m["m_count"] * rc, e_frp=m["m_frp"] * rf).drop(columns=["rlat", "rlon"])


def _r2(y, p):
    ss = ((y - y.mean()) ** 2).sum()
    return float(1 - ((y - p) ** 2).sum() / ss) if ss > 0 else float("nan")


def score(test, pred_col, y_col="v_count"):
    y, p = np.log1p(test[y_col]), np.log1p(test[pred_col])
    tot = test.groupby("date")[[y_col, pred_col]].sum()
    return {"r2_log_cell_month": round(_r2(y, p), 3),
            "median_abs_pct_error_global_month": round(float((tot[pred_col] / tot[y_col] - 1).abs().median() * 100), 1)}


def overlap_table(modis, viirs, months):
    m = modis[modis["date"].isin(months)]
    v = viirs[viirs["date"].isin(months)]
    return m.merge(v, on=["lat", "lon", "date"], how="outer").fillna(
        {"m_count": 0, "m_frp": 0, "v_count": 0, "v_frp": 0})


def holdout(modis, viirs, months, split_frac=0.7):
    months = sorted(months)
    cut = months[int(len(months) * split_frac)]
    train = overlap_table(modis, viirs, [d for d in months if d < cut])
    test = overlap_table(modis, viirs, [d for d in months if d >= cut])
    test = test[test["m_count"] > 0].copy()  # a ratio can only convert months MODIS saw
    fit = fit_ratios(train)
    t = apply_ratios(test.drop(columns=[c for c in ("ratio_count", "ratio_frp") if c in test]), fit)
    t["raw"] = t["m_count"]
    t["one_ratio"] = t["m_count"] * fit["global_count"]
    t["raw_frp"] = t["m_frp"]
    t["one_ratio_frp"] = t["m_frp"] * fit["global_frp"]
    return {
        "train": f"{months[0]:%Y-%m} to {max(d for d in months if d < cut):%Y-%m}",
        "test": f"{cut:%Y-%m} to {months[-1]:%Y-%m}",
        "test_cell_months": int(len(t)),
        "count": {"ember": score(t, "e_count"), "one_global_ratio": score(t, "one_ratio"), "raw_modis": score(t, "raw")},
        "frp": {"ember": score(t, "e_frp", "v_frp"), "one_global_ratio": score(t, "one_ratio_frp", "v_frp"),
                "raw_modis": score(t, "raw_frp", "v_frp")},
    }


def build(modis, viirs, months):
    fit = fit_ratios(overlap_table(modis, viirs, months))
    est = apply_ratios(modis[~modis["date"].isin(months)], fit)
    est = est.assign(hotspot_count=est["e_count"], frp_mw=est["e_frp"], is_estimated=True)
    obs = viirs[viirs["date"].isin(months)].assign(hotspot_count=lambda d: d["v_count"],
                                                     frp_mw=lambda d: d["v_frp"], is_estimated=False)
    rec = pd.concat([est[["lat", "lon", "date", "hotspot_count", "frp_mw", "is_estimated"]],
                     obs[["lat", "lon", "date", "hotspot_count", "frp_mw", "is_estimated"]]])
    # a month MODIS saw fire in keeps at least one hotspot after conversion
    rec["hotspot_count"] = np.maximum(np.round(rec["hotspot_count"]), 1).astype(int)
    out = pd.DataFrame({
        "cell_id": rec["lat"].map("{:g}".format) + "_" + rec["lon"].map("{:g}".format),
        "lat": rec["lat"].map("{:g}".format), "lon": rec["lon"].map("{:g}".format),
        "date": rec["date"].dt.strftime("%Y-%m-%d"),
        "hotspot_count": rec["hotspot_count"], "frp_mw": np.round(rec["frp_mw"]).astype(int),
        "is_estimated": np.where(rec["is_estimated"], "true", "false"),
    }).sort_values(["date", "cell_id"])
    return out, fit


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--viirs", nargs="+", required=True, help="FIRMS zips/CSVs holding VIIRS S-NPP")
    ap.add_argument("--modis-grid", default="public/data/hotspots.csv",
                    help="the MODIS-only grid file made by analysis.hotspots_csv")
    ap.add_argument("--res", type=float, default=2.0)
    ap.add_argument("--out", default="public/data/hotspots.csv")
    ap.add_argument("--report", default="public/data/harmonization.json")
    ap.add_argument("--validation", default="public/data/validation.json")
    a = ap.parse_args(argv)

    print("MODIS grid:", a.modis_grid)
    modis = load_modis_grid(a.modis_grid)
    print("VIIRS:")
    viirs = load_viirs_monthly(a.viirs, a.res)
    months = [d for d in full_months(viirs) if d <= modis["date"].max()]
    print(f"overlap: {len(months)} full VIIRS months, {months[0]:%Y-%m} to {months[-1]:%Y-%m}")

    check = holdout(modis, viirs, months)
    out, fit = build(modis, viirs, months)
    out.to_csv(a.out, index=False, lineterminator="\n")

    report = {
        "method": "per-cell VIIRS/MODIS ratios for hotspot count and FRP, shrunk toward 10 degree region and world",
        "grid_degrees": a.res,
        "overlap_months": f"{months[0]:%Y-%m} to {months[-1]:%Y-%m} ({len(months)} months, VIIRS S-NPP)",
        "world_ratio_count": round(fit["global_count"], 3),
        "world_ratio_frp": round(fit["global_frp"], 3),
        "holdout": check,
        "record": {"rows": int(len(out)), "estimated_rows": int((out["is_estimated"] == "true").sum()),
                   "first": out["date"].min(), "last": out["date"].max()},
    }
    Path(a.report).write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))

    vp = Path(a.validation)
    if vp.exists():
        v = json.loads(vp.read_text())
        e, r = check["count"]["ember"], check["count"]["raw_modis"]
        o = check["count"]["one_global_ratio"]
        metric = {
            "label": "Harmonized counts vs real VIIRS",
            "value": f"{e['r2_log_cell_month']:.2f}",
            "unit": "R²",
            "detail": (f"MODIS converted to VIIRS terms, scored on {check['test']} months the model never saw "
                       f"({check['test_cell_months']:,} cell-months, log scale). One world ratio: "
                       f"{o['r2_log_cell_month']:.2f}; raw MODIS: {r['r2_log_cell_month']:.2f}. "
                       f"World monthly total off by a median {e['median_abs_pct_error_global_month']:.0f}% "
                       f"(raw MODIS: {r['median_abs_pct_error_global_month']:.0f}%)."),
        }
        v["metrics"] = [m for m in v.get("metrics", []) if m.get("label") != metric["label"]] + [metric]
        vp.write_text(json.dumps(v, indent=1, ensure_ascii=False))
    print(f"{a.out}: {len(out):,} rows")


if __name__ == "__main__":
    main()
