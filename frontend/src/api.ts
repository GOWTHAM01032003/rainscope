export type ModelId = 'ifs' | 'aifs-single'
export type DeliveryStatus = 'live' | 'cached' | 'mixed'
export type RunSelection = { model: ModelId; date: string; cycle: string; step: number }

export type GribMetadata = {
  param_id: number
  step_type: string
  level_type: string
  grid_type: string
  reference_time_utc: string
  validity_time_utc: string
  start_step: number
  end_step: number
}

export type ForecastMetadata = {
  model: ModelId
  display_name?: string
  mode?: string
  comparison_expression?: string
  sources?: ForecastMetadata[]
  run_time_utc: string
  valid_time_utc: string
  forecast_step_hours: number
  accumulation_interval_hours: [number, number]
  accumulation_label: string
  parameter: 'tp'
  level: 'surface'
  grib_metadata: GribMetadata
  grib_units: string
  display_units: 'mm'
  display_range_mm: [number, number]
  resolution: string
  grid_dimensions: [number, number]
  grib_sha256: string
  source: string
  delivery_status: DeliveryStatus
  retrieved_at_utc: string
  raster_url: string
  attribution: string
  interpretation: string
}

export type PointValue = {
  forecast_step_hours: number
  valid_time_utc: string
  retrieved_at_utc: string
  grib_units: string
  native_grib_accumulation_interval_hours: [number, number]
  display_interval_hours: [number, number]
  interval_amount_mm: number | null
  cumulative_amount_mm: number | null
  derived_from_cumulative_grib_fields: boolean
  missing: boolean
}

export type PointSeries = {
  model: ModelId
  source: 'ECMWF Open Data'
  attribution: string
  run_time_utc: string
  requested_location: { latitude: number; longitude: number }
  grid_location: { latitude: number; longitude: number; distance_from_click_km: number }
  display_units: 'mm'
  delivery_status: DeliveryStatus
  series_method: string
  points: PointValue[]
}

export type ComparisonMetadata = {
  mode: 'model_disagreement'
  model: 'comparison'
  display_name: string
  comparison_expression: 'AIFS Single − IFS (mm)'
  interpretation: string
  run_time_utc: string
  valid_time_utc: string
  forecast_step_hours: number
  accumulation_interval_hours: [number, number]
  accumulation_label: string
  display_units: 'mm difference'
  display_range_mm: [number, number]
  grid_dimensions: [number, number]
  valid_grid_cells: number
  total_grid_cells: number
  delivery_status: DeliveryStatus
  sources: ForecastMetadata[]
  raster_url: string
  attribution: string
}

export type HistoryPair = {
  model: ModelId
  older: { run_time_utc: string; forecast_step_hours: number; sha256: string }
  newer: { run_time_utc: string; forecast_step_hours: number; sha256: string }
  valid_time_utc: string
  older_accumulation_interval_hours: [number, number]
  newer_accumulation_interval_hours: [number, number]
  accumulation_start_time_utc: string
  accumulation_end_time_utc: string
}

export type HistoryCandidates = { cached_fields: number; comparable_pairs: HistoryPair[]; message: string | null }
export type HistoryChange = {
  mode: 'run_to_run_change'
  model: ModelId
  older_run_time_utc: string
  newer_run_time_utc: string
  valid_time_utc: string
  older_accumulation_interval_hours: [number, number]
  newer_accumulation_interval_hours: [number, number]
  accumulation_start_time_utc: string
  accumulation_end_time_utc: string
  display_units: 'mm change'
  expression: 'newer run − older run'
  valid_grid_cells: number
  total_grid_cells: number
  interpretation: string
  raster_url: string
  delivery_status: 'cached'
  older_sha256: string
  newer_sha256: string
}
export type HistoryPointAmount = {
  run_time_utc: string
  retrieved_at_utc: string
  forecast_step_hours: number
  accumulation_interval_hours: [number, number]
  grib_units: string
  value_mm: number | null
  sha256: string
}
export type HistoryPoint = {
  model: ModelId
  valid_time_utc: string
  accumulation_start_time_utc: string
  accumulation_end_time_utc: string
  requested_location: { latitude: number; longitude: number }
  older_grid_location: { latitude: number; longitude: number; distance_from_click_km: number }
  newer_grid_location: { latitude: number; longitude: number; distance_from_click_km: number }
  display_units: 'mm'
  expression: 'newer run − older run'
  older: HistoryPointAmount
  newer: HistoryPointAmount
  difference_mm: number | null
  comparison_available: boolean
  message: string
  delivery_status: 'cached'
}

