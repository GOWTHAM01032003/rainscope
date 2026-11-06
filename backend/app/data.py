from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
from requests.exceptions import HTTPError, RequestException
from eccodes import (
    codes_get,
    codes_get_array,
    codes_grib_new_from_file,
    codes_release,
)
from ecmwf.opendata import Client
from PIL import Image

from .errors import ForecastDecodeError, ForecastNetworkError, ForecastNotPublishedError


MODELS = {
    "ifs": {"client_model": "ifs", "stream": "oper", "type": "fc", "resol": "0p25"},
    "aifs-single": {"client_model": "aifs-single", "stream": "oper", "type": "fc", "resol": "0p25"},
}


def supported_step(model: str, cycle: int, step: int) -> bool:
    """Return ECMWF Open Data's documented precipitation lead schedule."""
    if not isinstance(step, int) or step < 0:
        return False
    if model == "aifs-single":
        return step <= 360 and step % 6 == 0
    if model != "ifs":
        return False
    if step <= 144 and step % 3 == 0:
        return True
    return cycle in (0, 12) and 150 <= step <= 360 and step % 6 == 0


@dataclass(frozen=True)
class Forecast:
    model: str
    run: datetime
    step: int
    start_step: int
    end_step: int
    valid_time: datetime
    param_id: int
    step_type: str
    level_type: str
    grid_type: str
    units: str
    grib_path: Path
    png_path: Path
    sha256: str
    width: int
    height: int
    served_from_cache: bool
    retrieved_at: datetime


def precipitation_to_mm(values: np.ndarray, units: str) -> np.ndarray:
    normalized = units.strip().lower().replace(" ", "")
    if normalized in {"m", "mofwater"}:
        return values.astype(np.float32) * 1000.0
    if normalized in {"mm", "kgm**-2", "kgm-2"}:
        return values.astype(np.float32)
    raise ValueError(f"Unsupported total-precipitation units: {units!r}")


def _message_metadata(path: Path, expected_run: datetime, expected_step: int, expected_model: str | None = None):
    with path.open("rb") as stream:
        handle = codes_grib_new_from_file(stream)
        if handle is None:
            raise ValueError("Downloaded file contains no GRIB message")
        try:
            metadata = {key: codes_get(handle, key) for key in (
                "shortName", "paramId", "units", "typeOfLevel", "stepType",
                "startStep", "endStep", "dataDate", "dataTime", "validityDate",
                "validityTime", "gridType", "Ni", "Nj", "missingValue",
            )}
            if metadata["shortName"] != "tp":
                raise ValueError(f"Expected tp, got {metadata['shortName']!r}")
            if metadata["typeOfLevel"] != "surface":
                raise ValueError(f"Expected surface level, got {metadata['typeOfLevel']!r}")
            if metadata["stepType"] != "accum":
                raise ValueError(f"Expected accumulated precipitation, got {metadata['stepType']!r}")
            if int(metadata["endStep"]) != expected_step:
                raise ValueError(f"GRIB endStep {metadata['endStep']} does not match requested {expected_step}")
            if int(metadata["dataDate"]) != int(expected_run.strftime("%Y%m%d")):
                raise ValueError("GRIB reference date differs from requested run")
            if int(metadata["dataTime"]) != expected_run.hour * 100:
                raise ValueError("GRIB reference time differs from requested run")
            expected_valid = expected_run + timedelta(hours=expected_step)
            valid_time = datetime.strptime(
                f"{int(metadata['validityDate']):08d}{int(metadata['validityTime']):04d}",
                "%Y%m%d%H%M",
            ).replace(tzinfo=UTC)
            if valid_time != expected_valid:
                raise ValueError(f"GRIB validity {valid_time.isoformat()} does not match expected {expected_valid.isoformat()}")
            if metadata["gridType"] != "regular_ll":
                raise ValueError(f"Unsupported GRIB grid {metadata['gridType']!r}; expected regular_ll")
            if expected_model == "ifs" and (int(metadata["paramId"]) != 228 or str(metadata["units"]).strip().lower() != "m"):
                raise ValueError("IFS total precipitation must be parameter 228 with units m")
            if expected_model == "aifs-single" and (int(metadata["paramId"]) != 228228 or str(metadata["units"]).strip().lower().replace(" ", "") != "kgm**-2"):
                raise ValueError("AIFS Single total precipitation must be parameter 228228 with units kg m**-2")
            if int(metadata["startStep"]) < 0 or int(metadata["endStep"]) < int(metadata["startStep"]):
                raise ValueError("GRIB accumulation interval is invalid")
            values = np.asarray(codes_get_array(handle, "values"), dtype=np.float32)
            lats = np.asarray(codes_get_array(handle, "latitudes"), dtype=np.float32)
            lons = np.asarray(codes_get_array(handle, "longitudes"), dtype=np.float32)
            if values.size != int(metadata["Ni"]) * int(metadata["Nj"]):
                raise ValueError("GRIB value count differs from declared grid dimensions")
            if (lats.size != values.size or lons.size != values.size
                    or not np.isfinite(float(metadata["missingValue"]))
                    or not np.all(np.isfinite(lats)) or not np.all(np.isfinite(lons))
                    or np.any(np.abs(lats) > 90) or np.any(np.abs(lons) > 360)):
                raise ValueError("GRIB coordinate or missing-value metadata is invalid")
        finally:
            codes_release(handle)
    return metadata, values, lats, lons


