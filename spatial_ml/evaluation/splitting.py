"""Temporal, spatial-block, and combined held-out evaluation partitions."""

import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple, Union

import pandas as pd
from pyproj import Transformer

from spatial_ml.data.samples import TrainingSample

_EQUAL_AREA_TRANSFORMER = Transformer.from_crs(
    "EPSG:4326", "EPSG:6933", always_xy=True
)
Boundary = Union[str, pd.Timestamp]


@dataclass(frozen=True)
class EvaluationSplit:
    """Disjoint parent-level partitions and their reproducible split config."""

    train: Tuple[TrainingSample, ...]
    validation: Tuple[TrainingSample, ...]
    test: Tuple[TrainingSample, ...]
    dropped_parent_ids: Tuple[str, ...]
    strategy: str
    configuration: Dict[str, object]
    partition_blocks: Dict[str, Tuple[str, ...]]

    def __post_init__(self) -> None:
        parent_sets = [
            {sample.modis_id for sample in partition}
            for partition in (self.train, self.validation, self.test)
        ]
        if (
            parent_sets[0] & parent_sets[1]
            or parent_sets[0] & parent_sets[2]
            or parent_sets[1] & parent_sets[2]
        ):
            raise ValueError("A parent MODIS sample cannot cross evaluation splits.")
        if self.strategy in {"spatial", "combined"}:
            block_sets = [
                set(self.partition_blocks.get(name, ()))
                for name in ("train", "validation", "test")
            ]
            if (
                block_sets[0] & block_sets[1]
                or block_sets[0] & block_sets[2]
                or block_sets[1] & block_sets[2]
            ):
                raise ValueError("A spatial block cannot cross held-out partitions.")


def _parse_datetime_boundary(value: Optional[Boundary], name: str) -> pd.Timestamp:
    if value is None:
        raise ValueError(f"{name} is required for temporal evaluation.")
    text = str(value).strip()
    try:
        boundary = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid date or timestamp.") from exc
    if pd.isna(boundary):
        raise ValueError(f"{name} must be a valid date or timestamp.")
    if boundary.tzinfo is not None:
        boundary = boundary.tz_convert("UTC").tz_localize(None)
    if text.isdigit() and len(text) == 4:
        boundary = pd.Timestamp(f"{text}-12-31 23:59:59.999999999")
    elif len(text) <= 10 and boundary == boundary.normalize():
        boundary = boundary + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return boundary


def _sample_datetime(sample: TrainingSample) -> pd.Timestamp:
    value = sample.metadata.get("observation_datetime")
    if value is None:
        raise ValueError(
            f"Sample {sample.modis_id!r} has no observation_datetime metadata."
        )
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Sample {sample.modis_id!r} has an invalid observation_datetime."
        ) from exc
    if pd.isna(timestamp):
        raise ValueError(
            f"Sample {sample.modis_id!r} has an invalid observation_datetime."
        )
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    return timestamp


