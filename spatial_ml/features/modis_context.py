"""Neighbouring-MODIS context channels for the local target grid.

Without elevation or land-cover rasters, every input the models received was the
same for all cells of a sample (the parent MODIS FRP and confidence) apart from
the cell position, so nothing told the cells apart. A fire front, though, is
usually seen as several adjacent MODIS pixels from the same overpass. These
channels describe, for each target cell, the other MODIS detections from that
same overpass around it.

Channels (per target cell):
- modis_neighbors_1km: other same-overpass MODIS detections within 1 km of the cell centre
- modis_neighbor_frp_idw: their FRP, inverse-distance weighted (MW per km)
- modis_nearest_neighbor_km: distance to the nearest one (capped at 3 km)
- modis_scan_km / modis_track_km: the parent pixel's footprint (same for all cells)
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from .environmental import EnvironmentalFeatureSet

FEATURE_NAMES = (
    "modis_neighbors_1km",
    "modis_neighbor_frp_idw",
    "modis_nearest_neighbor_km",
    "modis_scan_km",
    "modis_track_km",
)
SEARCH_RADIUS_M = 3000.0
SAME_OVERPASS_MINUTES = 5.0


def build_modis_context(pairs: pd.DataFrame, modis: pd.DataFrame, grids: Dict[str, object]) -> Dict[str, EnvironmentalFeatureSet]:
    """Return one feature set per parent MODIS id.

    pairs: overlap pairs (modis_id, modis_latitude, modis_longitude, modis_datetime, modis_satellite)
    modis: cleaned MODIS records (latitude, longitude, datetime, satellite, frp, scan, track)
    grids: parent MODIS id -> LocalTargetGrid
    """
    detections = modis.copy()
    detections["datetime"] = pd.to_datetime(detections["datetime"])
    for column in ("frp", "scan", "track"):
        if column not in detections:
            detections[column] = np.nan
    groups = {key: g for key, g in detections.groupby([detections["satellite"].astype(str), detections["datetime"].dt.date])}

    result: Dict[str, EnvironmentalFeatureSet] = {}
    for pair in pairs.drop_duplicates("modis_id").to_dict(orient="records"):
        parent_id = str(pair["modis_id"])
        grid = grids[parent_id]
        when = pd.Timestamp(pair["modis_datetime"])
        same_day = groups.get((str(pair["modis_satellite"]), when.date()))
        values = np.zeros((*grid.shape, len(FEATURE_NAMES)), dtype=np.float32)
        values[..., 2] = SEARCH_RADIUS_M / 1000.0
        scan = track = np.nan
        if same_day is not None:
            near_time = same_day[(same_day["datetime"] - when).abs() <= pd.Timedelta(minutes=SAME_OVERPASS_MINUTES)]
            # cheap degree prefilter before projecting
            lat0, lon0 = float(pair["modis_latitude"]), float(pair["modis_longitude"])
            dlat = SEARCH_RADIUS_M / 111_000.0 + 0.01
            dlon = dlat / max(np.cos(np.radians(lat0)), 0.1)
            near = near_time[(near_time["latitude"].sub(lat0).abs() <= dlat) & (near_time["longitude"].sub(lon0).abs() <= dlon)]
            if len(near):
                x, y = grid.geographic_to_local(near["longitude"].to_numpy(), near["latitude"].to_numpy())
                d0 = np.hypot(x, y)
                parent = int(np.argmin(d0))
                if d0[parent] < 50:  # the parent detection itself
                    scan, track = float(near["scan"].iloc[parent]), float(near["track"].iloc[parent])
                    keep = np.arange(len(near)) != parent
                    x, y, frp = x[keep], y[keep], near["frp"].to_numpy(dtype=float)[keep]
                else:
                    frp = near["frp"].to_numpy(dtype=float)
                frp = np.where(np.isfinite(frp), frp, 0.0)
                for row in range(grid.height):
                    for column in range(grid.width):
                        cx, cy = grid.cell_center_local(row, column)
                        d = np.hypot(x - cx, y - cy)
                        if d.size:
                            values[row, column, 0] = float((d <= 1000.0).sum())
                            values[row, column, 1] = float((frp / (d / 1000.0 + 0.375)).sum())
                            values[row, column, 2] = float(min(d.min(), SEARCH_RADIUS_M) / 1000.0)
        values[..., 3] = scan
        values[..., 4] = track
        result[parent_id] = EnvironmentalFeatureSet(
            values=values,
            feature_names=FEATURE_NAMES,
            metadata={"sources": [{"name": "modis_same_overpass_context", "radius_m": SEARCH_RADIUS_M}]},
        )
    return result
