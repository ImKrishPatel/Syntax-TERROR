"""analysis.harmonize: ratios are recovered and the record is labelled."""
import numpy as np
import pandas as pd

from analysis import harmonize as h


def _grids(ratio=3.0):
    rng = np.random.default_rng(1)
    months = pd.date_range("2021-01-01", "2023-12-01", freq="MS")
    cells = [(55.0 + 2 * i, -115.0) for i in range(4)]
    rows = [(la, lo, d, float(rng.integers(5, 50))) for la, lo in cells for d in months]
    modis = pd.DataFrame(rows, columns=["lat", "lon", "date", "m_count"]).assign(m_frp=lambda d: d["m_count"] * 40)
    ov = modis[modis["date"] >= "2022-01-01"]
    viirs = pd.DataFrame({"lat": ov["lat"], "lon": ov["lon"], "date": ov["date"],
                          "v_count": ov["m_count"] * ratio, "v_frp": ov["m_frp"] * 0.5})
    return modis, viirs


def test_fit_recovers_ratio():
    modis, viirs = _grids()
    months = sorted(viirs["date"].unique())
    fit = h.fit_ratios(h.overlap_table(modis, viirs, months))
    assert abs(fit["global_count"] - 3.0) < 1e-9
    assert np.allclose(fit["cell"]["ratio_count"], 3.0)


def test_record_labels_estimated_and_observed():
    modis, viirs = _grids()
    months = sorted(viirs["date"].unique())
    out, _ = h.build(modis, viirs, months)
    est = out[out["is_estimated"] == "true"]
    assert set(pd.to_datetime(est["date"]).dt.year) == {2021}
    assert (out["is_estimated"] == "false").sum() == len(viirs)
    first = modis[modis["date"] == "2021-01-01"].iloc[0]
    row = est[(est["date"] == "2021-01-01") & (est["lat"] == f"{first['lat']:g}")].iloc[0]
    assert row["hotspot_count"] == round(first["m_count"] * 3)


def test_holdout_beats_raw_modis():
    modis, viirs = _grids()
    r = h.holdout(modis, viirs, sorted(viirs["date"].unique()))
    assert r["count"]["ember"]["median_abs_pct_error_global_month"] < r["count"]["raw_modis"]["median_abs_pct_error_global_month"]
