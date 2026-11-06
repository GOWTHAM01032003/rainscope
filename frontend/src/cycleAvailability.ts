import type { Availability, ModelId } from './api'

export type InitialRunSelection = { model: ModelId; date: string; cycle: string; step: number }

/** Pick the newest past run and an actually cached lead from validated cache metadata. */
export function latestValidatedRun(availability: Availability, now = Date.now()): InitialRunSelection | null {
  const entries = availability.entries
    .filter(entry => entry.delivery_status === 'cached'
      && Number.isInteger(entry.forecast_step_hours)
      && entry.forecast_step_hours >= 0
      && Number.isFinite(Date.parse(entry.run_time_utc))
      && Date.parse(entry.run_time_utc) <= now)
  if (!entries.length) return null

  const runs = new Map<string, typeof entries>()
  for (const entry of entries) {
    const run = new Date(entry.run_time_utc)
    const date = run.toISOString().slice(0, 10)
    const cycle = String(run.getUTCHours()).padStart(2, '0')
    const key = `${entry.model}|${date}|${cycle}`
    runs.set(key, [...(runs.get(key) ?? []), entry])
  }
  const candidates = [...runs.values()].sort((a, b) => Date.parse(b[0].run_time_utc) - Date.parse(a[0].run_time_utc)
    || (a[0].model === b[0].model ? 0 : a[0].model === 'ifs' ? -1 : 1))
  const latest = candidates[0]
  const preferred = [...latest].sort((a, b) => Math.abs(a.forecast_step_hours - 24) - Math.abs(b.forecast_step_hours - 24)
    || a.forecast_step_hours - b.forecast_step_hours)[0]
  const run = new Date(preferred.run_time_utc)
  return {
    model: preferred.model,
    date: run.toISOString().slice(0, 10),
    cycle: String(run.getUTCHours()).padStart(2, '0'),
    step: preferred.forecast_step_hours,
  }
}

export const SUPPORTED_CYCLES_UTC = ['00', '06', '12', '18'] as const

export type CycleStatus = {
  cycle: string
  available: boolean
  future: boolean
  cachedSteps: number[]
  reason: string
}

/** Availability is about a validated model/run/lead, never about a place. */
export function cycleStatuses(
  availability: Availability | null,
  model: ModelId,
  date: string,
  selectedStep: number,
  now = Date.now(),
): CycleStatus[] {
  return SUPPORTED_CYCLES_UTC.map(cycle => {
    const runTime = Date.parse(`${date}T${cycle}:00:00Z`)
    const future = !Number.isFinite(runTime) || runTime > now
    const entries = availability?.entries.filter(entry =>
      entry.model === model && entry.run_time_utc === `${date}T${cycle}:00:00Z`,
    ) ?? []
    const cachedSteps = [...new Set(entries.map(entry => entry.forecast_step_hours))].sort((a, b) => a - b)
    const available = !future && cachedSteps.includes(selectedStep)
    const reason = future
      ? 'This UTC cycle has not occurred yet.'
      : available
        ? `Validated GRIB cached for lead +${selectedStep} h.`
        : cachedSteps.length
          ? `Run has validated cached leads, but +${selectedStep} h is not among them.`
          : 'No validated GRIB for this model, date, and cycle is cached yet.'
    return { cycle, available, future, cachedSteps, reason }
  })
}

/** Guard cycle changes: callers may switch only to a cycle validated at this lead. */
export function canSelectCycle(statuses: CycleStatus[], cycle: string): boolean {
  return statuses.some(status => status.cycle === cycle && status.available)
}

export function forecastSelectionUrl(path: string, selection: { model: ModelId; date: string; cycle: string; step: number }, locationId?: string): string {
  const query = new URLSearchParams({ model: selection.model, date: selection.date, cycle: selection.cycle, step: String(selection.step) })
  if (locationId) query.set('location', locationId)
  return `${path}?${query}`
}
