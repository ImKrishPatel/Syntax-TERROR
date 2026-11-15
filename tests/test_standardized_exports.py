import json

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import shape

from spatial_ml.data.target_grid import LocalTargetGrid
from spatial_ml.exports import (
    ExportValidationError,
    export_fine_scale_probabilities,
    export_matched_observations,
    export_modis_observations,
    export_viirs_observations,
    prepare_observations,
)
from spatial_ml.exports.cli import run_exports


def _write_observations(path, sensor, observations):
    columns = [
        "id",
        "latitude",
        "longitude",
        "acq_date",
        "acq_time",
        "satellite",
        "instrument",
        "confidence",
        "frp",
        "daynight",
        "product",
        "version",
        "frp_units",
        "extra_source_field",
    ]
    pd.DataFrame(observations, columns=columns).to_csv(path, index=False)


def _modis_observations():
    return [
        (
            "m-near",
            40.0,
            -120.0,
            "2021-11-14",
            "1200",
            "Terra",
            "MODIS",
            "high",
            12.5,
            "D",
            "MOD14",
            "6.1",
            "MW",
            "kept-modis",
        ),
        (
            "m-unmatched",
            41.0,
            -120.0,
            "2021-11-14",
            "1500",
            "Aqua",
            "MODIS",
            "75",
            4.0,
            "N",
            "MYD14",
            "6.1",
            "MW",
            "also-kept",
        ),
        (
            "m-before",
            40.0,
            -120.0,
            "2021-11-13",
            "1200",
            "Terra",
            "MODIS",
            "nominal",
            3.0,
            "D",
            "MOD14",
            "6.1",
            "MW",
            "outside-default-period",
        ),
    ]


def _viirs_observations():
    return [
        (
            "v-near-1",
            40.0005,
            -120.0,
            "2021-11-14",
            "1205",
            "SNPP",
            "VIIRS",
            "nominal",
            1.2,
            "D",
            "VNP14",
            "2.0",
            "MW",
            "viirs-extra-a",
        ),
        (
            "v-near-2",
            40.0010,
            -120.0,
            "2021-11-14",
            "1210",
            "NOAA-20",
            "VIIRS",
            "high",
            2.4,
            "D",
            "VJ114",
            "2.0",
            "MW",
            "viirs-extra-b",
        ),
        (
            "v-before",
            40.0005,
            -120.0,
            "2021-11-13",
            "1205",
            "SNPP",
            "VIIRS",
            "low",
            1.0,
            "D",
            "VNP14",
            "2.0",
            "MW",
            "outside-default-period",
        ),
    ]


def _write_m8_batch(
    path, *, probability=0.25, duplicate=False, origin_longitude=-120.0
):
    grid = LocalTargetGrid(
        40.0, origin_longitude, resolution_m=375.0, width=2, height=2
    )
    rows = []
    for row in range(2):
        for column in range(2):
            x, y = grid.cell_center_local(row, column)
            longitude, latitude = grid.local_to_geographic(x, y)
            rows.append(
                {
                    "parent_modis_observation_id": "modis-parent-1",
                    "target_cell_id": f"r{row:02d}_c{column:02d}",
                    "cell_center_latitude": float(latitude),
                    "cell_center_longitude": float(longitude),
                    "grid_resolution_m": 375,
                    "observation_datetime": "2005-04-03T11:30:00",
                    "source_latitude": 40.0,
                    "source_longitude": origin_longitude,
                    "source_timestamp": "2005-04-03 11:30:00",
                    "satellite": "Terra",
                    "instrument": "MODIS",
                    "raw_estimated_detection_probability": probability,
                    "calibrated_estimated_detection_probability": 0.3,
                    "uncertainty_estimate": 0.1,
                    "uncertainty_type": "model_disagreement_proxy",
                    "uncertainty_method": "ensemble member standard deviation",
                    "source_sensor": "MODIS",
                    "source_file_name": "historical.csv",
                    "source_file_sha256": "abc123",
                    "source_row_number": row * 2 + column + 2,
                    "source_observation_id": "firms-source-1",
                    "model_name": "ProbabilisticSpatialEnsemble",
                    "model_version": "1.0.0",
                    "preprocessing_version": "1.0.0",
                    "calibration_version": "1.0.0",
                    "calibration_method": "platt",
                    "estimated": True,
                    "reference_observation_available": False,
                }
            )
    if duplicate:
        rows.append(rows[0].copy())
    pd.DataFrame(rows).to_csv(path, index=False)


