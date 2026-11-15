"""Project paths and spatial-temporal settings for the spatial ML module."""

import os
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MODIS_RESOLUTION_METERS = 1000
VIIRS_TARGET_RESOLUTION_METERS = 375

HISTORICAL_SYNTHESIS_START_YEAR = 2000
HISTORICAL_SYNTHESIS_END_YEAR = 2011
HISTORICAL_DEFAULT_START_DATE = "2000-11-01"
HISTORICAL_DEFAULT_END_DATE = "2011-12-31"
TERRA_MODIS_START_DATE = "2000-11-01"
AQUA_MODIS_START_DATE = "2002-07-01"
OVERLAP_START_YEAR = 2012
HISTORICAL_INFERENCE_BATCH_SIZE = 1000

DEFAULT_TIME_TOLERANCE_MINUTES = 30
SPATIAL_MATCH_RADIUS_M = 1000.0

TARGET_GRID_RESOLUTION_M = VIIRS_TARGET_RESOLUTION_METERS
TARGET_GRID_WIDTH = 3
TARGET_GRID_HEIGHT = 3
TARGET_GRID_PROJECTION = "local_aeqd"


def _configured_overlap_end_year() -> Optional[int]:
    value = os.getenv("SPATIAL_ML_OVERLAP_END_YEAR")
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            "SPATIAL_ML_OVERLAP_END_YEAR must be a four-digit year."
        ) from exc


OVERLAP_END_YEAR = _configured_overlap_end_year()

DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
MODIS_DATA_DIR = RAW_DATA_DIR / "modis"
VIIRS_DATA_DIR = RAW_DATA_DIR / "viirs"
OVERLAP_DATA_DIR = DATA_DIR / "overlap"
ENVIRONMENTAL_DATA_DIR = DATA_DIR / "environmental"
MODELS_DIR = PROJECT_ROOT / "models"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
HISTORICAL_OUTPUTS_DIR = OUTPUTS_DIR / "historical_estimates"
RESULTS_DIR = PROJECT_ROOT / "results"

PROJECT_DIRECTORIES = {
    "project root": PROJECT_ROOT,
    "data": DATA_DIR,
    "raw data": RAW_DATA_DIR,
    "processed data": PROCESSED_DATA_DIR,
    "MODIS data": MODIS_DATA_DIR,
    "VIIRS data": VIIRS_DATA_DIR,
    "overlap data": OVERLAP_DATA_DIR,
    "environmental data": ENVIRONMENTAL_DATA_DIR,
    "models": MODELS_DIR,
    "outputs": OUTPUTS_DIR,
    "historical outputs": HISTORICAL_OUTPUTS_DIR,
    "results": RESULTS_DIR,
}
