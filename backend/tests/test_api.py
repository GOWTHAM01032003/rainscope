from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.errors import ForecastDecodeError, ForecastNetworkError, ForecastNotPublishedError
from backend.app.data import Forecast
from backend.app.main import app


client = TestClient(app, base_url="http://localhost")


def test_health_readiness_and_catalog_have_typed_capabilities():
    assert client.get("/api/health").json() == {"status": "ok", "service": "rainscope-api"}
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    assert set(ready.json()) == {"status", "cache_directory", "cache_available", "source"}
    assert ready.json()["status"] == ("ready" if ready.json()["cache_available"] else "not_ready")
    body = client.get("/api/catalog").json()
    assert {item["id"] for item in body["models"]} == {"ifs", "aifs-single"}
    assert body["field"]["short_name"] == "tp"
    assert "accumulated" in body["field"]["meaning"]
    assert "/api/availability" in body["endpoints"]


def test_unknown_hosts_are_rejected_by_the_api():
    response = client.get("/api/health", headers={"host": "untrusted.invalid"})
    assert response.status_code == 400


def test_render_and_local_probe_hosts_are_allowed_without_wildcards():
    from fastapi import FastAPI
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient as StarletteTestClient

    from backend.app.main import trusted_host_allowlist

    hosts = trusted_host_allowlist({"RENDER_EXTERNAL_HOSTNAME": "rainscope-api.onrender.com"})
    assert {"rainscope-api.onrender.com", "127.0.0.1", "localhost", "testserver"}.issubset(hosts)
    assert "*" not in hosts
    import pytest
    with pytest.raises(RuntimeError, match="wildcard hosts are not allowed"):
        trusted_host_allowlist({"RAINSCOPE_ALLOWED_HOSTS": "*"})

    probe_app = FastAPI()

    @probe_app.get("/api/health")
    def probe_health():
        return JSONResponse({"status": "ok", "service": "rainscope-api"})

    guarded_probe = TrustedHostMiddleware(probe_app, allowed_hosts=hosts)
    probe_client = StarletteTestClient(guarded_probe, base_url="http://testserver")
    for hostname in ("rainscope-api.onrender.com", "127.0.0.1", "localhost", "testserver"):
        assert probe_client.get("/api/health", headers={"host": hostname}).status_code == 200
    assert probe_client.get("/api/health", headers={"host": "untrusted.invalid"}).status_code == 400


def test_health_is_live_with_an_empty_forecast_cache(monkeypatch):
    monkeypatch.setattr(main.store, "cached_forecasts", lambda **_kwargs: [])
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "rainscope-api"}
    availability = client.get("/api/availability")
    assert availability.status_code == 200
    assert availability.json()["entries"] == []


def test_timezone_lookup_returns_offline_iana_zone_without_forwarding_coordinates(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "timezones_for_coordinates", lambda lat, lon: seen.append((lat, lon)) or ("Asia/Kolkata",))
    response = client.get("/api/timezone?lat=22.57261234&lon=88.36391234")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["time_zone"] == "Asia/Kolkata"
    assert response.json()["coordinates_are_sent_to_third_party"] is False
    assert response.json()["matching_time_zones"] == ["Asia/Kolkata"]
    assert response.json()["boundary_ambiguous"] is False
    assert seen == [(22.57261234, 88.36391234)]


def test_timezone_lookup_has_structured_unavailable_and_invalid_coordinate_errors(monkeypatch):
    from backend.app.timezones import TimezoneLookupUnavailable

    def unavailable(*_):
        raise TimezoneLookupUnavailable("offline zone data unavailable")

    monkeypatch.setattr(main, "timezones_for_coordinates", unavailable)
    response = client.get("/api/timezone?lat=10&lon=20")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "timezone_lookup_unavailable"
    invalid = client.get("/api/timezone?lat=91&lon=20")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"


def test_timezone_lookup_with_no_matching_zone_returns_explicit_not_found(monkeypatch):
    monkeypatch.setattr(main, "timezones_for_coordinates", lambda *_: ())
    response = client.get("/api/timezone?lat=0&lon=0")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "timezone_not_found"
    assert "Show UTC" in response.json()["error"]["message"]