def test_modis_export_keeps_original_source_fields_and_observed_status(
    tmp_path,
) -> None:
    source = tmp_path / "modis.csv"
    _write_observations(source, "MODIS", _modis_observations())
    cleaned, source_info = prepare_observations(
        source,
        "MODIS",
        start_date="2021-11-14",
        end_date="2026-07-31",
    )

    summary = export_modis_observations(
        cleaned, tmp_path / "modis_observations.csv", source_info
    )

    exported = pd.read_csv(tmp_path / "modis_observations.csv")
    assert summary["row_count"] == 2
    assert exported["estimated"].eq(False).all()
    assert exported["sensor"].eq("MODIS").all()
    assert exported.loc[0, "original_source_identifier"] == "m-near"
    assert exported.loc[0, "source_file"] == source.name
    assert exported.loc[0, "source_file_sha256"] == source_info["source_sha256"]
    assert exported.loc[0, "confidence_source_value"] == "high"
    assert exported.loc[0, "frp_units"] == "MW"
    source_fields = json.loads(exported.loc[0, "source_fields_json"])
    assert source_fields["extra_source_field"] == "kept-modis"
    assert source_fields["product"] == "MOD14"
    reimported_source_fields = json.loads(
        pd.read_csv(tmp_path / "modis_observations.csv").loc[
            0, "source_fields_json"
        ]
    )
    assert reimported_source_fields["id"] == "m-near"


def test_viirs_export_remains_sensor_specific_and_empty_export_is_safe(
    tmp_path,
) -> None:
    source = tmp_path / "viirs.csv"
    _write_observations(source, "VIIRS", _viirs_observations())
    cleaned, source_info = prepare_observations(
        source,
        "VIIRS",
        start_date="2040-01-01",
        end_date="2040-12-31",
    )

    result = export_viirs_observations(
        cleaned, tmp_path / "viirs_observations.csv", source_info
    )

    empty_export = pd.read_csv(tmp_path / "viirs_observations.csv")
    assert result["row_count"] == 0
    assert empty_export.empty
    assert "confidence_source_value" in empty_export.columns

    all_records, all_source_info = prepare_observations(source, "VIIRS")
    export_viirs_observations(
        all_records,
        tmp_path / "all_viirs.csv",
        all_source_info,
    )
    viirs = pd.read_csv(tmp_path / "all_viirs.csv")
    assert viirs["sensor"].eq("VIIRS").all()
    assert set(viirs["satellite"]) == {"SNPP", "NOAA-20"}


def test_header_only_observation_input_exports_a_valid_empty_dataset(
    tmp_path,
) -> None:
    source = tmp_path / "empty.csv"
    _write_observations(source, "MODIS", [])
    cleaned, source_info = prepare_observations(source, "MODIS")

    result = export_modis_observations(
        cleaned, tmp_path / "empty-modis.csv", source_info
    )

    assert result["row_count"] == 0
    assert result["warnings"]
    assert pd.read_csv(tmp_path / "empty-modis.csv").empty


