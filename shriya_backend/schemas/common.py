from datetime import date
from typing import Literal
from pydantic import BaseModel, Field


class FireRecord(BaseModel):
    date: date
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    grid_id: str | None = None
    hotspot_count: int = Field(ge=0)
    frp_mw: float | None = Field(default=None, ge=0)
    sensor: Literal["MODIS", "VIIRS", "ESTIMATED"]
    is_estimated: bool
    source: str | None = None
