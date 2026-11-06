"""End-to-end contract check for Vite's /api proxy and this FastAPI app.

Uses the checked-in, already validated ECMWF cache. It does not synthesize a
forecast and skips when that source field or the session's local test ports
are unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_PORT = 8000
VITE_PORT = 5173
SELECTED_MODEL = "ifs"
SELECTED_DATE = "2026-11-06"
SELECTED_CYCLE = "06"
SELECTED_LATITUDE = 17.3840
SELECTED_LONGITUDE = 78.4564
SELECTED_PLACE = f"lat={SELECTED_LATITUDE}&lon={SELECTED_LONGITUDE}"
OVERVIEW_PATH = (
    f"/api/point-series?model={SELECTED_MODEL}&date={SELECTED_DATE}&cycle={SELECTED_CYCLE}"
    f"&end_step=24&{SELECTED_PLACE}"
)
PLANNER_PATH = (
    f"/api/point-series?model={SELECTED_MODEL}&date={SELECTED_DATE}&cycle={SELECTED_CYCLE}"
    f"&end_step=6&{SELECTED_PLACE}"
)


def _port_is_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _get(url: str, timeout: float = 4) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read()
    except urllib.error.HTTPError as response:
        return response.code, {key.lower(): value for key, value in response.headers.items()}, response.read()


def test_vite_proxy_exact_hyderabad_outlook_planner_and_timezone_contract():
    if not shutil.which("npm"):
        pytest.skip("npm is required to verify the Vite proxy")
    if not shutil.which("node"):
        pytest.skip("Node.js is required to run the frontend response validator")
    cached_gribs = [PROJECT_ROOT / f"data/cache/ifs-20261106T06z-{step:03d}.grib2" for step in (6, 12, 18, 24)]
    if not all(path.is_file() for path in cached_gribs):
        pytest.skip("the validated IFS GRIB sequence needed for the selected-place and planner proxy check is not cached")
    if not _port_is_free(BACKEND_PORT) or not _port_is_free(VITE_PORT):
        pytest.skip("RainScope integration-test port is already occupied; no process was stopped")

    env = os.environ.copy()
    env["RAINSCOPE_API_PROXY_TARGET"] = f"http://127.0.0.1:{BACKEND_PORT}"
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    frontend = None
    try:
        api_origin = f"http://127.0.0.1:{BACKEND_PORT}"
        proxy_origin = f"http://127.0.0.1:{VITE_PORT}"
        api_ready = False
        for _ in range(80):
            if backend.poll() is not None:
                pytest.fail("project FastAPI process exited before readiness")
            try:
                status, _, _ = _get(f"{api_origin}/api/health", timeout=0.5)
                if status == 200:
                    api_ready = True
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.15)
        assert api_ready, "project FastAPI process did not become ready"
        assert Path(f"/proc/{backend.pid}/cwd").resolve() == PROJECT_ROOT

        frontend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=PROJECT_ROOT / "frontend", env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proxy_ready = False
        for _ in range(100):
            if frontend.poll() is not None:
                pytest.fail("Vite process exited before readiness")
            try:
                status, _, _ = _get(f"{proxy_origin}/api/health", timeout=0.5)
                if status == 200:
                    proxy_ready = True
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        assert proxy_ready, "Vite /api proxy did not reach this FastAPI process"

        ready_status, ready_headers, ready_body = _get(f"{proxy_origin}/api/ready")
        assert ready_status == 200 and "application/json" in ready_headers.get("content-type", "")
        assert json.loads(ready_body)["cache_available"] is True

        openapi_status, _, openapi_body = _get(f"{api_origin}/openapi.json")
        assert openapi_status == 200
        routes = json.loads(openapi_body)["paths"]
        assert "/api/timezone" in routes
        assert "/api/point-series" in routes

        direct_zone_status, direct_zone_headers, direct_zone_body = _get(
            f"{api_origin}/api/timezone?{SELECTED_PLACE}"
        )
        assert direct_zone_status in (200, 503)
        assert "application/json" in direct_zone_headers.get("content-type", "")
        if direct_zone_status == 503:
            assert json.loads(direct_zone_body)["error"]["code"] == "timezone_lookup_unavailable"
        else:
            direct_zone = json.loads(direct_zone_body)
            assert direct_zone["time_zone"] == "Asia/Kolkata"
            assert direct_zone["coordinates_are_sent_to_third_party"] is False

        availability_status, availability_headers, availability_body = _get(
            f"{proxy_origin}/api/availability?model=ifs&date=2026-11-06&cycle=6", timeout=20,
        )
        assert availability_status == 200 and "application/json" in availability_headers.get("content-type", "")
        availability = json.loads(availability_body)
        assert availability["source"] == "validated_local_cache"
        assert {entry["forecast_step_hours"] for entry in availability["entries"]} >= {6, 12, 18, 24}
        selected_forecast_status, selected_forecast_headers, selected_forecast_body = _get(
            f"{proxy_origin}/api/forecast?model=ifs&date=2026-11-06&cycle=06&step=24", timeout=20,
        )
        assert selected_forecast_status == 200, selected_forecast_body.decode(errors="replace")
        assert "application/json" in selected_forecast_headers.get("content-type", "")
        selected_forecast = json.loads(selected_forecast_body)
        assert selected_forecast["source"] == "ECMWF Open Data"
        assert selected_forecast["model"] == "ifs"
        assert selected_forecast["run_time_utc"] == "2026-11-06T06:00:00Z"
        assert selected_forecast["valid_time_utc"] == "2026-11-07T06:00:00Z"
        assert selected_forecast["accumulation_interval_hours"] == [0, 24]
        assert selected_forecast["grib_units"] == "m" and selected_forecast["display_units"] == "mm"
        raster_path = selected_forecast["raster_url"]
        raster_status, raster_headers, raster_body = _get(f"{proxy_origin}{raster_path}", timeout=20)
        assert raster_status == 200 and "image/png" in raster_headers.get("content-type", "")
        assert raster_body.startswith(b"\x89PNG\r\n\x1a\n") and len(raster_body) > 1000
        aifs_forecast_status, aifs_forecast_headers, aifs_forecast_body = _get(
            f"{proxy_origin}/api/forecast?model=aifs-single&date=2026-11-06&cycle=06&step=24", timeout=20,
        )
        assert aifs_forecast_status == 200, aifs_forecast_body.decode(errors="replace")
        assert "application/json" in aifs_forecast_headers.get("content-type", "")
        aifs_forecast = json.loads(aifs_forecast_body)
        assert aifs_forecast["source"] == "ECMWF Open Data"
        assert aifs_forecast["model"] == "aifs-single"
        assert aifs_forecast["run_time_utc"] == "2026-11-06T06:00:00Z"
        assert aifs_forecast["valid_time_utc"] == "2026-11-07T06:00:00Z"
        assert aifs_forecast["accumulation_interval_hours"] == [0, 24]
        assert aifs_forecast["grib_units"] == "kg m**-2" and aifs_forecast["display_units"] == "mm"
        aifs_raster_status, aifs_raster_headers, aifs_raster_body = _get(
            f"{proxy_origin}{aifs_forecast['raster_url']}", timeout=20,
        )
        assert aifs_raster_status == 200 and "image/png" in aifs_raster_headers.get("content-type", "")
        assert aifs_raster_body.startswith(b"\x89PNG\r\n\x1a\n") and len(aifs_raster_body) > 1000
        aifs_point_status, aifs_point_headers, aifs_point_body = _get(
            f"{proxy_origin}/api/point-series?model=aifs-single&date=2026-11-06&cycle=06&end_step=24&{SELECTED_PLACE}",
            timeout=20,
        )
        assert aifs_point_status == 200 and "application/json" in aifs_point_headers.get("content-type", "")
        aifs_point = json.loads(aifs_point_body)
        assert aifs_point["source"] == "ECMWF Open Data" and aifs_point["model"] == "aifs-single"
        assert aifs_point["run_time_utc"] == "2026-11-06T06:00:00Z" and len(aifs_point["points"]) == 4
        assert [point["display_interval_hours"] for point in aifs_point["points"]] == [[0, 6], [6, 12], [12, 18], [18, 24]]
        assert all(point["grib_units"] == "kg m**-2" for point in aifs_point["points"])
        assert [point["derived_from_cumulative_grib_fields"] for point in aifs_point["points"]] == [False, True, True, True]
        cached_choice = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e",
             "import { latestCachedPlannerRun } from './frontend/src/plannerRuns.ts';"
             "import { cycleStatuses, latestValidatedRun } from './frontend/src/cycleAvailability.ts';"
             "import { validateAvailability } from './frontend/src/api.ts';"
             "let input=''; for await (const chunk of process.stdin) input+=chunk;"
             "const availability=validateAvailability(JSON.parse(input));"
             "const initial=latestValidatedRun(availability,Date.parse('2026-11-06T19:00:00Z'));"
             "const choice=latestCachedPlannerRun(availability.entries,'ifs','2026-11-06T12:00:00Z',24);"
             "const cycles=cycleStatuses(availability,'ifs','2026-11-06',6,Date.parse('2026-11-06T16:00:00Z'));"
             "process.stdout.write(JSON.stringify({initial,choice,cycles}));"],
            cwd=PROJECT_ROOT, input=availability_body.decode(), capture_output=True, text=True, check=True,
        )
        cached_choice_body = json.loads(cached_choice.stdout)
        assert cached_choice_body["initial"] == {
            "model": "ifs", "date": "2026-11-06", "cycle": "06", "step": 24,
        }
        choice = cached_choice_body["choice"]
        assert choice["model"] == "ifs"
        assert choice["run_time_utc"] == "2026-11-06T06:00:00Z"
        assert choice["date"] == "2026-11-06"
        assert choice["cycle"] == "06"
        assert set(choice["available_steps"]) >= {6, 12, 18, 24}
        cycle_status = {item["cycle"]: item for item in cached_choice_body["cycles"]}
        assert cycle_status["06"]["available"] is True
        assert cycle_status["12"]["available"] == any(
            entry["model"] == "ifs" and entry["run_time_utc"] == "2026-11-06T12:00:00Z"
            and entry["forecast_step_hours"] == 6 for entry in availability["entries"]
        )

        cycle12_status, cycle12_headers, cycle12_body = _get(
            f"{proxy_origin}/api/forecast?model=ifs&date=2026-11-06&cycle=12&step=6", timeout=30,
        )
        assert "application/json" in cycle12_headers.get("content-type", "")
        if cycle12_status == 404:
            assert json.loads(cycle12_body)["error"]["code"] == "run_unpublished"
        elif cycle12_status == 200:
            published = json.loads(cycle12_body)
            assert published["source"] == "ECMWF Open Data"
            assert published["model"] == "ifs"
            assert published["run_time_utc"] == "2026-11-06T12:00:00Z"
            assert published["forecast_step_hours"] == 6
            assert published["delivery_status"] in {"cached", "live", "mixed"}
        else:
            assert cycle12_status == 503
            assert json.loads(cycle12_body)["error"]["code"] in {
                "upstream_network_error", "upstream_error",
            }

        for path, expected_steps in ((OVERVIEW_PATH, [6, 12, 18, 24]), (PLANNER_PATH, [6])):
            point_status, point_headers, point_body = _get(f"{proxy_origin}{path}", timeout=20)
            assert point_status == 200, point_body.decode(errors="replace")
            assert "application/json" in point_headers.get("content-type", "")
            assert point_headers.get("Cache-Control", point_headers.get("cache-control")) == "no-store"
            point = json.loads(point_body)
            # These required values prevent an HTML page or an older API schema
            # from being mistaken for a valid point forecast.
            assert point["source"] == "ECMWF Open Data"
            assert point["attribution"] and "ECMWF" in point["attribution"]
            assert point["model"] == SELECTED_MODEL
            assert point["delivery_status"] == "cached"
            assert point["display_units"] == "mm"
            assert point["run_time_utc"] == "2026-11-06T06:00:00Z"
            assert point["requested_location"] == {"latitude": SELECTED_LATITUDE, "longitude": SELECTED_LONGITUDE}
            assert point["grid_location"]["distance_from_click_km"] >= 0
            assert len(point["points"]) == len(expected_steps)
            for item, step in zip(point["points"], expected_steps, strict=True):
                assert item["forecast_step_hours"] == step
                assert item["valid_time_utc"]
                assert item["retrieved_at_utc"]
                assert item["grib_units"] == "m"
                assert item["native_grib_accumulation_interval_hours"] == [0, step]
                assert item["display_interval_hours"][1] == step
                assert isinstance(item["missing"], bool)
                assert item["interval_amount_mm"] is None or isinstance(item["interval_amount_mm"], (int, float))
            validator = subprocess.run(
                ["node", "--experimental-strip-types", "--input-type=module", "-e",
                 "import { validatePointSeries } from './frontend/src/api.ts';"
                 "let input=''; for await (const chunk of process.stdin) input+=chunk;"
                 "const result=validatePointSeries(JSON.parse(input));"
                 "process.stdout.write(JSON.stringify({source:result.source,model:result.model,points:result.points.length}));"],
                cwd=PROJECT_ROOT, input=point_body.decode(), capture_output=True, text=True, check=True,
            )
            assert json.loads(validator.stdout) == {
                "source": "ECMWF Open Data", "model": SELECTED_MODEL, "points": len(expected_steps),
            }
        # A bad model is a structured JSON validation error, not an HTML fallback.
        invalid_status, invalid_headers, invalid_body = _get(
            f"{proxy_origin}/api/point-series?model=unknown&date={SELECTED_DATE}&cycle=06&end_step=6&{SELECTED_PLACE}"
        )
        assert invalid_status == 422 and "application/json" in invalid_headers.get("content-type", "")
        assert json.loads(invalid_body)["error"]["code"] == "invalid_request"

        zone_status, zone_headers, zone_body = _get(
            f"{proxy_origin}/api/timezone?{SELECTED_PLACE}"
        )
        assert zone_status == direct_zone_status
        assert zone_status != 404, zone_body.decode(errors="replace")
        assert zone_headers.get("Cache-Control", zone_headers.get("cache-control")) == "no-store"
        if zone_status == 200:
            zone = json.loads(zone_body)
            assert zone["source"] == "offline_timezonefinder"
            assert zone["coordinates_are_sent_to_third_party"] is False
            assert zone["time_zone"] == "Asia/Kolkata"
        else:
            assert zone_status == 503
            assert json.loads(zone_body)["error"]["code"] == "timezone_lookup_unavailable"
            assert json.loads(zone_body)["error"]["code"] == json.loads(direct_zone_body)["error"]["code"]
    finally:
        for process in (frontend, backend):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
