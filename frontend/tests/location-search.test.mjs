import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  deduplicatePlaceResults,
  formatPlaceDisplayName,
  meaningfulLocationCharacters,
  savedLocationFromPlaceResult,
  searchPlaces,
} from '../src/locationSearch.ts'
import { restoreSavedLocations, serializeSavedLocations } from '../src/restore.ts'

const response = results => new Response(JSON.stringify({ results }), { status: 200, headers: { 'Content-Type': 'application/json' } })

test('one- and two-character input sends no geocoding request', async () => {
  let calls = 0
  const fetcher = async () => { calls += 1; return response([]) }
  assert.equal(meaningfulLocationCharacters('  A '), 1)
  assert.deepEqual(await searchPlaces('Hy', new AbortController().signal, fetcher), [])
  assert.deepEqual(await searchPlaces('  L ', new AbortController().signal, fetcher), [])
  assert.equal(calls, 0)
})

test('three meaningful characters trigger the shared geocoder and return a short, labeled result list', async () => {
  let requested
  const results = [
    { id: 1, name: 'Hyderabad', admin1: 'Telangana', country: 'India', latitude: 17.385, longitude: 78.4867 },
    { id: 1, name: 'Hyderabad', admin1: 'Telangana', country: 'India', latitude: 17.385, longitude: 78.4867 },
    { id: 2, name: 'Hyderabad', admin1: 'Sindh', country: 'Pakistan', latitude: 25.396, longitude: 68.3578 },
  ]
  const found = await searchPlaces('Hyd', new AbortController().signal, async (url, init) => {
    requested = { url, init }
    return response(results)
  })
  assert.equal(requested.url.searchParams.get('name'), 'Hyd')
  assert.equal(requested.url.searchParams.get('count'), '5')
  assert.ok(requested.init.signal)
  assert.equal(found.length, 2)
  assert.equal(found[0].displayName, 'Hyderabad, Telangana, India')
  assert.equal(found[1].displayName, 'Hyderabad, Sindh, Pakistan')
})

test('stale geocoder responses are rejected after their request is cancelled', async () => {
  let resolveOld
  const oldController = new AbortController()
  const staleRequest = searchPlaces('Old', oldController.signal, () => new Promise(resolve => { resolveOld = resolve }))
  const currentController = new AbortController()
  const current = await searchPlaces('New', currentController.signal, async () => response([
    { id: 3, name: 'New Delhi', admin1: 'Delhi', country: 'India', latitude: 28.6139, longitude: 77.209 },
  ]))
  oldController.abort()
  resolveOld(response([{ id: 4, name: 'Old Town', country: 'Example', latitude: 1, longitude: 1 }]))
  await assert.rejects(staleRequest, { name: 'AbortError' })
  assert.equal(current[0].displayName, 'New Delhi, Delhi, India')
})

test('display names include available admin and country fields without duplicates', () => {
  assert.equal(formatPlaceDisplayName({ name: 'Hyderabad', admin1: 'Hyderabad', admin2: 'Telangana', country: 'India' }), 'Hyderabad, India')
  assert.equal(formatPlaceDisplayName({ name: 'Small town', admin2: 'District', country: 'Country' }), 'Small town, District, Country')
})

test('selecting a result persists its display name and exact coordinates together', () => {
  const place = { id: 8, name: 'Hyderabad, Telangana, India', latitude: 17.385123, longitude: 78.486712, displayName: 'Hyderabad, Telangana, India' }
  const saved = savedLocationFromPlaceResult(place, 'saved-hyderabad')
  const storage = serializeSavedLocations([saved], [])
  const restored = restoreSavedLocations(storage)
  assert.deepEqual(restored.locations[0], {
    id: 'saved-hyderabad', name: 'Hyderabad, Telangana, India', latitude: 17.385123,
    longitude: 78.486712, kind: 'saved',
  })
})

test('duplicate geocoder results are removed without merging distinct cities that share a name', () => {
  const make = (id, latitude, country) => ({ id, name: 'Hyderabad', displayName: `Hyderabad, ${country}`, latitude, longitude: 1, country })
  assert.equal(deduplicatePlaceResults([make(1, 1, 'India'), make(1, 1, 'India'), make(2, 2, 'Pakistan')]).length, 2)
})

test('the shared picker serves Overview, Compare, Planner, and Locations without fetching forecasts while typing', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const picker = await readFile(new URL('../src/LocationPicker.tsx', import.meta.url), 'utf8')
  assert.ok((app.match(/<LocationPicker /g) ?? []).length >= 4)
  assert.ok(picker.includes('}, 350)'))
  assert.ok(picker.includes('requestId.current'))
  assert.ok(picker.includes('activeController.current?.abort()'))
  assert.doesNotMatch(picker, /rainApi\./)
  assert.ok(picker.includes('onSelectResult(place)'))
})