def test_matched_export_keeps_every_candidate_and_unmatched_modis(tmp_path) -> None:
    modis_path = tmp_path / "modis.csv"
    viirs_path = tmp_path / "viirs.csv"
    _write_observations(modis_path, "MODIS", _modis_observations()[:2])
    _write_observations(viirs_path, "VIIRS", _viirs_observations()[:2])
    modis, _ = prepare_observations(modis_path, "MODIS")
    viirs, _ = prepare_observations(viirs_path, "VIIRS")

    result = export_matched_observations(
        modis,
        viirs,
        tmp_path / "modis_viirs_matched.csv",
        spatial_radius_m=1000,
        temporal_tolerance_minutes=30,
    )

    exported = pd.read_csv(tmp_path / "modis_viirs_matched.csv")
    matches = exported.loc[exported["match_status"] == "candidate_match"]
    unmatched = exported.loc[exported["match_status"] == "unmatched"]
    assert result["row_count"] == 3
    assert len(matches) == 2
    assert matches["modis_observation_id"].nunique() == 1
    assert matches["selected_nearest_match"].sum() == 1
    assert matches["modis_satellite"].eq("Terra").all()
    assert matches["viirs_instrument"].eq("VIIRS").all()
    assert matches["modis_source_file_sha256"].notna().all()
    assert matches["temporal_difference_seconds"].ge(0).all()
    assert unmatched.iloc[0]["viirs_candidate_count"] == 0
    assert pd.isna(unmatched.iloc[0]["viirs_observation_id"])


def test_missing_viirs_candidates_are_explicit_unmatched_modis_rows(
    tmp_path,
) -> None:
    source = tmp_path / "modis.csv"
    viirs_source = tmp_path / "viirs-empty.csv"
    _write_observations(source, "MODIS", _modis_observations()[:2])
    _write_observations(viirs_source, "VIIRS", [])
    modis, _ = prepare_observations(source, "MODIS")
    viirs, _ = prepare_observations(viirs_source, "VIIRS")

    result = export_matched_observations(
        modis, viirs, tmp_path / "matched.csv"
    )

    matched = pd.read_csv(tmp_path / "matched.csv")
    assert result["row_count"] == 2
    assert matched["match_status"].eq("unmatched_no_viirs_input").all()
    assert matched["viirs_observation_id"].isna().all()


def test_probability_csv_geojson_and_provenance_are_preserved(tmp_path) -> None:
    source = tmp_path / "m8-batches"
    source.mkdir()
    _write_m8_batch(source / "predictions-000000.csv")

    result = export_fine_scale_probabilities(
        source,
        tmp_path / "fine_scale_fire_probability.csv",
        geojson_path=tmp_path / "fine_scale_fire_probability.geojson",
    )

    table = pd.read_csv(tmp_path / "fine_scale_fire_probability.csv")
    collection = json.loads(
        (tmp_path / "fine_scale_fire_probability.geojson").read_text()
    )
    assert result["row_count"] == 4
    assert table["raw_estimated_probability"].between(0, 1).all()
    assert table["estimated"].eq(True).all()
    assert table["reference_observation_available"].eq(False).all()
    assert table["uncertainty_type"].eq("model_disagreement_proxy").all()
    assert table["cell_boundary_wgs84"].notna().all()
    assert len(collection["features"]) == 4
    assert all(
        shape(feature["geometry"]).is_valid
        and shape(feature["geometry"]).geom_type == "Polygon"
        for feature in collection["features"]
    )
    properties = collection["features"][0]["properties"]
    assert properties["parent_modis_id"] == "modis-parent-1"
    assert properties["estimated"] is True
    assert properties["reference_observation_available"] is False
    assert properties["source_provenance"]["source_observation_id"] == "firms-source-1"
    assert "crs" not in collection


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"probability": 1.2}, "raw_estimated_probability"),
        ({"duplicate": True}, "duplicate row keys"),
    ],
)
def test_probability_validation_rejects_bad_range_and_duplicate_keys(
    tmp_path, kwargs, message
) -> None:
    source = tmp_path / "predictions.csv"
    _write_m8_batch(source, **kwargs)

    with pytest.raises(ExportValidationError, match=message):
        export_fine_scale_probabilities(
            source, tmp_path / "fine_scale_fire_probability.csv"
        )


def test_invalid_uncertainty_and_non_estimate_flags_are_rejected(tmp_path) -> None:
    source = tmp_path / "predictions.csv"
    _write_m8_batch(source)
    records = pd.read_csv(source)
    records.loc[0, "uncertainty_estimate"] = -0.1
    records.to_csv(source, index=False)
    with pytest.raises(ExportValidationError, match="nonnegative"):
        export_fine_scale_probabilities(source, tmp_path / "out.csv")

    records.loc[0, "uncertainty_estimate"] = 0.1
    records.loc[0, "reference_observation_available"] = True
    records.to_csv(source, index=False)
    with pytest.raises(ExportValidationError, match="reference_observation_available=false"):
        export_fine_scale_probabilities(source, tmp_path / "out.csv")


