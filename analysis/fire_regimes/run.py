"""Fire regime clustering: end-to-end run.

    python -m analysis.fire_regimes.run                       # uses public/data/hotspots.csv
    python -m analysis.fire_regimes.run --firms a.csv b.csv   # raw FIRMS archive CSVs

Writes into public/data/:
    fire_regimes.geojson   one square per grid cell (DATA_FORMAT.md section 2 + extras)
    regime_summary.json    per-regime cards, monthly curves, validation report
"""
import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import adjusted_rand_score

from . import cluster as cl
from . import features as ft
from . import ingest
from .label import known_region_check, label

SENSOR_CHANGE_YEAR = 2012  # VIIRS starts; earlier values are model estimates


def windows(first, last, size=5, anchor=SENSOR_CHANGE_YEAR):
    """Windows of `size` years with a boundary at the sensor change, so no window
    mixes real VIIRS data and pre-2012 estimates. Stubs < 3 years are merged."""
    def side(lo, hi, backward):
        ws, edge = [], (hi if backward else lo)
        while (edge >= lo) if backward else (edge <= hi):
            w = (max(lo, edge - size + 1), edge) if backward else (edge, min(hi, edge + size - 1))
            ws.append(w)
            edge = w[0] - 1 if backward else w[1] + 1
        ws.sort()
        # fold a short stub (< 3 years) into its neighbour
        if len(ws) > 1 and ws[0][1] - ws[0][0] < 2:
            ws[:2] = [(ws[0][0], ws[1][1])]
        if len(ws) > 1 and ws[-1][1] - ws[-1][0] < 2:
            ws[-2:] = [(ws[-2][0], ws[-1][1])]
        return ws

    anchor = min(max(anchor, first), last + 1)
    before = side(first, anchor - 1, True) if anchor > first else []
    after = side(anchor, last, False) if anchor <= last else []
    return before + after


def regime_shifts(monthly, pts, feats, prep, labels, label_to_id, win_size):
    """Step 7: re-compute features per window, assign each to the nearest
    full-period regime centre (same scaler, so regimes stay comparable)."""
    X = prep.transform(feats)
    ids, cents = cl.centroids(X, labels)
    first, last = int(feats["first_year"].iloc[0]), int(feats["last_year"].iloc[0])
    wins = windows(first, last, win_size)
    per_cell = {c: [] for c in feats["cell_id"]}
    for a, b in wins:
        wf, _ = ft.build(monthly, pts, years=range(a, b + 1), min_hotspots=3, min_active_years=1)
        wf = wf[wf["cell_id"].isin(per_cell)]
        got = dict(zip(wf["cell_id"], (label_to_id[int(l)] for l in cl.nearest(prep.transform(wf), ids, cents)))) \
            if len(wf) else {}
        for c in per_cell:
            per_cell[c].append(got.get(c))
    return wins, per_cell


def detect_shift(wins, hist):
    """A cell has shifted only if its first two active windows agree, its last
    two agree, and the two differ, so one noisy window can't trigger it.
    Also flags shifts that happen exactly at the 2012 sensor change, which
    could be a harmonisation artefact rather than a real change."""
    seen = [(w, h) for w, h in zip(wins, hist) if h is not None]
    if len(seen) < 4:
        return False, None, None, False
    (_, a1), (_, a2), (_, b1), (wb2, b2) = seen[0], seen[1], seen[-2], seen[-1]
    if not (a1 == a2 and b1 == b2 and a1 != b1):
        return False, None, None, False
    j = len(seen) - 1
    while j > 0 and seen[j - 1][1] == b1:
        j -= 1
    crosses = seen[j - 1][0][1] < SENSOR_CHANGE_YEAR <= seen[j][0][0]
    return True, a1, b1, bool(crosses)


def year_split_stability(monthly, pts, feats, prep, labels):
    """Step 6b: features from odd years vs even years, both assigned to the
    full-period centres; ARI between the two halves."""
    X = prep.transform(feats)
    ids, cents = cl.centroids(X, labels)
    yrs = sorted(monthly["year"].unique())
    halves = []
    for sub in (yrs[0::2], yrs[1::2]):
        wf, _ = ft.build(monthly, pts, years=sub, min_hotspots=1, min_active_years=1)
        halves.append(dict(zip(wf["cell_id"], cl.nearest(prep.transform(wf), ids, cents))))
    common = [c for c in feats["cell_id"] if c in halves[0] and c in halves[1]]
    return float(adjusted_rand_score([halves[0][c] for c in common], [halves[1][c] for c in common])) if common else None