export type Availability = {
  source: 'validated_local_cache'
  checked_at_utc: string
  model_filter: ModelId | null
  date_filter: string | null
  cycle_filter_utc: number | null
  candidates_require_retrieval_validation: boolean
  entries: Array<{
    model: ModelId
    run_time_utc: string
    forecast_step_hours: number
    valid_time_utc: string
    accumulation_interval_hours: [number, number]
    grib_units: string
    retrieved_at_utc: string
    sha256: string
    delivery_status: 'cached'
  }>
  message: string
}

export function validateAvailability(value: unknown): Availability {
  const fail = (): never => {
    throw new RainScopeRequestError('The API availability response is malformed; no run was selected.', 'invalid_availability_response', 502)
  }
  if (!isRecord(value) || value.source !== 'validated_local_cache'
    || typeof value.checked_at_utc !== 'string' || !Number.isFinite(Date.parse(value.checked_at_utc))
    || value.candidates_require_retrieval_validation !== true
    || typeof value.message !== 'string' || !Array.isArray(value.entries)) return fail()
  for (const entry of value.entries) {
    if (!isRecord(entry) || !['ifs', 'aifs-single'].includes(String(entry.model))
      || typeof entry.run_time_utc !== 'string' || !Number.isFinite(Date.parse(entry.run_time_utc))
      || typeof entry.forecast_step_hours !== 'number' || !Number.isInteger(entry.forecast_step_hours) || entry.forecast_step_hours < 0
      || typeof entry.valid_time_utc !== 'string' || !Number.isFinite(Date.parse(entry.valid_time_utc))
      || !Array.isArray(entry.accumulation_interval_hours) || entry.accumulation_interval_hours.length !== 2
      || !entry.accumulation_interval_hours.every(value=>typeof value==='number'&&Number.isInteger(value))
      || entry.accumulation_interval_hours[0]<0 || entry.accumulation_interval_hours[1]!==entry.forecast_step_hours
      || Date.parse(entry.valid_time_utc)!==Date.parse(entry.run_time_utc)+entry.forecast_step_hours*3600000
      || typeof entry.grib_units !== 'string' || typeof entry.retrieved_at_utc !== 'string'
      || !Number.isFinite(Date.parse(entry.retrieved_at_utc)) || typeof entry.sha256 !== 'string' || !/^[a-f\d]{64}$/i.test(entry.sha256)
      || entry.delivery_status !== 'cached') return fail()
  }
  return value as unknown as Availability
}

export type Catalog = {
  models: Array<{ id: ModelId; label: string; resolution: string; documented_steps_hours: string }>
  field: { short_name: 'tp'; level: 'surface'; meaning: string }
  verified_unit_conventions: Record<ModelId, { param_id: number; grib_units: string; display_units: 'mm'; conversion: string }>
  availability_policy: string
  limitations: string[]
  endpoints: string[]
}
export type Health = { status: 'ok'; service: string }
export type Readiness = { status: 'ready' | 'not_ready'; cache_directory: string; cache_available: boolean; source: string }
export type LocationTimezone = { time_zone: string; matching_time_zones: string[]; boundary_ambiguous: boolean; source: 'offline_timezonefinder'; coordinates_are_sent_to_third_party: false; note: string }

