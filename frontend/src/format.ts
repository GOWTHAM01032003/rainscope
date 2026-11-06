export type RainDisplayUnits = 'mm' | 'in'
export type TimePreference = 'UTC' | 'location' | 'device'

export function isValidIanaTimeZone(value: unknown): value is string {
  if (typeof value !== 'string' || !value.trim()) return false
  try {
    new Intl.DateTimeFormat('en', { timeZone: value }).format(new Date(0))
    return true
  } catch {
    return false
  }
}

/** Prefer locally resolved coordinate zones; a geocoder zone is only a fallback
 * for a place explicitly selected from a name search, never a coordinate lookup. */
export function resolvePlaceTimeZone(coordinateZones: string[], savedZone?: unknown): string | null {
  const localZone = coordinateZones.find(isValidIanaTimeZone)
  return localZone ?? (isValidIanaTimeZone(savedZone) ? savedZone : null)
}

export function formatUtcTime(value: string, locale?: string): string {
  return `${new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(new Date(value))} UTC`
}

export function formatLocalTime(value: string, zone?: string, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
    ...(zone ? { timeZone: zone } : {}),
  }).format(new Date(value))
}

export function forecastTimeLabels(value: string, preference: TimePreference, locationZone?: string | null) {
  if (preference === 'UTC') return { primary: formatUtcTime(value), secondary: undefined }
  if (preference === 'device') return { primary: formatLocalTime(value), secondary: `Device time · ${formatUtcTime(value)}` }
  if (locationZone && isValidIanaTimeZone(locationZone)) {
    return { primary: `${formatLocalTime(value, locationZone)} · ${locationZone}`, secondary: formatUtcTime(value) }
  }
  return { primary: formatUtcTime(value), secondary: 'Selected-place time zone unavailable · UTC shown' }
}

/** Format amounts for people without implying a rounded small value is zero. */
export function formatAmount(mm: number | null, units: RainDisplayUnits, digits = 1): string {
  if (mm === null || !Number.isFinite(mm)) return 'Missing'
  const converted = units === 'mm' ? mm : mm / 25.4
  const rounded = Number(converted.toFixed(digits))
  if (mm !== 0 && rounded === 0) {
    return `${mm < 0 ? '−' : ''}<${(1 / 10 ** digits).toFixed(digits)} ${units}`
  }
  return `${rounded.toFixed(digits)} ${units}`
}
