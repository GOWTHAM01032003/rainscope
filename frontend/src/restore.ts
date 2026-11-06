export type RestoredModel = 'ifs' | 'aifs-single'

export type RestoredLocation = {
  id: string
  name: string
  latitude: number
  longitude: number
  kind?: 'my-location' | 'saved'
  timeZone?: string
  timeZoneSource?: 'open-meteo-geocoding'
  [key: string]: unknown
}

export type ForecastRouteState = {
  model: RestoredModel
  date: string
  cycle: string
  step: number
  requestedLocation: string | null
  notice: string | null
}

const cycles = ['00', '06', '12', '18']
const models: RestoredModel[] = ['ifs', 'aifs-single']

export function supportedLeads(model: RestoredModel, cycle: string): number[] {
  if (model === 'aifs-single') return Array.from({ length: 61 }, (_, i) => i * 6)
  const leads = Array.from({ length: 49 }, (_, i) => i * 3)
  if (cycle === '00' || cycle === '12') leads.push(...Array.from({ length: 36 }, (_, i) => 150 + i * 6))
  return leads
}

export function nearestLead(leads: number[], requested: number): number {
  return leads.reduce((best, value) => Math.abs(value - requested) < Math.abs(best - requested) ? value : best, leads[0] ?? 24)
}

function validDate(value: string | null, today: string): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const parsed = new Date(`${value}T00:00:00Z`)
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value || value > today) return null
  return value
}

export function restoreForecastRoute(search: string, now = new Date()): ForecastRouteState {
  const query = new URLSearchParams(search)
  const today = now.toISOString().slice(0, 10)
  const notices: string[] = []
  const requestedModel = query.get('model')
  const model: RestoredModel = models.includes(requestedModel as RestoredModel) ? requestedModel as RestoredModel : 'ifs'
  if (requestedModel && !models.includes(requestedModel as RestoredModel)) notices.push('The shared model was not supported; IFS is selected.')

  const requestedDate = query.get('date')
  const date = validDate(requestedDate, today) ?? today
  if (requestedDate && date !== requestedDate) notices.push('The shared run date was invalid or in the future; today’s UTC date is selected.')

  const availableCycles = cycles.filter(cycle => date !== today || Number(cycle) <= now.getUTCHours())
  const requestedCycle = query.get('cycle')
  const cycle = requestedCycle && availableCycles.includes(requestedCycle) ? requestedCycle : availableCycles.at(-1) ?? '00'
  if (requestedCycle && cycle !== requestedCycle) notices.push('The shared cycle was invalid or has not occurred yet; the latest elapsed UTC cycle is selected.')

  const leads = supportedLeads(model, cycle)
  const rawStep = query.get('step')
  const parsedStep = rawStep === null || rawStep.trim() === '' ? 24 : Number(rawStep)
  const step = Number.isInteger(parsedStep) && leads.includes(parsedStep) ? parsedStep : nearestLead(leads, Number.isFinite(parsedStep) ? parsedStep : 24)
  if (rawStep !== null && step !== parsedStep) notices.push('The shared forecast lead was unsupported; the nearest supported lead is selected.')

  const requestedLocation = query.get('location')
  const safeLocation = requestedLocation && requestedLocation.length <= 128 && /^[\w-]+$/.test(requestedLocation) ? requestedLocation : null
  if (requestedLocation && !safeLocation) notices.push('The shared place identifier was malformed; your saved place selection will be used if available.')
  return { model, date, cycle, step, requestedLocation: safeLocation, notice: notices.length ? notices.join(' ') : null }
}

function isLocation(value: unknown): value is RestoredLocation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return typeof item.id === 'string' && item.id.length > 0 && item.id.length <= 128
    && /^[\w-]+$/.test(item.id)
    && typeof item.name === 'string' && item.name.trim().length > 0 && item.name.length <= 200
    && typeof item.latitude === 'number' && Number.isFinite(item.latitude) && item.latitude >= -90 && item.latitude <= 90
    && typeof item.longitude === 'number' && Number.isFinite(item.longitude) && item.longitude >= -180 && item.longitude <= 180
}

export function restoreSavedLocations(raw: string | null): {
  locations: RestoredLocation[]
  preservedEntries: unknown[]
  canPersist: boolean
  notice: string | null
} {
  if (raw === null) return { locations: [], preservedEntries: [], canPersist: true, notice: null }
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch {
    return { locations: [], preservedEntries: [], canPersist: false, notice: 'Saved place data could not be read and has been left untouched. Valid places can still be added for this session.' }
  }
  if (!Array.isArray(parsed)) {
    return { locations: [], preservedEntries: [], canPersist: false, notice: 'Saved place data has an unsupported format and has been left untouched. Valid places can still be added for this session.' }
  }
  const locations: RestoredLocation[] = []
  const preservedEntries: unknown[] = []
  const seen = new Set<string>()
  for (const entry of parsed) {
    if (!isLocation(entry) || seen.has(entry.id)) { preservedEntries.push(entry); continue }
    seen.add(entry.id)
    locations.push(entry)
  }
  return {
    locations,
    preservedEntries,
    canPersist: true,
    notice: preservedEntries.length ? `${preservedEntries.length} unsupported saved-place entr${preservedEntries.length === 1 ? 'y was' : 'ies were'} preserved and not loaded.` : null,
  }
}

export function restoredLocationSelection(requested: string | null, stored: string, locations: RestoredLocation[]): { id: string; notice: string | null } {
  const validIds = new Set(locations.map(item => item.id))
  if (requested && validIds.has(requested)) return { id: requested, notice: null }
  if (requested) return { id: validIds.has(stored) ? stored : locations[0]?.id ?? '', notice: 'The shared place is not in this browser’s saved places. Choose or add a place to continue.' }
  if (validIds.has(stored)) return { id: stored, notice: null }
  return { id: locations[0]?.id ?? '', notice: stored ? 'The previously selected place is unavailable; choose a saved place or add one.' : null }
}

export function serializeSavedLocations(locations: RestoredLocation[], preservedEntries: unknown[]): string {
  return JSON.stringify([...locations, ...preservedEntries])
}
