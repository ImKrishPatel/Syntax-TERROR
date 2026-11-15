"""Small convolutional ensemble for spatial VIIRS point-occupancy probability."""

import logging
import math
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from spatial_ml import config
from spatial_ml.data.samples import TrainingSample
from spatial_ml.models.probabilistic_baseline import (
    SpatialProbabilityEstimate,
    sample_to_cell_feature_matrix,
)

LOGGER = logging.getLogger(__name__)
MODEL_VERSION = "1.0.0"
ARTIFACT_FORMAT_VERSION = 1
TARGET_DEFINITION = "observed_viirs_occupancy"


def _serializable_scalar(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, np.generic):
        return _serializable_scalar(value.item())
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def training_sample_to_spatial_tensor(
    sample: TrainingSample,
) -> Tuple[np.ndarray, Tuple[str, ...]]:
    """Convert an M5 feature matrix into [channels, north-to-south rows, columns].

    The shared M5 feature matrix is row-major: its first ``width`` rows map to
    target-grid row 0 from west to east, then row 1, and so on. Reshaping before
    moving the feature axis preserves the established target-grid ordering.
    Target values and VIIRS status/evidence metadata are never included.
    """
    matrix, feature_names = sample_to_cell_feature_matrix(sample)
    height, width = sample.target_grid.shape
    if matrix.shape[0] != height * width:
        raise ValueError("Cell features do not match the sample target-grid shape.")
    channels_last = matrix.reshape(height, width, len(feature_names))
    return np.ascontiguousarray(channels_last.transpose(2, 0, 1)), feature_names


@dataclass(frozen=True)
class _PreprocessingState:
    """Training-fitted median imputation and standardization parameters."""

    medians: np.ndarray
    means: np.ndarray
    scales: np.ndarray
    indicator_indices: Tuple[int, ...]
    feature_names: Tuple[str, ...]

    @property
    def channel_names(self) -> Tuple[str, ...]:
        indicator_names = tuple(
            f"missing_{self.feature_names[index]}"
            for index in self.indicator_indices
        )
        return self.feature_names + indicator_names

    @classmethod
    def fit(
        cls, matrices: Sequence[np.ndarray], feature_names: Tuple[str, ...]
    ) -> "_PreprocessingState":
        if not matrices:
            raise ValueError("Training feature matrices are required.")
        values = np.concatenate(matrices, axis=0).astype(np.float64, copy=False)
        values = np.where(np.isfinite(values), values, np.nan)
        if values.ndim != 2 or values.shape[1] != len(feature_names):
            raise ValueError("Training features do not match their feature names.")

        medians = np.zeros(values.shape[1], dtype=np.float64)
        for index in range(values.shape[1]):
            present = values[np.isfinite(values[:, index]), index]
            if present.size:
                medians[index] = float(np.median(present))
        indicator_indices = tuple(
            int(index)
            for index in np.flatnonzero(np.isnan(values).any(axis=0))
        )
        imputed = np.where(np.isfinite(values), values, medians)
        means = imputed.mean(axis=0)
        scales = imputed.std(axis=0)
        scales[scales == 0] = 1.0
        return cls(
            medians=medians,
            means=means,
            scales=scales,
            indicator_indices=indicator_indices,
            feature_names=feature_names,
        )

    def transform(self, matrix: np.ndarray) -> np.ndarray:
        values = np.asarray(matrix, dtype=np.float64)
        if values.ndim != 2 or values.shape[1] != len(self.feature_names):
            raise ValueError("Prediction features do not match the fitted schema.")
        values = np.where(np.isfinite(values), values, np.nan)
        missing = np.isnan(values)
        imputed = np.where(missing, self.medians, values)
        scaled = (imputed - self.means) / self.scales
        if self.indicator_indices:
            indicators = missing[:, self.indicator_indices].astype(np.float64)
            scaled = np.concatenate((scaled, indicators), axis=1)
        return scaled.astype(np.float32)

    def to_payload(self) -> Dict[str, object]:
        return {
            "medians": self.medians.tolist(),
            "means": self.means.tolist(),
            "scales": self.scales.tolist(),
            "indicator_indices": list(self.indicator_indices),
            "feature_names": list(self.feature_names),
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, object]) -> "_PreprocessingState":
        return cls(
            medians=np.asarray(payload["medians"], dtype=np.float64),
            means=np.asarray(payload["means"], dtype=np.float64),
            scales=np.asarray(payload["scales"], dtype=np.float64),
            indicator_indices=tuple(
                int(index) for index in payload["indicator_indices"]
            ),
            feature_names=tuple(str(name) for name in payload["feature_names"]),
        )


