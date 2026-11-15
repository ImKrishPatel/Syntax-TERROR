"""Sanity test: plant 4 known regimes (+ a block that shifts in 2012) in a
synthetic grid and check the pipeline finds and names them.

    python -m analysis.fire_regimes.test_synthetic
"""
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import adjusted_rand_score

from .run import main

rng = np.random.default_rng(1)
M = np.arange(12)


def bump(peak, width):
    d = np.minimum(np.abs(M - peak), 12 - np.abs(M - peak))
    return np.exp(-0.5 * (d / width) ** 2)


# truth: seasonal shape, hotspots/year, FRP per hotspot (lognormal mu, sigma), year-to-year sigma
REGIMES = {
    "high_intensity": dict(shape=bump(6, 0.8), n=60, frp=(4.2, 0.9), yr=1.0),
    "agricultural": dict(shape=bump(4, 0.4) + bump(10, 0.4), n=300, frp=(2.6, 0.3), yr=0.15),
    "year_round": dict(shape=np.ones(12) + 0.2 * bump(2, 2), n=200, frp=(3.3, 0.4), yr=0.2),
    "seasonal_wildfire": dict(shape=bump(8, 1.3), n=120, frp=(3.4, 0.4), yr=0.3),
}
BLOCKS = {  # (lat0, lon0) of a 10x10 block of 0.5 degree cells
    "high_intensity": (55, -120), "agricultural": (28, 73),
    "year_round": (-3, 15), "seasonal_wildfire": (-15, 128),
}


def make(path):
    rows, truth = [], {}
    years = range(2001, 2025)
    yf = {y: np.exp(rng.normal(0, 1)) for y in years}  # shared big/small years
    for reg, (la, lo) in list(BLOCKS.items()) + [("shift", (40, -100))]:
        for i in range(10):
            for j in range(10):
                lat, lon = la + 0.25 + 0.5 * i, lo + 0.25 + 0.5 * j
                cid = f"{lat:g}_{lon:g}"
                truth[cid] = reg
                for y in years:
                    p = REGIMES["agricultural" if y < 2012 else "seasonal_wildfire"] if reg == "shift" else REGIMES[reg]
                    year_mult = yf[y] ** p["yr"] * np.exp(rng.normal(0, 0.2))
                    lam = p["n"] * year_mult * p["shape"] / p["shape"].sum() * rng.uniform(0.6, 1.4)
                    for m, c in enumerate(rng.poisson(lam)):
                        if c:
                            frp = rng.lognormal(*p["frp"], size=c).sum()
                            rows.append((cid, lat, lon, f"{y}-{m+1:02d}-01", c, round(frp, 1), y < 2012))
    pd.DataFrame(rows, columns=["cell_id", "lat", "lon", "date", "hotspot_count", "frp_mw", "is_estimated"]).to_csv(path, index=False)
    return truth


def test():
    with tempfile.TemporaryDirectory() as d:
        truth = make(Path(d) / "h.csv")
        main(["--input", str(Path(d) / "h.csv"), "--out", d])
        gj = json.loads((Path(d) / "fire_regimes.geojson").read_text())
        summ = json.loads((Path(d) / "regime_summary.json").read_text())
    props = {f["properties"]["cell_id"]: f["properties"] for f in gj["features"]}
    arch = {r["regime_id"]: r["archetype"] for r in summ["regimes"]}
    cells = [c for c in truth if truth[c] != "shift" and c in props]
    ari = adjusted_rand_score([truth[c] for c in cells], [props[c]["regime_id"] for c in cells])
    named = {reg: pd.Series([arch[props[c]["regime_id"]] for c in cells if truth[c] == reg]).mode()[0] for reg in BLOCKS}
    shift_rate = np.mean([props[c]["shifted"] for c in truth if truth[c] == "shift"])
    false_shift = np.mean([props[c]["shifted"] for c in cells])
    cross = np.mean([props[c]["shift_crosses_sensor_change"] for c in truth if truth[c] == "shift" and props[c]["shifted"]] or [0])
    print(f"\nARI vs planted regimes: {ari:.2f}")
    print("planted -> named archetype:", named)
    print(f"shift block flagged: {shift_rate:.0%}, false shifts elsewhere: {false_shift:.0%}, "
          f"flagged as at sensor change: {cross:.0%}")
    print("known-region check:", summ["validation"]["known_regions"])
    assert ari > 0.8, ari
    assert all(named[k] == k for k in BLOCKS), named
    assert shift_rate > 0.7 and false_shift < 0.05




