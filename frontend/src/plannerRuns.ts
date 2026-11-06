import type { Availability, ModelId } from './api'

export type CachedPlannerRun = {
  model: ModelId
  run_time_utc: string
  date: string
  cycle: string
  available_steps: number[]
}

/** Find the newest strictly earlier validated cached run with every 6 h step
 * needed for this planner window. This proposes a cache explicitly; it never
 * changes the selected forecast on its own. */
export function latestCachedPlannerRun(
  entries: Availability['entries'],
  model: ModelId,
  selectedRunTimeUtc: string,
  windowHours: number,
): CachedPlannerRun | null {
  const selectedTime = Date.parse(selectedRunTimeUtc)
  const required = Array.from({ length: windowHours / 6 }, (_, index) => (index + 1) * 6)
  if (!Number.isFinite(selectedTime) || !required.length || required.at(-1)! > 72) return null

  const groups = new Map<string, { runTime: string; steps: Set<number> }>()
  for (const entry of entries) {
    if (entry.model !== model || Date.parse(entry.run_time_utc) >= selectedTime) continue
    const key = `${entry.model}|${entry.run_time_utc}`
    const group = groups.get(key) ?? { runTime: entry.run_time_utc, steps: new Set<number>() }
    group.steps.add(entry.forecast_step_hours)
    groups.set(key, group)
  }

  const candidates = [...groups.values()]
    .filter(group => required.every(step => group.steps.has(step)))
    .sort((a, b) => Date.parse(b.runTime) - Date.parse(a.runTime))
  const selected = candidates[0]
  if (!selected) return null
  return {
    model,
    run_time_utc: selected.runTime,
    date: selected.runTime.slice(0, 10),
    cycle: selected.runTime.slice(11, 13),
    available_steps: [...selected.steps].sort((a, b) => a - b),
  }
}
