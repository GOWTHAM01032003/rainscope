import numpy as np
import pytest
from PIL import Image
from datetime import UTC, datetime
from pathlib import Path
from eccodes import codes_grib_new_from_samples, codes_release, codes_set, codes_set_values, codes_write

from backend.app import data as forecast_data
from backend.app.data import ForecastStore, _message_metadata, _render_png, precipitation_to_mm, supported_step


def write_test_grib(path: Path):
    handle = codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        keys = {
            "Ni": 2, "Nj": 2,
            "latitudeOfFirstGridPointInDegrees": 90,
            "longitudeOfFirstGridPointInDegrees": 0,
            "latitudeOfLastGridPointInDegrees": -90,
            "longitudeOfLastGridPointInDegrees": 180,
            "iDirectionIncrementInDegrees": 180,
            "jDirectionIncrementInDegrees": 180,
            "paramId": 228, "dataDate": 20261105, "dataTime": 600,
            "typeOfLevel": "surface", "stepType": "accum", "startStep": 0, "endStep": 24,
        }
        for key, value in keys.items():
            codes_set(handle, key, value)
        codes_set_values(handle, np.array([0.001, 0.002, 0.003, 0.004]))
        with path.open("wb") as stream:
            codes_write(handle, stream)
    finally:
        codes_release(handle)


def test_metres_of_water_are_converted_to_millimetres():
    result = precipitation_to_mm(np.array([0.0, 0.001, 0.025]), "m")
    np.testing.assert_allclose(result, [0.0, 1.0, 25.0])


def test_mass_per_area_units_are_numerically_mm_equivalent():
    result = precipitation_to_mm(np.array([0.0, 1.0, 25.0]), "kg m**-2")
    np.testing.assert_allclose(result, [0.0, 1.0, 25.0])


def test_unknown_units_are_rejected():
    with pytest.raises(ValueError, match="Unsupported"):
        precipitation_to_mm(np.array([1.0]), "m/s")


def test_forecast_source_configuration_is_restricted_to_supported_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("ECMWF_SOURCE", raising=False)
    assert ForecastStore(cache_dir=tmp_path).source == "ecmwf"
    assert ForecastStore(cache_dir=tmp_path, source="aws").source == "aws"
    with pytest.raises(ValueError, match="ECMWF_SOURCE must be either"):
        ForecastStore(cache_dir=tmp_path, source="https://unexpected.example")
    monkeypatch.setenv("ECMWF_SOURCE", "typo")
    with pytest.raises(ValueError, match="ECMWF_SOURCE must be either"):
        ForecastStore(cache_dir=tmp_path)


def test_documented_model_and_cycle_step_schedules():
    assert supported_step("ifs", 6, 3)
    assert supported_step("ifs", 18, 144)
    assert not supported_step("ifs", 6, 150)
    assert supported_step("ifs", 0, 150)
    assert supported_step("ifs", 12, 360)
    assert not supported_step("ifs", 6, 147)
    assert supported_step("aifs-single", 6, 360)
    assert not supported_step("aifs-single", 6, 3)


def test_raster_converts_metres_and_keeps_missing_values_transparent(tmp_path):
    target = tmp_path / "precip.png"
    values = np.array([0.0, 0.001, 9999.0, np.nan], dtype=np.float32)
    lats = np.array([90.0, 90.0, -90.0, -90.0])
    lons = np.array([-180.0, 0.0, -180.0, 0.0])

    _render_png(values, lats, lons, 2, 2, "m", 9999.0, target)

    with Image.open(target) as image:
        pixels = np.asarray(image.convert("RGBA"))
    assert pixels[0, 0, 3] > 0  # zero rainfall is visible and distinct from missing
    assert pixels[0, 1, 3] > pixels[0, 0, 3]  # 0.001 m was converted to 1 mm
    assert pixels[1, 0, 3] == 0
    assert pixels[1, 1, 3] == 0


def test_grib_metadata_accumulation_and_valid_time_are_verified(tmp_path):
    path = tmp_path / "forecast.grib2"
    write_test_grib(path)
    metadata, values, _, _ = _message_metadata(path, datetime(2026, 11, 5, 6, tzinfo=UTC), 24)
    assert metadata["paramId"] == 228
    assert metadata["units"] == "m"
    assert metadata["stepType"] == "accum"
    assert (metadata["startStep"], metadata["endStep"]) == (0, 24)
    assert datetime.strptime(
        f"{metadata['validityDate']:08d}{metadata['validityTime']:04d}", "%Y%m%d%H%M"
    ).replace(tzinfo=UTC) == datetime(2026, 11, 6, 6, tzinfo=UTC)
    np.testing.assert_allclose(precipitation_to_mm(values, metadata["units"]), [1, 2, 3, 4])
    with pytest.raises(ValueError, match="AIFS Single total precipitation"):
        _message_metadata(path, datetime(2026, 11, 5, 6, tzinfo=UTC), 24, "aifs-single")
    with pytest.raises(ValueError, match="endStep"):
        _message_metadata(path, datetime(2026, 11, 5, 6, tzinfo=UTC), 18, "ifs")


def test_validated_cache_is_reused_without_upstream_retrieval(tmp_path):
    path = tmp_path / "ifs-20261105T06z-024.grib2"
    write_test_grib(path)
    raster = path.with_suffix(".png")
    _render_png(np.array([.001, .002, .003, .004]), np.array([90, 90, -90, -90]),
                np.array([0, 180, 0, 180]), 2, 2, "m", 9999, raster)
    forecast = ForecastStore(cache_dir=tmp_path).get("ifs", datetime(2026, 11, 5, 6, tzinfo=UTC), 24)
    assert forecast.served_from_cache is True
    assert forecast.start_step == 0 and forecast.end_step == 24
    assert forecast.valid_time == datetime(2026, 11, 6, 6, tzinfo=UTC)


def test_cached_forecast_discovery_filters_files_before_grib_validation(tmp_path, monkeypatch):
    selected = tmp_path / "ifs-20261106T06z-006.grib2"
    unrelated = tmp_path / "ifs-20261106T12z-006.grib2"
    selected.write_bytes(b"validated fixture bytes")
    unrelated.write_bytes(b"unrelated bytes")
    inspected = []

    def metadata(path, run, step, model):
        inspected.append(path.name)
        return ({"validityDate": 20261106, "validityTime": 1200, "startStep": 0, "endStep": step,
                 "paramId": 228, "stepType": "accum", "typeOfLevel": "surface", "gridType": "regular_ll",
                 "units": "m", "Ni": 2, "Nj": 2}, np.zeros(4), np.zeros(4), np.zeros(4))

    monkeypatch.setattr(forecast_data, "_message_metadata", metadata)
    found = ForecastStore(cache_dir=tmp_path).cached_forecasts(
        model="ifs", forecast_date=datetime(2026, 11, 6).date(), cycle=6,
    )
    assert [item.step for item in found] == [6]
    assert inspected == [selected.name]
