# Fire regime clustering (Pearl)

Groups grid cells with similar fire behaviour into named regimes, and flags cells whose regime changed over time.

## Run

```bash
pip install -r analysis/requirements.txt
python -m analysis.fire_regimes.run                          # from public/data/hotspots.csv
python -m analysis.fire_regimes.run --firms modis.csv viirs.csv   # raw FIRMS archive CSVs
python -m analysis.fire_regimes.test_synthetic               # sanity test, should pass
```

Writes `public/data/fire_regimes.geojson` and `public/data/regime_summary.json`.
Re-run it whenever `hotspots.csv` is replaced with real data.
Note: `node scripts/make-mock-data.mjs` overwrites `fire_regimes.geojson` with mock boxes, so run this after it.

## Pipeline

| step | file | what it does |
| --- | --- | --- |
| 1 ingest | `ingest.py` | Grid CSV, or raw FIRMS: drops low confidence (MODIS < 30, VIIRS "low") and FIRMS `type` 1-3 (volcanoes, gas flares/industry, offshore), bins to 0.5° |
| 2 features | `features.py` | Per cell: frequency, seasonality (circular mean + concentration + season length), FRP mean/p95/skew, duration (burning-month runs), re-burn episodes, year-to-year variability, Theil-Sen trend + Mann-Kendall p, share of pre-2012 estimates. Raw FIRMS adds night share, fire-day share, burn-day runs, re-burns. Persistent-heat filter removes flare-like cells |
| 3 preprocess | `cluster.py` | log transforms, RobustScaler, PCA (90% variance) |
| 4 cluster | `cluster.py` | K-Means (k by silhouette, elbow logged) and HDBSCAN side by side. Default: K-Means labels every cell, HDBSCAN noise becomes an "unusual" flag; HDBSCAN is used instead if it separates clearly better |
| 5 label | `label.py` | Names each regime from its profile (summer wildfire, agricultural-style, high-intensity, year-round, sporadic), Okabe-Ito colour-blind-safe colours, plain-English summary sentence |
| 6 validate | `run.py`, `label.py` | Silhouette, Davies-Bouldin, seed stability (ARI), odd vs even year stability (ARI), known-region check (Canadian boreal, Punjab/Haryana, Amazon arc, N. Australia, Central Africa) |
| 7 shifts | `run.py` | 5-year windows aligned to 2012 so no window mixes real VIIRS and estimates; each window assigned to the full-period regime centres. A shift needs 2 windows of agreement on each side. Shifts right at 2012 are flagged as possible sensor artefacts |
| 9 outputs | `run.py` | GeoJSON layer + summary JSON |

Yearly trends are reported but not clustered by default (they are noisy and would split regimes by trend instead of behaviour). Use `--with-trend` to include them.

## Outputs

`fire_regimes.geojson`: one square per cell. Properties: `regime_id`, `regime_name`, `color` (as in DATA_FORMAT.md) plus `cell_id`, `confidence` (0 = on the border of two regimes, 1 = at the centre), `is_outlier`, `hotspots_per_year`, `peak_month`, `frp_mean_mw`, `trend_pct_per_year`, `estimated_share`, `regime_by_window`, `shifted`, `shift_from`, `shift_to`, `shift_crosses_sensor_change`.

`regime_summary.json`: `regimes[]` with `name`, `archetype`, `color`, `n_cells`, `summary` (plain English), `profile` (medians), `z_profile` (vs all cells), `monthly_curve` (12 values, sums to 1); `windows`; `validation` report.

## Not done yet

- Land cover check (MODIS MCD12Q1) to confirm cropland vs forest names
- Radar/box charts, monthly curve chart and summary cards in the app (data is in `regime_summary.json`)
- Dissolving cells into regime polygons if the global file gets too big
