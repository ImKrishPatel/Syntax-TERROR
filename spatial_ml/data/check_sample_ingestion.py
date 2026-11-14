"""Run the offline FIRMS ingestion example using synthetic test fixtures."""

import json
from pathlib import Path
from typing import Any

from . import (
    clean_firms_records,
    load_modis,
    load_viirs,
    quality_control_summary,
    temporal_range_summary,
)


def _json_default(value: Any) -> str:
    return str(value)


def main() -> None:
    """Print normalized/cleaned counts and QC summaries for sample CSVs."""
    fixture_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    modis_raw = load_modis(fixture_dir / "modis_sample_synthetic.csv")
    viirs_raw = load_viirs(fixture_dir / "viirs_sample_synthetic.csv")
    modis_clean = clean_firms_records(modis_raw)
    viirs_clean = clean_firms_records(viirs_raw)

    print(
        "MODIS: "
        f"raw={len(modis_raw)}, normalized={len(modis_raw)}, "
        f"cleaned={len(modis_clean)}"
    )
    print(
        "VIIRS: "
        f"raw={len(viirs_raw)}, normalized={len(viirs_raw)}, "
        f"cleaned={len(viirs_clean)}"
    )
    summaries = {
        "MODIS": quality_control_summary(modis_raw, modis_clean, "MODIS"),
        "VIIRS": quality_control_summary(viirs_raw, viirs_clean, "VIIRS"),
        "temporal_ranges": temporal_range_summary(modis_clean, viirs_clean),
    }
    print(json.dumps(summaries, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
