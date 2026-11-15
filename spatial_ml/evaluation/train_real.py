"""Train and compare M5/M6 on partitioned real M4 sample shards."""

import argparse
import hashlib
import json
import os
import platform
import random
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import torch

from spatial_ml import config
from spatial_ml.data.samples import TrainingSample
from spatial_ml.evaluation.comparison import (
    compare_models,
    write_evaluation_artifacts,
)
from spatial_ml.evaluation.probabilities import evaluate_probability_grids
from spatial_ml.evaluation.splitting import create_evaluation_split
from spatial_ml.models import ProbabilisticRandomForest, ProbabilisticSpatialEnsemble
from spatial_ml.pipeline.real_data import load_training_sample_shards


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, indent=2, allow_nan=False, default=str)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _year(sample: TrainingSample) -> int:
    return pd.Timestamp(sample.metadata["observation_datetime"]).year


def _reservoir_samples(
    sample_paths: Sequence[Path],
    *,
    per_year_cap: int,
    random_seed: int,
) -> Tuple[List[TrainingSample], Dict[str, int], Dict[str, int]]:
    reservoirs: Dict[int, List[TrainingSample]] = {}
    seen_by_year: Dict[int, int] = {}
    random_by_year: Dict[int, random.Random] = {}
    identifiers = set()
    for sample in load_training_sample_shards(sample_paths):
        if sample.modis_id in identifiers:
            raise ValueError(f"Duplicate M4 parent ID in shards: {sample.modis_id}")
        identifiers.add(sample.modis_id)
        sample_year = _year(sample)
        reservoir = reservoirs.setdefault(sample_year, [])
        seen = seen_by_year.get(sample_year, 0) + 1
        seen_by_year[sample_year] = seen
        generator = random_by_year.setdefault(
            sample_year, random.Random(random_seed + sample_year)
        )
        if len(reservoir) < per_year_cap:
            reservoir.append(sample)
        else:
            selected = generator.randrange(seen)
            if selected < per_year_cap:
                reservoir[selected] = sample

    selected = [
        sample
        for sample_year in sorted(reservoirs)
        for sample in reservoirs[sample_year]
    ]
    selected.sort(
        key=lambda sample: (
            pd.Timestamp(sample.metadata["observation_datetime"]),
            sample.modis_id,
        )
    )
    return (
        selected,
        {str(key): value for key, value in sorted(seen_by_year.items())},
        {str(key): len(value) for key, value in sorted(reservoirs.items())},
    )


def _partition_counts(samples: Sequence[TrainingSample]) -> Dict[str, object]:
    if not samples:
        return {
            "parent_count": 0,
            "target_cell_count": 0,
            "positive_cell_count": 0,
            "negative_cell_count": 0,
            "positive_cell_prevalence": None,
            "date_range": None,
            "latitude_bounds": None,
            "longitude_bounds": None,
        }
    positive = sum(int(np.count_nonzero(sample.target_grid == 1)) for sample in samples)
    cells = sum(int(sample.target_grid.size) for sample in samples)
    timestamps = [
        pd.Timestamp(sample.metadata["observation_datetime"]) for sample in samples
    ]
    latitudes = [float(sample.metadata["modis_latitude"]) for sample in samples]
    longitudes = [float(sample.metadata["modis_longitude"]) for sample in samples]
    return {
        "parent_count": len(samples),
        "target_cell_count": cells,
        "positive_cell_count": positive,
        "negative_cell_count": cells - positive,
        "positive_cell_prevalence": positive / cells if cells else None,
        "date_range": [min(timestamps).isoformat(), max(timestamps).isoformat()],
        "latitude_bounds": [min(latitudes), max(latitudes)],
        "longitude_bounds": [min(longitudes), max(longitudes)],
    }