def make_firms(d):
    """Fake FIRMS archive files: a zip (2016-2021) and a CSV (2015-2016) that
    overlap in 2016, plus gas flares and low-confidence rows to be filtered."""
    import zipfile
    r = np.random.default_rng(2)
    rows = []
    for day in pd.date_range("2015-01-01", "2024-12-31"):
        m = day.month - 1
        # boreal summer wildfire block: big, night-burning, Jun-Aug
        for _ in range(r.poisson(40 * bump(6, 0.9)[m])):
            rows.append((56 + r.uniform(0, 4), -120 + r.uniform(0, 4), day, r.lognormal(4.3, 0.9), 80, "N" if r.random() < .45 else "D", 0))
        # crop burning block: small, daytime, Apr and Oct
        for _ in range(r.poisson(60 * (bump(3, 0.4) + bump(9, 0.4))[m])):
            rows.append((29 + r.uniform(0, 3), 74 + r.uniform(0, 3), day, r.lognormal(2.5, 0.3), 70, "N" if r.random() < .05 else "D", 0))
        rows.append((29.9, 50.1, day, 30.0, 90, "N", 2))                   # gas flare -> dropped
        rows.append((57.0, -118.0, day, 900.0, 10, "D", 0))                  # low confidence -> dropped
    df = pd.DataFrame(rows, columns=["latitude", "longitude", "acq_date", "frp", "confidence", "daynight", "type"])
    df["acq_date"] = df["acq_date"].dt.strftime("%Y-%m-%d")
    df["instrument"], df["acq_time"], df["satellite"] = "MODIS", 1200, "Terra"
    yr = df["acq_date"].str[:4].astype(int)
    df[yr <= 2016].to_csv(Path(d) / "fire_archive_M-C61_1.csv", index=False)
    with zipfile.ZipFile(Path(d) / "DL_FIRE_M-C61_2.zip", "w") as z:
        z.writestr("fire_archive_M-C61_2.csv", df[yr >= 2016].to_csv(index=False))
    v = df.head(5).assign(instrument="VIIRS", confidence="n")
    v.to_csv(Path(d) / "fire_archive_SV-C2_3.csv", index=False)
    return int(((df["type"] == 0) & (df["confidence"] >= 30)).sum())


def test_firms():
    from . import ingest
    with tempfile.TemporaryDirectory() as d:
        expected = make_firms(d)
        modis = [str(Path(d) / "fire_archive_M-C61_1.csv"), str(Path(d) / "DL_FIRE_M-C61_2.zip")]
        day = ingest.load_firms_raw(modis)
        print(f"\nkept {day['count'].sum():,.0f} detections, expected {expected:,} (overlap removed once)")
        assert day["count"].sum() == expected
        assert not ((day["lat"] == 29.75) & (day["lon"] == 50.25)).any(), "gas flare kept"
        try:
            ingest.load_firms_raw(modis + [str(Path(d) / "fire_archive_SV-C2_3.csv")])
            raise AssertionError("mixed sensors not caught")
        except ValueError as e:
            print("mixed sensors caught:", e)
        main(["--firms", *modis, "--out", d])
        summ = json.loads((Path(d) / "regime_summary.json").read_text())
        for r in summ["regimes"]:
            print(f"  {r['regime_name']:<60} night_share={r['profile'].get('night_share')}")
        arch = {r["archetype"] for r in summ["regimes"]}
        assert "agricultural" in arch and arch & {"high_intensity", "seasonal_wildfire"}, arch


if __name__ == "__main__":
    test()
    test_firms()
