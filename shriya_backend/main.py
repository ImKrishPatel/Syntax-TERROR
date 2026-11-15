
import os
import csv
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


# --------------------------------------------------
# PROJECT PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


# --------------------------------------------------
# FASTAPI APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="Wildfire FRP API",
    description="API for MODIS and VIIRS wildfire data and FRP analysis",
    version="1.0.0"
)


# --------------------------------------------------
# CORS CONFIGURATION
# --------------------------------------------------

FRONTEND_URL = os.getenv("FRONTEND_URL", "").strip()

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

if FRONTEND_URL:
    ALLOWED_ORIGINS.append(FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# HELPER FUNCTIONS
# --------------------------------------------------

def convert_value(value):
    """Convert CSV values into appropriate JSON types."""

    if value == "":
        return None

    if value.lower() == "true":
        return True

    if value.lower() == "false":
        return False

    try:
        number = float(value)

        if number.is_integer():
            return int(number)

        return number

    except ValueError:
        return value


def read_csv_page(filename, offset=0, limit=100):
    """Read only the requested page of a CSV file."""

    filepath = DATA_DIR / filename

    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {filename}"
        )

    results = []

    try:
        with filepath.open(
            "r",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(file)

            for _ in range(offset):
                if next(reader, None) is None:
                    break

            for row in reader:
                results.append({
                    key: convert_value(value)
                    for key, value in row.items()
                })

                if len(results) > limit:
                    break

    except (OSError, csv.Error) as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    has_more = len(results) > limit

    return {
        "data": results[:limit],
        "offset": offset,
        "limit": limit,
        "has_more": has_more
    }


def read_json_file(filename):
    """Read a JSON file from the data directory."""

    filepath = DATA_DIR / filename

    if not filepath.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {filename}"
        )

    try:
        with filepath.open("r", encoding="utf-8") as file:
            return json.load(file)

    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


def validate_sensor(sensor):
    """Validate the requested satellite sensor."""

    sensor = sensor.upper()

    if sensor not in ("MODIS", "VIIRS"):
        raise HTTPException(
            status_code=400,
            detail="Sensor must be MODIS or VIIRS"
        )

    return sensor


# --------------------------------------------------
# GENERAL ENDPOINTS
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "Wildfire FRP API is running",
        "documentation": "/docs",
        "available_sensors": ["MODIS", "VIIRS"]
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


# --------------------------------------------------
# FRP DATA ENDPOINTS
# --------------------------------------------------

@app.get("/frp/daily")
def daily_frp(
    sensor: str = "MODIS",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    sensor = validate_sensor(sensor)

    return read_csv_page(
        f"{sensor.lower()}_daily_frp_real.csv",
        offset,
        limit
    )


@app.get("/frp/regional")
def regional_frp(
    sensor: str = "MODIS",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    sensor = validate_sensor(sensor)

    return read_csv_page(
        f"{sensor.lower()}_regional_frp_real.csv",
        offset,
        limit
    )


@app.get("/frp/anomalies")
def frp_anomalies(
    sensor: str = "MODIS",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    sensor = validate_sensor(sensor)

    return read_csv_page(
        f"{sensor.lower()}_anomalies_real.csv",
        offset,
        limit
    )


@app.get("/frp/clusters")
def frp_clusters(
    sensor: str = "MODIS",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    sensor = validate_sensor(sensor)

    return read_csv_page(
        f"{sensor.lower()}_regional_clusters_real.csv",
        offset,
        limit
    )


@app.get("/frp/matches")
def frp_matches(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000)
):
    return read_csv_page(
        "real_frp_matches_multi_date.csv",
        offset,
        limit
    )


@app.get("/frp/calibration")
def frp_calibration():
    return read_json_file(
        "frp_multi_date_calibration_results.json"
    )


@app.get("/frp/matching-diagnostics")
def matching_diagnostics():
    return read_json_file(
        "real_frp_matching_diagnostics.json"
    )


# --------------------------------------------------
# TEAM PROJECT MODULE ROUTES
# --------------------------------------------------

from shriya_backend.routes.modules import router as modules_router

app.include_router(modules_router)


# --------------------------------------------------
# AI CHATBOT ROUTE
# --------------------------------------------------

from shriya_backend.routes.chat import router as chat_router

app.include_router(chat_router)