def _render_png(values: np.ndarray, lats: np.ndarray, lons: np.ndarray, width: int, height: int, units: str, missing_value: float, target: Path) -> None:
    values = np.asarray(values, dtype=np.float32)
    lats = np.asarray(lats, dtype=np.float32)
    lons = np.asarray(lons, dtype=np.float32)
    if values.size != width * height or lats.size != values.size or lons.size != values.size:
        raise ValueError("GRIB values and coordinates do not match the declared grid dimensions")
    valid_values = np.isfinite(values) & (values != missing_value)
    millimetres = np.full(values.shape, np.nan, dtype=np.float32)
    millimetres[valid_values] = precipitation_to_mm(values[valid_values], units)
    image_values = np.full((height, width), np.nan, dtype=np.float32)
    x = np.mod(np.rint(np.mod(lons + 180.0, 360.0) * width / 360.0).astype(int), width)
    y = np.clip(np.rint((90.0 - lats) * (height - 1) / 180.0).astype(int), 0, height - 1)
    image_values[y[valid_values], x[valid_values]] = millimetres[valid_values]

    # Fixed, documented display scale: 0–25 mm per forecast accumulation interval.
    stops = np.array([0.0, 1.0, 5.0, 10.0, 25.0], dtype=np.float32)
    colors = np.array([[242, 244, 247, 70], [173, 216, 230, 170], [65, 160, 215, 190], [38, 95, 190, 210], [100, 35, 150, 225]], dtype=np.float32)
    valid = np.isfinite(image_values)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    clipped = np.clip(image_values[valid], stops[0], stops[-1])
    for channel in range(4):
        rgba[:, :, channel][valid] = np.interp(clipped, stops, colors[:, channel]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(target, format="PNG", optimize=True)


class ForecastStore:
    def __init__(self, cache_dir: Path | None = None, source: str | None = None):
        self.cache_dir = cache_dir or Path(os.getenv("RAINSCOPE_CACHE_DIR", "data/cache"))
        configured_source = source or os.getenv("ECMWF_SOURCE", "ecmwf")
        if configured_source not in {"ecmwf", "aws"}:
            raise ValueError("ECMWF_SOURCE must be either 'ecmwf' or 'aws'")
        self.source = configured_source
        self._lock_guard = threading.Lock()
        self._key_locks: dict[tuple[str, str, int], tuple[threading.Lock, int]] = {}

    def get(self, model: str, run: datetime, step: int) -> Forecast:
        key = (model, run.isoformat(), step)
        with self._lock_guard:
            lock, users = self._key_locks.get(key, (threading.Lock(), 0))
            self._key_locks[key] = (lock, users + 1)
        try:
            with lock:
                return self._get_locked(model, run, step)
        finally:
            with self._lock_guard:
                current_lock, users = self._key_locks[key]
                if users <= 1:
                    self._key_locks.pop(key, None)
                else:
                    self._key_locks[key] = (current_lock, users - 1)

    def _get_locked(self, model: str, run: datetime, step: int) -> Forecast:
        if model not in MODELS:
            raise ValueError(f"Unsupported model {model!r}")
        if run.tzinfo is None or run.minute or run.second or run.hour not in (0, 6, 12, 18):
            raise ValueError("Run must be timezone-aware and at 00, 06, 12, or 18 UTC")
        run = run.astimezone(UTC)
        if not supported_step(model, run.hour, step):
            raise ValueError(f"Step {step}h is not documented for {model} at the {run.hour:02d} UTC cycle")

        key = f"{model}-{run:%Y%m%dT%H}z-{step:03d}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Cached runs are retained for Forecast Changes and user inspection.
        # Do not silently evict source GRIBs during an ordinary request.
        grib_path = self.cache_dir / f"{key}.grib2"
        png_path = self.cache_dir / f"{key}.png"
        if grib_path.exists():
            try:
                metadata, values, lats, lons = _message_metadata(grib_path, run, step, model)
                if png_path.exists():
                    with Image.open(png_path) as image:
                        image.verify()
                    with Image.open(png_path) as image:
                        if image.size != (int(metadata["Ni"]), int(metadata["Nj"])):
                            raise ValueError("Cached precipitation raster dimensions do not match the GRIB")
                else:
                    temp_png = png_path.with_name(f".{png_path.name}.tmp")
                    try:
                        _render_png(
                            values, lats, lons, int(metadata["Ni"]), int(metadata["Nj"]),
                            str(metadata["units"]), float(metadata["missingValue"]), temp_png,
                        )
                        try:
                            os.link(temp_png, png_path)
                        except FileExistsError:
                            pass
                    finally:
                        temp_png.unlink(missing_ok=True)
                return self._forecast(model, run, step, metadata, grib_path, png_path, served_from_cache=True)
            except Exception as exc:
                raise ForecastDecodeError(f"Cached forecast failed validation; cache files were preserved: {str(exc)[:200]}") from exc

        config = MODELS[model]
        client = Client(source=self.source, model=config["client_model"], resol=config["resol"], maximum_retries=2, retry_after=2)
        fd, temp_name = tempfile.mkstemp(prefix=f".{key}-", suffix=".grib2", dir=self.cache_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            try:
                client.retrieve(
                    stream=config["stream"], type=config["type"],
                    date=run.strftime("%Y%m%d"), time=run.hour,
                    param="tp", step=step, target=str(temp_path),
                )
            except ValueError as exc:
                if "Cannot find index entries matching" in str(exc):
                    raise ForecastNotPublishedError(str(exc)) from exc
                raise
            except HTTPError as exc:
                response = getattr(exc, "response", None)
                if response is not None and response.status_code in (404, 416):
                    raise ForecastNotPublishedError(f"ECMWF source returned HTTP {response.status_code} for the requested GRIB index/data") from exc
                raise ForecastNetworkError(str(exc)) from exc
            except RequestException as exc:
                raise ForecastNetworkError(str(exc)) from exc
            except OSError as exc:
                raise ForecastNetworkError(str(exc)) from exc
            try:
                metadata, values, lats, lons = _message_metadata(temp_path, run, step, model)
            except Exception as exc:
                raise ForecastDecodeError(f"Retrieved GRIB failed validation: {str(exc)[:200]}") from exc
            width = int(metadata["Ni"])
            height = int(metadata["Nj"])
            temp_png = temp_path.with_suffix(".png")
            try:
                _render_png(values, lats, lons, width, height, str(metadata["units"]), float(metadata["missingValue"]), temp_png)
            except Exception as exc:
                raise ForecastDecodeError(f"Could not render validated GRIB: {str(exc)[:200]}") from exc
            # Atomic, no-clobber publication: concurrent workers or an existing
            # cache entry must never cause user cache data to be overwritten.
            try:
                os.link(temp_path, grib_path)
                os.link(temp_png, png_path)
            except FileExistsError as exc:
                raise ForecastDecodeError("A cache entry appeared during retrieval; preserved the existing files and will validate them on retry") from exc
        finally:
            temp_path.unlink(missing_ok=True)
            temp_path.with_suffix(".png").unlink(missing_ok=True)

        return self._forecast(model, run, step, metadata, grib_path, png_path, served_from_cache=False)

    def _prune_cache(self) -> None:
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - 4 * 86400
        pattern = re.compile(r"-(\d{8}T\d{2})z-\d{3}\.grib2$")
        for grib_path in self.cache_dir.glob("*.grib2"):
            match = pattern.search(grib_path.name)
            if not match:
                continue
            run_time = datetime.strptime(match.group(1), "%Y%m%dT%H").replace(tzinfo=UTC)
            if run_time.timestamp() < cutoff:
                grib_path.unlink(missing_ok=True)
                grib_path.with_suffix(".png").unlink(missing_ok=True)

    def cached_forecasts(
        self, model: str | None = None, forecast_date: date | None = None, cycle: int | None = None,
    ) -> list[Forecast]:
        """Return only locally cached GRIBs that pass the same metadata checks as live data."""
        found: list[Forecast] = []
        pattern = re.compile(r"^(ifs|aifs-single)-(\d{8})T(\d{2})z-(\d{3})\.grib2$")
        if model and forecast_date and cycle is not None:
            candidates = self.cache_dir.glob(f"{model}-{forecast_date:%Y%m%d}T{cycle:02d}z-*.grib2")
        elif model and forecast_date:
            candidates = self.cache_dir.glob(f"{model}-{forecast_date:%Y%m%d}T??z-*.grib2")
        elif forecast_date:
            cycle_pattern = f"{cycle:02d}" if cycle is not None else "??"
            candidates = [path for candidate_model in MODELS
                          for path in self.cache_dir.glob(f"{candidate_model}-{forecast_date:%Y%m%d}T{cycle_pattern}z-*.grib2")]
        elif model:
            candidates = self.cache_dir.glob(f"{model}-*.grib2")
        else:
            candidates = self.cache_dir.glob("*.grib2")
        for path in sorted(candidates):
            match = pattern.match(path.name)
            if not match:
                continue
            model, day, hour, step_text = match.groups()
            if cycle is not None and int(hour) != cycle:
                continue
            run = datetime.strptime(f"{day}{hour}", "%Y%m%d%H").replace(tzinfo=UTC)
            step = int(step_text)
            try:
                metadata, values, lats, lons = _message_metadata(path, run, step, model)
                png_path = path.with_suffix(".png")
                found.append(self._forecast(model, run, step, metadata, path, png_path, served_from_cache=True))
            except Exception:
                continue
        return found

    @staticmethod
    def _forecast(model, run, step, metadata, grib_path, png_path, served_from_cache):
        digest = hashlib.sha256(grib_path.read_bytes()).hexdigest()
        valid_time = datetime.strptime(
            f"{int(metadata['validityDate']):08d}{int(metadata['validityTime']):04d}",
            "%Y%m%d%H%M",
        ).replace(tzinfo=UTC)
        return Forecast(
            model=model, run=run, step=step,
            start_step=int(metadata["startStep"]), end_step=int(metadata["endStep"]),
            valid_time=valid_time, param_id=int(metadata["paramId"]),
            step_type=str(metadata["stepType"]), level_type=str(metadata["typeOfLevel"]),
            grid_type=str(metadata["gridType"]),
            units=str(metadata["units"]), grib_path=grib_path, png_path=png_path,
            sha256=digest, width=int(metadata["Ni"]), height=int(metadata["Nj"]),
            served_from_cache=served_from_cache,
            retrieved_at=datetime.fromtimestamp(grib_path.stat().st_mtime, UTC),
        )


def nearest_grid_point(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> tuple[int, float, float, float]:
    lon_delta = (lons.astype(np.float64) - lon + 180.0) % 360.0 - 180.0
    lat_radians = np.deg2rad(lat)
    distance_squared = (lats.astype(np.float64) - lat) ** 2 + (np.cos(lat_radians) * lon_delta) ** 2
    index = int(np.argmin(distance_squared))
    grid_lat = float(lats[index])
    grid_lon = float((lons[index] + 180.0) % 360.0 - 180.0)
    dlat = np.deg2rad(grid_lat - lat)
    dlon = np.deg2rad((grid_lon - lon + 180.0) % 360.0 - 180.0)
    haversine = np.sin(dlat / 2) ** 2 + np.cos(lat_radians) * np.cos(np.deg2rad(grid_lat)) * np.sin(dlon / 2) ** 2
    distance_km = 6371.0088 * 2 * np.arcsin(np.sqrt(haversine))
    return index, grid_lat, grid_lon, float(distance_km)


def sample_forecast(forecast: Forecast, lat: float, lon: float) -> dict:
    metadata, values, lats, lons = _message_metadata(forecast.grib_path, forecast.run, forecast.step, forecast.model)
    index, grid_lat, grid_lon, distance_km = nearest_grid_point(lats, lons, lat, lon)
    raw_value = float(values[index])
    missing = not np.isfinite(raw_value) or raw_value == float(metadata["missingValue"])
    value_mm = None if missing else float(precipitation_to_mm(np.array([raw_value], dtype=np.float32), forecast.units)[0])
    return {
        "grid_latitude": grid_lat,
        "grid_longitude": grid_lon,
        "grid_distance_km": distance_km,
        "value_mm": value_mm,
        "missing": missing,
    }


def absolute_accumulation_period(forecast: Forecast) -> tuple[datetime, datetime]:
    """Map the GRIB's run-relative accumulation steps onto absolute UTC times."""
    return (
        forecast.run + timedelta(hours=forecast.start_step),
        forecast.run + timedelta(hours=forecast.end_step),
    )


def make_forecast_difference(left: Forecast, right: Forecast, target: Path, limit_mm: float = 25.0) -> tuple[int, int]:
    if left.valid_time != right.valid_time:
        raise ValueError("Forecasts have different validity times")
    left_interval = absolute_accumulation_period(left)
    right_interval = absolute_accumulation_period(right)
    if left_interval != right_interval:
        raise ValueError("Forecast fields cover different absolute accumulation periods")
    if (left.width, left.height, left.grid_type) != (right.width, right.height, right.grid_type):
        raise ValueError("Model grids have different dimensions or grid types")

    left_meta, left_values, left_lats, left_lons = _message_metadata(left.grib_path, left.run, left.step, left.model)
    right_meta, right_values, right_lats, right_lons = _message_metadata(right.grib_path, right.run, right.step, right.model)
    if not np.allclose(left_lats, right_lats, atol=1e-5) or not np.allclose(left_lons, right_lons, atol=1e-5):
        raise ValueError("Model grid coordinates are not aligned")

    valid = (
        np.isfinite(left_values) & np.isfinite(right_values)
        & (left_values != float(left_meta["missingValue"]))
        & (right_values != float(right_meta["missingValue"]))
    )
    difference = np.full(left_values.shape, np.nan, dtype=np.float32)
    left_mm = precipitation_to_mm(left_values[valid], left.units)
    right_mm = precipitation_to_mm(right_values[valid], right.units)
    difference[valid] = right_mm - left_mm

    width, height = left.width, left.height
    image_values = np.full((height, width), np.nan, dtype=np.float32)
    x = np.mod(np.rint(np.mod(left_lons + 180.0, 360.0) * width / 360.0).astype(int), width)
    y = np.clip(np.rint((90.0 - left_lats) * (height - 1) / 180.0).astype(int), 0, height - 1)
    image_values[y[valid], x[valid]] = difference[valid]

    stops = np.array([-limit_mm, -10.0, -1.0, 0.0, 1.0, 10.0, limit_mm], dtype=np.float32)
    colors = np.array([
        [35, 93, 180, 210], [91, 163, 220, 190], [190, 218, 237, 155],
        [245, 245, 240, 145], [247, 218, 202, 155], [226, 120, 92, 190], [164, 40, 54, 210],
    ], dtype=np.float32)
    valid_pixels = np.isfinite(image_values)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    clipped = np.clip(image_values[valid_pixels], -limit_mm, limit_mm)
    for channel in range(4):
        rgba[:, :, channel][valid_pixels] = np.interp(clipped, stops, colors[:, channel]).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(target, format="PNG", optimize=True)
    return int(valid.sum()), int(valid.size)


def make_model_disagreement(ifs: Forecast, aifs: Forecast, target: Path, limit_mm: float = 25.0) -> tuple[int, int]:
    if ifs.run != aifs.run:
        raise ValueError("Model forecasts have different reference times")
    return make_forecast_difference(ifs, aifs, target, limit_mm)
