# Fire regime results (global, MODIS)

Made with:

```bash
python -m analysis.fire_regimes.run --firms <11 MODIS C6.1 FIRMS zips> --sensor MODIS \
  --start 2015-11 --end 2026-11 --window 3 --k 7 --out analysis/fire_regimes/output
```

Data: NASA FIRMS MODIS C6.1, global, 2015-11-14 to 2026-11 (archive + NRT), ~43M detections.
FIRMS requests 849635, 849514, 849541, 849531, 849545, 849584, 849433, 849431, 849427, 849425, 849418.

- `fire_regimes.geojson`: 36,006 cells, 7 regimes (format in `../README.md`)
- `regime_summary.json`: regime cards, monthly curves, validation
- `fire_regimes_global_preview.png`: quick static map

k = 7: silhouette is highest at k=5 (0.21) and nearly flat from 6 to 10 (0.18-0.19); 7 gives distinct,
recognisable regimes. Known-region check: 4 of 5 match (Punjab/Haryana does not come out as agricultural).
Regime shifts use 3-year windows (2015-2017 ... 2024-2026) because the record is 11 years long.
