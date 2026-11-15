from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["Project Modules"])

MODULES = {
    "spatial": "Aashana's MODIS-to-VIIRS super-resolution",
    "regimes": "Pearl's fire regime clustering",
    "forecast": "Snavya's 7-30 day fire forecasting",
    "sonification": "Mridul's fire data sonification",
    "map": "Krish's interactive map and proximity features",
}


@router.get("/modules")
def list_modules():
    return {
        "modules": [
            {
                "id": key,
                "description": description,
                "status": "integration_pending"
            }
            for key, description in MODULES.items()
        ]
    }


@router.get("/spatial/status")
def spatial_status():
    return {"module": "spatial", "status": "integration_pending"}


@router.get("/regimes/status")
def regimes_status():
    return {"module": "regimes", "status": "integration_pending"}


@router.get("/forecast/status")
def forecast_status():
    return {"module": "forecast", "status": "integration_pending"}


@router.get("/sonification/status")
def sonification_status():
    return {"module": "sonification", "status": "integration_pending"}


@router.get("/map/status")
def map_status():
    return {"module": "map", "status": "integration_pending"}
