export type GeocodedPlace = {
  id: number
  name: string
  displayName: string
  latitude: number
  longitude: number
  country?: string
  admin1?: string
  admin2?: string
  timezone?: string
}

export type SavedPlaceCoordinates = { latitude: number; longitude: number }

export function savedLocationFromPlaceResult(place: GeocodedPlace, id: string) {
  return { id, name: place.displayName, latitude: place.latitude, longitude: place.longitude, kind: 'saved' as const }
}

export function meaningfulLocationCharacters(query: string): number {
  return Array.from(query.matchAll(/[\p{L}\p{N}]/gu)).length
}

export function formatPlaceDisplayName(place: Pick<GeocodedPlace, 'name' | 'admin1' | 'admin2' | 'country'>): string {
  const parts = [place.name, place.admin1 || place.admin2, place.country].filter((part): part is string => Boolean(part?.trim()))
  return parts.filter((part, index) => parts.findIndex(value => value.trim().toLocaleLowerCase() === part.trim().toLocaleLowerCase()) === index).join(', ')
}

export function deduplicatePlaceResults(results: GeocodedPlace[]): GeocodedPlace[] {
  const seen = new Set<string>()
  return results.filter(place => {
    const label = formatPlaceDisplayName(place).normalize('NFKC').toLocaleLowerCase()
    const key = `${place.id}|${label}|${place.latitude.toFixed(4)}|${place.longitude.toFixed(4)}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function findSavedPlaceAtCoordinates<T extends SavedPlaceCoordinates & { name?: string }>(places: T[], place: SavedPlaceCoordinates, expectedName?: string): T | undefined {
  const normalizedName = expectedName?.normalize('NFKC').trim().toLocaleLowerCase()
  return places.find(saved => Math.abs(saved.latitude - place.latitude) < 0.00001
    && Math.abs(saved.longitude - place.longitude) < 0.00001
    && (!normalizedName || saved.name?.normalize('NFKC').trim().toLocaleLowerCase() === normalizedName))
}

type GeocoderResponse = { results?: unknown }
type FetchLike = (input: URL, init?: RequestInit) => Promise<Response>

/** Sends only the user-entered place name. Forecast requests happen after explicit selection elsewhere. */
export async function searchPlaces(query: string, signal: AbortSignal, fetcher: FetchLike = fetch): Promise<GeocodedPlace[]> {
  if (meaningfulLocationCharacters(query) < 3) return []
  const url = new URL('https://geocoding-api.open-meteo.com/v1/search')
  url.searchParams.set('name', query.trim())
  url.searchParams.set('count', '5')
  url.searchParams.set('language', 'en')
  const response = await fetcher(url, { signal, headers: { Accept: 'application/json' } })
  if (signal.aborted) throw new DOMException('Place search cancelled.', 'AbortError')
  if (!response.ok) throw new Error(`Geocoding service returned HTTP ${response.status}.`)
  const body = await response.json() as GeocoderResponse
  if (signal.aborted) throw new DOMException('Place search cancelled.', 'AbortError')
  if (body.results !== undefined && !Array.isArray(body.results)) throw new Error('Geocoding service returned a malformed results list.')
  const results = (Array.isArray(body.results) ? body.results : []).flatMap((raw): GeocodedPlace[] => {
    if (!raw || typeof raw !== 'object') return []
    const value = raw as Record<string, unknown>
    if (!Number.isSafeInteger(value.id) || typeof value.name !== 'string' || !value.name.trim()
      || typeof value.latitude !== 'number' || !Number.isFinite(value.latitude) || value.latitude < -90 || value.latitude > 90
      || typeof value.longitude !== 'number' || !Number.isFinite(value.longitude) || value.longitude < -180 || value.longitude > 180) return []
    const optional = (key: string) => typeof value[key] === 'string' && (value[key] as string).trim() ? value[key] as string : undefined
    const base = {
      id: value.id as number,
      name: value.name.trim(),
      latitude: value.latitude,
      longitude: value.longitude,
      country: optional('country'),
      admin1: optional('admin1'),
      admin2: optional('admin2'),
      timezone: optional('timezone'),
    }
    return [{ ...base, displayName: formatPlaceDisplayName(base) }]
  })
  return deduplicatePlaceResults(results).slice(0, 5)
}