def test_timezone_lookup_surfaces_multiple_matching_boundary_zones(monkeypatch):
    monkeypatch.setattr(main, "timezones_for_coordinates", lambda *_: ("America/Vancouver", "America/Los_Angeles"))
    response = client.get("/api/timezone?lat=49.0&lon=-123.0")
    assert response.status_code == 200
    assert response.json()["time_zone"] == "America/Vancouver"
    assert response.json()["matching_time_zones"] == ["America/Vancouver", "America/Los_Angeles"]
    assert response.json()["boundary_ambiguous"] is True


def test_offline_timezonefinder_resolves_kochi_and_daylight_saving_location():
    import pytest

    pytest.importorskip("timezonefinder", reason="timezonefinder must be installed in the active RainScope environment")
    from backend.app.timezones import timezones_for_coordinates

    assert "Asia/Kolkata" in timezones_for_coordinates(9.9312, 76.2673)
    assert "America/New_York" in timezones_for_coordinates(40.7128, -74.0060)


def test_availability_reports_only_validated_local_cache(monkeypatch):
    monkeypatch.setattr(main.store, "cached_forecasts", lambda **_kwargs: [])
    response = client.get("/api/availability?model=ifs&date=2026-11-05&cycle=6")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "validated_local_cache"
    assert body["entries"] == []
    assert body["candidates_require_retrieval_validation"] is True
    assert "unverified" in body["message"]


def test_availability_scopes_cache_inspection_and_prevents_stale_http_responses(monkeypatch):
    seen = []
    monkeypatch.setattr(main.store, "cached_forecasts", lambda **filters: seen.append(filters) or [])
    response = client.get("/api/availability?model=ifs&date=2026-11-06&cycle=12")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert seen == [{"model": "ifs", "forecast_date": datetime(2026, 11, 6).date(), "cycle": 12}]


def test_forecast_metadata_route_returns_typed_provenance(monkeypatch, tmp_path):
    run = datetime(2026, 11, 5, 6, tzinfo=UTC)
    forecast = Forecast(
        model="ifs", run=run, step=24, start_step=0, end_step=24,
        valid_time=datetime(2026, 11, 6, 6, tzinfo=UTC), param_id=228,
        step_type="accum", level_type="surface", grid_type="regular_ll", units="m",
        grib_path=tmp_path / "ifs.grib2", png_path=tmp_path / "ifs.png", sha256="a" * 64,
        width=1440, height=721, served_from_cache=True, retrieved_at=run,
    )
    monkeypatch.setattr(main.store, "get", lambda *args, **kwargs: forecast)
    response = client.get("/api/forecast?model=ifs&date=2026-11-05&cycle=6&step=24")
    assert response.status_code == 200
    body = response.json()
    assert body["delivery_status"] == "cached"
    assert body["grib_metadata"]["param_id"] == 228
    assert body["grib_units"] == "m" and body["display_units"] == "mm"
    assert body["accumulation_interval_hours"] == [0, 24]
    assert body["valid_time_utc"] == "2026-11-06T06:00:00Z"


def test_future_forecast_cycle_is_rejected_before_retrieval(monkeypatch):
    def should_not_retrieve(*args, **kwargs):
        raise AssertionError("future cycle must be rejected first")

    monkeypatch.setattr(main.store, "get", should_not_retrieve)
    response = client.get("/api/forecast?model=ifs&date=2999-01-01&cycle=0&step=24")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "future_run"


def test_invalid_request_errors_are_structured():
    response = client.get("/api/forecast?model=demo&date=2026-11-05&cycle=6&step=24")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["context"]["fields"]


def test_api_rejects_steps_not_supported_for_model_and_cycle(monkeypatch):
    def should_not_retrieve(*args, **kwargs):
        raise AssertionError("unsupported lead must be rejected before retrieval")
    monkeypatch.setattr(main.store, "get", should_not_retrieve)
    aifs_three_hour = client.get("/api/forecast?model=aifs-single&date=2026-11-05&cycle=6&step=3")
    ifs_late_step_06z = client.get("/api/forecast?model=ifs&date=2026-11-05&cycle=6&step=150")
    assert aifs_three_hour.status_code == 422
    assert ifs_late_step_06z.status_code == 422
    assert aifs_three_hour.json()["error"]["code"] == "invalid_request"


