"""Make a smaller fire_regimes.geojson for the web app.

Keeps only the properties the app reads (DATA_FORMAT.md section 2) and writes
compact JSON. The full file stays in analysis/fire_regimes/output.

    python -m analysis.fire_regimes.slim analysis/fire_regimes/output/fire_regimes.geojson public/data/fire_regimes.geojson
"""
import json
import sys

KEEP = ["regime_id", "regime_name", "color", "hotspots_per_year", "peak_month", "shifted", "shift_from", "shift_to"]


def slim(src, dst):
    gj = json.load(open(src))
    for f in gj["features"]:
        p = f["properties"]
        q = {k: p[k] for k in KEEP if k in p}
        if not q.get("shifted"):           # drop empty shift fields
            for k in ("shifted", "shift_from", "shift_to"):
                q.pop(k, None)
        q["hotspots_per_year"] = round(q.get("hotspots_per_year", 0))
        f["properties"] = q
        f["geometry"]["coordinates"] = [[[round(x, 2), round(y, 2)] for x, y in ring]
                                        for ring in f["geometry"]["coordinates"]]
    with open(dst, "w") as fh:
        json.dump(gj, fh, separators=(",", ":"))


if __name__ == "__main__":
    slim(*sys.argv[1:3])
