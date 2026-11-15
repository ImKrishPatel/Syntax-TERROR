from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin

from spatial_ml.data.target_grid import LocalTargetGrid
from spatial_ml.features.environmental import (
    RasterFeatureSource,
    derive_slope_aspect,
    extract_environmental_features,
    load_raster_feature,
)


def _write_raster(
    path: Path,
    values: np.ndarray,
    transform: rasterio.Affine,
    crs: str = "EPSG:4326",
    nodata: float | int | None = None,
) -> None:
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": values.dtype,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values, 1)


def test_raster_features_are_reprojected_and_aligned(tmp_path: Path) -> None:
    grid = LocalTargetGrid(0.0, 0.0)
    transform = from_origin(-0.02, 0.02, 0.008, 0.008)
    elevation = np.full((5, 5), 100.0, dtype=np.float32)
    elevation[2, 2] = -9999.0
    land_cover = np.full((5, 5), 7, dtype=np.uint8)
    elevation_path = tmp_path / "synthetic_elevation.tif"
    land_cover_path = tmp_path / "synthetic_land_cover.tif"
    _write_raster(elevation_path, elevation, transform, nodata=-9999.0)
    _write_raster(land_cover_path, land_cover, transform)

    features = extract_environmental_features(
        grid,
        [
            RasterFeatureSource(
                "elevation_m", elevation_path, resampling=Resampling.nearest
            ),
            RasterFeatureSource(
                "land_cover", land_cover_path, resampling=Resampling.nearest
            ),
        ],
        missing_value=-9999.0,
        derive_terrain_from="elevation_m",
    )

    assert features.values.shape == (3, 3, 5)
    assert features.feature_names == (
        "elevation_m",
        "land_cover",
        "slope_degrees",
        "aspect_sin",
        "aspect_cos",
    )
    assert features.values[1, 1, 0] == -9999.0
    assert np.all(features.values[:, :, 1] == 7)
    assert np.isfinite(features.values[:, :, 2]).any()
    assert features.metadata["grid_crs"] == grid.crs.to_string()


def test_slope_and_aspect_are_derived_without_angle_discontinuity() -> None:
    resolution_m = 375.0
    x_values = np.tile(np.array([-resolution_m, 0.0, resolution_m]), (3, 1))

    terrain = derive_slope_aspect(x_values, resolution_m)

    np.testing.assert_allclose(terrain["slope_degrees"], 45.0)
    np.testing.assert_allclose(terrain["aspect_sin"], -1.0, atol=1e-6)
    np.testing.assert_allclose(terrain["aspect_cos"], 0.0, atol=1e-6)
    assert "aspect_degrees" not in terrain


def test_flat_aspect_is_missing_and_slope_is_zero() -> None:
    terrain = derive_slope_aspect(np.ones((3, 3)), 375.0)

    np.testing.assert_allclose(terrain["slope_degrees"], 0.0)
    assert np.isnan(terrain["aspect_sin"]).all()
    assert np.isnan(terrain["aspect_cos"]).all()


def test_missing_raster_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Environmental raster does not exist"):
        load_raster_feature(
            RasterFeatureSource("elevation", tmp_path / "missing.tif"),
            LocalTargetGrid(0.0, 0.0),
        )


def test_empty_environmental_sources_produce_zero_channels() -> None:
    grid = LocalTargetGrid(0.0, 0.0)

    features = extract_environmental_features(grid, [])

    assert features.values.shape == (3, 3, 0)
    assert features.feature_names == ()


def test_terrain_derivation_requires_named_elevation_source() -> None:
    with pytest.raises(ValueError, match="no source named"):
        extract_environmental_features(
            LocalTargetGrid(0.0, 0.0), [], derive_terrain_from="dem"
        )
