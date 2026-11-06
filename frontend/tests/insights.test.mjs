import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizePrecipitation } from '../src/insights.ts'

const point = (forecast_step_hours, interval_amount_mm) => ({ forecast_step_hours, interval_amount_mm })

test('briefing totals, threshold crossing, and wettest interval derive only from loaded point values', () => {
  const loadedForecast = [point(6, 0.4), point(12, 1.2), point(18, null), point(24, 0.7), point(30, 9)]
  const summary = summarizePrecipitation(loadedForecast, 24, 1)
  assert.equal(summary.totalMm, 2.3)
  assert.deepEqual(summary.points.map(value => value.forecast_step_hours), [6, 12, 24])
  assert.equal(summary.firstAtOrAboveThreshold.forecast_step_hours, 12)
  assert.equal(summary.wettest.forecast_step_hours, 12)
})

test('missing or non-finite periods produce no fabricated zero-total briefing', () => {
  const summary = summarizePrecipitation([point(6, null), point(12, Number.NaN)], 24, 1)
  assert.equal(summary.totalMm, null)
  assert.equal(summary.firstAtOrAboveThreshold, null)
  assert.equal(summary.wettest, null)
  assert.deepEqual(summary.points, [])
})