def test_unpublished_network_and_decode_failures_have_distinct_codes(monkeypatch):
    cases = [
        (ForecastNotPublishedError("missing"), 404, "run_unpublished"),
        (ForecastNetworkError("timeout"), 503, "upstream_network_error"),
        (ForecastDecodeError("bad GRIB"), 502, "grib_decode_error"),
    ]
    for exception, status, code in cases:
        monkeypatch.setattr(main.store, "get", lambda *args, exc=exception, **kwargs: (_ for _ in ()).throw(exc))
        response = client.get("/api/forecast?model=ifs&date=2026-11-04&cycle=6&step=24")
        assert response.status_code == status
        assert response.json()["error"]["code"] == code


def test_comparison_requires_matching_valid_times_and_intervals(monkeypatch, tmp_path):
    run = datetime(2026, 11, 5, 6, tzinfo=UTC)

    def fake_get(model, requested_run, step):
        return SimpleNamespace(
            model=model, run=requested_run, step=step, start_step=0, end_step=24,
            valid_time=requested_run.replace(day=6, hour=6 if model == "ifs" else 7),
            param_id=228 if model == "ifs" else 228228,
            step_type="accum", level_type="surface", grid_type="regular_ll",
            units="m" if model == "ifs" else "kg m**-2", width=1440, height=721,
            sha256="a" * 64, served_from_cache=True, retrieved_at=run,
            grib_path=tmp_path / f"{model}.grib2", png_path=tmp_path / f"{model}.png",
        )

    monkeypatch.setattr(main.store, "get", fake_get)
    monkeypatch.setattr(main.store, "cache_dir", tmp_path)
    response = client.get("/api/comparison?date=2026-11-05&cycle=6&step=24")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "comparison_not_aligned"


def test_history_empty_state_and_missing_run_do_not_download(monkeypatch):
    monkeypatch.setattr(main.store, "cached_forecasts", lambda: [])
    candidates = client.get("/api/history/candidates")
    assert candidates.status_code == 200
    body = candidates.json()
    assert body["cached_fields"] == 0
    assert body["comparable_pairs"] == []
    assert "No two cached" in body["message"]
    response = client.get("/api/history?model=ifs&older_date=2026-11-04&older_cycle=0&older_step=24&newer_date=2026-11-05&newer_cycle=0&newer_step=24")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "history_cache_missing"


def test_history_rejects_steps_outside_six_hour_increments():
    response = client.get("/api/history?model=ifs&older_date=2026-11-04&older_cycle=0&older_step=25&newer_date=2026-11-05&newer_cycle=0&newer_step=24")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_history_pairs_align_absolute_accumulation_windows_and_sample_cached_points(monkeypatch, tmp_path):
    older_run=datetime(2026,11,5,0,tzinfo=UTC)
    newer_run=datetime(2026,11,5,6,tzinfo=UTC)
    valid=datetime(2026,11,6,6,tzinfo=UTC)
    def field(run,step,start,end,sha):
        return Forecast(model="ifs",run=run,step=step,start_step=start,end_step=end,valid_time=valid,
            param_id=228,step_type="accum",level_type="surface",grid_type="regular_ll",units="m",
            grib_path=tmp_path/f"{sha}.grib2",png_path=tmp_path/f"{sha}.png",sha256=sha*64,
            width=1440,height=721,served_from_cache=True,retrieved_at=run)
    older=field(older_run,30,24,30,"a")
    newer=field(newer_run,24,18,24,"b")
    monkeypatch.setattr(main.store,"cached_forecasts",lambda:[older,newer])
    candidates=client.get("/api/history/candidates").json()
    assert len(candidates["comparable_pairs"])==1
    pair=candidates["comparable_pairs"][0]
    assert pair["older_accumulation_interval_hours"]==[24,30]
    assert pair["newer_accumulation_interval_hours"]==[18,24]
    assert pair["accumulation_start_time_utc"]=="2026-11-06T00:00:00Z"
    monkeypatch.setattr(main,"sample_forecast",lambda forecast,lat,lon:{
        "grid_latitude":12.0,"grid_longitude":78.0,"grid_distance_km":3.2,
        "value_mm":10.0 if forecast.run==older_run else 14.0,"missing":False})
    response=client.get("/api/history/point?model=ifs&older_date=2026-11-05&older_cycle=0&older_step=30&newer_date=2026-11-05&newer_cycle=6&newer_step=24&lat=12.1&lon=78.2")
    assert response.status_code==200
    body=response.json()
    assert body["comparison_available"] is True
    assert body["older"]["value_mm"]==10.0 and body["newer"]["value_mm"]==14.0
    assert body["difference_mm"]==4.0
    assert body["delivery_status"]=="cached"


