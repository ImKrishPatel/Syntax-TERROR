"""Parent-observation-level dataset partitions for spatial samples."""

import random
from dataclasses import dataclass
from typing import Iterable, Tuple

from .samples import TrainingSample


@dataclass(frozen=True)
class ParentSampleSplit:
    """Train/validation/test samples partitioned by parent MODIS ID."""

    train: Tuple[TrainingSample, ...]
    validation: Tuple[TrainingSample, ...]
    test: Tuple[TrainingSample, ...]

    def __post_init__(self) -> None:
        train_ids = {sample.modis_id for sample in self.train}
        validation_ids = {sample.modis_id for sample in self.validation}
        test_ids = {sample.modis_id for sample in self.test}
        if (
            train_ids & validation_ids
            or train_ids & test_ids
            or validation_ids & test_ids
        ):
            raise ValueError("A parent MODIS sample cannot cross data partitions.")


def split_samples_by_parent(
    samples: Iterable[TrainingSample],
    validation_fraction: float = 0.2,
    test_fraction: float = 0.2,
    random_seed: int = 42,
) -> ParentSampleSplit:
    """Split complete parent samples; never partition individual grid cells.

    Input metadata such as observation timestamps remains attached to every
    sample for later chronological or event-aware split strategies.
    """
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between zero and one.")
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one.")
    if validation_fraction + test_fraction >= 1:
        raise ValueError(
            "validation_fraction and test_fraction must sum to less than one."
        )
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ValueError("random_seed must be an integer.")

    sample_list = list(samples)
    if not sample_list:
        raise ValueError("At least one sample is required for splitting.")
    parent_ids = sorted({sample.modis_id for sample in sample_list})
    if len(parent_ids) < 3:
        raise ValueError("At least three unique parent MODIS IDs are required.")

    validation_count = max(1, int(round(len(parent_ids) * validation_fraction)))
    test_count = max(1, int(round(len(parent_ids) * test_fraction)))
    if validation_count + test_count >= len(parent_ids):
        raise ValueError(
            "Split fractions leave no parent samples for training; provide more "
            "samples or smaller validation/test fractions."
        )

    shuffled_ids = parent_ids.copy()
    random.Random(random_seed).shuffle(shuffled_ids)
    test_ids = set(shuffled_ids[:test_count])
    validation_ids = set(
        shuffled_ids[test_count : test_count + validation_count]
    )
    train_ids = set(shuffled_ids[test_count + validation_count :])

    return ParentSampleSplit(
        train=tuple(sample for sample in sample_list if sample.modis_id in train_ids),
        validation=tuple(
            sample for sample in sample_list if sample.modis_id in validation_ids
        ),
        test=tuple(sample for sample in sample_list if sample.modis_id in test_ids),
    )
