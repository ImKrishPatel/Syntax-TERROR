"""Step 2: per-cell feature engineering.

Monthly features work on any input. Daily features (day/night, burn-day runs,
re-burns) need raw FIRMS points and are added only when those are given.
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

MONTH_ANGLES = 2 * np.pi * (np.arange(12) + 0.5) / 12

# features fed to the clustering (all already log-transformed where skewed)
BASE_FEATURES = [
    "log_hotspots_per_year",   # frequency
    "fire_month_share",        # share of all months with any fire
    "active_year_share",       # share of years with any fire
    "season_sin", "season_cos",  # circular mean month, shrunk by concentration
    "season_concentration",    # 1 = one sharp peak, 0 = flat / year-round
    "season_length_months",    # months holding 80% of activity
    "log_frp_mean",            # MW per hotspot
    "log_frp_p95",
    "frp_skew",                # log(p95 / median): few extreme fires vs many small
    "longest_run_months",      # duration: consecutive burning months per year
    "runs_per_year",           # re-burn: separate fire episodes per year
    "log_interannual_cv",      # boom-bust years vs steady
]
DAILY_FEATURES = ["night_share", "fire_day_share", "mean_run_days", "reburns_per_year"]
TREND_FEATURES = ["trend_pct_per_year", "frp_trend_pct_per_year"]


def _cube(monthly, years):
    """Dense arrays [cell, year, month] for counts, frp and estimated counts."""
    cells = monthly.groupby("cell_id", as_index=False)[["lat", "lon"]].first()
    ci = pd.Index(cells["cell_id"])
    yi = {y: i for i, y in enumerate(years)}
    m = monthly[monthly["year"].isin(yi)]
    c, y, mo = ci.get_indexer(m["cell_id"]), m["year"].map(yi).to_numpy(), m["month"].to_numpy() - 1
    shape = (len(ci), len(years), 12)
    C, F, E = (np.zeros(shape) for _ in range(3))
    np.add.at(C, (c, y, mo), m["count"].to_numpy())
    np.add.at(F, (c, y, mo), m["frp"].to_numpy())
    np.add.at(E, (c, y, mo), m["est_count"].to_numpy())
    return cells, C, F, E


def _runs(active):
    """Longest run and number of runs of True along the last axis (12 months)."""
    a = active.astype(int)
    starts = (np.diff(np.concatenate([np.zeros(a.shape[:-1] + (1,), int), a], -1), axis=-1) == 1).sum(-1)
    longest = np.zeros(a.shape[:-1])
    cur = np.zeros(a.shape[:-1])
    for k in range(a.shape[-1]):
        cur = (cur + 1) * a[..., k]
        longest = np.maximum(longest, cur)
    return longest, starts


def trends(annual, years):
    """Vectorised Theil-Sen slope (% of the cell's mean per year) and
    Mann-Kendall p-value (no tie correction) for every row of annual[cell, year]."""
    i, j = np.triu_indices(len(years), 1)
    dy = annual[:, j] - annual[:, i]
    slope = np.median(dy / (years[j] - years[i]), 1)
    n = len(years)
    S = np.sign(dy).sum(1)
    var = n * (n - 1) * (2 * n + 5) / 18
    z = (S - np.sign(S)) / np.sqrt(var)
    p = 2 * norm.sf(np.abs(z))
    mean = annual.mean(1)
    pct = np.where(mean > 0, 100 * slope / np.where(mean > 0, mean, 1), 0.0)
    few = np.count_nonzero(annual, 1) < 3
    return np.where(few, 0.0, pct), np.where(few, 1.0, p)


def coverage(monthly, years, start=None, end=None):
    """[year, month] mask of months the record actually covers.

    A record that starts or stops mid-year (e.g. 2026 up to November) would
    otherwise look like a quiet year. Pass start/end ("YYYY-MM") to be exact;
    otherwise the first/last year counts as partial only if its activity starts
    later / stops earlier than it usually does in other years.
    """
    cov = np.ones((len(years), 12), bool)
    ym = monthly[monthly["year"].isin(years)].groupby("year")["month"].agg(["min", "max"])
    if len(ym) < 3:
        return cov
    if end:
        ey, em = map(int, end.split("-"))
    else:
        ey, em = years[-1], 12
        if years[-1] in ym.index and ym.loc[years[-1], "max"] < ym["max"].iloc[:-1].median() - 1:
            em = int(ym.loc[years[-1], "max"])
    if start:
        sy, sm = map(int, start.split("-"))
    else:
        sy, sm = years[0], 1
        if years[0] in ym.index and ym.loc[years[0], "min"] > ym["min"].iloc[1:].median() + 1:
            sm = int(ym.loc[years[0], "min"])
    for i, y in enumerate(years):
        if y == ey:
            cov[i, em:] = False
        if y == sy:
            cov[i, : sm - 1] = False
    return cov


def monthly_features(monthly, years=None, start=None, end=None):
    years = sorted(monthly["year"].unique()) if years is None else list(years)
    cells, C, F, E = _cube(monthly, years)
    cov = coverage(monthly, years, start, end)
    full = cov.all(1)                       # complete years, for per-year stats
    yrs = np.array(years, float)
    total = C.sum((1, 2))
    ok = total > 0
    cells, C, F, E, total = cells[ok].reset_index(drop=True), C[ok], F[ok], E[ok], total[ok]

    f = cells.copy()
    n_years = len(years)
    f["total_hotspots"] = total
    f["hotspots_per_year"] = total / (cov.sum() / 12)   # partial years count as fractions
    f["log_hotspots_per_year"] = np.log1p(f["hotspots_per_year"])
    f["fire_month_share"] = (C > 0).sum((1, 2)) / cov.sum()
    annual = C.sum(2)
    f["active_year_share"] = (annual > 0).mean(1)

    # seasonality: circular statistics over the monthly climatology; each
    # month is averaged over only the years that cover it
    clim = C.sum(1) / np.maximum(cov.sum(0), 1)
    share = clim / np.maximum(clim.sum(1, keepdims=True), 1e-12)
    s, c = share @ np.sin(MONTH_ANGLES), share @ np.cos(MONTH_ANGLES)
    f["season_sin"], f["season_cos"] = s, c
    f["season_concentration"] = np.hypot(s, c)
    f["peak_month"] = share.argmax(1) + 1
    srt = -np.sort(-share, 1)
    f["season_length_months"] = (np.cumsum(srt, 1) < 0.8).sum(1) + 1
    f["monthly_share"] = list(np.round(share, 4))

    # intensity: FRP per hotspot over burning months
    with np.errstate(invalid="ignore", divide="ignore"):
        per = np.where(C > 0, F / C, np.nan).reshape(len(f), -1)
    f["frp_mean"] = F.sum((1, 2)) / total
    f["frp_median"] = np.nanmedian(per, 1)
    f["frp_p95"] = np.nanpercentile(per, 95, 1)
    f["log_frp_mean"] = np.log1p(f["frp_mean"])
    f["log_frp_p95"] = np.log1p(f["frp_p95"])
    f["frp_skew"] = np.log((f["frp_p95"] + 1) / (f["frp_median"] + 1))

    # duration / re-burn at monthly resolution, averaged over years with fire
    longest, runs = _runs(C > 0)
    has = annual > 0
    denom = np.maximum(has.sum(1), 1)
    f["longest_run_months"] = (longest * has).sum(1) / denom
    f["runs_per_year"] = (runs * has).sum(1) / denom

    # year-to-year stats and trends use complete years only
    if full.sum() >= 3:
        annual, yrs = annual[:, full], yrs[full]
    with np.errstate(invalid="ignore", divide="ignore"):
        cv = annual.std(1) / annual.mean(1)
    f["interannual_cv"] = np.nan_to_num(cv)
    f["log_interannual_cv"] = np.log1p(f["interannual_cv"])

    # yearly trends (shown on the cards; only clustered with --with-trend)
    f["trend_pct_per_year"], f["trend_p"] = trends(annual, yrs)
    f["frp_trend_pct_per_year"] = trends(F.sum(2)[:, full] if full.sum() >= 3 else F.sum(2), yrs)[0]
    f["partial_months_excluded"] = int((~cov).sum())

    # trust: share of this cell's activity that is a pre-2012 model estimate
    f["estimated_share"] = E.sum((1, 2)) / total
    f["first_year"], f["last_year"] = years[0], years[-1]
    return f


def daily_features(day, years=None):
    """Day/night ratio and burn-day statistics from the cell-day table."""
    if years is not None:
        day = day[day["date"].dt.year.isin(list(years))]
    n_days = max(1, (day["date"].max() - day["date"].min()).days + 1)
    g = day.groupby("cell_id")
    det = (g["night"].sum() / g["count"].sum()).rename("night_share").to_frame()
    days = day[["cell_id", "date"]].sort_values(["cell_id", "date"])
    gap = days.groupby("cell_id")["date"].diff().dt.days
    days["new_run"] = gap.isna() | (gap > 1)
    days["new_episode"] = gap.isna() | (gap > 7)   # re-burn = new fire after a week off
    days["run"] = days.groupby("cell_id")["new_run"].cumsum()
    run_len = days.groupby(["cell_id", "run"]).size().groupby("cell_id").mean()
    n_years = max(1, day["date"].dt.year.nunique())
    out = pd.DataFrame({
        "fire_day_share": days.groupby("cell_id").size() / n_days,
        "mean_run_days": run_len,
        "reburns_per_year": days.groupby("cell_id")["new_episode"].sum() / n_years,
    })
    return det.join(out).reset_index()


def drop_static_sources(f):
    """Persistent-heat filter: gas flares / industry burn every month at a steady
    rate, unlike any vegetation fire regime. Used when FIRMS "type" is missing."""
    static = (f["fire_month_share"] > 0.9) & (f["season_concentration"] < 0.15) & (f["interannual_cv"] < 0.2)
    return f[~static].reset_index(drop=True), int(static.sum())


def build(monthly, pts=None, years=None, min_hotspots=10, min_active_years=3, start=None, end=None):
    f = monthly_features(monthly, years, start, end)
    if pts is not None:
        f = f.merge(daily_features(pts, years), on="cell_id", how="left")
        for col in DAILY_FEATURES:
            f[col] = f[col].fillna(f[col].median())
    f, n_static = drop_static_sources(f)
    n_years = f["last_year"].iloc[0] - f["first_year"].iloc[0] + 1 if len(f) else 1
    keep = (f["total_hotspots"] >= min_hotspots) & (f["active_year_share"] * n_years >= min_active_years)
    return f[keep].reset_index(drop=True), {"static_cells_removed": n_static,
                                             "sparse_cells_removed": int((~keep).sum())}
