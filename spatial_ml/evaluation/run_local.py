"""Run held-out evaluation from user-supplied local FIRMS CSV files."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd
from rasterio.enums import Resampling

from spatial_ml import config
from spatial_ml.data import (
    LocalTargetGrid,
    TrainingSample,
    build_training_sample,
    clean_firms_records,
    load_modis,
    load_viirs,
)
from spatial_ml.evaluation.comparison import (
    compare_models,
    write_evaluation_artifacts,
)
from spatial_ml.evaluation.splitting import create_evaluation_split
from spatial_ml.features.environmental import (
    RasterFeatureSource,
    extract_environmental_features,
)
from spatial_ml.models import ProbabilisticRandomForest, ProbabilisticSpatialEnsemble
from spatial_ml.pipeline.overlap import generate_overlap_dataset


def _file_provenance(path: Path) -> Dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "file_name": path.name,
        "sha256": digest.hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _build_samples(
    pairs: pd.DataFrame,
    candidates: pd.DataFrame,
    environmental_sources: Sequence[RasterFeatureSource],
) -> List[TrainingSample]:
    samples = []
    candidates_by_parent = {
        str(parent_id): group.reset_index(drop=True)
        for parent_id, group in candidates.groupby("modis_id", sort=False)
    }
    for pair in pairs.to_dict(orient="records"):
        parent_id = str(pair["modis_id"])
        grid = LocalTargetGrid(
            float(pair["modis_latitude"]),
            float(pair["modis_longitude"]),
        )
        environmental = None
        if environmental_sources:
            environmental = extract_environmental_features(
                grid,
                environmental_sources,
                derive_terrain_from=(
                    "elevation_m"
                    if any(
                        source.name == "elevation_m"
                        for source in environmental_sources
                    )
                    else None
                ),
            )
        samples.append(
            build_training_sample(
                pair,
                candidates_by_parent.get(parent_id, candidates.iloc[0:0]),
                environmental=environmental,
                grid=grid,
            )
        )
    return samples


def evaluate_local_firms(
    modis_path: str | Path,
    viirs_path: str | Path,
    *,
    output_dir: str | Path = config.RESULTS_DIR / "milestone_7",
    strategy: str = "temporal",
    train_end: Optional[str] = None,
    validation_end: Optional[str] = None,
    block_size_m: Optional[float] = None,
    time_tolerance_minutes: Optional[float] = None,
    spatial_radius_m: Optional[float] = None,
    elevation_path: Optional[str | Path] = None,
    land_cover_path: Optional[str | Path] = None,
    calibration_method: str = "platt",
    random_seed: int = 42,
) -> Dict[str, Path]:
    """Build overlap samples locally, fit both baselines, and save held-out reports."""
    modis_source = Path(modis_path)
    viirs_source = Path(viirs_path)
    modis = clean_firms_records(load_modis(modis_source))
    viirs = clean_firms_records(load_viirs(viirs_source))
    pairs, candidates = generate_overlap_dataset(
        modis,
        viirs,
        time_tolerance_minutes=time_tolerance_minutes,
        spatial_radius_m=spatial_radius_m,
    )
    if pairs.empty:
        raise ValueError("The supplied FIRMS inputs produced no overlap samples.")

    environmental_sources = []
    if elevation_path is not None:
        environmental_sources.append(
            RasterFeatureSource("elevation_m", Path(elevation_path))
        )
    if land_cover_path is not None:
        environmental_sources.append(
            RasterFeatureSource(
                "land_cover",
                Path(land_cover_path),
                resampling=Resampling.nearest,
            )
        )
    samples = _build_samples(pairs, candidates, environmental_sources)
    split = create_evaluation_split(
        samples,
        strategy,
        train_end=train_end,
        validation_end=validation_end,
        block_size_m=block_size_m,
        random_seed=random_seed,
    )

    random_forest = ProbabilisticRandomForest(random_state=random_seed).fit(
        split.train
    )
    grid_height, grid_width = split.train[0].target_grid.shape
    spatial_ensemble = ProbabilisticSpatialEnsemble(
        grid_height=grid_height,
        grid_width=grid_width,
        random_seed=random_seed,
    ).fit(split.train)
    input_provenance: Dict[str, object] = {
        "data_origin": "user-supplied local files; provenance is not independently verified",
        "modis_csv": _file_provenance(modis_source),
        "viirs_csv": _file_provenance(viirs_source),
        "environmental_rasters": [
            _file_provenance(Path(source.path))
            for source in environmental_sources
        ],
        "cleaned_modis_record_count": len(modis),
        "cleaned_viirs_record_count": len(viirs),
        "overlap_parent_count": len(pairs),
        "matched_candidate_count": len(candidates),
        "temporal_tolerance_minutes": (
            config.DEFAULT_TIME_TOLERANCE_MINUTES
            if time_tolerance_minutes is None
            else time_tolerance_minutes
        ),
        "spatial_radius_m": (
            config.SPATIAL_MATCH_RADIUS_M
            if spatial_radius_m is None
            else spatial_radius_m
        ),
        "target_definition": (
            "binary VIIRS point occupancy in modeled local cells"
        ),
    }
    evaluation = compare_models(
        split,
        random_forest,
        spatial_ensemble,
        calibration_method=calibration_method,
        input_provenance=input_provenance,
    )
    return write_evaluation_artifacts(evaluation, output_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate local MODIS/VIIRS FIRMS data using held-out parent "
            "samples. This command does not download NASA data."
        )
    )
    parser.add_argument("--modis", required=True, type=Path, help="Local MODIS FIRMS CSV")
    parser.add_argument("--viirs", required=True, type=Path, help="Local VIIRS FIRMS CSV")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=config.RESULTS_DIR / "milestone_7",
    )
    parser.add_argument(
        "--strategy",
        choices=("temporal", "spatial", "combined"),
        default="temporal",
    )
    parser.add_argument("--train-end", help="Inclusive training end date/year")
    parser.add_argument("--validation-end", help="Inclusive validation end date/year")
    parser.add_argument(
        "--block-size-m",
        type=float,
        help="Required for spatial or combined splits",
    )
    parser.add_argument("--time-tolerance-minutes", type=float)
    parser.add_argument("--spatial-radius-m", type=float)
    parser.add_argument("--elevation", type=Path, help="Optional local DEM raster")
    parser.add_argument(
        "--land-cover",
        type=Path,
        help="Optional local categorical land-cover raster",
    )
    parser.add_argument(
        "--calibration",
        choices=("platt", "isotonic"),
        default="platt",
    )
    parser.add_argument("--random-seed", type=int, default=42)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        artifacts = evaluate_local_firms(
            arguments.modis,
            arguments.viirs,
            output_dir=arguments.output_dir,
            strategy=arguments.strategy,
            train_end=arguments.train_end,
            validation_end=arguments.validation_end,
            block_size_m=arguments.block_size_m,
            time_tolerance_minutes=arguments.time_tolerance_minutes,
            spatial_radius_m=arguments.spatial_radius_m,
            elevation_path=arguments.elevation,
            land_cover_path=arguments.land_cover,
            calibration_method=arguments.calibration,
            random_seed=arguments.random_seed,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: str(path) for key, path in artifacts.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
