from pydantic import BaseModel, Field


class Geo(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class Error(BaseModel):
    error: str
    detail: str | None = None


class StatusResponse(BaseModel):
    status: str
