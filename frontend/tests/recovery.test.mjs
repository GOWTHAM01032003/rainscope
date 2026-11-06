import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const read = path => readFile(new URL(path, import.meta.url), 'utf8')

test('app startup has no full-screen recovery boundary or reload action', async () => {
  const [main, app, styles] = await Promise.all([
    read('../src/main.tsx'), read('../src/App.tsx'), read('../src/style.css'),
  ])
  assert.doesNotMatch(main, /<ErrorBoundary|location\.reload/)
  assert.doesNotMatch(app, /window\.location\.reload/)
  assert.doesNotMatch(styles, /\.app-crash/)
})

test('unexpected app render failures keep a navigable shell and log the stack', async () => {
  const [main, boundary] = await Promise.all([read('../src/main.tsx'), read('../src/AppShellBoundary.tsx')])
  assert.match(main, /<AppShellBoundary><App \/><\/AppShellBoundary>/)
  assert.match(boundary, /className="app-shell"/)
  assert.match(boundary, /aria-label="Main navigation"/)
  assert.match(boundary, /console\.error\('RainScope application render failed', error\)/)
  assert.match(boundary, /className="route-inline-error"/)
  assert.match(boundary, /Your saved places and preferences were left untouched/)
})

test('route render errors stay inside the app shell, log their cause, and can be retried', async () => {
  const [app, boundary, styles] = await Promise.all([read('../src/App.tsx'), read('../src/RouteErrorBoundary.tsx'), read('../src/style.css')])
  const sidebar = app.indexOf('<aside className="sidebar">')
  const sidebarEnd = app.indexOf('</aside>', sidebar)
  const main = app.indexOf('<main className="main-content">')
  const routeBoundary = app.indexOf('<RouteErrorBoundary key={route}')
  assert.ok(sidebar >= 0 && sidebarEnd > sidebar && main > sidebarEnd && routeBoundary > main)
  assert.match(app.slice(routeBoundary), /<RouteErrorBoundary[^>]*>[\s\S]*?<Suspense[\s\S]*?\{selectedPage\(\)\}[\s\S]*?<\/RouteErrorBoundary>/)
  assert.match(boundary, /className="route-inline-error"/)
  assert.match(styles, /\.route-inline-error/)
  assert.match(boundary, /Retry section/)
  assert.match(boundary, /console\.error\(`RainScope route render failed:/)
  assert.doesNotMatch(boundary, /Back to Overview|recoverToOverview|location\.reload/)
})

test('timezone preference changes formatting state only and do not reload or refetch forecast data', async () => {
  const [app, map] = await Promise.all([read('../src/App.tsx'), read('../src/MapCanvas.tsx')])
  assert.match(app, /const saveSettings=\(next:Settings\)=>setSettings\(next\)/)
  assert.match(app, /if\(forecastMatchesSelection\)return;void loadForecast\(\)/)
  assert.match(app, /const pointAlreadyLoaded=Boolean\(cachedPoint&&cachedPoint\.model===model/)
  assert.match(app, /if\(!pointAlreadyLoaded\)inspectPoint\(/)
  assert.match(app, /pointRef\.current=null;setPoint\(null\)/)
  assert.doesNotMatch(app, /location\.reload\(|window\.location\.reload\(|document\.location/)
  assert.match(app, /Display time zone<select value=\{value\.timezone\} onChange=\{e=>save\(\{\.\.\.value,timezone:e\.target\.value as Settings\['timezone'\]\}\)\}/)
  assert.match(map, /let preservedMapView:/)
  assert.match(map, /preservedMapView = \{[\s\S]*?center: \[current\.lng, current\.lat\],[\s\S]*?opacity: preservedMapView\?\.opacity/)
  assert.match(map, /useState\(\(\) => preservedMapView\?\.opacity \?\? 0\.84\)/)
  assert.match(map, /if \(restoreViewOnMount\.current\)/)
})