def test_incomplete_or_inconsistent_probability_grid_does_not_create_geometry(
    tmp_path,
) -> None:
    source = tmp_path / "incomplete-grid.csv"
    _write_m8_batch(source)
    records = pd.read_csv(source).iloc[:-1]
    records.to_csv(source, index=False)

    with pytest.raises(ExportValidationError, match="complete, non-duplicated"):
        export_fine_scale_probabilities(
            source,
            tmp_path / "out.csv",
            geojson_path=tmp_path / "out.geojson",
        )
    assert not (tmp_path / "out.geojson").exists()


def test_geojson_cells_crossing_antimeridian_remain_valid(tmp_path) -> None:
    source = tmp_path / "dateline.csv"
    _write_m8_batch(source, origin_longitude=179.9999)

    export_fine_scale_probabilities(
        source,
        tmp_path / "dateline.csv.out",
        geojson_path=tmp_path / "dateline.geojson",
    )

    collection = json.loads((tmp_path / "dateline.geojson").read_text())
    geometries = [
        shape(feature["geometry"]) for feature in collection["features"]
    ]
    assert all(geometry.is_valid for geometry in geometries)
    assert any(geometry.geom_type == "MultiPolygon" for geometry in geometries)


def test_missing_input_and_missing_required_probability_fields_are_clear(
    tmp_path,
) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        export_fine_scale_probabilities(
            tmp_path / "missing", tmp_path / "out.csv"
        )
    source = tmp_path / "bad.csv"
    pd.DataFrame({"estimated": [True]}).to_csv(source, index=False)
    with pytest.raises(ExportValidationError, match="lacks required field"):
        export_fine_scale_probabilities(source, tmp_path / "out.csv")


def test_cli_date_filter_manifest_and_validation_only(tmp_path) -> None:
    modis_path = tmp_path / "modis.csv"
    _write_observations(modis_path, "MODIS", _modis_observations())
    output = tmp_path / "exports"

    checked = run_exports(
        products=["modis"],
        output_dir=output,
        modis_path=modis_path,
        start_date="2021-11-14",
        end_date="2021-11-14",
        validation_only=True,
    )
    assert checked["validation_only"] is True
    assert not output.exists()

    result = run_exports(
        products=["modis"],
        output_dir=output,
        modis_path=modis_path,
        start_date="2021-11-14",
        end_date="2021-11-14",
    )
    manifest = json.loads((output / "export_manifest.json").read_text())
    assert manifest["export_schema_version"] == "1.0.0"
    assert manifest["products"]["modis"]["row_count"] == 2
    assert str(modis_path) in manifest["products"]["modis"]["source_files"]
    assert manifest["coverage_requested"]["start_date"] == "2021-11-14"
    assert result["manifest"] == str(output / "export_manifest.json")


def test_cli_validates_every_product_before_writing_any(
    tmp_path,
) -> None:
    modis_path = tmp_path / "modis.csv"
    _write_observations(modis_path, "MODIS", _modis_observations())
    invalid_probability = tmp_path / "bad-probabilities.csv"
    pd.DataFrame({"estimated": [True]}).to_csv(
        invalid_probability, index=False
    )
    output = tmp_path / "exports"

    with pytest.raises(ExportValidationError, match="lacks required field"):
        run_exports(
            products=["modis", "probabilities"],
            output_dir=output,
            modis_path=modis_path,
            probability_input=invalid_probability,
        )

    assert not output.exists()


def test_probability_date_filter_keeps_historical_dates_when_requested(
    tmp_path,
) -> None:
    source = tmp_path / "predictions.csv"
    _write_m8_batch(source)
    result = export_fine_scale_probabilities(
        source,
        tmp_path / "historical.csv",
        start_date="2000-01-01",
        end_date="2011-12-31",
    )
    assert result["row_count"] == 4
