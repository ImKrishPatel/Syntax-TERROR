"""MODIS-centered local grids and observed VIIRS target mapping."""

import math
from dataclasses import dataclass
from functools import cached_property
from typing import Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd
from affine import Affine
from pyproj import CRS, Transformer

from spatial_ml import config

Observation = Union[pd.Series, Dict[str, object]]


@dataclass(frozen=True)
class LocalTargetGrid:
    """Square local metric grid centered at a MODIS observation.

    Rows are ordered north-to-south and columns west-to-east. Cell bounds use
    the raster convention: left/top are inclusive; right/bottom are exclusive.
    """

    origin_latitude: float
    origin_longitude: float
    resolution_m: float = config.TARGET_GRID_RESOLUTION_M
    width: int = config.TARGET_GRID_WIDTH
    height: int = config.TARGET_GRID_HEIGHT
    projection_strategy: str = config.TARGET_GRID_PROJECTION
    target_crs: Optional[str] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.origin_latitude) or not -90 <= self.origin_latitude <= 90:
            raise ValueError("origin_latitude must be finite and within [-90, 90].")
        if not math.isfinite(self.origin_longitude) or not -180 <= self.origin_longitude <= 180:
            raise ValueError("origin_longitude must be finite and within [-180, 180].")
        if not math.isfinite(self.resolution_m) or self.resolution_m <= 0:
            raise ValueError("resolution_m must be a finite positive number.")
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width < 1:
            raise ValueError("width must be a positive integer.")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height < 1:
            raise ValueError("height must be a positive integer.")
        if self.projection_strategy == "local_aeqd":
            if self.target_crs is not None:
                raise ValueError(
                    "target_crs is only accepted with projection_strategy='custom'."
                )
        elif self.projection_strategy == "custom":
            if not self.target_crs:
                raise ValueError("target_crs is required for a custom projection.")
            self._validate_custom_crs(self.target_crs)
        else:
            raise ValueError(
                "projection_strategy must be 'local_aeqd' or 'custom'."
            )

    @staticmethod
    def _validate_custom_crs(value: str) -> CRS:
        try:
            crs = CRS.from_user_input(value)
        except Exception as exc:
            raise ValueError(f"Invalid target_crs {value!r}.") from exc
        if not crs.is_projected:
            raise ValueError("target_crs must be a projected CRS with meter units.")
        if len(crs.axis_info) < 2 or any(
            not math.isclose(axis.unit_conversion_factor, 1.0, rel_tol=0.0, abs_tol=1e-12)
            for axis in crs.axis_info[:2]
        ):
            raise ValueError("target_crs must use meters on its first two axes.")
        return crs

    def _local_aeqd_definition(self) -> str:
        return (
            f"+proj=aeqd +lat_0={self.origin_latitude:.12f} "
            f"+lon_0={self.origin_longitude:.12f} +datum=WGS84 "
            "+units=m +no_defs +type=crs"
        )

    @cached_property
    def crs(self) -> CRS:
        """Return the local AEQD CRS or validated user-selected projected CRS."""
        if self.projection_strategy == "custom":
            return self._validate_custom_crs(self.target_crs or "")
        return CRS.from_proj4(self._local_aeqd_definition())

    @cached_property
    def transformer(self) -> Transformer:
        """Transform geographic coordinates to the grid's projected CRS."""
        return Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)

    @cached_property
    def projected_origin(self) -> Tuple[float, float]:
        """Projected coordinate of the MODIS origin."""
        x, y = self.transformer.transform(
            self.origin_longitude, self.origin_latitude
        )
        return float(x), float(y)

    @property
    def shape(self) -> Tuple[int, int]:
        """Return the target array shape as (height, width)."""
        return self.height, self.width

    @property
    def bounds_local(self) -> Tuple[float, float, float, float]:
        """Return local-coordinate bounds as (left, bottom, right, top)."""
        half_width = self.width * self.resolution_m / 2.0
        half_height = self.height * self.resolution_m / 2.0
        return -half_width, -half_height, half_width, half_height

    @property
    def affine_transform(self) -> Affine:
        """Return the north-up raster transform in the target CRS."""
        origin_x, origin_y = self.projected_origin
        left, _, _, top = self.bounds_local
        return Affine(
            self.resolution_m,
            0.0,
            origin_x + left,
            0.0,
            -self.resolution_m,
            origin_y + top,
        )

    @property
    def metadata(self) -> Dict[str, object]:
        """Return serializable geometry and indexing metadata."""
        target_crs = (
            self._local_aeqd_definition()
            if self.projection_strategy == "local_aeqd"
            else self.crs.to_string()
        )
        return {
            "origin_latitude": self.origin_latitude,
            "origin_longitude": self.origin_longitude,
            "resolution_m": self.resolution_m,
            "width": self.width,
            "height": self.height,
            "shape": [self.height, self.width],
            "projection_strategy": self.projection_strategy,
            "target_crs": target_crs,
            "bounds_local_m": list(self.bounds_local),
            "row_order": "north_to_south",
            "column_order": "west_to_east",
            "cell_boundary_rule": "left/top inclusive; right/bottom exclusive",
        }

    def geographic_to_local(
        self,
        longitude: Union[float, np.ndarray],
        latitude: Union[float, np.ndarray],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Project geographic coordinates to MODIS-centered local meters."""
        x, y = self.transformer.transform(longitude, latitude)
        origin_x, origin_y = self.projected_origin
        return np.asarray(x, dtype=float) - origin_x, np.asarray(y, dtype=float) - origin_y

    def local_to_geographic(
        self,
        x_m: Union[float, np.ndarray],
        y_m: Union[float, np.ndarray],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Convert MODIS-centered local meters to longitude and latitude."""
        origin_x, origin_y = self.projected_origin
        longitude, latitude = self.transformer.transform(
            np.asarray(x_m, dtype=float) + origin_x,
            np.asarray(y_m, dtype=float) + origin_y,
            direction="INVERSE",
        )
        return np.asarray(longitude, dtype=float), np.asarray(latitude, dtype=float)

    def cell_for_local(self, x_m: float, y_m: float) -> Optional[Tuple[int, int]]:
        """Return (row, column), or None when a point is outside the grid."""
        left, bottom, right, top = self.bounds_local
        if (
            not math.isfinite(x_m)
            or not math.isfinite(y_m)
            or x_m < left
            or x_m >= right
            or y_m <= bottom
            or y_m > top
        ):
            return None
        column = min(
            self.width - 1,
            int(math.floor((x_m - left) / self.resolution_m)),
        )
        row = min(
            self.height - 1,
            int(math.floor((top - y_m) / self.resolution_m)),
        )
        return row, column

    def cell_for_geographic(
        self, longitude: float, latitude: float
    ) -> Optional[Tuple[int, int]]:
        """Project a geographic point and return its row/column if in-grid."""
        x_m, y_m = self.geographic_to_local(longitude, latitude)
        return self.cell_for_local(float(x_m), float(y_m))

    def cell_center_local(self, row: int, column: int) -> Tuple[float, float]:
        """Return the local-meter center coordinate of a target cell."""
        if not (0 <= row < self.height and 0 <= column < self.width):
            raise IndexError("Target-grid row or column is out of bounds.")
        left, _, _, top = self.bounds_local
        x_m = left + (column + 0.5) * self.resolution_m
        y_m = top - (row + 0.5) * self.resolution_m
        return x_m, y_m


@dataclass(frozen=True)
class GridTarget:
    """Binary VIIRS point-evidence grid and its source-observation mapping."""

    grid: LocalTargetGrid
    values: np.ndarray
    observations: pd.DataFrame

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=np.float32)
        if values.shape != self.grid.shape:
            raise ValueError(
                f"Target shape {values.shape} does not match grid {self.grid.shape}."
            )
        if not np.isfinite(values).all() or not np.isin(values, (0.0, 1.0)).all():
            raise ValueError("Primary VIIRS target values must be finite binary 0/1.")
        object.__setattr__(self, "values", values)


