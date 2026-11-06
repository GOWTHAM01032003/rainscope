# RainScope deployment guide

This project includes a local production image setup and a Render Blueprint; it does not provide hosting, ECMWF, tile, or geocoding service guarantees. Local Compose runs two containers: FastAPI/ecCodes and an Nginx static frontend. Nginx serves the React single-page app, routes same-origin `/api` requests to FastAPI, and falls back to `index.html` on deep links. The API runs one worker so its in-process download locks remain effective. A named volume stores validated GRIBs and derived rasters.

## Render Free Blueprint

The root `render.yaml` defines two Free services in Singapore: a Docker FastAPI/ecCodes web service and a static Vite frontend. The frontend build gets the API's public HTTPS URL through a Blueprint service reference; FastAPI gets the static site's exact origin for GET-only CORS and its own assigned hostname for trusted-host validation. Render's web service injects `PORT`; the API image binds to that port (defaulting to 8000 for local Compose). The static site has an SPA rewrite to `index.html` for direct routes and refreshes. No Render secrets or paid resources are required.

To deploy, connect the public GitHub repository in Render, choose **New → Blueprint**, select `GOWTHAM01032003/rainscope` on `main`, review that both services show the **Free** plan, and apply the Blueprint. Render must be authorized to access this repository. Do not add a persistent disk: it is not available to Free web services. The API cache is set to `/tmp/rainscope-cache` and is ephemeral; it may be lost when the instance sleeps, restarts, or redeploys. After a cold start, availability can therefore be empty until a user requests a run and ECMWF returns a GRIB that passes validation. A sleep/wake response may take about a minute. Free web services have limited CPU/RAM and can sleep after inactivity; GRIB decoding and upstream retrieval may be slow or exceed the free instance's resources. Render gives no service uptime guarantee on this plan.

After Render finishes both deployments, verify the frontend root and a deep link, then verify the API service's `/api/health`, `/api/ready`, and `/api/availability`. A live forecast is verified only after requesting a currently available run and receiving a real GRIB that passes RainScope's metadata checks; cached data in a different environment is not proof that the deployed cache contains it.

## Requirements

- Docker Compose v2 or Podman with its Compose provider, and permission to build and run containers.
- Outbound HTTPS from the API container to the selected ECMWF Open Data endpoint. The browser also needs access to the configured basemap and, when searching, Open-Meteo Geocoding.
- Persistent writable storage for the cache volume. Cache size grows as new GRIBs and comparison rasters are retained; set a monitoring and retention policy before sustained production use.
- An HTTPS reverse proxy or platform ingress for public use. Restrict the published web port to loopback and let the ingress terminate TLS and enforce request size, timeouts, rate limits, and the expected public host.

The API runtime is Python 3.12 and installs the pinned `timezonefinder` and timezone boundary data from `requirements.txt`. The GRIB decoder uses the Python `eccodes` package and the image also installs the matching OS ecCodes runtime/data packages. The frontend is built with Node 24 and `npm ci` from the checked-in lockfile. No database, account service, paid API, or client secret is required.

## Configure, build, and run

```sh
cp .env.example .env
# Edit RAINSCOPE_ALLOWED_HOSTS before public ingress; include only the proxy's Host names.
podman compose build
podman compose up -d
podman compose ps
```

Docker users can substitute `docker compose` for `podman compose`. By default, the site binds to `127.0.0.1:8080` and is reachable at `http://127.0.0.1:8080/`. Keep the loopback binding when a local reverse proxy is on the same machine. If a remote ingress must reach the port, set `RAINSCOPE_BIND_ADDRESS` to a private interface, restrict it with host firewall rules, and do not expose the API container port directly. For a public hostname, set `RAINSCOPE_ALLOWED_HOSTS` to the exact hostname(s) that Nginx forwards to FastAPI. Same-origin proxying is the default, so leave `RAINSCOPE_CORS_ORIGINS` blank. If serving the frontend from a separate origin, configure exact origins and build `VITE_API_BASE_URL` to the API origin; never use `*` with user data.

The health check at `/api/health` is process liveness. `/api/ready` confirms that the persistent cache path is writable; neither endpoint claims ECMWF is reachable or that a current forecast exists. After startup, verify:

```sh
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/api/ready
curl -fsS http://127.0.0.1:8080/api/availability
```

