"""Serializable ML-ready samples pairing MODIS inputs and VIIRS target grids."""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd

from spatial_ml.features.environmental import EnvironmentalFeatureSet

from .target_grid import (
    LocalTargetGrid,
    build_viirs_target_grid,
)

Observation = Union[pd.Series, Dict[str, object]]


def _value(record: Observation, names: Tuple[str, ...], default: object = None) -> object:
    for name in names:
        if name in record:
            value = record[name]
            if value is not None and not pd.isna(value):
                return value
    return default


def _json_safe(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True)
class TrainingSample:
    """One traceable input/target pair with aligned environmental channels."""

    modis_id: str
    modis_features: Dict[str, Any]
    environmental_features: np.ndarray
    environmental_feature_names: Tuple[str, ...]
    target_grid: np.ndarray
    target_observations: pd.DataFrame
    metadata: Dict[str, Any]

    def __post_init__(self) -> None:
        environmental = np.asarray(self.environmental_features, dtype=np.float32)
        target = np.asarray(self.target_grid, dtype=np.float32)
        if target.ndim != 2:
            raise ValueError("target_grid must be a two-dimensional array.")
        if environmental.ndim != 3 or environmental.shape[:2] != target.shape:
            raise ValueError(
                "Environmental features must align with target_grid as H×W×C."
            )
        if environmental.shape[2] != len(self.environmental_feature_names):
            raise ValueError("Environmental feature names do not match channels.")
        if not np.isfinite(target).all() or not np.isin(target, (0.0, 1.0)).all():
            raise ValueError("The primary VIIRS target must be binary 0/1.")
        if self.metadata.get("modis_id", self.modis_id) != self.modis_id:
            raise ValueError("metadata modis_id must match the sample modis_id.")
        object.__setattr__(self, "environmental_features", environmental)
        object.__setattr__(self, "target_grid", target)
        object.__setattr__(
            self, "target_observations", self.target_observations.copy()
        )

    def save(self, path: Union[str, Path]) -> Path:
        """Write arrays and JSON metadata to a pickle-free compressed NPZ."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "modis_id": self.modis_id,
            "modis_features": self.modis_features,
            "environmental_feature_names": self.environmental_feature_names,
            "target_observations": self.target_observations.to_dict(
                orient="records"
            ),
            "metadata": self.metadata,
        }
        serialized = json.dumps(_json_safe(payload), allow_nan=False)
        with destination.open("wb") as stream:
            np.savez_compressed(
                stream,
                environmental_features=self.environmental_features,
                target_grid=self.target_grid,
                metadata=np.asarray(serialized),
            )
        return destination

    @classmethod
    def load(cls, path: Union[str, Path]) -> "TrainingSample":
        """Load a sample saved by :meth:`save` without unpickling objects."""
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"Training sample does not exist: {source}")
        with np.load(source, allow_pickle=False) as archive:
            payload = json.loads(str(archive["metadata"].item()))
            environmental_features = archive["environmental_features"].copy()
            target_grid = archive["target_grid"].copy()
        return cls(
            modis_id=payload["modis_id"],
            modis_features=payload["modis_features"],
            environmental_features=environmental_features,
            environmental_feature_names=tuple(
                payload["environmental_feature_names"]
            ),
            target_grid=target_grid,
            target_observations=pd.DataFrame(
                payload["target_observations"]
            ),
            metadata=payload["metadata"],
        )


def build_training_sample(
    modis_observation: Observation,
    viirs_observations: pd.DataFrame,
    environmental: Optional[EnvironmentalFeatureSet] = None,
    grid: Optional[LocalTargetGrid] = None,
) -> TrainingSample:
    """Build a training sample without dropping unmatched MODIS observations."""
    modis_id_value = _value(
        modis_observation, ("modis_id", "observation_id")
    )
    if modis_id_value is None:
        raise ValueError("MODIS observation must include a stable modis_id.")
    latitude = _value(
        modis_observation, ("modis_latitude", "latitude")
    )
    longitude = _value(
        modis_observation, ("modis_longitude", "longitude")
    )
    timestamp = _value(
        modis_observation, ("modis_datetime", "datetime")
    )
    if latitude is None or longitude is None or timestamp is None:
        raise ValueError(
            "MODIS observation must include latitude, longitude, and datetime."
        )

    local_grid = grid or LocalTargetGrid(float(latitude), float(longitude))
    grid_target = build_viirs_target_grid(
        modis_observation, viirs_observations, local_grid
    )
    if environmental is None:
        environmental_values = np.empty(
            (*local_grid.shape, 0), dtype=np.float32
        )
        feature_names: Tuple[str, ...] = ()
        environmental_metadata: Dict[str, Any] = {"sources": []}
    else:
        environmental_values = environmental.values
        feature_names = environmental.feature_names
        environmental_metadata = environmental.metadata
        if environmental_values.shape[:2] != local_grid.shape:
            raise ValueError(
                "Environmental feature dimensions must match the target grid."
            )

    datetime_value = pd.Timestamp(timestamp)
    if datetime_value.tzinfo is not None:
        datetime_value = datetime_value.tz_convert("UTC").tz_localize(None)
    modis_features = {
        "frp": _value(
            modis_observation, ("modis_frp", "frp")
        ),
        "confidence": _value(
            modis_observation, ("modis_confidence", "confidence")
        ),
    }
    modis_id = str(modis_id_value)
    target_observations = grid_target.observations
    target_ids = (
        target_observations["viirs_id"].dropna().astype(str).tolist()
        if "viirs_id" in target_observations
        else []
    )
    in_grid_count = int(target_observations["in_grid"].sum())
    metadata: Dict[str, Any] = {
        "modis_id": modis_id,
        "observation_datetime": datetime_value.isoformat(),
        "source_sensors": ["MODIS", "VIIRS"],
        "modis_latitude": float(latitude),
        "modis_longitude": float(longitude),
        "modis_satellite": _value(
            modis_observation, ("modis_satellite", "satellite")
        ),
        "modis_instrument": _value(
            modis_observation, ("modis_instrument", "instrument")
        ),
        "grid": local_grid.metadata,
        "target_representation": "binary_viirs_point_observation_occupancy",
        "target_sensor": "VIIRS",
        "target_data_status": "observed",
        "target_observation_count": len(target_observations),
        "target_in_grid_observation_count": in_grid_count,
        "target_out_of_grid_observation_count": (
            len(target_observations) - in_grid_count
        ),
        "viirs_ids": target_ids,
        "environmental": environmental_metadata,
    }
    return TrainingSample(
        modis_id=modis_id,
        modis_features=modis_features,
        environmental_features=environmental_values,
        environmental_feature_names=feature_names,
        target_grid=grid_target.values,
        target_observations=target_observations,
        metadata=metadata,
    )
