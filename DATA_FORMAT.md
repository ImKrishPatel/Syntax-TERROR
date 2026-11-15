# Data format

All data files live in `public/data/`. The app loads them in the browser; there is no backend.
`hotspots.csv`, `fire_regimes.geojson` and `regime_summary.json` are now **real data** (NASA FIRMS MODIS, whole world).
`validation.json` holds real checks (see the numbers in it). `forecast.json` is optional: the Forecast tab stays hidden until a real forecast file exists.

To plug in real data, replace a file with one of the same name and format, then reload the page.

All values are in SI units.

## 1. Hotspots: `public/data/hotspots.csv` (Aashana + Shriya)

The harmonized record. One row per grid cell per month. Leave out rows with zero hotspots.

| column          | type    | example         | notes                                                       |
| --------------- | ------- | --------------- | ----------------------------------------------------------- |
| `cell_id`       | string  | `55.25_-114.75` | any unique ID for the cell                                  |
| `lat`           | number  | `55.25`         | cell centre latitude, degrees north                         |
| `lon`           | number  | `-114.75`       | cell centre longitude, degrees east (west is negative)      |
| `date`          | string  | `2023-08-01`    | `YYYY-MM-DD`, the first day of the month                    |
| `hotspot_count` | integer | `42`            | hotspots in the cell that month (VIIRS-equivalent)          |
| `frp_mw`        | number  | `1830.5`        | total fire radiative power, megawatts                       |
| `is_estimated`  | boolean | `false`         | `true` = model estimate (before 2012), `false` = real VIIRS |

- Column order does not matter, but the header names must match exactly.
- Several rows for the same cell and month are fine (for example weekly data). The app adds them up.
- The current file is real NASA FIRMS MODIS C6.1 hotspots, whole world, January 2006 to November 2026,
  on a 2 degree grid, made by `python -m analysis.hotspots_csv` (no `is_estimated` column: every row is
  an observation). 2 degrees keeps the file at ~17 MB so the browser can load it; the regime layer keeps
  0.5 degree detail. It will be replaced by the harmonized record. If the grid size changes, change
  `cellSize` in `js/config.js`.
- The area can be anywhere on Earth. The map draws a dashed outline around whatever the file covers.

## 2. Fire regimes: `public/data/fire_regimes.geojson` and `regime_summary.json` (Pearl)

Written by `python -m analysis.fire_regimes.run`. See `analysis/fire_regimes/README.md`.
The app uses these properties of each feature: `regime_id`, `regime_name`, `color`, and if present
`hotspots_per_year`, `peak_month`, `shifted`, `shift_from`, `shift_to`.
From the summary it uses `regimes[].summary`, `regimes[].monthly_curve` and `validation.kmeans.silhouette`.

Re-run the pipeline whenever `hotspots.csv` changes.

## 3. Forecast: `public/data/forecast.json` (Snavya)

```json
{
  "is_demo": false,
  "region": "Northern Alberta",
  "made_on": "2026-08-14",
  "unit": "hotspots per day",
  "days": [
    { "date": "2026-08-13", "expected": 410, "low": 300, "high": 520, "actual": 388 },
    { "date": "2026-08-15", "expected": 395, "low": 270, "high": 530 }
  ]
}
```

`low`, `high` and `actual` are optional. Days after `made_on` normally have no `actual`.

## 4. Validation numbers: `public/data/validation.json` (Aashana + Shriya)

```json
{
  "is_demo": false,
  "overlap_period": "2012 to 2024",
  "note": "Model estimates compared with real VIIRS in years where both sensors were flying.",
  "metrics": [
    { "label": "Monthly hotspot counts", "value": "0.93", "unit": "R²", "detail": "estimated vs real VIIRS, per cell" }
  ]
}
```

Up to about four metrics fit nicely.

## 5. Anomaly score (Shriya)

Right now the app works the score out itself in `js/stats.js`: for the selected area it adds up
hotspots per month, then compares each month with the same calendar month in every other year
(score = how many standard deviations from the average). If the team's method is different,
that file is the only one to change.

## 6. Sound (Mridul)

`js/sound.js` has a simple first version. Keep the two functions `playCalendar` and `stopCalendar`
and the rest of the app does not need to change.

## If a file is missing

The app still works. The panel for that file shows a short note saying which file to add.
Only `hotspots.csv` is required.
