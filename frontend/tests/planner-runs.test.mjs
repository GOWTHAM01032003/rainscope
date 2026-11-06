import test from 'node:test'
import assert from 'node:assert/strict'
import { latestCachedPlannerRun } from '../src/plannerRuns.ts'

const entry = (run, step, model = 'ifs') => ({
  model, run_time_utc: `${run}T06:00:00Z`, forecast_step_hours: step,
  valid_time_utc: `${run}T${String(6 + step).padStart(2, '0')}:00:00Z`,
  accumulation_interval_hours: [0, step], grib_units: 'm',
  retrieved_at_utc: `${run}T13:00:00Z`, sha256: 'a'.repeat(64), delivery_status: 'cached',
})

test('planner proposes the newest earlier cached run only when all selected-window steps are cached', () => {
  const entries = [
    entry('2026-11-06', 6), entry('2026-11-06', 12),
    entry('2026-11-06', 24), // incomplete 24 h coverage: 18 h is absent
    entry('2026-11-05', 6), entry('2026-11-05', 12), entry('2026-11-05', 18), entry('2026-11-05', 24),
    entry('2026-11-06', 6, 'aifs-single'),
  ]
  assert.deepEqual(latestCachedPlannerRun(entries, 'ifs', '2026-11-06T12:00:00Z', 24), {
    model: 'ifs', run_time_utc: '2026-11-05T06:00:00Z', date: '2026-11-05', cycle: '06',
    available_steps: [6, 12, 18, 24],
  })
})

test('an unpublished 12 UTC selection can explicitly offer the validated same-day 06 UTC cache', () => {
  const entries = [entry('2026-11-06', 6), entry('2026-11-06', 12), entry('2026-11-06', 18), entry('2026-11-06', 24)]
  assert.deepEqual(latestCachedPlannerRun(entries, 'ifs', '2026-11-06T12:00:00Z', 24), {
    model: 'ifs', run_time_utc: '2026-11-06T06:00:00Z', date: '2026-11-06', cycle: '06',
    available_steps: [6, 12, 18, 24],
  })
})

test('planner gives no fallback for a future, same-run, wrong-model, or incomplete cache', () => {
  const entries = [entry('2026-11-06', 6), entry('2026-11-05', 6, 'aifs-single')]
  assert.equal(latestCachedPlannerRun(entries, 'ifs', '2026-11-06T06:00:00Z', 6), null)
  assert.equal(latestCachedPlannerRun(entries, 'ifs', '2026-11-06T12:00:00Z', 12), null)
  assert.equal(latestCachedPlannerRun(entries, 'ifs', 'invalid', 6), null)
})
