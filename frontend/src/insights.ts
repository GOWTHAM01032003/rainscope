import type { PointValue } from './api'

export type PrecipitationSummary = {
  points: PointValue[]
  totalMm: number | null
  firstAtOrAboveThreshold: PointValue | null
  wettest: PointValue | null
}

/** Summarize only validated API point values; missing or non-finite fields never become zero. */
export function summarizePrecipitation(points: PointValue[], windowHours: number, thresholdMm: number): PrecipitationSummary {
  const valid = points.filter(point => point.forecast_step_hours <= windowHours
    && point.interval_amount_mm !== null && Number.isFinite(point.interval_amount_mm))
  const wettest = valid.reduce<PointValue | null>((best, point) => !best
    || (point.interval_amount_mm as number) > (best.interval_amount_mm as number) ? point : best, null)
  return {
    points: valid,
    totalMm: valid.length ? valid.reduce((sum, point) => sum + (point.interval_amount_mm as number), 0) : null,
    firstAtOrAboveThreshold: valid.find(point => (point.interval_amount_mm as number) >= thresholdMm) ?? null,
    wettest,
  }
}