def _validation_metrics(
    model: object, samples: Sequence[TrainingSample]
) -> Dict[str, object]:
    targets = np.stack([sample.target_grid for sample in samples])
    probabilities = np.stack(
        [model.predict(sample).probability for sample in samples]
    )
    result = evaluate_probability_grids(
        targets.reshape(1, -1), probabilities.reshape(1, -1)
    )
    reliability = result.reliability.loc[result.reliability["count"] > 0]
    ece = float(
        np.sum(
            reliability["count"].to_numpy(dtype=float)
            / targets.size
            * np.abs(
                reliability["mean_probability"].to_numpy(dtype=float)
                - reliability["observed_frequency"].to_numpy(dtype=float)
            )
        )
    )
    return {
        "parent_count": len(samples),
        "cell_count": int(targets.size),
        "positive_cell_count": int(np.count_nonzero(targets == 1)),
        "positive_cell_prevalence": float(np.mean(targets)),
        "brier_score": result.brier_score,
        "log_loss": result.log_loss,
        "roc_auc": result.roc_auc,
        "pr_auc": result.pr_auc,
        "expected_calibration_error": ece,
        "metric_status": result.metric_status,
    }


def _validation_selection(
    metrics: Mapping[str, Mapping[str, object]],
) -> Dict[str, object]:
    forest = metrics["random_forest"]
    spatial = metrics["spatial_ensemble"]
    forest_wins = (
        forest["brier_score"] < spatial["brier_score"]
        and forest["log_loss"] < spatial["log_loss"]
    )
    spatial_wins = (
        spatial["brier_score"] < forest["brier_score"]
        and spatial["log_loss"] < forest["log_loss"]
    )
    if forest_wins:
        selected = "random_forest"
    elif spatial_wins:
        selected = "spatial_ensemble"
    else:
        selected = None
    return {
        "decision_partition": "validation",
        "rule": (
            "select a model only when it has strictly lower validation Brier "
            "score and log loss than the other model; otherwise report "
            "inconclusive"
        ),
        "selected_model": selected,
        "status": "selected" if selected else "inconclusive",
    }


def _split_for_strategy(
    samples: Sequence[TrainingSample],
    strategy: str,
    block_size_m: float,
    random_seed: int,
):
    kwargs: Dict[str, object] = {"random_seed": random_seed}
    if strategy in {"temporal", "combined"}:
        kwargs.update({"train_end": "2018", "validation_end": "2019"})
    if strategy in {"spatial", "combined"}:
        kwargs["block_size_m"] = block_size_m
    return create_evaluation_split(samples, strategy, **kwargs)


