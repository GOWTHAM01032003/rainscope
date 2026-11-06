import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { findSavedPlaceAtCoordinates, meaningfulLocationCharacters, searchPlaces, type GeocodedPlace } from './locationSearch'

export type LocationPickerPlace = { id: string; name: string; latitude: number; longitude: number }

type Props = {
  places: LocationPickerPlace[]
  selectedId: string
  onSelectSaved: (id: string) => void
  onSelectResult: (place: GeocodedPlace) => void
  label?: string
  showSavedSelector?: boolean
}

export default function LocationPicker({ places, selectedId, onSelectSaved, onSelectResult, label = 'Location', showSavedSelector = true }: Props) {
  const inputId = useId()
  const resultId = useId()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<GeocodedPlace[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const requestId = useRef(0)
  const activeController = useRef<AbortController | null>(null)
  const resultButtons = useRef<Array<HTMLButtonElement | null>>([])
  const characterCount = meaningfulLocationCharacters(query)

  useEffect(() => {
    const request = ++requestId.current
    activeController.current?.abort()
    if (characterCount < 3) {
      setLoading(false)
      setResults([])
      setError('')
      return
    }

    const controller = new AbortController()
    activeController.current = controller
    setLoading(true)
    setError('')
    const timer = window.setTimeout(() => {
      void searchPlaces(query.trim(), controller.signal).then(found => {
        if (request === requestId.current && !controller.signal.aborted) {
          setResults(found)
          if (!found.length) setMessage('')
        }
      }).catch(reason => {
        if (request === requestId.current && !(reason instanceof DOMException && reason.name === 'AbortError')) {
          setResults([])
          setError(reason instanceof Error ? reason.message : 'Place search is unavailable. Try again or enter coordinates manually.')
        }
      }).finally(() => {
        if (request === requestId.current) setLoading(false)
      })
    }, 350)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
      if (activeController.current === controller) activeController.current = null
    }
  }, [query, characterCount])

  const changeQuery = (value: string) => {
    requestId.current += 1
    activeController.current?.abort()
    activeController.current = null
    setResults([])
    setLoading(meaningfulLocationCharacters(value) >= 3)
    setError('')
    setMessage('')
    setQuery(value)
  }

  const selectResult = (place: GeocodedPlace) => {
    const existing = findSavedPlaceAtCoordinates(places.filter(saved => saved.id !== 'my-location'), place, place.displayName)
    if (existing) {
      onSelectSaved(existing.id)
      setMessage(`Selected saved place ${existing.name}.`)
    } else {
      onSelectResult(place)
      setMessage(`Saved and selected ${place.displayName}.`)
    }
    setResults([])
    setQuery('')
  }

  const moveResultFocus = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' && results.length) {
      event.preventDefault()
      resultButtons.current[0]?.focus()
    }
  }

  return <section className={`location-picker${showSavedSelector ? ' has-saved-selector' : ''}`} aria-label={`${label} selection`}>
    {showSavedSelector && <label className="location-picker-saved">{label}<select aria-label={`${label} saved places`} value={selectedId} disabled={!places.length} onChange={event => onSelectSaved(event.target.value)}>
      {!places.length && <option value="">No saved places</option>}
      {places.map(place => <option key={place.id} value={place.id}>{place.name}</option>)}
    </select></label>}
    <label className="location-picker-search" htmlFor={inputId}>Search for a place
      <input id={inputId} type="search" value={query} onChange={event => changeQuery(event.target.value)} onKeyDown={moveResultFocus} autoComplete="off" maxLength={120} aria-controls={resultId} aria-expanded={results.length > 0} aria-describedby={`${inputId}-hint`} placeholder="City, town, or postal code" />
    </label>
    <small id={`${inputId}-hint`} className="location-picker-hint">Enter at least 3 letters or numbers. Results are searched after a short pause; typing alone never loads a forecast.</small>
    {characterCount < 3 && <p className="location-picker-status" role="status">Enter {3 - characterCount} more meaningful {3 - characterCount === 1 ? 'character' : 'characters'} to search. No request has been sent.</p>}
    {loading && <p className="location-picker-status" role="status">Searching places…</p>}
    {error && <p className="location-picker-error" role="alert">{error}</p>}
    {!loading && !error && characterCount >= 3 && !results.length && !message && <p className="location-picker-status" role="status">No matching places found. Try a more specific name, or add coordinates manually.</p>}
    {message && <p className="location-picker-status" role="status">{message}</p>}
    {results.length > 0 && <ul id={resultId} className="location-picker-results" aria-label="Place search results" aria-live="polite">{results.map((place, index) => <li key={`${place.id}-${place.latitude}-${place.longitude}`}><button ref={element => { resultButtons.current[index] = element }} type="button" onClick={() => selectResult(place)}>
      <strong>{place.displayName}</strong><small>Save and use these exact coordinates · {place.latitude.toFixed(4)}°, {place.longitude.toFixed(4)}°</small>
    </button></li>)}</ul>}
  </section>
}