class _SmallSpatialNetwork(nn.Module):
    """Grid-preserving convolutional probability network."""

    def __init__(
        self,
        input_channels: int,
        grid_height: int,
        grid_width: int,
        hidden_channels: int,
        conv_blocks: int,
    ) -> None:
        super().__init__()
        self.input_channels = input_channels
        self.grid_height = grid_height
        self.grid_width = grid_width
        layers = []
        channels = input_channels
        for _ in range(conv_blocks):
            layers.extend(
                [
                    nn.Conv2d(
                        channels,
                        hidden_channels,
                        kernel_size=3,
                        padding=1,
                    ),
                    nn.ReLU(),
                ]
            )
            channels = hidden_channels
        self.features = nn.Sequential(*layers)
        self.output_layer = nn.Conv2d(hidden_channels, 1, kernel_size=1)

    def forward_logits(self, inputs: torch.Tensor) -> torch.Tensor:
        if inputs.ndim != 4:
            raise ValueError("Spatial model input must have shape [batch, channels, rows, cols].")
        if tuple(inputs.shape[1:]) != (
            self.input_channels,
            self.grid_height,
            self.grid_width,
        ):
            raise ValueError(
                "Spatial model input shape must be "
                f"[batch, {self.input_channels}, {self.grid_height}, "
                f"{self.grid_width}]; got {tuple(inputs.shape)}."
            )
        return self.output_layer(self.features(inputs))

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.forward_logits(inputs))