def test_history_does_not_compare_different_absolute_periods(monkeypatch, tmp_path):
    run=datetime(2026,11,5,0,tzinfo=UTC)
    def field(step,start,end,sha):
        return Forecast(model="ifs",run=run.replace(hour=0 if step==30 else 6),step=step,start_step=start,end_step=end,
            valid_time=datetime(2026,11,6,6,tzinfo=UTC),param_id=228,step_type="accum",level_type="surface",
            grid_type="regular_ll",units="m",grib_path=tmp_path/f"{sha}.grib2",png_path=tmp_path/f"{sha}.png",
            sha256=sha*64,width=1440,height=721,served_from_cache=True,retrieved_at=run)
    older=field(30,24,30,"c")
    newer=field(24,0,24,"d") # same validity, but the older accumulation starts later
    monkeypatch.setattr(main.store,"cached_forecasts",lambda:[older,newer])
    candidates=client.get("/api/history/candidates").json()
    assert candidates["comparable_pairs"]==[]
    response=client.get("/api/history/point?model=ifs&older_date=2026-11-05&older_cycle=0&older_step=30&newer_date=2026-11-05&newer_cycle=6&newer_step=24&lat=12&lon=78")
    assert response.status_code==409
    assert response.json()["error"]["code"]=="comparison_not_aligned"


def test_point_series_includes_field_units_retrieval_time_and_source_attribution(monkeypatch, tmp_path):
    run=datetime(2026,11,5,6,tzinfo=UTC)
    forecast=Forecast(model="ifs",run=run,step=6,start_step=0,end_step=6,
        valid_time=datetime(2026,11,5,12,tzinfo=UTC),param_id=228,step_type="accum",
        level_type="surface",grid_type="regular_ll",units="m",grib_path=tmp_path/"point.grib2",
        png_path=tmp_path/"point.png",sha256="e"*64,width=1440,height=721,
        served_from_cache=True,retrieved_at=datetime(2026,11,5,7,tzinfo=UTC))
    monkeypatch.setattr(main.store,"get",lambda *args,**kwargs:forecast)
    monkeypatch.setattr(main,"sample_forecast",lambda *args,**kwargs:{
        "grid_latitude":12.0,"grid_longitude":78.0,"grid_distance_km":3.2,"value_mm":2.0,"missing":False})
    response=client.get("/api/point-series?model=ifs&date=2026-11-05&cycle=6&end_step=6&lat=12.1&lon=78.2")
    assert response.status_code==200
    assert response.headers["cache-control"] == "no-store"
    body=response.json()
    assert set(body) == {
        "model", "source", "attribution", "run_time_utc", "requested_location", "grid_location",
        "display_units", "delivery_status", "series_method", "points",
    }
    assert body["source"]=="ECMWF Open Data"
    assert "ECMWF" in body["attribution"] and "CC BY 4.0" in body["attribution"]
    assert body["requested_location"] == {"latitude":12.1,"longitude":78.2}
    assert body["grid_location"] == {"latitude":12.0,"longitude":78.0,"distance_from_click_km":3.2}
    assert body["display_units"] == "mm" and body["delivery_status"] == "cached"
    assert body["series_method"]
    assert body["points"][0]["grib_units"]=="m"
    assert body["points"][0]["retrieved_at_utc"]=="2026-11-05T07:00:00Z"
    assert body["points"][0]["display_interval_hours"] == [0,6]
    assert body["points"][0]["interval_amount_mm"] == 2.0