const configuredApiBase = import.meta.env?.VITE_API_BASE_URL?.replace(/\/+$/, '') ?? ''

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${configuredApiBase}${normalizedPath}`
}

export class RainScopeRequestError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code = 'request_failed', status = 0) {
    super(message)
    this.name = 'RainScopeRequestError'
    this.code = code
    this.status = status
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function networkError(): RainScopeRequestError {
  const destination = configuredApiBase ? `configured API ${configuredApiBase}` : 'the same-origin /api route'
  return new RainScopeRequestError(`Could not reach ${destination}. Check that FastAPI is running and that the deployed frontend routes /api to it.`, 'api_unreachable')
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try { response = await fetch(apiUrl(path), init) }
  catch (error) { if (isAbortError(error)) throw error; throw networkError() }
  const contentType = response.headers.get('content-type')?.toLowerCase() ?? ''
  if (!contentType.includes('application/json') && !contentType.includes('+json')) {
    const destination = configuredApiBase ? `configured API ${configuredApiBase}` : 'this site'
    throw new RainScopeRequestError(`The API returned ${contentType || 'an unknown response'} from ${destination}. For a separate frontend and API deployment, set VITE_API_BASE_URL at frontend build time; otherwise route /api to FastAPI on this domain.`, 'api_not_json', response.status)
  }
  if (!response.ok) {
    const payload = await response.clone().json().catch(() => null)
    const error = payload?.error
    const message = typeof error?.message === 'string'
      ? error.message
      : typeof payload?.detail === 'string' ? payload.detail : `RainScope API request failed (${response.status})`
    if (response.status === 404 && (!error?.code || error.code === 'not_found')) {
      const route = path.split('?', 1)[0]
      const mismatch = `The API route ${route} was not found (404). The frontend may be connected to an outdated backend or an incorrect /api proxy. Restart or point the proxy at the RainScope API. Server response: ${message}`
      console.error('[RainScope] API route mismatch', { route, status: response.status, serverMessage: message })
      throw new RainScopeRequestError(mismatch, 'api_route_not_found', response.status)
    }
    throw new RainScopeRequestError(message, typeof error?.code === 'string' ? error.code : 'request_failed', response.status)
  }
  return response
}

export async function apiImage(path: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response
  try { response = await fetch(apiUrl(path), { signal }) }
  catch (error) { if (isAbortError(error)) throw error; throw networkError() }
  if (!response.ok) {
    const payload = await response.clone().json().catch(() => null)
    const error = payload?.error
    throw new RainScopeRequestError(
      typeof error?.message === 'string' ? error.message : `Raster request failed (${response.status})`,
      typeof error?.code === 'string' ? error.code : 'raster_unavailable', response.status,
    )
  }
  const type = response.headers.get('content-type')?.toLowerCase() ?? ''
  if (!type.includes('image/png')) throw new RainScopeRequestError(`Expected a PNG forecast raster, received ${type || 'an unknown content type'}. Check the API URL and /api proxy.`, 'invalid_raster_response', response.status)
  const blob = await response.blob()
  if (!blob.size) throw new RainScopeRequestError('The forecast raster response was empty.', 'empty_raster', response.status)
  return blob
}

async function getJson<T>(path: string, signal?: AbortSignal, cache: RequestCache = 'default'): Promise<T> {
  return await (await apiFetch(path, { signal, cache })).json() as T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function isValidIanaZone(value: unknown): value is string {
  if (typeof value !== 'string' || !value) return false
  try {
    new Intl.DateTimeFormat('en', { timeZone: value }).format(new Date(0))
    return true
  } catch { return false }
}

export function validateTimezoneResponse(value: unknown): LocationTimezone {
  if (!isRecord(value)
    || !isValidIanaZone(value.time_zone)
    || !Array.isArray(value.matching_time_zones)
    || value.matching_time_zones.length === 0
    || !value.matching_time_zones.every(isValidIanaZone)
    || !value.matching_time_zones.includes(value.time_zone)
    || typeof value.boundary_ambiguous !== 'boolean'
    || value.source !== 'offline_timezonefinder'
    || value.coordinates_are_sent_to_third_party !== false
    || typeof value.note !== 'string') {
    throw new RainScopeRequestError('The time-zone service returned an invalid response. RainScope will show UTC for this place.', 'invalid_timezone_response', 502)
  }
  return value as unknown as LocationTimezone
}

export function validateForecastMetadata(value: unknown): ForecastMetadata {
  if (!isRecord(value)
    || !['ifs', 'aifs-single'].includes(String(value.model))
    || typeof value.run_time_utc !== 'string' || !Number.isFinite(Date.parse(value.run_time_utc))
    || typeof value.valid_time_utc !== 'string' || !Number.isFinite(Date.parse(value.valid_time_utc))
    || !Number.isInteger(value.forecast_step_hours)
    || !Array.isArray(value.accumulation_interval_hours) || value.accumulation_interval_hours.length !== 2
    || !value.accumulation_interval_hours.every(Number.isFinite)
    || !isRecord(value.grib_metadata)
    || !Number.isFinite(value.grib_metadata.param_id)
    || typeof value.grib_metadata.step_type !== 'string'
    || typeof value.grib_metadata.level_type !== 'string'
    || typeof value.grib_metadata.grid_type !== 'string'
    || typeof value.grib_units !== 'string'
    || value.display_units !== 'mm'
    || typeof value.accumulation_label !== 'string'
    || typeof value.retrieved_at_utc !== 'string' || !Number.isFinite(Date.parse(value.retrieved_at_utc))
    || typeof value.raster_url !== 'string'
    || !['live', 'cached', 'mixed'].includes(String(value.delivery_status))) {
    throw new RainScopeRequestError('The forecast service returned invalid metadata. RainScope kept the previous selection and did not display this response.', 'invalid_forecast_response', 502)
  }
  return value as unknown as ForecastMetadata
}

export function validatePointSeries(value: unknown): PointSeries {
  const fail = (field: string): never => {
    console.error('[RainScope] Point-series response schema mismatch', {
      route: '/api/point-series', field,
      receivedKeys: isRecord(value) ? Object.keys(value).sort() : [],
    })
    throw new RainScopeRequestError(
      `The point forecast response is malformed (invalid or missing ${field}). Retry the point inspection. If it continues, the API version or /api route may not match this frontend.`,
      'invalid_point_response', 502,
    )
  }
  if (!isRecord(value)) fail('JSON object')
  const body = value as Record<string, unknown>
  if (!['ifs', 'aifs-single'].includes(String(body.model))) fail('model')
  if (!Array.isArray(body.points)) fail('points array')
  if (!isRecord(body.requested_location)) fail('requested_location')
  if (!isRecord(body.grid_location)) fail('grid_location')
  const requested = body.requested_location as Record<string, unknown>
  const grid = body.grid_location as Record<string, unknown>
  if (!Number.isFinite(requested.latitude)) fail('requested_location.latitude')
  if (!Number.isFinite(requested.longitude)) fail('requested_location.longitude')
  if (!Number.isFinite(grid.latitude)) fail('grid_location.latitude')
  if (!Number.isFinite(grid.longitude)) fail('grid_location.longitude')
  if (!Number.isFinite(grid.distance_from_click_km)) fail('grid_location.distance_from_click_km')
  if (typeof body.run_time_utc !== 'string') fail('run_time_utc')
  if (body.display_units !== 'mm') fail('display_units')
  if (!['live', 'cached', 'mixed'].includes(String(body.delivery_status))) fail('delivery_status')
  if (body.source !== 'ECMWF Open Data') fail('source')
  if (typeof body.series_method !== 'string') fail('series_method')
  const validPoint = (point: unknown) => isRecord(point)
    && Number.isInteger(point.forecast_step_hours)
    && typeof point.valid_time_utc === 'string'
    && typeof point.retrieved_at_utc === 'string'
    && Array.isArray(point.display_interval_hours)
    && point.display_interval_hours.length === 2
    && point.display_interval_hours.every(Number.isFinite)
    && Array.isArray(point.native_grib_accumulation_interval_hours)
    && point.native_grib_accumulation_interval_hours.length === 2
    && point.native_grib_accumulation_interval_hours.every(Number.isFinite)
    && typeof point.grib_units === 'string'
    && typeof point.derived_from_cumulative_grib_fields === 'boolean'
    && (point.interval_amount_mm === null || Number.isFinite(point.interval_amount_mm))
    && (point.cumulative_amount_mm === null || Number.isFinite(point.cumulative_amount_mm))
    && typeof point.missing === 'boolean'
  const points = body.points as unknown[]
  const malformedIndex = points.findIndex(point => !validPoint(point))
  if (malformedIndex >= 0) fail(`points[${malformedIndex}] interval fields`)
  return value as unknown as PointSeries
}

function queryString(values: Record<string, string | number | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined) query.set(key, String(value))
  return query.toString()
}

export const rainApi = {
  health: () => getJson<Health>('/api/health'),
  readiness: () => getJson<Readiness>('/api/ready'),
  catalog: () => getJson<Catalog>('/api/catalog'),
  timezone: (lat: number, lon: number, signal?: AbortSignal) =>
    getJson<unknown>(`/api/timezone?${queryString({lat,lon})}`, signal, 'no-store').then(validateTimezoneResponse),
  availability: (query: { model?: ModelId; date?: string; cycle?: number } = {}, signal?: AbortSignal) =>
    getJson<unknown>(`/api/availability?${queryString(query)}`, signal, 'no-store').then(validateAvailability),
  forecast: (run: RunSelection, signal?: AbortSignal) =>
    getJson<unknown>(`/api/forecast?${queryString(run)}`, signal).then(validateForecastMetadata),
  pointSeries: (query: { model: ModelId; date: string; cycle: string; end_step: number; lat: number; lon: number }, signal?: AbortSignal) =>
    getJson<unknown>(`/api/point-series?${queryString(query)}`, signal, 'no-store').then(validatePointSeries),
  comparison: (run: Omit<RunSelection, 'model'>) =>
    getJson<ComparisonMetadata>(`/api/comparison?${queryString(run)}`),
  historyCandidates: () => getJson<HistoryCandidates>('/api/history/candidates'),
  history: (query: Record<string, string | number>) =>
    getJson<HistoryChange>(`/api/history?${queryString(query)}`),
  historyPoint: (query: Record<string, string | number>) =>
    getJson<HistoryPoint>(`/api/history/point?${queryString(query)}`),
}