Availability reports only locally cached fields that pass GRIB validation. A valid deployment smoke test should also use a known cached forecast request and its raster/point-series routes. Do not treat an empty cache as a zero-rain forecast or a successful page-shell response as a working forecast.

## Configuration

`compose.yaml` reads `.env` if present. `.env.example` has no credentials and lists the supported values:

- `RAINSCOPE_HTTP_PORT`: host-side web port (default `8080`).
- `RAINSCOPE_BIND_ADDRESS`: host bind address (default loopback `127.0.0.1`).
- `RAINSCOPE_ALLOWED_HOSTS`: comma-separated exact HTTP Host names accepted by FastAPI (default local names).
- `RAINSCOPE_CORS_ORIGINS`: optional comma-separated exact browser origins; unnecessary for the same-origin Nginx proxy.
- `ECMWF_SOURCE`: ECMWF Open Data source selector supported by the existing client (`ecmwf` or `aws`).
- `VITE_API_BASE_URL`: optional frontend build-time API origin. Normally blank because Nginx proxies `/api` on the same host.
- `VITE_BASEMAP_STYLE_URL`, `VITE_BASEMAP_TILE_URL`, `VITE_BASEMAP_ATTRIBUTION`: optional build-time basemap override. Configure the corresponding provider's required attribution and honor its current license, usage and caching terms. Do not assume public tile services offer production capacity.

Build-time Vite values are embedded in the static JavaScript. They are public configuration, never secrets. No API keys should be placed there.

## Update, backup, and rollback

Before updating, record the source revision and create a cache backup. Keep a backup outside the live volume and restrict access because it can be large.

```sh
podman compose ps
podman volume ls
mkdir -p backups
# Replace <compose-project> with the prefix shown by `podman volume ls`.
podman run --rm -v <compose-project>_forecast-cache:/cache:ro -v "$PWD/backups:/backup" docker.io/library/busybox:stable \
  tar -czf /backup/rainscope-cache-$(date -u +%Y%m%dT%H%M%SZ).tar.gz -C /cache .
```

Then rebuild and replace the containers without removing the volume:

```sh
podman compose build
podman compose up -d
podman compose ps
curl -fsS http://127.0.0.1:8080/api/ready
```

For rollback, check out the previously recorded source revision, rebuild, and run `podman compose up -d` again. Restore a cache backup only if the cache itself is damaged and after stopping the API; otherwise leave the validated cache untouched. **Do not run `podman compose down -v` or `docker compose down -v` during normal updates or rollback**: that removes the persistent forecast cache. Verify health, readiness, a known cached forecast, and a deep link after each update.

## Operations, providers, and limitations

- Logs are container stdout/stderr (`podman compose logs -f api web`). Avoid logging personal coordinates or environment contents. The app stores saved places and preferences in the user’s browser; the backend cache stores source GRIBs and derived assets.
- Keep one API worker unless request deduplication is moved to a shared lock service. Current locks are process-local. Monitor disk usage; no automatic cache pruning is enabled because cached runs support history comparisons.
- ECMWF Open Data run publication and rolling archive availability vary. RainScope validates a retrieved GRIB's model, parameter, steps, times, grid and units before serving it. There is no guaranteed refresh schedule, radar, observed rainfall, official warning, calibrated probability, or flood forecast.
- ECMWF requires source acknowledgement and provides Open Data under CC BY 4.0; review current [ECMWF Open Data terms](https://apps.ecmwf.int/datasets/licences/general/) and [catalogue](https://www.ecmwf.int/en/forecasts/datasets/open-data) before use.
- The default basemap uses the public OpenFreeMap Liberty style. Review its current [terms](https://openfreemap.org/tos/); the public service is provided as-is without an availability guarantee. Map requests happen from the viewer's browser.
- Place search uses Open-Meteo Geocoding and GeoNames attribution. Its current [terms](https://open-meteo.com/en/terms) and licensing/usage conditions apply; the documented free API is non-commercial. Select a different appropriately licensed geocoder before commercial operation. Typing is debounced and does not request a forecast until the user selects a result.
- Offline timezone lookup uses the pinned timezonefinder boundary data in the API container. If those data fail to load, RainScope clearly uses UTC; it does not send coordinates to a third-party timezone service or fall back to the computer timezone.
- `/api/health` and `/api/ready` do not establish upstream readiness. Set up external monitoring for API reachability, cache disk, ECMWF retrieval failures, basemap availability and geocoder limits. Public operation requires an operator to monitor and respond to these failures.