class ProbabilisticSpatialEnsemble:
    """Train and predict with a small deep ensemble of spatial CNNs.

    Every training example is a complete parent MODIS sample with all its grid
    cells together. The target is observed VIIRS point occupancy, not physical
    fire state. Ensemble disagreement is reported as an uncalibrated
    uncertainty proxy.
    """

    def __init__(
        self,
        grid_height: int = config.TARGET_GRID_HEIGHT,
        grid_width: int = config.TARGET_GRID_WIDTH,
        hidden_channels: int = 8,
        conv_blocks: int = 2,
        ensemble_size: int = 3,
        epochs: int = 20,
        batch_size: int = 16,
        learning_rate: float = 1e-3,
        random_seed: Optional[int] = 42,
    ) -> None:
        integer_parameters = {
            "grid_height": grid_height,
            "grid_width": grid_width,
            "hidden_channels": hidden_channels,
            "conv_blocks": conv_blocks,
            "epochs": epochs,
            "batch_size": batch_size,
        }
        for name, value in integer_parameters.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer.")
        if (
            isinstance(ensemble_size, bool)
            or not isinstance(ensemble_size, int)
            or ensemble_size < 2
        ):
            raise ValueError("ensemble_size must be an integer of at least two.")
        if not math.isfinite(learning_rate) or learning_rate <= 0:
            raise ValueError("learning_rate must be a finite positive number.")
        if random_seed is not None and (
            isinstance(random_seed, bool) or not isinstance(random_seed, int)
        ):
            raise ValueError("random_seed must be an integer or None.")

        self.grid_height = grid_height
        self.grid_width = grid_width
        self.hidden_channels = hidden_channels
        self.conv_blocks = conv_blocks
        self.ensemble_size = ensemble_size
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.random_seed = random_seed
        self._preprocessing: Optional[_PreprocessingState] = None
        self._members: list[_SmallSpatialNetwork] = []
        self.feature_names: Tuple[str, ...] = ()
        self.input_channel_names: Tuple[str, ...] = ()
        self.training_sample_ids: Tuple[str, ...] = ()
        self.excluded_sample_ids: Tuple[str, ...] = ()
        self.training_parent_metadata: Tuple[Dict[str, object], ...] = ()
        self.training_cell_count = 0
        self.member_seeds: Tuple[int, ...] = ()
        self.positive_class_weight: Optional[float] = None

    def _new_network(self, input_channels: int) -> _SmallSpatialNetwork:
        return _SmallSpatialNetwork(
            input_channels=input_channels,
            grid_height=self.grid_height,
            grid_width=self.grid_width,
            hidden_channels=self.hidden_channels,
            conv_blocks=self.conv_blocks,
        )

    def fit(
        self, samples: Iterable[TrainingSample]
    ) -> "ProbabilisticSpatialEnsemble":
        """Fit preprocessing and ensemble members using training samples only."""
        sample_list = list(samples)
        if not sample_list:
            raise ValueError("At least one training sample is required.")

        included: list[TrainingSample] = []
        excluded_ids = []
        for sample in sample_list:
            if sample.metadata.get("target_data_status") != "observed":
                raise ValueError(
                    f"Sample {sample.modis_id!r} must have observed VIIRS targets; "
                    "estimated/non-observed targets cannot be used for training."
                )
            if (
                sample.metadata.get("target_representation")
                != "binary_viirs_point_observation_occupancy"
            ):
                raise ValueError(
                    f"Sample {sample.modis_id!r} does not use the observed VIIRS "
                    "point-occupancy target definition."
                )
            if sample.target_grid.shape != (
                self.grid_height,
                self.grid_width,
            ):
                raise ValueError(
                    f"Sample {sample.modis_id!r} grid shape "
                    f"{sample.target_grid.shape} does not match configured "
                    f"({self.grid_height}, {self.grid_width})."
                )
            if np.any(sample.target_grid == 1):
                included.append(sample)
            else:
                excluded_ids.append(sample.modis_id)

        if excluded_ids:
            LOGGER.warning(
                "Excluded %d sample(s) with no in-grid observed VIIRS point "
                "evidence: %s",
                len(excluded_ids),
                ", ".join(excluded_ids),
            )
        if not included:
            raise ValueError(
                "No training samples contain in-grid observed VIIRS point evidence."
            )

        expected_environment_names = included[0].environmental_feature_names
        matrices = []
        targets = []
        feature_names: Optional[Tuple[str, ...]] = None
        for sample in included:
            if sample.environmental_feature_names != expected_environment_names:
                raise ValueError(
                    "All training samples must use the same environmental "
                    "features in the same order."
                )
            matrix, sample_feature_names = sample_to_cell_feature_matrix(sample)
            if feature_names is None:
                feature_names = sample_feature_names
            elif sample_feature_names != feature_names:
                raise ValueError("Training sample feature schemas do not match.")
            matrices.append(matrix)
            targets.append(sample.target_grid.reshape(-1).astype(np.float32))

        target_values = np.concatenate(targets)
        if not np.isin(target_values, (0.0, 1.0)).all():
            raise ValueError("Training targets must be binary observed VIIRS occupancy.")
        positive_count = int(np.count_nonzero(target_values == 1))
        negative_count = int(np.count_nonzero(target_values == 0))
        if not positive_count or not negative_count:
            raise ValueError(
                "Training requires both observed-positive and unoccupied "
                "target cells."
            )

        preprocessing = _PreprocessingState.fit(
            matrices, feature_names or ()
        )
        input_channel_names = preprocessing.channel_names
        processed_tensors = []
        for matrix in matrices:
            height, width = self.grid_height, self.grid_width
            channel_last = preprocessing.transform(matrix).reshape(
                height, width, len(input_channel_names)
            )
            processed_tensors.append(
                np.ascontiguousarray(channel_last.transpose(2, 0, 1))
            )

        inputs = torch.from_numpy(np.stack(processed_tensors)).to(torch.float32)
        targets_tensor = torch.from_numpy(
            target_values.reshape(-1, 1, self.grid_height, self.grid_width)
        ).to(torch.float32)
        dataset = TensorDataset(inputs, targets_tensor)
        positive_weight = torch.tensor(
            [negative_count / positive_count], dtype=torch.float32
        )
        positive_class_weight = negative_count / positive_count

        if self.random_seed is None:
            member_seeds = tuple(
                secrets.randbits(31) for _ in range(self.ensemble_size)
            )
        else:
            member_seeds = tuple(
                self.random_seed + index for index in range(self.ensemble_size)
            )

        members = []
        for member_seed in member_seeds:
            generator = torch.Generator(device="cpu")
            generator.manual_seed(member_seed)
            loader = DataLoader(
                dataset,
                batch_size=min(self.batch_size, len(dataset)),
                shuffle=True,
                generator=generator,
                num_workers=0,
            )
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(member_seed)
                network = self._new_network(len(input_channel_names))
                optimizer = torch.optim.Adam(
                    network.parameters(), lr=self.learning_rate
                )
                loss_function = nn.BCEWithLogitsLoss(
                    pos_weight=positive_weight
                )
                network.train()
                for _ in range(self.epochs):
                    for batch_inputs, batch_targets in loader:
                        optimizer.zero_grad(set_to_none=True)
                        logits = network.forward_logits(batch_inputs)
                        loss = loss_function(logits, batch_targets)
                        loss.backward()
                        optimizer.step()
            network.eval()
            members.append(network)

        self._preprocessing = preprocessing
        self._members = members
        self.feature_names = feature_names or ()
        self.input_channel_names = input_channel_names
        self.training_sample_ids = tuple(
            sample.modis_id for sample in included
        )
        self.excluded_sample_ids = tuple(excluded_ids)
        self.training_parent_metadata = tuple(
            {
                "modis_id": sample.modis_id,
                "observation_datetime": _serializable_scalar(
                    sample.metadata.get("observation_datetime")
                ),
                "event_id": _serializable_scalar(
                    sample.metadata.get("event_id")
                ),
            }
            for sample in included
        )
        self.training_cell_count = int(target_values.size)
        self.member_seeds = member_seeds
        self.positive_class_weight = positive_class_weight
        return self

    def _input_tensor(self, sample: TrainingSample) -> torch.Tensor:
        if self._preprocessing is None:
            raise RuntimeError("The spatial ensemble must be fitted before prediction.")
        if sample.target_grid.shape != (self.grid_height, self.grid_width):
            raise ValueError(
                f"Prediction grid shape {sample.target_grid.shape} does not match "
                f"configured ({self.grid_height}, {self.grid_width})."
            )
        matrix, feature_names = sample_to_cell_feature_matrix(sample)
        if feature_names != self.feature_names:
            raise ValueError(
                "Prediction sample feature schema does not match the fitted model."
            )
        channel_last = self._preprocessing.transform(matrix).reshape(
            self.grid_height,
            self.grid_width,
            len(self.input_channel_names),
        )
        channel_first = np.ascontiguousarray(channel_last.transpose(2, 0, 1))
        return torch.from_numpy(channel_first).unsqueeze(0).to(torch.float32)

    def predict(self, sample: TrainingSample) -> SpatialProbabilityEstimate:
        """Return ensemble mean probability and per-cell ensemble disagreement."""
        if not self._members:
            raise RuntimeError("The spatial ensemble must be fitted before prediction.")
        inputs = self._input_tensor(sample)
        member_probabilities = []
        with torch.no_grad():
            for network in self._members:
                member_probabilities.append(
                    network(inputs)[0, 0].cpu().numpy()
                )
        metadata: Dict[str, object] = {
            "product_type": (
                "MODEL-ESTIMATED FINE-SCALE FIRE-DETECTION "
                "PROBABILITY + UNCERTAINTY"
            ),
            "target_definition": TARGET_DEFINITION,
            "target_interpretation": (
                "Probability of observed VIIRS point occupancy in a modeled "
                "target cell, not physical fire state or a VIIRS pixel footprint."
            ),
            "target_data_status": "model_estimate",
            "model": "ProbabilisticSpatialEnsemble",
            "model_version": MODEL_VERSION,
            "modis_id": sample.modis_id,
            "observation_datetime": sample.metadata.get(
                "observation_datetime"
            ),
            "grid": sample.metadata.get("grid"),
            "input_feature_names": list(self.feature_names),
            "input_channel_names": list(self.input_channel_names),
            "uncertainty_type": (
                "model-estimated uncalibrated ensemble disagreement proxy"
            ),
            "uncertainty_method": (
                "standard deviation across independently initialized model "
                "ensemble probabilities; an uncalibrated disagreement proxy, "
                "not a confidence interval or posterior"
            ),
        }
        return summarize_ensemble_probabilities(
            np.stack(member_probabilities, axis=0), metadata
        )

    def save(self, path: str | Path) -> Path:
        """Save weights and preprocessing/configuration in a safe tensor payload."""
        if not self._members or self._preprocessing is None:
            raise RuntimeError("The spatial ensemble must be fitted before saving.")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, object] = {
            "artifact_type": "spatial_ml_probabilistic_spatial_ensemble",
            "format_version": ARTIFACT_FORMAT_VERSION,
            "model_version": MODEL_VERSION,
            "configuration": {
                "grid_height": self.grid_height,
                "grid_width": self.grid_width,
                "hidden_channels": self.hidden_channels,
                "conv_blocks": self.conv_blocks,
                "ensemble_size": self.ensemble_size,
                "epochs": self.epochs,
                "batch_size": self.batch_size,
                "learning_rate": self.learning_rate,
                "random_seed": self.random_seed,
            },
            "feature_names": list(self.feature_names),
            "input_channel_names": list(self.input_channel_names),
            "preprocessing": self._preprocessing.to_payload(),
            "member_seeds": list(self.member_seeds),
            "model_state_dicts": [
                {
                    name: value.detach().cpu()
                    for name, value in network.state_dict().items()
                }
                for network in self._members
            ],
            "training": {
                "sample_ids": list(self.training_sample_ids),
                "excluded_sample_ids": list(self.excluded_sample_ids),
                "parent_metadata": list(self.training_parent_metadata),
                "cell_count": self.training_cell_count,
                "positive_class_weight": self.positive_class_weight,
                "target_definition": TARGET_DEFINITION,
            },
        }
        torch.save(payload, destination)
        return destination

    @classmethod
    def load(cls, path: str | Path) -> "ProbabilisticSpatialEnsemble":
        """Load a saved artifact using weights-only deserialization."""
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"Spatial model artifact does not exist: {source}")
        payload = torch.load(source, map_location="cpu", weights_only=True)
        if not isinstance(payload, dict):
            raise ValueError("Spatial model artifact must contain a mapping.")
        if (
            payload.get("artifact_type")
            != "spatial_ml_probabilistic_spatial_ensemble"
            or payload.get("format_version") != ARTIFACT_FORMAT_VERSION
        ):
            raise ValueError("Unsupported spatial model artifact format.")
        if payload.get("model_version") != MODEL_VERSION:
            raise ValueError("Unsupported spatial model version.")

        configuration = payload["configuration"]
        if not isinstance(configuration, dict):
            raise ValueError("Spatial model artifact configuration is invalid.")
        model = cls(**configuration)
        model.feature_names = tuple(str(name) for name in payload["feature_names"])
        model.input_channel_names = tuple(
            str(name) for name in payload["input_channel_names"]
        )
        model._preprocessing = _PreprocessingState.from_payload(
            payload["preprocessing"]
        )
        if (
            model._preprocessing.feature_names != model.feature_names
            or model._preprocessing.channel_names != model.input_channel_names
        ):
            raise ValueError(
                "Saved preprocessing feature names do not match model channels."
            )
        model.member_seeds = tuple(int(seed) for seed in payload["member_seeds"])
        state_dicts = payload["model_state_dicts"]
        if not isinstance(state_dicts, list) or len(state_dicts) != model.ensemble_size:
            raise ValueError("Saved model state count does not match ensemble size.")
        model._members = []
        for state_dict in state_dicts:
            with torch.random.fork_rng(devices=[]):
                network = model._new_network(len(model.input_channel_names))
            network.load_state_dict(state_dict)
            network.eval()
            model._members.append(network)

        training = payload["training"]
        if not isinstance(training, dict):
            raise ValueError("Spatial model artifact training metadata is invalid.")
        if training.get("target_definition") != TARGET_DEFINITION:
            raise ValueError("Spatial model artifact has an unknown target definition.")
        model.training_sample_ids = tuple(training["sample_ids"])
        model.excluded_sample_ids = tuple(training["excluded_sample_ids"])
        model.training_parent_metadata = tuple(training["parent_metadata"])
        model.training_cell_count = int(training["cell_count"])
        model.positive_class_weight = float(training["positive_class_weight"])
        return model


def summarize_ensemble_probabilities(
    member_probabilities: np.ndarray,
    metadata: Optional[Dict[str, object]] = None,
) -> SpatialProbabilityEstimate:
    """Compute an ensemble mean grid and population standard-deviation proxy."""
    values = np.asarray(member_probabilities, dtype=np.float32)
    if values.ndim != 3 or values.shape[0] < 2:
        raise ValueError(
            "Ensemble probabilities must have shape [members, rows, cols] "
            "with at least two members."
        )
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
        raise ValueError("Ensemble member probabilities must be finite and in [0, 1].")
    return SpatialProbabilityEstimate(
        probability=values.mean(axis=0),
        uncertainty_std=values.std(axis=0),
        metadata=dict(metadata or {}),
    )