def train_real_models(
    sample_dir: str | Path,
    *,
    results_dir: str | Path = config.RESULTS_DIR / "real_m7_2016_2021",
    models_dir: str | Path = config.MODELS_DIR / "real_2016_2021",
    per_year_cap: int = 10_000,
    block_size_m: float = 500_000.0,
    random_seed: int = 42,
    cnn_epochs: int = 5,
    cnn_batch_size: int = 256,
    cnn_ensemble_size: int = 3,
    device: Optional[str] = "auto",
    strategies: Sequence[str] = ("temporal", "spatial", "combined"),
) -> Dict[str, object]:
    """Train both models on a reproducible year-stratified real M4 subset."""
    for name, value in (
        ("per_year_cap", per_year_cap),
        ("cnn_epochs", cnn_epochs),
        ("cnn_batch_size", cnn_batch_size),
        ("cnn_ensemble_size", cnn_ensemble_size),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer.")
    if not np.isfinite(block_size_m) or block_size_m <= 0:
        raise ValueError("block_size_m must be positive and finite.")
    if not strategies or set(strategies) - {"temporal", "spatial", "combined"}:
        raise ValueError("Select temporal, spatial, and/or combined strategies.")

    source = Path(sample_dir)
    sample_paths = sorted(source.glob("????-??/training_samples.npz"))
    if not sample_paths:
        raise FileNotFoundError(f"No partitioned M4 sample shards found in {source}.")
    results = Path(results_dir)
    models = Path(models_dir)
    if results.exists() or models.exists():
        raise FileExistsError(
            "Refusing to overwrite existing real-data model outputs; choose new "
            f"directories (results={results}, models={models})."
        )

    selected, population_by_year, sampled_by_year = _reservoir_samples(
        sample_paths,
        per_year_cap=per_year_cap,
        random_seed=random_seed,
    )
    if len({sample.modis_id for sample in selected}) != len(selected):
        raise ValueError("The sampled M4 parent IDs are not unique.")
    if not selected:
        raise ValueError("No eligible real M4 samples are available for training.")

    results.mkdir(parents=True, exist_ok=False)
    models.mkdir(parents=True, exist_ok=False)
    manifest_path = results / "real_training_manifest.json"
    manifest: Dict[str, object] = {
        "manifest_version": "1.0.0",
        "status": "in_progress",
        "input_sample_shards": [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sample_paths
        ],
        "sampling": {
            "method": "uniform reservoir sampling without replacement per calendar year",
            "maximum_parents_per_year": per_year_cap,
            "random_seed": random_seed,
            "eligible_population_by_year": population_by_year,
            "selected_parents_by_year": sampled_by_year,
            "selected_parent_count": len(selected),
        },
        "target": (
            "binary observed VIIRS point occupancy in modeled local cells; "
            "not physical fire state or a VIIRS pixel footprint"
        ),
        "environmental_features": [],
        "environmental_data_status": "unavailable_no_local_rasters",
        "models": {},
        "strategies": {},
    }
    _atomic_json(manifest_path, manifest)

    for strategy in dict.fromkeys(strategies):
        try:
            split = _split_for_strategy(
                selected, strategy, block_size_m, random_seed
            )
        except ValueError as exc:
            manifest["strategies"][strategy] = {
                "status": "unavailable",
                "reason": str(exc),
                "selection": "not weakened or replaced",
            }
            _atomic_json(manifest_path, manifest)
            continue

        strategy_models_dir = models / strategy
        strategy_models_dir.mkdir(parents=True, exist_ok=False)
        started = time.perf_counter()
        forest = ProbabilisticRandomForest(random_state=random_seed).fit(split.train)
        forest_duration = time.perf_counter() - started
        forest_path = forest.save(strategy_models_dir / "random_forest.joblib")

        spatial = ProbabilisticSpatialEnsemble(
            grid_height=config.TARGET_GRID_HEIGHT,
            grid_width=config.TARGET_GRID_WIDTH,
            ensemble_size=cnn_ensemble_size,
            epochs=cnn_epochs,
            batch_size=cnn_batch_size,
            random_seed=random_seed,
        ).fit(
            split.train,
            validation_samples=split.validation,
            early_stopping_patience=2,
            device=device,
        )
        spatial_path = spatial.save(strategy_models_dir / "spatial_ensemble.pt")

        validation_metrics = {
            "random_forest": _validation_metrics(forest, split.validation),
            "spatial_ensemble": _validation_metrics(spatial, split.validation),
        }
        selection = _validation_selection(validation_metrics)
        input_provenance = {
            "data_origin": "local NASA FIRMS M-C61 and SV-C2 archives",
            "processing_manifest": str(
                source / "processing_manifest.json"
            ),
            "sampling": manifest["sampling"],
            "environmental_data_status": "unavailable_no_local_rasters",
            "temporal_tolerance_minutes": config.DEFAULT_TIME_TOLERANCE_MINUTES,
            "spatial_radius_m": config.SPATIAL_MATCH_RADIUS_M,
            "target_definition": (
                "observed VIIRS point occupancy in modeled local target cells"
            ),
            "parent_split_strategy": strategy,
            "training_only_preprocessing": True,
            "class_imbalance": {
                "random_forest": "balanced_subsample class weights",
                "spatial_ensemble": "training-cell negative/positive BCE weight",
            },
        }
        evaluation = compare_models(
            split,
            forest,
            spatial,
            calibration_method="platt",
            input_provenance=input_provenance,
        )
        evaluation.report["validation_raw_metrics"] = validation_metrics
        evaluation.report["validation_model_selection"] = selection
        evaluation.report["partition_counts"] = {
            name: _partition_counts(getattr(split, name))
            for name in ("train", "validation", "test")
        }
        evaluation.report["training_execution"] = {
            "random_forest_duration_seconds": forest_duration,
            "spatial_ensemble_duration_seconds": spatial.training_duration_seconds,
            "spatial_ensemble_device": spatial.training_device,
            "spatial_ensemble_history": list(spatial.training_history),
            "hardware": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "cpu_count": os.cpu_count(),
                "torch_mps_available": torch.backends.mps.is_available(),
                "torch_cuda_available": torch.cuda.is_available(),
            },
            "test_set_used_for_model_selection": False,
        }
        evaluation.report["model_artifacts"] = {
            "random_forest": {
                "path": str(forest_path),
                "sha256": _sha256(forest_path),
                "training_parent_count": len(forest.training_sample_ids),
                "training_cell_count": forest.training_cell_count,
                "feature_importances": forest.feature_importances(),
            },
            "spatial_ensemble": {
                "path": str(spatial_path),
                "sha256": _sha256(spatial_path),
                "training_parent_count": len(spatial.training_sample_ids),
                "training_cell_count": spatial.training_cell_count,
                "member_seeds": list(spatial.member_seeds),
            },
        }
        evaluation_dir = results / strategy
        artifacts = write_evaluation_artifacts(evaluation, evaluation_dir)
        manifest["strategies"][strategy] = {
            "status": "complete",
            "split_configuration": split.configuration,
            "dropped_parent_count": len(split.dropped_parent_ids),
            "partitions": evaluation.report["partition_counts"],
            "validation_raw_metrics": validation_metrics,
            "validation_model_selection": selection,
            "test_metrics": {
                name: {
                    "raw": evaluation.report["models"][name]["test_raw"],
                    "calibrated": evaluation.report["models"][name][
                        "test_calibrated"
                    ],
                }
                for name in ("random_forest", "spatial_ensemble")
            },
            "artifacts": {
                key: str(path) for key, path in artifacts.items()
            },
            "model_artifacts": evaluation.report["model_artifacts"],
        }
        _atomic_json(manifest_path, manifest)
        print(
            f"M7 {strategy}: {len(split.train)} train, "
            f"{len(split.validation)} validation, {len(split.test)} test; "
            f"validation selection={selection['status']}"
        )

    manifest["status"] = (
        "complete"
        if any(
            item.get("status") == "complete"
            for item in manifest["strategies"].values()
        )
        else "unavailable"
    )
    _atomic_json(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Train and compare M5/M6 on sampled parent-level real M4 shards. "
            "M4 remains fully stored; training sampling is recorded."
        )
    )
    parser.add_argument("--sample-dir", type=Path, required=True)
    parser.add_argument(
        "--results-dir", type=Path, default=config.RESULTS_DIR / "real_m7_2016_2021"
    )
    parser.add_argument(
        "--models-dir", type=Path, default=config.MODELS_DIR / "real_2016_2021"
    )
    parser.add_argument("--per-year-cap", type=int, default=10_000)
    parser.add_argument("--block-size-m", type=float, default=500_000.0)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--cnn-epochs", type=int, default=5)
    parser.add_argument("--cnn-batch-size", type=int, default=256)
    parser.add_argument("--cnn-ensemble-size", type=int, default=3)
    parser.add_argument("--device", default="auto", help="auto, cpu, mps, or cuda")
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=("temporal", "spatial", "combined"),
        default=("temporal", "spatial", "combined"),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        manifest = train_real_models(
            arguments.sample_dir,
            results_dir=arguments.results_dir,
            models_dir=arguments.models_dir,
            per_year_cap=arguments.per_year_cap,
            block_size_m=arguments.block_size_m,
            random_seed=arguments.random_seed,
            cnn_epochs=arguments.cnn_epochs,
            cnn_batch_size=arguments.cnn_batch_size,
            cnn_ensemble_size=arguments.cnn_ensemble_size,
            device=arguments.device,
            strategies=arguments.strategies,
        )
    except (FileNotFoundError, FileExistsError, OSError, ValueError, RuntimeError) as exc:
        _parser().error(str(exc))
    print(f"Training status: {manifest['status']}")
    print(
        f"Manifest: {arguments.results_dir / 'real_training_manifest.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
