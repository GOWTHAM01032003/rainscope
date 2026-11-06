import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { canSelectCycle, cycleStatuses, forecastSelectionUrl, latestValidatedRun } from '../src/cycleAvailability.ts'

const date = '2026-11-06'
const entries = [6, 12, 18, 24].map(step => ({
  model: 'ifs', run_time_utc: `${date}T06:00:00Z`, forecast_step_hours: step,
}))

test('availability distinguishes validated 06 UTC from unvalidated 12 UTC for the selected model, date, and lead', () => {
  const statuses = cycleStatuses({ entries }, 'ifs', date, 6, Date.parse(`${date}T16:00:00Z`))
  assert.deepEqual(statuses.filter(item=>['06','12'].includes(item.cycle)).map(({ cycle, available, future }) => ({ cycle, available, future })), [
    { cycle: '06', available: true, future: false },
    { cycle: '12', available: false, future: false },
  ])
  assert.match(statuses.find(item=>item.cycle==='12').reason, /No validated GRIB/)
})

test('availability is independent of place and requires the exact selected lead', () => {
  const run = cycleStatuses({ entries }, 'ifs', date, 24, Date.parse(`${date}T16:00:00Z`))
  assert.equal(run.find(item=>item.cycle==='06').available, true)
  const missingLead = cycleStatuses({ entries }, 'ifs', date, 30, Date.parse(`${date}T16:00:00Z`))
  assert.equal(missingLead.find(item=>item.cycle==='06').available, false)
  assert.match(missingLead.find(item=>item.cycle==='06').reason, /\+30 h is not among them/)
})

test('cached runs from another model or date never mark the selected cycle available', () => {
  const wrongModel = cycleStatuses({ entries }, 'aifs-single', date, 6, Date.parse(`${date}T16:00:00Z`))
  const wrongDate = cycleStatuses({ entries }, 'ifs', '2026-11-05', 6, Date.parse(`${date}T16:00:00Z`))
  assert.equal(wrongModel.find(item=>item.cycle==='06').available, false)
  assert.equal(wrongDate.find(item=>item.cycle==='06').available, false)
})

test('future cycles cannot be chosen before their UTC run time', () => {
  const statuses = cycleStatuses({ entries: [] }, 'ifs', date, 6, Date.parse(`${date}T09:00:00Z`))
  assert.equal(statuses.find(item=>item.cycle==='12').future, true)
  assert.equal(statuses.find(item=>item.cycle==='12').available, false)
  assert.equal(canSelectCycle(statuses, '12'), false)
})

test('only a validated cycle can be explicitly selected; unavailable cycles leave state untouched', () => {
  const statuses = cycleStatuses({ entries }, 'ifs', date, 6, Date.parse(`${date}T16:00:00Z`))
  assert.equal(canSelectCycle(statuses, '06'), true)
  assert.equal(canSelectCycle(statuses, '12'), false)
})

test('explicit cycle selection serializes the same model, date, cycle, lead, and saved place into the URL', () => {
  assert.equal(
    forecastSelectionUrl('/', { model: 'ifs', date, cycle: '06', step: 6 }, 'hyderabad-id'),
    `/?model=ifs&date=${date}&cycle=06&step=6&location=hyderabad-id`,
  )
})

test('Overview, map, point inspection, and planner share the selected model/date/cycle/lead state', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /ForecastControls model=\{model\}[^\n]+cycle=\{cycle\}[^\n]+step=\{step\}/)
  assert.match(app, /rainApi\.pointSeries\(\{model,date,cycle,end_step:/)
  assert.match(app, /<PlannerPage[^\n]+model=\{model\} date=\{date\} cycle=\{cycle\}/)
  assert.match(app, /Selected run: <strong>\{MODEL_LABEL\[model\]\} · \{date\} \{cycle\} UTC/)
  assert.match(app, /forecastSelectionUrl\(route,\{model,date,cycle:nextCycle,step\}/)
})

test('first visit chooses the newest past validated run and nearest useful cached lead', () => {
  const availability = { entries: [
    { model: 'ifs', run_time_utc: '2026-11-06T06:00:00Z', forecast_step_hours: 6, delivery_status: 'cached' },
    { model: 'ifs', run_time_utc: '2026-11-06T06:00:00Z', forecast_step_hours: 24, delivery_status: 'cached' },
    { model: 'aifs-single', run_time_utc: '2026-11-06T12:00:00+00:00', forecast_step_hours: 24, delivery_status: 'cached' },
  ] }
  assert.deepEqual(latestValidatedRun(availability, Date.parse('2026-11-06T13:00:00Z')),
    { model: 'aifs-single', date: '2026-11-06', cycle: '12', step: 24 })
})

test('first visit does not select future or absent validated runs', () => {
  assert.equal(latestValidatedRun({ entries: [
    { model: 'ifs', run_time_utc: '2026-11-06T18:00:00Z', forecast_step_hours: 24, delivery_status: 'cached' },
  ] }, Date.parse('2026-11-06T13:00:00Z')), null)
  assert.equal(latestValidatedRun({ entries: [] }), null)
})

test('explicitly checking the current cycle displays only its validated response; checking another cycle does not switch runs', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /const validated=await rainApi\.forecast\(\{model,date,cycle:nextCycle,step\}\)/)
  assert.match(app, /if\(nextCycle===cycle\)[\s\S]+?forecastRef\.current=validated;setForecast\(validated\)/)
  assert.match(app, /onCheck:\(next:string\)=>void checkCycleWithEcmwf\(next\)/)
})
