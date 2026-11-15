"""Command-line entry point for team-standardized dataset exports."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional, Sequence

from spatial_ml import config
from spatial_ml.exports.standardized import (
    EXPORT_FILENAMES,
    export_fine_scale_probabilities,
    export_matched_observations,
    export_modis_observations,
    export_viirs_observations,
    prepare_observations,
    write_export_manifest,
)

_PRODUCTS = tuple(EXPORT_FILENAMES)


def run_exports(
    *,
    products: Sequence[str],
    output_dir: str | Path,
    modis_path: Optional[str | Path] = None,
    viirs_path: Optional[str | Path] = None,
    probability_input: Optional[str | Path] = None,
    start_date: Optional[str] = config.TEAM_EXPORT_START_DATE,
    end_date: Optional[str] = config.TEAM_EXPORT_END_DATE,
    spatial_radius_m: Optional[float] = None,
    temporal_tolerance_minutes: Optional[float] = None,
    geojson: bool = False,
    validation_only: bool = False,
) -> Dict[str, object]:
    """Run selected exports without downloading or inferring any data."""
    selected = tuple(dict.fromkeys(products))
    unknown = set(selected) - set(_PRODUCTS)
    if unknown:
        raise ValueError(f"Unknown export products: {sorted(unknown)}")
    if not selected:
        raise ValueError("Select at least one export product.")
    if geojson and "probabilities" not in selected:
        raise ValueError("--geojson is only available with the probabilities product.")

    output = Path(output_dir)
    product_results: Dict[str, object] = {}
    modis_records = None
    viirs_records = None
    modis_info = None
    viirs_info = None

    def need_modis() -> None:
        nonlocal modis_records, modis_info
        if modis_records is None:
            if modis_path is None:
                raise ValueError("The selected export requires --modis.")
            modis_records, modis_info = prepare_observations(
                modis_path, "MODIS", start_date=start_date, end_date=end_date
            )

    def need_viirs() -> None:
        nonlocal viirs_records, viirs_info
        if viirs_records is None:
            if viirs_path is None:
                raise ValueError("The selected export requires --viirs.")
            viirs_records, viirs_info = prepare_observations(
                viirs_path, "VIIRS", start_date=start_date, end_date=end_date
            )

    def build_product_summaries(
        destination_dir: Path, *, validate_only: bool
    ) -> Dict[str, object]:
        summaries: Dict[str, object] = {}
        for product in selected:
            destination = destination_dir / EXPORT_FILENAMES[product]
            if product == "modis":
                need_modis()
                assert modis_records is not None and modis_info is not None
                summaries[product] = export_modis_observations(
                    modis_records,
                    destination,
                    modis_info,
                    validate_only=validate_only,
                )
            elif product == "viirs":
                need_viirs()
                assert viirs_records is not None and viirs_info is not None
                summaries[product] = export_viirs_observations(
                    viirs_records,
                    destination,
                    viirs_info,
                    validate_only=validate_only,
                )
            elif product == "matched":
                need_modis()
                need_viirs()
                assert modis_records is not None and viirs_records is not None
                assert modis_info is not None and viirs_info is not None
                summaries[product] = export_matched_observations(
                    modis_records,
                    viirs_records,
                    destination,
                    spatial_radius_m=spatial_radius_m,
                    temporal_tolerance_minutes=temporal_tolerance_minutes,
                    start_date=start_date,
                    end_date=end_date,
                    source_files=(
                        modis_info["source_file"],
                        viirs_info["source_file"],
                    ),
                    validate_only=validate_only,
                )
            else:
                if probability_input is None:
                    raise ValueError(
                        "The probabilities export requires --probability-input."
                    )
                geojson_path = (
                        destination_dir / "fine_scale_fire_probability.geojson"
                    if geojson
                    else None
                )
                summaries[product] = export_fine_scale_probabilities(
                    probability_input,
                    destination,
                    start_date=start_date,
                    end_date=end_date,
                    geojson_path=geojson_path,
                    validate_only=validate_only,
                )
        return summaries

    if validation_only:
        product_results = build_product_summaries(
            output, validate_only=True
        )
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{output.name}.staging-", dir=output.parent
        ) as staging_name:
            staging_dir = Path(staging_name)
            product_results = build_product_summaries(
                staging_dir, validate_only=False
            )
            output.mkdir(parents=True, exist_ok=True)
            for product in selected:
                staged_file = staging_dir / EXPORT_FILENAMES[product]
                os.replace(staged_file, output / staged_file.name)
            if geojson:
                staged_geojson = staging_dir / "fine_scale_fire_probability.geojson"
                os.replace(
                    staged_geojson,
                    output / "fine_scale_fire_probability.geojson",
                )

    sources = {}
    if modis_info is not None:
        sources["MODIS"] = modis_info
    if viirs_info is not None:
        sources["VIIRS"] = viirs_info
    manifest_path = write_export_manifest(
        output,
        product_results,
        source_inputs=sources,
        start_date=start_date,
        end_date=end_date,
        validate_only=validation_only,
    )
    return {
        "products": product_results,
        "sources": sources,
        "manifest": None if validation_only else str(manifest_path),
        "validation_only": validation_only,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export local FIRMS observations, M3 matches, and existing M8 "
            "probability batches. No data are downloaded or predicted."
        )
    )
    parser.add_argument(
        "--products",
        nargs="+",
        required=True,
        choices=_PRODUCTS,
        help="Choose one or more of: modis, viirs, matched, probabilities",
    )
    parser.add_argument("--modis", type=Path, help="Local MODIS FIRMS CSV")
    parser.add_argument("--viirs", type=Path, help="Local VIIRS FIRMS CSV")
    parser.add_argument(
        "--probability-input",
        type=Path,
        help="M8 prediction CSV or directory containing predictions-*.csv batches",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=config.OUTPUTS_DIR / "team_exports",
    )
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="Disable the default 2021-11-14 through 2026-07-31 date filter",
    )
    parser.add_argument(
        "--start-date", help="Inclusive ISO date (overrides default)"
    )
    parser.add_argument(
        "--end-date", help="Inclusive ISO date (overrides default)"
    )
    parser.add_argument(
        "--spatial-radius-m",
        type=float,
        help="M3 geodesic matching radius in meters",
    )
    parser.add_argument(
        "--temporal-tolerance-minutes",
        type=float,
        help="M3 same-day temporal matching tolerance",
    )
    parser.add_argument(
        "--geojson",
        action="store_true",
        help="Also write probability cells as WGS84 GeoJSON polygons",
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Validate requested products and print summaries without writing files",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.all_dates and (
        arguments.start_date is not None or arguments.end_date is not None
    ):
        parser.error("--all-dates cannot be combined with --start-date or --end-date.")
    start_date = None if arguments.all_dates else (
        arguments.start_date or config.TEAM_EXPORT_START_DATE
    )
    end_date = None if arguments.all_dates else (
        arguments.end_date or config.TEAM_EXPORT_END_DATE
    )
    try:
        result = run_exports(
            products=arguments.products,
            output_dir=arguments.output_dir,
            modis_path=arguments.modis,
            viirs_path=arguments.viirs,
            probability_input=arguments.probability_input,
            start_date=start_date,
            end_date=end_date,
            spatial_radius_m=arguments.spatial_radius_m,
            temporal_tolerance_minutes=arguments.temporal_tolerance_minutes,
            geojson=arguments.geojson,
            validation_only=arguments.validation_only,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