def cell_polygon(lat, lon, res):
    h = res / 2
    w, e, s, n = round(lon - h, 4), round(lon + h, 4), round(lat - h, 4), round(lat + h, 4)
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="public/data/hotspots.csv", help="grid CSV (DATA_FORMAT.md)")
    ap.add_argument("--firms", nargs="*", help="raw FIRMS archive CSVs (adds day/night + daily features)")
    ap.add_argument("--res", type=float, default=0.5, help="grid size in degrees")
    ap.add_argument("--method", choices=["auto", "kmeans", "hdbscan"], default="auto")
    ap.add_argument("--k", type=int, nargs="*", default=list(range(3, 9)), help="k values to try")
    ap.add_argument("--with-trend", action="store_true", help="also cluster on yearly trends")
    ap.add_argument("--no-pca", action="store_true")
    ap.add_argument("--window", type=int, default=5, help="years per regime-shift window")
    ap.add_argument("--out", default="public/data")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--start", help="first month the record covers, YYYY-MM (default: auto-detect)")
    ap.add_argument("--end", help="last month the record covers, YYYY-MM, e.g. 2026-11 (default: auto-detect)")
    a = ap.parse_args(argv)

    # 1. ingest
    pts = None
    if a.firms:
        pts = ingest.load_firms_raw(a.firms, a.res)
        monthly = ingest.points_to_monthly(pts)
    else:
        monthly = ingest.load_grid_csv(a.input)
    print(f"[1] {len(monthly):,} cell-months, {monthly['cell_id'].nunique():,} cells, "
          f"{monthly['year'].min()}-{monthly['year'].max()}")

    # 2. features
    feats, dropped = ft.build(monthly, pts, start=a.start, end=a.end)
    if len(feats) and feats["partial_months_excluded"].iloc[0]:
        print(f"    partial year(s): {feats['partial_months_excluded'].iloc[0]} uncovered months handled")
    used = ft.BASE_FEATURES + (ft.DAILY_FEATURES if pts is not None else []) + \
        (ft.TREND_FEATURES if a.with_trend else [])
    print(f"[2] {len(feats):,} cells kept, {len(used)} features; dropped {dropped}")

    # 3-4. preprocess + cluster
    prep = cl.Preprocessor(used, None if a.no_pca else 0.9).fit(feats)
    X = prep.transform(feats)
    labels, conf, outlier, report = cl.cluster(X, a.method, a.k, a.seed)
    print(f"[4] chose {report['chosen']}: silhouette {report['silhouette']:.3f}, "
          f"DB {report['davies_bouldin']:.3f}; HDBSCAN found {report['hdbscan']['n_clusters']} "
          f"({report['hdbscan']['noise_share']:.0%} noise)")

    # 5. label
    regimes, lab2id = label(feats, labels, conf, used)
    rid = np.array([lab2id[int(l)] for l in labels])

    # 6. validate
    k = len(regimes)
    report["seed_stability_ari"] = cl.seed_stability(X, k, labels)
    report["year_split_stability_ari"] = year_split_stability(monthly, pts, feats, prep, labels)
    report["known_regions"] = known_region_check(feats, rid, regimes)
    report["features_used"] = used
    report["cells_dropped"] = dropped
    print(f"[6] seed ARI {report['seed_stability_ari']:.2f}, odd/even-year ARI "
          f"{report['year_split_stability_ari']}")

    # 7. regime shifts
    wins, per_cell = regime_shifts(monthly, pts, feats, prep, labels, lab2id, a.window)
    by_id = {r["regime_id"]: r for r in regimes}

    # 9. outputs
    features_out, n_shift = [], 0
    for i, row in feats.iterrows():
        hist = per_cell[row["cell_id"]]
        shifted, frm, to, crosses = detect_shift(wins, hist)
        n_shift += shifted
        r = by_id[int(rid[i])]
        props = {
            "regime_id": r["regime_id"], "regime_name": r["regime_name"], "color": r["color"],
            "cell_id": row["cell_id"], "confidence": round(float(conf[i]), 2),
            "is_outlier": bool(outlier[i]),
            "hotspots_per_year": round(float(row["hotspots_per_year"]), 1),
            "peak_month": int(row["peak_month"]),
            "frp_mean_mw": round(float(row["frp_mean"]), 1),
            "trend_pct_per_year": round(float(row["trend_pct_per_year"]), 1),
            "estimated_share": round(float(row["estimated_share"]), 2),
            "regime_by_window": hist,
            "shifted": bool(shifted),
            "shift_from": by_id[frm]["regime_name"] if shifted else None,
            "shift_to": by_id[to]["regime_name"] if shifted else None,
            "shift_crosses_sensor_change": bool(crosses),
        }
        features_out.append({"type": "Feature", "properties": props,
                             "geometry": cell_polygon(row["lat"], row["lon"], a.res)})

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gj = {"type": "FeatureCollection", "features": features_out}
    (out / "fire_regimes.geojson").write_text(json.dumps(gj, separators=(",", ":")))
    for r in regimes:
        r.pop("label")
        r["n_shifted_cells"] = int(sum(f["properties"]["shifted"] and f["properties"]["regime_id"] == r["regime_id"]
                                       for f in features_out))
    summary = {"windows": [list(w) for w in wins], "sensor_change_year": SENSOR_CHANGE_YEAR,
               "grid_res_deg": a.res, "n_cells": len(feats), "n_shifted_cells": int(n_shift),
               "regimes": regimes, "validation": report}
    (out / "regime_summary.json").write_text(json.dumps(summary, indent=1, default=float))
    print(f"[9] wrote {out/'fire_regimes.geojson'} ({len(features_out):,} cells, {n_shift} shifted) "
          f"and {out/'regime_summary.json'}")
    for r in regimes:
        print(f"    {r['regime_id']}. {r['regime_name']:<55} {r['n_cells']:>5} cells  conf {r['mean_confidence']:.2f}")


if __name__ == "__main__":
    main()
