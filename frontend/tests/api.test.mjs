import test from 'node:test'
import assert from 'node:assert/strict'
import { rainApi, RainScopeRequestError } from '../src/api.ts'

const timezone = {
  time_zone: 'Asia/Kolkata',
  matching_time_zones: ['Asia/Kolkata'],
  boundary_ambiguous: false,
  source: 'offline_timezonefinder',
  coordinates_are_sent_to_third_party: false,
  note: 'Resolved locally.',
}

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

test('a delayed malformed timezone response rejects into the feature-level error path', async () => {
  const originalFetch = globalThis.fetch
  const response = deferred()
  globalThis.fetch = () => response.promise
  try {
    const result = rainApi.timezone(17.385, 78.4867)
    await new Promise(resolve => setTimeout(resolve, 25))
    response.resolve(new Response(JSON.stringify({ ...timezone, matching_time_zones: null }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await assert.rejects(result, error => error instanceof RainScopeRequestError
      && error.code === 'invalid_timezone_response'
      && /show UTC/.test(error.message))
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('availability contract rejects malformed or non-cache results before startup can select them', async () => {
  const originalFetch=globalThis.fetch
  const response={source:'validated_local_cache',checked_at_utc:'2026-11-06T12:00:00Z',model_filter:null,date_filter:null,
    cycle_filter_utc:null,candidates_require_retrieval_validation:true,message:'cached only',entries:[{
      model:'ifs',run_time_utc:'2026-11-06T06:00:00Z',forecast_step_hours:24,valid_time_utc:'2026-11-07T06:00:00Z',
      accumulation_interval_hours:[0,24],grib_units:'m',retrieved_at_utc:'2026-11-06T08:00:00Z',sha256:'a'.repeat(64),delivery_status:'cached'}]}
  try{
    globalThis.fetch=async()=>new Response(JSON.stringify(response),{status:200,headers:{'content-type':'application/json'}})
    assert.equal((await rainApi.availability()).entries.length,1)
    globalThis.fetch=async()=>new Response(JSON.stringify({...response,entries:[{...response.entries[0],delivery_status:'live'}]}),{status:200,headers:{'content-type':'application/json'}})
    await assert.rejects(rainApi.availability(),error=>error instanceof RainScopeRequestError&&error.code==='invalid_availability_response')
    globalThis.fetch=async()=>new Response('<html>Not Found</html>',{status:404,headers:{'content-type':'text/html'}})
    await assert.rejects(rainApi.availability(),error=>error instanceof RainScopeRequestError&&error.code==='api_not_json')
  }finally{globalThis.fetch=originalFetch}
})

test('a failed delayed timezone request remains a structured feature error', async () => {
  const originalFetch = globalThis.fetch
  const response = deferred()
  globalThis.fetch = () => response.promise
  try {
    const result = rainApi.timezone(17.385, 78.4867)
    await new Promise(resolve => setTimeout(resolve, 25))
    response.resolve(new Response(JSON.stringify({ error: { code: 'timezone_lookup_unavailable', message: 'Offline timezone data is unavailable.' } }), {
      status: 503, headers: { 'content-type': 'application/json' },
    }))
    await assert.rejects(result, error => error instanceof RainScopeRequestError
      && error.code === 'timezone_lookup_unavailable'
      && /Offline timezone data/.test(error.message))
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('the Map point timezone request accepts the backend offline-zone response', async () => {
  const originalFetch = globalThis.fetch
  let requestedUrl, requestInit
  globalThis.fetch = async (url, init) => {
    requestedUrl = String(url)
    requestInit = init
    return new Response(JSON.stringify(timezone), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  try {
    const result = await rainApi.timezone(17.385, 78.4867)
    assert.match(requestedUrl, /^\/api\/timezone\?lat=17\.385&lon=78\.4867$/)
    assert.equal(requestInit.cache, 'no-store')
    assert.equal(result.time_zone, 'Asia/Kolkata')
    assert.equal(result.coordinates_are_sent_to_third_party, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a timezone not-found response remains a specific recoverable UTC-fallback error', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ error: {
    code: 'timezone_not_found', message: 'No IANA time zone matched these coordinates. Show UTC for this location.', context: {},
  } }), { status: 404, headers: { 'content-type': 'application/json' } })
  try {
    await assert.rejects(rainApi.timezone(0, 0), error => error instanceof RainScopeRequestError
      && error.code === 'timezone_not_found' && error.status === 404)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a missing timezone API route identifies a stale or misrouted backend and preserves UTC fallback', async () => {
  const originalFetch = globalThis.fetch
  const originalError = console.error
  console.error = () => {}
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Not Found' }), {
    status: 404, headers: { 'content-type': 'application/json' },
  })
  try {
    await assert.rejects(rainApi.timezone(17.385, 78.4867), error => error instanceof RainScopeRequestError
      && error.code === 'api_route_not_found' && error.status === 404
      && /\/api\/timezone/.test(error.message) && /outdated backend|incorrect \/api proxy/.test(error.message))
  } finally {
    globalThis.fetch = originalFetch
    console.error = originalError
  }
})

test('a delayed malformed forecast response is rejected before it can replace the selected forecast', async () => {
  const originalFetch = globalThis.fetch
  const response = deferred()
  globalThis.fetch = () => response.promise
  try {
    const result = rainApi.forecast({ model: 'aifs-single', date: '2026-11-06', cycle: '06', step: 6 })
    await new Promise(resolve => setTimeout(resolve, 25))
    response.resolve(new Response(JSON.stringify({ model: 'aifs-single', delivery_status: 'live' }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await assert.rejects(result, error => error instanceof RainScopeRequestError && error.code === 'invalid_forecast_response')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('a malformed delayed point-series response is rejected before it can enter React state', async () => {
  const originalFetch = globalThis.fetch
  const originalError = console.error
  console.error = () => {}
  const response = deferred()
  globalThis.fetch = () => response.promise
  try {
    const result = rainApi.pointSeries({ model: 'aifs-single', date: '2026-11-06', cycle: '06', end_step: 6, lat: 17.385, lon: 78.4867 })
    await new Promise(resolve => setTimeout(resolve, 25))
    response.resolve(new Response(JSON.stringify({ model: 'aifs-single', points: {}, requested_location: {}, grid_location: {} }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await assert.rejects(result, error => error instanceof RainScopeRequestError
      && error.code === 'invalid_point_response'
      && /Retry the point inspection/.test(error.message))
  } finally {
    globalThis.fetch = originalFetch
    console.error = originalError
  }
})

test('a malformed point response names the incompatible field and logs safe schema diagnostics', async () => {
  const originalFetch = globalThis.fetch
  const originalError = console.error
  const logged = []
  console.error = (...args) => logged.push(args)
  globalThis.fetch = async () => new Response(JSON.stringify({ model: 'aifs-single', points: [],
    requested_location: { latitude: 17.385, longitude: 78.4867 },
    grid_location: { latitude: 17.5, longitude: 78.5, distance_from_click_km: 12.87 },
    run_time_utc: '2026-11-06T06:00:00Z', display_units: 'mm', delivery_status: 'cached',
    series_method: 'sampled', }), { status: 200, headers: { 'content-type': 'application/json' } })
  try {
    await assert.rejects(rainApi.pointSeries({ model: 'aifs-single', date: '2026-11-06', cycle: '06', end_step: 6, lat: 17.385, lon: 78.4867 }),
      error => error instanceof RainScopeRequestError && error.code === 'invalid_point_response'
        && /invalid or missing source/.test(error.message) && /API version/.test(error.message))
    assert.equal(logged[0]?.[0], '[RainScope] Point-series response schema mismatch')
    assert.equal(logged[0]?.[1]?.field, 'source')
  } finally {
    globalThis.fetch = originalFetch
    console.error = originalError
  }
})

test('a valid Map point forecast response preserves the selected coordinates and interval semantics', async () => {
  const originalFetch = globalThis.fetch
  let requestedUrl, requestInit
  const body = {
    model: 'aifs-single', source: 'ECMWF Open Data', attribution: 'ECMWF CC BY 4.0',
    run_time_utc: '2026-11-06T06:00:00Z',
    requested_location: { latitude: 17.385, longitude: 78.4867 },
    grid_location: { latitude: 17.5, longitude: 78.5, distance_from_click_km: 12.87 },
    display_units: 'mm', delivery_status: 'cached', series_method: 'Validated GRIB intervals.',
    points: [{ forecast_step_hours: 6, valid_time_utc: '2026-11-06T12:00:00Z',
      retrieved_at_utc: '2026-11-06T13:10:54Z', grib_units: 'kg m**-2',
      native_grib_accumulation_interval_hours: [0, 6], display_interval_hours: [0, 6],
      interval_amount_mm: 0.578125, cumulative_amount_mm: 0.578125,
      derived_from_cumulative_grib_fields: false, missing: false }],
  }
  globalThis.fetch = async (url, init) => {
    requestedUrl = String(url)
    requestInit = init
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  try {
    const result = await rainApi.pointSeries({ model: 'aifs-single', date: '2026-11-06', cycle: '06', end_step: 6, lat: 17.385, lon: 78.4867 })
    assert.match(requestedUrl, /^\/api\/point-series\?model=aifs-single&date=2026-11-06&cycle=06&end_step=6&lat=17\.385&lon=78\.4867$/)
    assert.equal(requestInit.cache, 'no-store')
    assert.deepEqual(result.requested_location, { latitude: 17.385, longitude: 78.4867 })
    assert.deepEqual(result.points[0].display_interval_hours, [0, 6])
    assert.equal(result.points[0].interval_amount_mm, 0.578125)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('an unpublished Map point interval keeps the structured not-found error', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({ error: {
    code: 'run_unpublished', message: 'ECMWF has not published a required point-series step.', context: { step_hours: 6 },
  } }), { status: 404, headers: { 'content-type': 'application/json' } })
  try {
    await assert.rejects(rainApi.pointSeries({ model: 'aifs-single', date: '2026-11-06', cycle: '06', end_step: 6, lat: 17.385, lon: 78.4867 }),
      error => error instanceof RainScopeRequestError && error.code === 'run_unpublished' && error.status === 404)
  } finally {
    globalThis.fetch = originalFetch
  }
})
