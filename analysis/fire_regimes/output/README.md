# Fire regime results (global, MODIS, 2005-2026)

Made with:

```bash
python -m analysis.fire_regimes.run --firms <21 MODIS C6.1 FIRMS zips> --sensor MODIS \
  --start 2005-11 --end 2026-11 --k 7 --out analysis/fire_regimes/output
```

Data: NASA FIRMS MODIS C6.1, global, 2005-11-14 to 2026-11 (archive + NRT for the last months), one sensor
throughout so regime shifts are not sensor artefacts. FIRMS requests: 849561, 849569, 849576, 849582, 849987,
849508, 849516, 849523, 849527, 849529, 849635, 849514, 849541, 849531, 849545, 849584, 849433, 849431,
849427, 849425, 849418.

- `fire_regimes.geojson`: 41,419 cells (0.5°), 7 regimes (format in `../README.md`)
- `regime_summary.json`: regime cards, monthly curves, validation
- `fire_regimes_global_preview.png`, `fire_regime_shifts_preview.png`: quick static maps

Validation: silhouette 0.20, Davies-Bouldin 1.42, seed stability ARI 0.95, odd vs even year ARI 0.47.
k = 7 (silhouette is nearly flat from 6 to 10; 7 gives distinct, recognisable regimes, and the same 7 appear
with 11 or 21 years of data). Known regions: 4 of 5 match (Punjab/Haryana does not come out as agricultural).
Regime shifts: windows 2005-2011, 2012-2016, 2017-2021, 2022-2026; a shift needs the first two and the
last two windows to agree and differ. 2,033 cells (4.9%) shifted.