def _record_value(
    record: Observation, names: Tuple[str, ...], default: object = None
) -> object:
    for name in names:
        if name in record:
            value = record[name]
            if value is not None and not pd.isna(value):
                return value
    return default


def _local_offsets(
    record: pd.Series,
    grid: LocalTargetGrid,
    longitude_column: str,
    latitude_column: str,
) -> Tuple[float, float]:
    if grid.projection_strategy == "local_aeqd" and {
        "delta_x_m",
        "delta_y_m",
    }.issubset(record.index):
        x_value, y_value = record["delta_x_m"], record["delta_y_m"]
        if pd.notna(x_value) and pd.notna(y_value):
            return float(x_value), float(y_value)

    longitude, latitude = record[longitude_column], record[latitude_column]
    if pd.isna(longitude) or pd.isna(latitude):
        return math.nan, math.nan
    x_m, y_m = grid.geographic_to_local(float(longitude), float(latitude))
    return float(x_m), float(y_m)


def build_viirs_target_grid(
    modis_observation: Observation,
    viirs_observations: pd.DataFrame,
    grid: Optional[LocalTargetGrid] = None,
) -> GridTarget:
    """Map VIIRS observations to cells using binary observed-point occupancy.

    Existing MODIS-centered ``delta_x_m``/``delta_y_m`` candidate coordinates
    are reused for the default local AEQD grid. FRP and confidence remain in
    the observation table and do not alter the primary target values.
    """
    latitude = _record_value(
        modis_observation, ("modis_latitude", "latitude")
    )
    longitude = _record_value(
        modis_observation, ("modis_longitude", "longitude")
    )
    if latitude is None or longitude is None:
        raise ValueError("MODIS observation must provide latitude and longitude.")

    target_grid = grid or LocalTargetGrid(float(latitude), float(longitude))
    if not (
        math.isclose(target_grid.origin_latitude, float(latitude), abs_tol=1e-12)
        and math.isclose(target_grid.origin_longitude, float(longitude), abs_tol=1e-12)
    ):
        raise ValueError("Target grid origin must match the MODIS observation.")

    modis_id = _record_value(
        modis_observation, ("modis_id", "observation_id")
    )
    observations = viirs_observations.copy().reset_index(drop=True)
    if "valid" in observations:
        observations = observations.loc[
            observations["valid"].fillna(False).astype(bool)
        ].reset_index(drop=True)
    elif "match_valid" in observations:
        observations = observations.loc[
            observations["match_valid"].fillna(False).astype(bool)
        ].reset_index(drop=True)

    if modis_id is not None and "modis_id" in observations:
        candidate_ids = observations["modis_id"].dropna().astype(str).unique()
        if any(candidate_id != str(modis_id) for candidate_id in candidate_ids):
            raise ValueError("VIIRS rows include candidates for a different MODIS ID.")

    latitude_column = (
        "viirs_latitude"
        if "viirs_latitude" in observations
        else "latitude"
    )
    longitude_column = (
        "viirs_longitude"
        if "viirs_longitude" in observations
        else "longitude"
    )
    if not observations.empty:
        missing_coordinates = {
            latitude_column,
            longitude_column,
        }.difference(observations.columns)
        if missing_coordinates:
            raise ValueError(
                "VIIRS observations must retain geographic coordinates."
            )

    target_values = np.zeros(target_grid.shape, dtype=np.float32)
    target_rows = []
    target_columns = []
    in_grid_flags = []
    for _, observation in observations.iterrows():
        x_m, y_m = _local_offsets(
            observation, target_grid, longitude_column, latitude_column
        )
        cell = target_grid.cell_for_local(x_m, y_m)
        if cell is None:
            target_rows.append(pd.NA)
            target_columns.append(pd.NA)
            in_grid_flags.append(False)
            continue
        row, column = cell
        target_values[row, column] = 1.0
        target_rows.append(row)
        target_columns.append(column)
        in_grid_flags.append(True)

    observations["target_row"] = pd.array(target_rows, dtype="Int64")
    observations["target_col"] = pd.array(target_columns, dtype="Int64")
    observations["in_grid"] = in_grid_flags
    return GridTarget(target_grid, target_values, observations)
