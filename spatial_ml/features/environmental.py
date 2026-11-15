"""Provider-agnostic raster features aligned to a local target grid."""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Union

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from spatial_ml.data.target_grid import LocalTargetGrid


@dataclass(frozen=True)
class RasterFeatureSource:
    """A local single-band raster and its feature name/resampling policy."""

    name: str
    path: Union[str, Path]
    band: int = 1
    resampling: Resampling = Resampling.bilinear

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if not self.name.strip():
            raise ValueError("Raster feature name must not be empty.")
        if self.band < 1:
            raise ValueError("Raster band indexes start at 1.")


@dataclass(frozen=True)
class EnvironmentalFeatureSet:
    """Aligned H×W×C environmental values and source provenance."""

    values: np.ndarray
    feature_names: Tuple[str, ...]
    metadata: Dict[str, object]

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=np.float32)
        if values.ndim != 3:
            raise ValueError("Environmental features must have shape (height, width, channels).")
        if values.shape[2] != len(self.feature_names):
            raise ValueError("Feature-name count must match the feature channel count.")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("Environmental feature names must be unique.")
        object.__setattr__(self, "values", values)


def load_raster_feature(
    source: RasterFeatureSource,
    grid: LocalTargetGrid,
    missing_value: float = math.nan,
) -> np.ndarray:
    """Reproject/resample one raster band onto the local target-grid geometry."""
    path = Path(source.path)
    if not path.is_file():
        raise FileNotFoundError(f"Environmental raster does not exist: {path}")
    if not (math.isnan(missing_value) or math.isfinite(missing_value)):
        raise ValueError("missing_value must be finite or NaN.")

    try:
        with rasterio.open(path) as dataset:
            if source.band > dataset.count:
                raise ValueError(
                    f"Raster {path} has {dataset.count} bands; "
                    f"band {source.band} was requested."
                )
            with WarpedVRT(
                dataset,
                crs=grid.crs,
                transform=grid.affine_transform,
                width=grid.width,
                height=grid.height,
                resampling=source.resampling,
                nodata=np.nan,
                dtype="float32",
            ) as aligned:
                values = aligned.read(source.band, masked=True)
                result = np.asarray(values.filled(np.nan), dtype=np.float32)
    except rasterio.errors.RasterioIOError as exc:
        raise ValueError(f"Could not read environmental raster {path}: {exc}") from exc

    if not math.isnan(missing_value):
        result = np.where(np.isfinite(result), result, missing_value).astype(np.float32)
    return result


def derive_slope_aspect(
    elevation: np.ndarray, resolution_m: float
) -> Dict[str, np.ndarray]:
    """Derive slope degrees and downhill aspect sine/cosine from an aligned DEM."""
    elevation_values = np.asarray(elevation, dtype=np.float64)
    if elevation_values.ndim != 2:
        raise ValueError("Elevation must be a two-dimensional array.")
    if not math.isfinite(resolution_m) or resolution_m <= 0:
        raise ValueError("resolution_m must be a finite positive number.")

    height, width = elevation_values.shape
    if width > 1:
        dz_dx = np.gradient(elevation_values, resolution_m, axis=1)
    else:
        dz_dx = np.full(elevation_values.shape, np.nan)
    if height > 1:
        dz_dy = np.gradient(elevation_values, -resolution_m, axis=0)
    else:
        dz_dy = np.full(elevation_values.shape, np.nan)

    gradient_magnitude = np.hypot(dz_dx, dz_dy)
    slope = np.degrees(np.arctan(gradient_magnitude))
    aspect_degrees = np.mod(
        np.degrees(np.arctan2(-dz_dx, -dz_dy)), 360.0
    )
    flat = gradient_magnitude <= 1e-12
    aspect_degrees[flat] = np.nan
    return {
        "slope_degrees": slope.astype(np.float32),
        "aspect_sin": np.sin(np.radians(aspect_degrees)).astype(np.float32),
        "aspect_cos": np.cos(np.radians(aspect_degrees)).astype(np.float32),
    }


def extract_environmental_features(
    grid: LocalTargetGrid,
    sources: Sequence[RasterFeatureSource],
    missing_value: float = math.nan,
    derive_terrain_from: Optional[str] = None,
) -> EnvironmentalFeatureSet:
    """Align configured rasters and optionally derive terrain from elevation.

    Missing source pixels remain NaN by default. No imputation or normalization
    is performed, avoiding data-leakage across later train/validation splits.
    """
    names = [source.name for source in sources]
    if len(set(names)) != len(names):
        raise ValueError("Raster feature source names must be unique.")
    if derive_terrain_from is not None and derive_terrain_from not in names:
        raise ValueError(
            f"Cannot derive terrain; no source named {derive_terrain_from!r} exists."
        )

    aligned: Dict[str, np.ndarray] = {
        source.name: load_raster_feature(source, grid, missing_value=math.nan)
        for source in sources
    }
    feature_names = list(names)
    if derive_terrain_from is not None:
        terrain = derive_slope_aspect(
            aligned[derive_terrain_from], grid.resolution_m
        )
        for name, values in terrain.items():
            aligned[name] = values
            feature_names.append(name)

    if feature_names:
        values = np.stack([aligned[name] for name in feature_names], axis=-1)
    else:
        values = np.empty((*grid.shape, 0), dtype=np.float32)
    if not math.isnan(missing_value):
        values = np.where(np.isfinite(values), values, missing_value).astype(
            np.float32
        )

    metadata = {
        "sources": [
            {
                "name": source.name,
                "path": Path(source.path).name,
                "band": source.band,
                "resampling": source.resampling.name,
            }
            for source in sources
        ],
        "derived_terrain_from": derive_terrain_from,
        "missing_value": "NaN" if math.isnan(missing_value) else missing_value,
        "feature_names": feature_names,
        "grid_crs": grid.crs.to_string(),
    }
    return EnvironmentalFeatureSet(
        values=values,
        feature_names=tuple(feature_names),
        metadata=metadata,
    )
