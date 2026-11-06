import test from 'node:test'
import assert from 'node:assert/strict'
import { formatAmount, formatLocalTime, formatUtcTime, forecastTimeLabels, resolvePlaceTimeZone } from '../src/format.ts'

test('formats exact zero separately from nonzero amounts below display precision', () => {
  assert.equal(formatAmount(0, 'mm'), '0.0 mm')
  assert.equal(formatAmount(0.004, 'mm'), '<0.1 mm')
  assert.equal(formatAmount(0.14, 'mm'), '0.1 mm')
  assert.equal(formatAmount(null, 'mm'), 'Missing')
})

test('uses the selected display unit and preserves small nonzero direction', () => {
  assert.equal(formatAmount(25.4, 'in'), '1.0 in')
  assert.equal(formatAmount(0.01, 'in'), '<0.1 in')
  assert.equal(formatAmount(-0.01, 'mm'), '−<0.1 mm')
})

test('selected place local time keeps its IANA zone and UTC reference across a date change', () => {
  const labels = forecastTimeLabels('2026-11-06T00:00:00Z', 'location', 'Asia/Kolkata')
  assert.match(labels.primary, /Nov 6, 2026, 05:30 AM GMT\+5:30 · Asia\/Kolkata/)
  assert.equal(labels.secondary, 'Nov 6, 2026, 12:00 AM UTC')
  assert.equal(formatUtcTime('2026-11-06T00:00:00Z'), 'Nov 6, 2026, 12:00 AM UTC')
})

test('IANA formatting observes both sides of a daylight-saving clock change', () => {
  assert.match(formatLocalTime('2026-11-01T05:30:00Z', 'America/New_York'), /Nov 1, 2026, 01:30 AM EDT/)
  assert.match(formatLocalTime('2026-11-01T06:30:00Z', 'America/New_York'), /Nov 1, 2026, 01:30 AM EST/)
})

test('local coordinate zone takes precedence and invalid/missing zones fall back explicitly to UTC', () => {
  assert.equal(resolvePlaceTimeZone(['Asia/Kolkata'], 'America/New_York'), 'Asia/Kolkata')
  assert.equal(resolvePlaceTimeZone([], 'Invalid/Nowhere'), null)
  assert.equal(resolvePlaceTimeZone([], undefined), null)
  const fallback = forecastTimeLabels('2026-11-06T00:00:00Z', 'location', null)
  assert.equal(fallback.primary, 'Nov 6, 2026, 12:00 AM UTC')
  assert.match(fallback.secondary, /Selected-place time zone unavailable/)
})