def test_point_forecast_succeeds_independently_when_timezone_lookup_is_unavailable(monkeypatch, tmp_path):
    from backend.app.timezones import TimezoneLookupUnavailable
    run = datetime(2026, 11, 5, 6, tzinfo=UTC)
    forecast = Forecast(model="ifs", run=run, step=6, start_step=0, end_step=6,
        valid_time=datetime(2026, 11, 5, 12, tzinfo=UTC), param_id=228, step_type="accum",
        level_type="surface", grid_type="regular_ll", units="m", grib_path=tmp_path/"independent.grib2",
        png_path=tmp_path/"independent.png", sha256="9"*64, width=1440, height=721,
        served_from_cache=True, retrieved_at=run)
    monkeypatch.setattr(main, "timezones_for_coordinates", lambda *_: (_ for _ in ()).throw(TimezoneLookupUnavailable("zone unavailable")))
    monkeypatch.setattr(main.store, "get", lambda *args, **kwargs: forecast)
    monkeypatch.setattr(main, "sample_forecast", lambda *args, **kwargs: {
        "grid_latitude": 12.0, "grid_longitude": 78.0, "grid_distance_km": 3.2,
        "value_mm": 2.0, "missing": False,
    })
    timezone = client.get("/api/timezone?lat=12.1&lon=78.2")
    point = client.get("/api/point-series?model=ifs&date=2026-11-05&cycle=6&end_step=6&lat=12.1&lon=78.2")
    assert timezone.status_code == 503
    assert timezone.json()["error"]["code"] == "timezone_lookup_unavailable"
    assert point.status_code == 200
    body = point.json()
    assert body["requested_location"] == {"latitude": 12.1, "longitude": 78.2}
    assert body["points"][0]["display_interval_hours"] == [0, 6]
    assert body["points"][0]["interval_amount_mm"] == 2.0


def test_point_forecast_unpublished_step_returns_structured_not_found(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise ForecastNotPublishedError("not published")

    monkeypatch.setattr(main.store, "get", unavailable)
    response = client.get("/api/point-series?model=ifs&date=2026-11-05&cycle=6&end_step=6&lat=12.1&lon=78.2")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "run_unpublished"
    assert response.json()["error"]["context"] == {
        "model": "ifs", "run_time_utc": "2026-11-05T06:00:00+00:00", "step_hours": 6,
    }


def test_point_series_preserves_decoded_precision_for_display_rounding(monkeypatch, tmp_path):
    run = datetime(2026, 11, 5, 6, tzinfo=UTC)
    forecast = Forecast(
        model="ifs", run=run, step=6, start_step=0, end_step=6,
        valid_time=datetime(2026, 11, 5, 12, tzinfo=UTC), param_id=228,
        step_type="accum", level_type="surface", grid_type="regular_ll", units="m",
        grib_path=tmp_path / "precision.grib2", png_path=tmp_path / "precision.png",
        sha256="f" * 64, width=1440, height=721, served_from_cache=True,
        retrieved_at=run,
    )
    monkeypatch.setattr(main.store, "get", lambda *args, **kwargs: forecast)
    monkeypatch.setattr(main, "sample_forecast", lambda *args, **kwargs: {
        "grid_latitude": 12.0, "grid_longitude": 78.0, "grid_distance_km": 3.2,
        "value_mm": 0.0042, "missing": False,
    })
    response = client.get("/api/point-series?model=ifs&date=2026-11-05&cycle=6&end_step=6&lat=12.1&lon=78.2")
    assert response.status_code == 200
    point = response.json()["points"][0]
    assert point["interval_amount_mm"] == 0.0042
    assert point["cumulative_amount_mm"] == 0.0042
