from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ModelId = Literal["ifs", "aifs-single"]
DeliveryStatus = Literal["live", "cached", "mixed"]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(APIModel):
    status: Literal["ok"]
    service: str


class ReadinessResponse(APIModel):
    status: Literal["ready", "not_ready"]
    cache_directory: str
    cache_available: bool
    source: str


class TimezoneResponse(APIModel):
    time_zone: str
    matching_time_zones: list[str]
    boundary_ambiguous: bool
    source: Literal["offline_timezonefinder"] = "offline_timezonefinder"
    coordinates_are_sent_to_third_party: Literal[False] = False
    note: str


class ModelCatalogItem(APIModel):
    id: ModelId
    label: str
    resolution: str
    documented_steps_hours: str


class UnitConvention(APIModel):
    param_id: int
    grib_units: str
    display_units: str
    conversion: str


class ForecastField(APIModel):
    short_name: Literal["tp"]
    level: Literal["surface"]
    meaning: str


class CatalogResponse(APIModel):
    models: list[ModelCatalogItem]
    field: ForecastField
    verified_unit_conventions: dict[ModelId, UnitConvention]
    availability_policy: str
    limitations: list[str]
    endpoints: list[str]


class CacheAvailability(APIModel):
    model: ModelId
    run_time_utc: datetime
    forecast_step_hours: int
    valid_time_utc: datetime
    accumulation_interval_hours: tuple[int, int]
    grib_units: str
    retrieved_at_utc: datetime
    sha256: str
    delivery_status: Literal["cached"] = "cached"


class AvailabilityResponse(APIModel):
    source: Literal["validated_local_cache"] = "validated_local_cache"
    checked_at_utc: datetime
    model_filter: ModelId | None
    date_filter: str | None
    cycle_filter_utc: int | None
    candidates_require_retrieval_validation: bool = True
    entries: list[CacheAvailability]
    message: str


class GribMetadata(APIModel):
    param_id: int
    step_type: str
    level_type: str
    grid_type: str
    reference_time_utc: datetime
    validity_time_utc: datetime
    start_step: int
    end_step: int


class ForecastResponse(APIModel):
    model: ModelId
    run_time_utc: datetime
    valid_time_utc: datetime
    forecast_step_hours: int
    accumulation_interval_hours: tuple[int, int]
    accumulation_label: str
    parameter: Literal["tp"]
    level: Literal["surface"]
    grib_metadata: GribMetadata
    grib_units: str
    display_units: Literal["mm"]
    display_range_mm: tuple[float, float]
    resolution: str
    grid_dimensions: tuple[int, int]
    grib_sha256: str
    source: str
    delivery_status: DeliveryStatus
    retrieved_at_utc: datetime
    raster_url: str
    attribution: str
    interpretation: str


class PointValue(APIModel):
    forecast_step_hours: int
    valid_time_utc: datetime
    retrieved_at_utc: datetime
    grib_units: str
    native_grib_accumulation_interval_hours: tuple[int, int]
    display_interval_hours: tuple[int, int]
    interval_amount_mm: float | None
    cumulative_amount_mm: float | None
    derived_from_cumulative_grib_fields: bool
    missing: bool


class PointLocation(APIModel):
    latitude: float
    longitude: float


class GridLocation(PointLocation):
    distance_from_click_km: float


class PointSeriesResponse(APIModel):
    model: ModelId
    source: Literal["ECMWF Open Data"]
    attribution: str
    run_time_utc: datetime
    requested_location: PointLocation
    grid_location: GridLocation
    display_units: Literal["mm"]
    delivery_status: DeliveryStatus
    series_method: str
    points: list[PointValue]


class ComparisonResponse(APIModel):
    mode: Literal["model_disagreement"]
    model: Literal["comparison"]
    display_name: str
    comparison_expression: Literal["AIFS Single − IFS (mm)"]
    interpretation: str
    run_time_utc: datetime
    valid_time_utc: datetime
    forecast_step_hours: int
    accumulation_interval_hours: tuple[int, int]
    accumulation_label: str
    display_units: Literal["mm difference"]
    display_range_mm: tuple[float, float]
    grid_dimensions: tuple[int, int]
    valid_grid_cells: int
    total_grid_cells: int
    delivery_status: DeliveryStatus
    sources: list[ForecastResponse]
    raster_url: str
    attribution: str


class HistoryRun(APIModel):
    run_time_utc: datetime
    forecast_step_hours: int
    sha256: str


class HistoryPair(APIModel):
    model: ModelId
    older: HistoryRun
    newer: HistoryRun
    valid_time_utc: datetime
    older_accumulation_interval_hours: tuple[int, int]
    newer_accumulation_interval_hours: tuple[int, int]
    accumulation_start_time_utc: datetime
    accumulation_end_time_utc: datetime


class HistoryCandidatesResponse(APIModel):
    cached_fields: int
    comparable_pairs: list[HistoryPair]
    message: str | None


class HistoryResponse(APIModel):
    mode: Literal["run_to_run_change"]
    model: ModelId
    older_run_time_utc: datetime
    newer_run_time_utc: datetime
    valid_time_utc: datetime
    older_accumulation_interval_hours: tuple[int, int]
    newer_accumulation_interval_hours: tuple[int, int]
    accumulation_start_time_utc: datetime
    accumulation_end_time_utc: datetime
    display_units: Literal["mm change"]
    expression: Literal["newer run − older run"]
    valid_grid_cells: int
    total_grid_cells: int
    interpretation: str
    raster_url: str
    delivery_status: Literal["cached"]
    older_sha256: str
    newer_sha256: str


class HistoryPointAmount(APIModel):
    run_time_utc: datetime
    retrieved_at_utc: datetime
    forecast_step_hours: int
    accumulation_interval_hours: tuple[int, int]
    grib_units: str
    value_mm: float | None
    sha256: str


class HistoryPointResponse(APIModel):
    model: ModelId
    valid_time_utc: datetime
    accumulation_start_time_utc: datetime
    accumulation_end_time_utc: datetime
    requested_location: PointLocation
    older_grid_location: GridLocation
    newer_grid_location: GridLocation
    display_units: Literal["mm"]
    expression: Literal["newer run − older run"]
    older: HistoryPointAmount
    newer: HistoryPointAmount
    difference_mm: float | None
    comparison_available: bool
    message: str
    delivery_status: Literal["cached"]


class ErrorDetail(APIModel):
    code: str
    message: str
    context: dict[str, object] = Field(default_factory=dict)


class ErrorResponse(APIModel):
    error: ErrorDetail
