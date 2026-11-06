from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .data import Forecast, ForecastStore, absolute_accumulation_period, make_forecast_difference, make_model_disagreement, sample_forecast, supported_step
from .errors import ForecastDecodeError, ForecastNetworkError, ForecastNotPublishedError, RainScopeAPIError
from .schemas import (
    AvailabilityResponse, CatalogResponse, ComparisonResponse, ForecastResponse,
    HealthResponse, HistoryCandidatesResponse, HistoryPointResponse, HistoryResponse, PointSeriesResponse,
    ReadinessResponse, TimezoneResponse,
)
from .timezones import TimezoneLookupUnavailable, timezones_for_coordinates


app = FastAPI(title="RainScope API", version="0.1.0")
allowed_hosts = [host.strip() for host in os.getenv("RAINSCOPE_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
if not allowed_hosts or "*" in allowed_hosts:
    raise RuntimeError("RAINSCOPE_ALLOWED_HOSTS must contain explicit host names; wildcard hosts are not allowed.")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
cors_origins = [origin.strip().rstrip("/") for origin in os.getenv("RAINSCOPE_CORS_ORIGINS", "").split(",") if origin.strip()]
if "*" in cors_origins:
    raise RuntimeError("RAINSCOPE_CORS_ORIGINS must contain explicit origins; wildcard CORS is not allowed.")
if cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET"], allow_headers=["*"])
store = ForecastStore()
ECMWF_ATTRIBUTION = (
    "This output is based on data and products of the European Centre for Medium-Range Weather Forecasts (ECMWF). "
    "Copyright © 2026 ECMWF. Source: ECMWF Open Data. Licensed under CC BY 4.0. "
    "RainScope converts precipitation units to millimetres and reprojects the raster for display. "
    "ECMWF does not warrant accuracy, completeness, or uninterrupted availability and accepts no liability for errors or resulting loss or damage."
)


@app.exception_handler(RainScopeAPIError)
async def rainscope_error_handler(_: Request, exc: RainScopeAPIError):
    return JSONResponse(status_code=exc.status_code, content={
        "error": {"code": exc.code, "message": exc.message, "context": exc.context},
    }, headers={"Cache-Control": "no-store"})


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    code = {404: "not_found", 409: "data_conflict", 422: "invalid_request", 503: "service_unavailable"}.get(exc.status_code, "request_failed")
    message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    context = exc.detail if isinstance(exc.detail, dict) else {}
    return JSONResponse(status_code=exc.status_code, content={
        "error": {"code": code, "message": message, "context": context},
    }, headers={**(exc.headers or {}), "Cache-Control": "no-store"})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    fields = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"], "code": item["type"]}
              for item in exc.errors()]
    return JSONResponse(status_code=422, content={
        "error": {"code": "invalid_request", "message": "Request parameters are invalid.", "context": {"fields": fields}},
    }, headers={"Cache-Control": "no-store"})


@app.get("/api/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "service": "rainscope-api"}


@app.get("/api/ready", response_model=ReadinessResponse)
async def readiness():
    cache_path = store.cache_dir
    probe = cache_path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    cache_available = probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)
    return {
        "status": "ready" if cache_available else "not_ready",
        "cache_directory": str(cache_path),
        "cache_available": cache_available,
        "source": store.source,
    }


@app.get("/api/timezone", response_model=TimezoneResponse)
async def timezone_lookup(
    response: Response,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    response.headers["Cache-Control"] = "no-store"
    try:
        zones = await asyncio.to_thread(timezones_for_coordinates, lat, lon)
    except TimezoneLookupUnavailable as exc:
        raise RainScopeAPIError(503, "timezone_lookup_unavailable", str(exc)) from exc
    if not zones:
        raise RainScopeAPIError(404, "timezone_not_found", "No IANA time zone matched these coordinates. Show UTC for this location.")
    return {
        "time_zone": zones[0],
        "matching_time_zones": list(zones),
        "boundary_ambiguous": len(zones) > 1,
        "source": "offline_timezonefinder",
        "coordinates_are_sent_to_third_party": False,
        "note": "IANA time-zone lookup tests local boundary polygons. Where polygons overlap, the first listed zone is the package's documented smaller-area choice; exact boundary points can remain ambiguous. Forecast timestamps remain UTC internally.",
    }


@app.get("/api/catalog", response_model=CatalogResponse)
async def catalog():
    return {
        "models": [
            {"id": "ifs", "label": "IFS", "resolution": "0.25°", "documented_steps_hours": "0–144 h every 3 h; 00/12 UTC also 150–360 h every 6 h"},
            {"id": "aifs-single", "label": "AIFS Single", "resolution": "0.25°", "documented_steps_hours": "six-hour steps through 360 hours; checked by retrieval"},
        ],
        "field": {"short_name": "tp", "level": "surface", "meaning": "total precipitation accumulated over the GRIB startStep–endStep interval"},
        "verified_unit_conventions": {"ifs": {"param_id": 228, "grib_units": "m", "display_units": "mm", "conversion": "multiply by 1000"}, "aifs-single": {"param_id": 228228, "grib_units": "kg m**-2", "display_units": "mm", "conversion": "numerically equivalent for water accumulation"}},
        "availability_policy": "A listed cycle or step is not assumed available; a real GRIB must be retrieved or pass local-cache validation before serving.",
        "limitations": ["AIFS ENS and calibrated probabilities are not implemented", "No radar, nowcasting, warnings, or flood prediction", "Run-to-run change requires two comparable validated cached fields"],
        "endpoints": ["/api/health", "/api/ready", "/api/catalog", "/api/availability", "/api/forecast", "/api/forecast/raster", "/api/point-series", "/api/comparison", "/api/comparison/raster", "/api/history/candidates", "/api/history", "/api/history/point", "/api/history/raster", "/api/timezone"],
    }


def _availability(model: str | None, forecast_date: date | None, cycle: int | None):
    cached = store.cached_forecasts(model=model, forecast_date=forecast_date, cycle=cycle)
    entries = [item for item in cached
               if (model is None or item.model == model)
               and (forecast_date is None or item.run.date() == forecast_date)
               and (cycle is None or item.run.hour == cycle)]
    entries.sort(key=lambda item: (item.run, item.model, item.step))
    return {
        "checked_at_utc": datetime.now(UTC),
        "model_filter": model,
        "date_filter": forecast_date.isoformat() if forecast_date else None,
        "cycle_filter_utc": cycle,
        "entries": [{
            "model": item.model,
            "run_time_utc": item.run,
            "forecast_step_hours": item.step,
            "valid_time_utc": item.valid_time,
            "accumulation_interval_hours": [item.start_step, item.end_step],
            "grib_units": item.units,
            "retrieved_at_utc": item.retrieved_at,
            "sha256": item.sha256,
            "delivery_status": "cached",
        } for item in entries],
        "message": "Only already validated local cache entries are listed. An absent run or step is unverified; request its forecast to check ECMWF publication.",
    }


@app.get("/api/availability", response_model=AvailabilityResponse)
async def availability(
    response: Response,
    model: str | None = Query(None, pattern="^(ifs|aifs-single)$"),
    forecast_date: date | None = Query(None, alias="date"),
    cycle: int | None = Query(None, ge=0, le=18),
):
    response.headers["Cache-Control"] = "no-store"
    if cycle is not None and cycle not in (0, 6, 12, 18):
        raise RainScopeAPIError(422, "invalid_request", "Cycle must be 00, 06, 12, or 18 UTC.")
    if forecast_date and forecast_date > datetime.now(UTC).date():
        raise RainScopeAPIError(422, "invalid_request", "A future UTC date cannot contain an available forecast run.")
    return await asyncio.to_thread(_availability, model, forecast_date, cycle)


def _retrieve(model: str, forecast_date: date, cycle: int, step: int) -> Forecast:
    run = datetime(forecast_date.year, forecast_date.month, forecast_date.day, cycle, tzinfo=UTC)
    if not supported_step(model, cycle, step):
        raise RainScopeAPIError(422, "invalid_request", f"Step {step}h is not documented for {model} at the {cycle:02d} UTC cycle.")
    if run > datetime.now(UTC):
        raise RainScopeAPIError(422, "future_run", "Selected forecast cycle is in the future.", {"run_time_utc": run.isoformat()})
    try:
        return store.get(model, run, step)
    except ForecastNotPublishedError as exc:
        raise RainScopeAPIError(404, "run_unpublished", "ECMWF has not published the requested model, run, and step.",
                                {"model": model, "run_time_utc": run.isoformat(), "step_hours": step}) from exc
    except ForecastNetworkError as exc:
        raise RainScopeAPIError(503, "upstream_network_error", "The ECMWF Open Data request failed at the network layer.",
                                {"reason": str(exc)[:240]}) from exc
    except ForecastDecodeError as exc:
        raise RainScopeAPIError(502, "grib_decode_error", "The source GRIB failed validation or could not be decoded.",
                                {"reason": str(exc)[:240]}) from exc
    except ValueError as exc:
        raise RainScopeAPIError(422, "invalid_request", str(exc)) from exc
    except Exception as exc:
        raise RainScopeAPIError(503, "upstream_error", f"Forecast unavailable from ECMWF Open Data ({type(exc).__name__}: {str(exc)[:200]}). No substitute data was used.") from exc


def _metadata(forecast: Forecast):
    return {
        "model": forecast.model,
        "run_time_utc": forecast.run.isoformat(),
        "valid_time_utc": forecast.valid_time.isoformat(),
        "forecast_step_hours": forecast.step,
        "accumulation_interval_hours": [forecast.start_step, forecast.end_step],
        "accumulation_label": f"{forecast.end_step - forecast.start_step}-hour accumulation ({forecast.start_step}–{forecast.end_step} h)",
        "parameter": "tp",
        "level": "surface",
        "grib_metadata": {
            "param_id": forecast.param_id,
            "step_type": forecast.step_type,
            "level_type": forecast.level_type,
            "grid_type": forecast.grid_type,
            "reference_time_utc": forecast.run.isoformat(),
            "validity_time_utc": forecast.valid_time.isoformat(),
            "start_step": forecast.start_step,
            "end_step": forecast.end_step,
        },
        "grib_units": forecast.units,
        "display_units": "mm",
        "display_range_mm": [0, 25],
        "resolution": "0.25°",
        "grid_dimensions": [forecast.width, forecast.height],
        "grib_sha256": forecast.sha256,
        "source": "ECMWF Open Data",
        "delivery_status": "cached" if forecast.served_from_cache else "live",
        "retrieved_at_utc": forecast.retrieved_at.isoformat(),
        "raster_url": f"/api/forecast/raster?model={forecast.model}&date={forecast.run:%Y-%m-%d}&cycle={forecast.run.hour:02d}&step={forecast.step}",
        "attribution": ECMWF_ATTRIBUTION,
        "interpretation": "Total precipitation accumulation for the GRIB accumulation interval; not a precipitation rate or an observation.",
    }


def _comparison(forecast_date: date, cycle: int, step: int):
    if not supported_step("ifs", cycle, step) or not supported_step("aifs-single", cycle, step):
        raise RainScopeAPIError(422, "invalid_request", "Comparison steps must be available for both models: 0–144 h every 6 h; 00/12 UTC also 150–360 h every 6 h.")
    run = datetime(forecast_date.year, forecast_date.month, forecast_date.day, cycle, tzinfo=UTC)
    if run > datetime.now(UTC):
        raise RainScopeAPIError(422, "future_run", "Selected forecast cycle is in the future.", {"run_time_utc": run.isoformat()})
    try:
        ifs = store.get("ifs", run, step)
        aifs = store.get("aifs-single", run, step)
        store.cache_dir.mkdir(parents=True, exist_ok=True)
        raster_path = store.cache_dir / f"comparison-{run:%Y%m%dT%H}z-{step:03d}.png"
        temp_path = raster_path.with_name(f".{raster_path.name}.tmp")
        try:
            valid_cells, total_cells = make_model_disagreement(ifs, aifs, temp_path)
            try:
                os.link(temp_path, raster_path)
            except FileExistsError:
                pass
        finally:
            temp_path.unlink(missing_ok=True)
    except HTTPException:
        raise
    except ForecastNotPublishedError as exc:
        raise RainScopeAPIError(404, "run_unpublished", "ECMWF has not published both models for this run and step.",
                                {"run_time_utc": run.isoformat(), "step_hours": step}) from exc
    except ForecastNetworkError as exc:
        raise RainScopeAPIError(503, "upstream_network_error", "An ECMWF model request failed at the network layer.",
                                {"reason": str(exc)[:240]}) from exc
    except ForecastDecodeError as exc:
        raise RainScopeAPIError(502, "grib_decode_error", "A source GRIB failed validation or could not be decoded.",
                                {"reason": str(exc)[:240]}) from exc
    except ValueError as exc:
        raise RainScopeAPIError(409, "comparison_not_aligned", f"Models cannot be compared for this run: {exc}") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Both real model fields are required for comparison ({type(exc).__name__}: {str(exc)[:240]}). No substitute data was used.",
        ) from exc

    statuses = {"cached" if ifs.served_from_cache else "live", "cached" if aifs.served_from_cache else "live"}
    delivery_status = statuses.pop() if len(statuses) == 1 else "mixed"
    return {
        "mode": "model_disagreement",
        "model": "comparison",
        "display_name": "Model disagreement",
        "comparison_expression": "AIFS Single − IFS (mm)",
        "interpretation": "This field is model disagreement, not probability or confidence. Positive values mean AIFS Single forecasts more accumulated precipitation than IFS; negative values mean less.",
        "run_time_utc": run.isoformat(),
        "valid_time_utc": ifs.valid_time.isoformat(),
        "forecast_step_hours": step,
        "accumulation_interval_hours": [ifs.start_step, ifs.end_step],
        "accumulation_label": f"{ifs.end_step - ifs.start_step}-hour accumulation ({ifs.start_step}–{ifs.end_step} h)",
        "display_units": "mm difference",
        "display_range_mm": [-25, 25],
        "grid_dimensions": [ifs.width, ifs.height],
        "valid_grid_cells": valid_cells,
        "total_grid_cells": total_cells,
        "delivery_status": delivery_status,
        "sources": [_metadata(ifs), _metadata(aifs)],
        "raster_url": f"/api/comparison/raster?date={run:%Y-%m-%d}&cycle={run.hour:02d}&step={step}",
        "attribution": ECMWF_ATTRIBUTION,
    }


def _point_series(model: str, forecast_date: date, cycle: int, end_step: int, lat: float, lon: float):
    run = datetime(forecast_date.year, forecast_date.month, forecast_date.day, cycle, tzinfo=UTC)
    if run > datetime.now(UTC):
        raise RainScopeAPIError(422, "future_run", "Selected forecast cycle is in the future.", {"run_time_utc": run.isoformat()})
    if end_step < 6 or end_step > 72 or end_step % 6:
        raise RainScopeAPIError(422, "invalid_request", "Point timeline supports six-hour steps from 6 through 72 hours.")

    points = []
    previous_cumulative = None
    previous_end = 0
    grid_location = None
    delivery_modes = []
    for step in range(6, end_step + 1, 6):
        try:
            forecast = store.get(model, run, step)
            sample = sample_forecast(forecast, lat, lon)
        except ForecastNotPublishedError as exc:
            raise RainScopeAPIError(404, "run_unpublished", "ECMWF has not published a required point-series step.",
                                    {"model": model, "run_time_utc": run.isoformat(), "step_hours": step}) from exc
        except ForecastNetworkError as exc:
            raise RainScopeAPIError(503, "upstream_network_error", "A point-series ECMWF request failed at the network layer.",
                                    {"step_hours": step, "reason": str(exc)[:200]}) from exc
        except ForecastDecodeError as exc:
            raise RainScopeAPIError(502, "grib_decode_error", "A point-series GRIB failed validation or could not be decoded.",
                                    {"step_hours": step, "reason": str(exc)[:200]}) from exc
        except ValueError as exc:
            raise RainScopeAPIError(422, "invalid_request", str(exc), {"step_hours": step}) from exc
        except Exception as exc:
            raise RainScopeAPIError(503, "upstream_error", f"Point time series unavailable at step {step}h ({type(exc).__name__}: {str(exc)[:180]}). No substitute data was used.") from exc

        grid_location = grid_location or {
            "latitude": sample["grid_latitude"],
            "longitude": sample["grid_longitude"],
            "distance_from_click_km": round(sample["grid_distance_km"], 2),
        }
        delivery_modes.append("cached" if forecast.served_from_cache else "live")
        amount = sample["value_mm"]
        native_start, native_end = forecast.start_step, forecast.end_step
        derived = False
        if native_start == 0:
            period_start = previous_end
            if amount is not None:
                if previous_cumulative is not None:
                    amount = amount - previous_cumulative
                    derived = True
                elif previous_end > 0:
                    amount = None
                previous_cumulative = sample["value_mm"]
            else:
                previous_cumulative = None
            previous_end = native_end
        else:
            period_start = native_start
            previous_end = native_end

        points.append({
            "forecast_step_hours": step,
            "valid_time_utc": forecast.valid_time.isoformat(),
            "retrieved_at_utc": forecast.retrieved_at.isoformat(),
            "grib_units": forecast.units,
            "native_grib_accumulation_interval_hours": [native_start, native_end],
            "display_interval_hours": [period_start, native_end],
            # Keep decoded precision in the API. Display rounding belongs in
            # the client; location-window totals must sum the unrounded values.
            "interval_amount_mm": amount,
            "cumulative_amount_mm": None if sample["value_mm"] is None or native_start != 0 else sample["value_mm"],
            "derived_from_cumulative_grib_fields": derived,
            "missing": sample["missing"],
        })

    return {
        "model": model,
        "source": "ECMWF Open Data",
        "attribution": ECMWF_ATTRIBUTION,
        "run_time_utc": run.isoformat(),
        "requested_location": {"latitude": lat, "longitude": lon},
        "grid_location": grid_location,
        "display_units": "mm",
        "delivery_status": delivery_modes[0] if len(set(delivery_modes)) == 1 else "mixed",
        "series_method": "Each GRIB field is inspected. Cumulative 0–N h totals are differenced into adjacent intervals where applicable; native intervals are left unchanged.",
        "points": points,
    }


@app.get("/api/forecast", response_model=ForecastResponse)
async def forecast(
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    forecast_date: date = Query(alias="date"),
    cycle: int = Query(0, ge=0, le=18),
    step: int = Query(24, ge=0, le=360),
):
    if cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    data = await asyncio.to_thread(_retrieve, model, forecast_date, cycle, step)
    return _metadata(data)


@app.get("/api/forecast/raster")
async def raster(
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    forecast_date: date = Query(alias="date"),
    cycle: int = Query(0, ge=0, le=18),
    step: int = Query(24, ge=0, le=360),
):
    if cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    data = await asyncio.to_thread(_retrieve, model, forecast_date, cycle, step)
    return FileResponse(data.png_path, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/comparison", response_model=ComparisonResponse)
async def comparison(
    forecast_date: date = Query(alias="date"),
    cycle: int = Query(0, ge=0, le=18),
    step: int = Query(24, ge=0, le=360),
):
    if cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    return await asyncio.to_thread(_comparison, forecast_date, cycle, step)


@app.get("/api/comparison/raster")
async def comparison_raster(
    forecast_date: date = Query(alias="date"),
    cycle: int = Query(0, ge=0, le=18),
    step: int = Query(24, ge=0, le=360),
):
    if cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    comparison_data = await asyncio.to_thread(_comparison, forecast_date, cycle, step)
    path = store.cache_dir / f"comparison-{forecast_date:%Y%m%d}T{cycle:02d}z-{step:03d}.png"
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/point-series", response_model=PointSeriesResponse)
async def point_series(
    response: Response,
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    forecast_date: date = Query(alias="date"),
    cycle: int = Query(0, ge=0, le=18),
    end_step: int = Query(24, ge=6, le=72),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    response.headers["Cache-Control"] = "no-store"
    if cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    return await asyncio.to_thread(_point_series, model, forecast_date, cycle, end_step, lat, lon)


def _history_candidates():
    cached = store.cached_forecasts()
    pairs = []
    for index, left in enumerate(cached):
        for right in cached[index + 1:]:
            if left.model != right.model or left.run == right.run:
                continue
            left_interval=absolute_accumulation_period(left)
            right_interval=absolute_accumulation_period(right)
            if left.valid_time != right.valid_time or left_interval != right_interval:
                continue
            if (left.param_id,left.level_type,left.step_type,left.grid_type,left.width,left.height,left.units) != (right.param_id,right.level_type,right.step_type,right.grid_type,right.width,right.height,right.units):
                continue
            older, newer = sorted((left, right), key=lambda item: item.run)
            pairs.append({
                "model": left.model,
                "older": {"run_time_utc": older.run.isoformat(), "forecast_step_hours": older.step, "sha256": older.sha256},
                "newer": {"run_time_utc": newer.run.isoformat(), "forecast_step_hours": newer.step, "sha256": newer.sha256},
                "valid_time_utc": left.valid_time.isoformat(),
                "older_accumulation_interval_hours": [older.start_step, older.end_step],
                "newer_accumulation_interval_hours": [newer.start_step, newer.end_step],
                "accumulation_start_time_utc": left_interval[0].isoformat(),
                "accumulation_end_time_utc": left_interval[1].isoformat(),
            })
    return {"cached_fields": len(cached), "comparable_pairs": pairs,
            "message": None if pairs else "No two cached, validated runs currently share the same valid time and absolute accumulation start/end times. History will appear when comparable real GRIBs are cached."}


@app.get("/api/history/candidates", response_model=HistoryCandidatesResponse)
async def history_candidates():
    return await asyncio.to_thread(_history_candidates)


def _history_difference(model: str, older_date: date, older_cycle: int, older_step: int,
                        newer_date: date, newer_cycle: int, newer_step: int):
    older,newer=_cached_history_pair(model,older_date,older_cycle,older_step,newer_date,newer_cycle,newer_step)
    raster_path = store.cache_dir / f"history-{model}-{older.run:%Y%m%dT%H}z-{older.step:03d}-{newer.run:%Y%m%dT%H}z-{newer.step:03d}.png"
    temp_path = raster_path.with_name(f".{raster_path.name}.tmp")
    try:
        valid_cells, total_cells = make_forecast_difference(older, newer, temp_path)
        try:
            os.link(temp_path, raster_path)
        except FileExistsError:
            pass
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=f"Cached GRIBs failed comparison validation: {exc}") from exc
    start_time,end_time=absolute_accumulation_period(older)
    return {
        "mode": "run_to_run_change", "model": model,
        "older_run_time_utc": older.run.isoformat(), "newer_run_time_utc": newer.run.isoformat(),
        "valid_time_utc": older.valid_time.isoformat(),
        "older_accumulation_interval_hours": [older.start_step,older.end_step],
        "newer_accumulation_interval_hours": [newer.start_step,newer.end_step],
        "accumulation_start_time_utc": start_time.isoformat(),
        "accumulation_end_time_utc": end_time.isoformat(),
        "display_units": "mm change", "expression": "newer run − older run",
        "valid_grid_cells": valid_cells, "total_grid_cells": total_cells,
        "interpretation": "Difference between two cached deterministic forecasts for the same absolute precipitation period, valid time, and aligned grid; not probability or observed change.",
        "raster_url": f"/api/history/raster?model={model}&older_date={older.run:%Y-%m-%d}&older_cycle={older.run.hour}&older_step={older.step}&newer_date={newer.run:%Y-%m-%d}&newer_cycle={newer.run.hour}&newer_step={newer.step}",
        "delivery_status": "cached", "older_sha256": older.sha256, "newer_sha256": newer.sha256,
    }


def _cached_history_pair(model: str, older_date: date, older_cycle: int, older_step: int,
                         newer_date: date, newer_cycle: int, newer_step: int):
    if model not in ("ifs", "aifs-single"):
        raise HTTPException(status_code=422, detail="History supports IFS and AIFS Single")
    cached = store.cached_forecasts()
    def select(day: date, cycle: int, step: int):
        run = datetime(day.year, day.month, day.day, cycle, tzinfo=UTC)
        return next((item for item in cached if item.model == model and item.run == run and item.step == step), None)
    older = select(older_date, older_cycle, older_step)
    newer = select(newer_date, newer_cycle, newer_step)
    if older is None or newer is None:
        raise RainScopeAPIError(404, "history_cache_missing", "Both selected runs must exist as validated local GRIB cache entries; no download or substitute is used for history.")
    if older.run >= newer.run:
        raise RainScopeAPIError(422, "invalid_request", "Older run must precede the newer run.")
    older_interval=absolute_accumulation_period(older)
    newer_interval=absolute_accumulation_period(newer)
    if older.valid_time != newer.valid_time or older_interval != newer_interval:
        raise RainScopeAPIError(409, "comparison_not_aligned", "Runs cannot be compared: valid times and absolute accumulation start/end times must match.")
    if (older.param_id,older.level_type,older.step_type,older.grid_type,older.width,older.height,older.units)!=(newer.param_id,newer.level_type,newer.step_type,newer.grid_type,newer.width,newer.height,newer.units):
        raise RainScopeAPIError(409, "comparison_not_aligned", "Runs cannot be compared: parameter, level, step type, grid, dimensions, or original units differ.")
    return older,newer


def _history_point(model: str, older_date: date, older_cycle: int, older_step: int,
                   newer_date: date, newer_cycle: int, newer_step: int, lat: float, lon: float):
    older,newer=_cached_history_pair(model,older_date,older_cycle,older_step,newer_date,newer_cycle,newer_step)
    try:
        old=sample_forecast(older,lat,lon)
        new=sample_forecast(newer,lat,lon)
    except Exception as exc:
        raise RainScopeAPIError(502,"history_point_decode_failed",f"A cached comparison point could not be decoded: {str(exc)[:200]}") from exc
    same_grid=(old["grid_latitude"]==new["grid_latitude"] and old["grid_longitude"]==new["grid_longitude"])
    both_valid=old["value_mm"] is not None and new["value_mm"] is not None
    comparable=same_grid and both_valid
    message="Both cached forecasts sample the same grid cell and absolute precipitation period." if comparable else "Difference unavailable: " + ("the requested cell is missing in at least one cached GRIB." if not both_valid else "the requested coordinates resolve to different grid cells.")
    return {
        "model":model,"valid_time_utc":older.valid_time.isoformat(),
        "accumulation_start_time_utc":absolute_accumulation_period(older)[0].isoformat(),
        "accumulation_end_time_utc":absolute_accumulation_period(older)[1].isoformat(),
        "requested_location":{"latitude":lat,"longitude":lon},
        "older_grid_location":{"latitude":old["grid_latitude"],"longitude":old["grid_longitude"],"distance_from_click_km":round(old["grid_distance_km"],2)},
        "newer_grid_location":{"latitude":new["grid_latitude"],"longitude":new["grid_longitude"],"distance_from_click_km":round(new["grid_distance_km"],2)},
        "display_units":"mm","expression":"newer run − older run",
        "older":{"run_time_utc":older.run.isoformat(),"retrieved_at_utc":older.retrieved_at.isoformat(),"forecast_step_hours":older.step,"accumulation_interval_hours":[older.start_step,older.end_step],"grib_units":older.units,"value_mm":old["value_mm"],"sha256":older.sha256},
        "newer":{"run_time_utc":newer.run.isoformat(),"retrieved_at_utc":newer.retrieved_at.isoformat(),"forecast_step_hours":newer.step,"accumulation_interval_hours":[newer.start_step,newer.end_step],"grib_units":newer.units,"value_mm":new["value_mm"],"sha256":newer.sha256},
        "difference_mm":None if not comparable else round(new["value_mm"]-old["value_mm"],3),
        "comparison_available":comparable,"message":message,"delivery_status":"cached",
    }


@app.get("/api/history", response_model=HistoryResponse)
async def history_difference(
    older_date: date, newer_date: date,
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    older_cycle: int = Query(ge=0, le=18), older_step: int = Query(ge=0, le=360),
    newer_cycle: int = Query(ge=0, le=18), newer_step: int = Query(ge=0, le=360),
):
    if older_cycle not in (0, 6, 12, 18) or newer_cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    if not all(supported_step(model, cycle, step) for cycle, step in ((older_cycle, older_step), (newer_cycle, newer_step))):
        raise RainScopeAPIError(422, "invalid_request", "A selected history step is not supported by the model at that UTC cycle.")
    return await asyncio.to_thread(_history_difference, model, older_date, older_cycle, older_step, newer_date, newer_cycle, newer_step)


@app.get("/api/history/point", response_model=HistoryPointResponse)
async def history_point(
    older_date: date, newer_date: date,
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    older_cycle: int = Query(ge=0, le=18), older_step: int = Query(ge=0, le=360),
    newer_cycle: int = Query(ge=0, le=18), newer_step: int = Query(ge=0, le=360),
    lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
):
    if older_cycle not in (0, 6, 12, 18) or newer_cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    if not all(supported_step(model, cycle, step) for cycle, step in ((older_cycle, older_step), (newer_cycle, newer_step))):
        raise RainScopeAPIError(422, "invalid_request", "A selected history step is not supported by the model at that UTC cycle.")
    return await asyncio.to_thread(_history_point, model, older_date, older_cycle, older_step, newer_date, newer_cycle, newer_step, lat, lon)


@app.get("/api/history/raster")
async def history_raster(
    older_date: date, newer_date: date,
    model: str = Query("ifs", pattern="^(ifs|aifs-single)$"),
    older_cycle: int = Query(ge=0, le=18), older_step: int = Query(ge=0, le=360),
    newer_cycle: int = Query(ge=0, le=18), newer_step: int = Query(ge=0, le=360),
):
    if older_cycle not in (0, 6, 12, 18) or newer_cycle not in (0, 6, 12, 18):
        raise HTTPException(status_code=422, detail="Cycle must be 00, 06, 12, or 18 UTC")
    if not all(supported_step(model, cycle, step) for cycle, step in ((older_cycle, older_step), (newer_cycle, newer_step))):
        raise RainScopeAPIError(422, "invalid_request", "A selected history step is not supported by the model at that UTC cycle.")
    await asyncio.to_thread(_history_difference, model, older_date, older_cycle, older_step, newer_date, newer_cycle, newer_step)
    older_run = datetime(older_date.year, older_date.month, older_date.day, older_cycle, tzinfo=UTC)
    newer_run = datetime(newer_date.year, newer_date.month, newer_date.day, newer_cycle, tzinfo=UTC)
    path = store.cache_dir / f"history-{model}-{older_run:%Y%m%dT%H}z-{older_step:03d}-{newer_run:%Y%m%dT%H}z-{newer_step:03d}.png"
    return FileResponse(path, media_type="image/png")
