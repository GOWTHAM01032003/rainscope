import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

test('Overview omits the full saved My location row while Locations retains its management details', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const overviewStart = app.indexOf("if(route==='/'||route==='/map')return <>")
  const overviewCall = app.slice(overviewStart, app.indexOf("{route==='/map'&&<PageHeading", overviewStart))
  const locationsStart = app.indexOf('function LocationsPage(')
  const locationsCall = app.slice(locationsStart, app.indexOf('<section className="panel location-search">', locationsStart))
  assert.doesNotMatch(overviewCall, /<MyLocationSection/)
  assert.match(app, /route==='\/'&&!activeLocation&&<section className="first-visit-panel"[\s\S]+?<MyLocationSection[\s\S]+?hideSavedSummary/)
  assert.doesNotMatch(overviewCall, /Saved location summary/)
  assert.match(locationsCall, /onRemove=\{onRemoveMyLocation\}/)
  assert.doesNotMatch(locationsCall, /hideSavedSummary/)
  assert.match(app, /location\?hideSavedSummary\?<div className="my-location-compact"/)
  assert.match(app, /CoordinateDetails latitude=\{location\.latitude\} longitude=\{location\.longitude\}/)
})

test('clean Overview checks validated cache before loading a forecast and offers a retryable no-data state', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /hasExplicitForecastSelection\?'ready':'checking'/)
  assert.match(app, /rainApi\.availability\(\{\},controller\.signal\)/)
  assert.match(app, /latestValidatedRun\(availability\)/)
  assert.match(app, /if\(initialRunStatus!=='ready'\)return;if\(route==='\/'\|\|route==='\/map'\)/)
  assert.match(app, /No validated run was found/)
  assert.match(app, /Retry validated-run check/)
  assert.match(app, /global basemap stays available, but it is not a precipitation forecast/)
})