def spatial_block_id(
    sample: TrainingSample, block_size_m: float
) -> str:
    """Return an equal-area EPSG:6933 block ID for a MODIS parent sample."""
    if not math.isfinite(block_size_m) or block_size_m <= 0:
        raise ValueError("block_size_m must be a finite positive number.")
    try:
        latitude = float(sample.metadata["modis_latitude"])
        longitude = float(sample.metadata["modis_longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Sample {sample.modis_id!r} needs finite MODIS coordinates "
            "for spatial evaluation."
        ) from exc
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise ValueError(
            f"Sample {sample.modis_id!r} has invalid MODIS coordinates."
        )
    x_m, y_m = _EQUAL_AREA_TRANSFORMER.transform(longitude, latitude)
    if not math.isfinite(x_m) or not math.isfinite(y_m):
        raise ValueError(
            f"Sample {sample.modis_id!r} could not be projected into a spatial block."
        )
    return f"x{math.floor(x_m / block_size_m)}_y{math.floor(y_m / block_size_m)}"


def _choose_blocks(
    block_ids: Tuple[str, ...],
    validation_fraction: float,
    test_fraction: float,
    random_seed: int,
) -> Dict[str, Tuple[str, ...]]:
    if len(block_ids) < 3:
        raise ValueError(
            "At least three distinct spatial blocks are required for "
            "train/validation/test evaluation."
        )
    validation_count = max(1, int(round(len(block_ids) * validation_fraction)))
    test_count = max(1, int(round(len(block_ids) * test_fraction)))
    if validation_count + test_count >= len(block_ids):
        raise ValueError(
            "Spatial holdout fractions leave no blocks for training; use more "
            "blocks or smaller fractions."
        )
    shuffled = list(block_ids)
    random.Random(random_seed).shuffle(shuffled)
    test = tuple(sorted(shuffled[:test_count]))
    validation = tuple(
        sorted(shuffled[test_count : test_count + validation_count])
    )
    train = tuple(sorted(shuffled[test_count + validation_count :]))
    return {"train": train, "validation": validation, "test": test}


def create_evaluation_split(
    samples: Iterable[TrainingSample],
    strategy: str,
    *,
    train_end: Optional[Boundary] = None,
    validation_end: Optional[Boundary] = None,
    block_size_m: Optional[float] = None,
    validation_block_fraction: float = 0.2,
    test_block_fraction: float = 0.2,
    random_seed: int = 42,
) -> EvaluationSplit:
    """Create parent-level temporal, spatial-block, or strict combined splits.

    Temporal boundaries are inclusive end dates: training is through
    ``train_end``, validation is after that through ``validation_end``, and
    test is later. For ``combined``, both the time window and a distinct
    spatial block partition must match; samples outside those intersections
    are explicitly listed as dropped.
    """
    if strategy not in {"temporal", "spatial", "combined"}:
        raise ValueError(
            "strategy must be 'temporal', 'spatial', or 'combined'; "
            "random cell-level fallback is not supported."
        )
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ValueError("random_seed must be an integer.")
    for name, value in (
        ("validation_block_fraction", validation_block_fraction),
        ("test_block_fraction", test_block_fraction),
    ):
        if not math.isfinite(value) or not 0 < value < 1:
            raise ValueError(f"{name} must be between zero and one.")
    if validation_block_fraction + test_block_fraction >= 1:
        raise ValueError(
            "validation and test block fractions must sum to less than one."
        )

    sample_list = list(samples)
    if not sample_list:
        raise ValueError("No samples are available for evaluation splitting.")
    parent_ids = {sample.modis_id for sample in sample_list}
    if len(parent_ids) < 3:
        raise ValueError(
            "At least three unique parent MODIS samples are required for "
            "train/validation/test evaluation."
        )

    dates: Dict[str, pd.Timestamp] = {}
    blocks: Dict[str, str] = {}
    for sample in sample_list:
        sample_id = sample.modis_id
        if strategy in {"temporal", "combined"}:
            value = _sample_datetime(sample)
            if sample_id in dates and dates[sample_id] != value:
                raise ValueError(
                    f"Parent sample {sample_id!r} has conflicting timestamps."
                )
            dates[sample_id] = value
        if strategy in {"spatial", "combined"}:
            if block_size_m is None:
                raise ValueError(
                    "block_size_m is required for spatial or combined evaluation."
                )
            value = spatial_block_id(sample, block_size_m)
            if sample_id in blocks and blocks[sample_id] != value:
                raise ValueError(
                    f"Parent sample {sample_id!r} maps to conflicting spatial blocks."
                )
            blocks[sample_id] = value

    train_boundary = validation_boundary = None
    if strategy in {"temporal", "combined"}:
        train_boundary = _parse_datetime_boundary(train_end, "train_end")
        validation_boundary = _parse_datetime_boundary(
            validation_end, "validation_end"
        )
        if train_boundary >= validation_boundary:
            raise ValueError("train_end must be earlier than validation_end.")

    block_partitions: Dict[str, Tuple[str, ...]] = {
        "train": (),
        "validation": (),
        "test": (),
    }
    if strategy in {"spatial", "combined"}:
        block_partitions = _choose_blocks(
            tuple(sorted(set(blocks.values()))),
            validation_block_fraction,
            test_block_fraction,
            random_seed,
        )
    block_owner = {
        block: partition
        for partition, partition_blocks in block_partitions.items()
        for block in partition_blocks
    }

    parent_owner: Dict[str, str] = {}
    dropped_parent_ids = []
    for sample_id in sorted(parent_ids):
        temporal_partition: Optional[str] = None
        if strategy in {"temporal", "combined"}:
            sample_time = dates[sample_id]
            if sample_time <= train_boundary:
                temporal_partition = "train"
            elif sample_time <= validation_boundary:
                temporal_partition = "validation"
            else:
                temporal_partition = "test"

        if strategy == "temporal":
            parent_owner[sample_id] = temporal_partition or ""
        elif strategy == "spatial":
            parent_owner[sample_id] = block_owner[blocks[sample_id]]
        else:
            spatial_partition = block_owner[blocks[sample_id]]
            if temporal_partition == spatial_partition:
                parent_owner[sample_id] = temporal_partition
            else:
                dropped_parent_ids.append(sample_id)

    partitions = {
        name: tuple(
            sample
            for sample in sample_list
            if parent_owner.get(sample.modis_id) == name
        )
        for name in ("train", "validation", "test")
    }
    empty_partitions = [name for name, partition in partitions.items() if not partition]
    if empty_partitions:
        counts = {
            name: len({sample.modis_id for sample in partition})
            for name, partition in partitions.items()
        }
        raise ValueError(
            "Evaluation split is not meaningful; empty partition(s): "
            f"{', '.join(empty_partitions)}. Parent counts: {counts}."
        )

    return EvaluationSplit(
        train=partitions["train"],
        validation=partitions["validation"],
        test=partitions["test"],
        dropped_parent_ids=tuple(dropped_parent_ids),
        strategy=strategy,
        configuration={
            "strategy": strategy,
            "train_end": (
                train_boundary.isoformat() if train_boundary is not None else None
            ),
            "validation_end": (
                validation_boundary.isoformat()
                if validation_boundary is not None
                else None
            ),
            "block_size_m": block_size_m,
            "validation_block_fraction": validation_block_fraction,
            "test_block_fraction": test_block_fraction,
            "random_seed": random_seed,
            "partition_blocks": {
                name: list(values)
                for name, values in block_partitions.items()
            },
        },
        partition_blocks=block_partitions,
    )
