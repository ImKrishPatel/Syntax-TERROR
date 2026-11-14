# Data format

All data files live in `public/data/`. The app fetches them in the browser; there is no backend.
The files there now are **mock data** made by `node scripts/make-mock-data.mjs`.
To plug in real data, replace the file with one of the same name and format.

All values are in SI units.

## 1. Hotspots: `public/data/hotspots.csv`

One row per grid cell per time step. Leave out rows with zero hotspots.

| column          | type    | example         | notes                                                        |
| --------------- | ------- | --------------- | ------------------------------------------------------------ |
| `cell_id`       | string  | `55.25_-114.75` | any unique ID for the cell                                   |
| `lat`           | number  | `55.25`         | cell centre latitude, degrees north                          |
| `lon`           | number  | `-114.75`       | cell centre longitude, degrees east (west is negative)       |
| `date`          | string  | `2023-08-01`    | `YYYY-MM-DD`. Monthly data uses the first day of the month   |
| `hotspot_count` | integer | `42`            | hotspots in the cell for that time step (VIIRS-equivalent)   |
| `frp_mw`        | number  | `1830.5`        | total fire radiative power, megawatts                        |
| `is_estimated`  | boolean | `false`         | `true` = model estimate (before 2012), `false` = real VIIRS  |

Column order does not matter, but the header names must match exactly.
The mock file uses a 0.5 degree grid, monthly, 2001 to 2024, western Canada.

## 2. Fire regimes: `public/data/fire_regimes.geojson` (Pearl)

A GeoJSON `FeatureCollection` of `Polygon` / `MultiPolygon` features in WGS84 (lon, lat).
Each feature needs these properties:

| property      | type   | example               | notes                                   |
| ------------- | ------ | --------------------- | --------------------------------------- |
| `regime_id`   | number | `3`                   | unique per regime                       |
| `regime_name` | string | `Infrequent, high-intensity` | shown in the legend and on click |
| `color`       | string | `#e15759`             | optional; the app picks one if missing  |

## 3. Anomaly stats (Shriya)

Right now the verdict card computes its own numbers from `hotspots.csv` in `src/data/anomaly.js`:
for the selected area it sums hotspots per month, then compares each month against the same
calendar month in every other year (z-score and rank).
If Shriya's method differs, it replaces the function in that one file.

## Using the data in a component

```jsx
import { useSelection } from '../state/selection.js'
import { useHotspots, filterRecords } from '../data/hotspots.js'

const { area, bounds, from, to } = useSelection()   // what the user selected
const { records, loading, error } = useHotspots()   // every row of hotspots.csv
const rows = filterRecords(records, area, from, to) // rows inside the area and dates
```
