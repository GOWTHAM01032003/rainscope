import test from 'node:test'
import assert from 'node:assert/strict'
import { restoreForecastRoute, restoreSavedLocations, restoredLocationSelection, serializeSavedLocations } from '../src/restore.ts'

const now = new Date('2026-11-06T09:15:00Z')

test('root refresh restores a safe current forecast selection', () => {
  const restored = restoreForecastRoute('', now)
  assert.deepEqual(restored, {
    model: 'ifs', date: '2026-11-06', cycle: '06', step: 24,
    requestedLocation: null, notice: null,
  })
})

test('direct deep link restores supported model, run, lead, and a known saved place', () => {
  const path = '/map?model=aifs-single&date=2026-11-05&cycle=06&step=24&location=home'
  const url = new URL(path, 'https://rainscope.test')
  const restored = restoreForecastRoute(url.search, now)
  const saved = restoreSavedLocations(JSON.stringify([{ id: 'home', name: 'Hyderabad', latitude: 17.385, longitude: 78.4867 }]))
  const selection = restoredLocationSelection(restored.requestedLocation, '', saved.locations)
  assert.equal(url.pathname, '/map')
  assert.equal(restored.model, 'aifs-single')
  assert.equal(restored.date, '2026-11-05')
  assert.equal(restored.cycle, '06')
  assert.equal(restored.step, 24)
  assert.equal(selection.id, 'home')
  assert.equal(selection.notice, null)
})

test('the reported Overview URL refresh restores forecast fields and safely handles absent saved-place data', () => {
  const requestedId = 'a26f7963-6fb6-42dd-8a8f-c24e62bc981c'
  const url = new URL(`/?model=aifs-single&date=2026-11-06&cycle=06&step=6&location=${requestedId}`, 'https://rainscope.test')
  const restored = restoreForecastRoute(url.search, now)
  assert.equal(url.pathname, '/')
  assert.deepEqual(
    { model: restored.model, date: restored.date, cycle: restored.cycle, step: restored.step, requestedLocation: restored.requestedLocation },
    { model: 'aifs-single', date: '2026-11-06', cycle: '06', step: 6, requestedLocation: requestedId },
  )

  const noSavedPlaceData = restoreSavedLocations(null)
  const selection = restoredLocationSelection(restored.requestedLocation, '', noSavedPlaceData.locations)
  assert.equal(selection.id, '')
  assert.match(selection.notice, /not in this browser’s saved places/)
  assert.deepEqual(noSavedPlaceData.locations, [])
})

test('a stale shared location falls back to an existing saved choice without deleting places', () => {
  const savedPlace = { id: 'home', name: 'Hyderabad', latitude: 17.385, longitude: 78.4867 }
  const raw = JSON.stringify([savedPlace])
  const saved = restoreSavedLocations(raw)
  const selection = restoredLocationSelection('a26f7963-6fb6-42dd-8a8f-c24e62bc981c', 'home', saved.locations)
  assert.equal(selection.id, 'home')
  assert.match(selection.notice, /not in this browser’s saved places/)
  assert.equal(serializeSavedLocations(saved.locations, saved.preservedEntries), raw)
})

test('invalid or outdated URL and storage state recover without deleting saved bytes', () => {
  const restored = restoreForecastRoute('?model=demo&date=2999-02-31&cycle=18&step=9999&location=not%20an%20id', now)
  assert.equal(restored.model, 'ifs')
  assert.equal(restored.date, '2026-11-06')
  assert.equal(restored.cycle, '06')
  assert.ok(restored.notice)

  const valid = { id: 'home', name: 'Home', latitude: 10, longitude: 20 }
  const invalid = { id: 'bad', name: 'Invalid coordinates', latitude: 999, longitude: 20 }
  const storage = restoreSavedLocations(JSON.stringify([valid, invalid, null]))
  assert.deepEqual(storage.locations.map(item => item.id), ['home'])
  assert.deepEqual(storage.preservedEntries, [invalid, null])
  assert.deepEqual(JSON.parse(serializeSavedLocations(storage.locations, storage.preservedEntries)), [valid, invalid, null])
  assert.equal(restoredLocationSelection('missing', '', storage.locations).id, 'home')

  const malformedRaw = '{not json'
  const malformed = restoreSavedLocations(malformedRaw)
  assert.equal(malformed.canPersist, false)
  assert.equal(malformed.locations.length, 0)
  assert.equal(malformedRaw, '{not json')
})
