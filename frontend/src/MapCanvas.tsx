import { useEffect, useRef, useState } from 'react'
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl'
import { apiImage } from './api'

const openFreeMapStyle = 'https://tiles.openfreemap.org/styles/liberty'
const configuredTiles = import.meta.env.VITE_BASEMAP_TILE_URL?.trim() || ''
const configuredStyle = import.meta.env.VITE_BASEMAP_STYLE_URL?.trim() || ''
const configuredAttribution = import.meta.env.VITE_BASEMAP_ATTRIBUTION?.trim() || ''
const usingRasterTiles = Boolean(configuredTiles && configuredAttribution)
const usingStyleUrl = !configuredTiles && (!configuredStyle || configuredAttribution)
const basemapConfigured = usingRasterTiles || usingStyleUrl
const basemapStyle: string | maplibregl.StyleSpecification = usingRasterTiles ? {
  version: 8,
  sources: {
    basemap: {
      type: 'raster',
      tiles: [configuredTiles],
      tileSize: 256,
      attribution: configuredAttribution,
    },
  },
  layers: [
    { id: 'map-background', type: 'background', paint: { 'background-color': '#dce8e4' } },
    { id: 'basemap', type: 'raster', source: 'basemap' },
  ],
} : usingStyleUrl ? (configuredStyle || openFreeMapStyle) : {
  version: 8,
  sources: {},
  layers: [{ id: 'map-background', type: 'background', paint: { 'background-color': '#dce8e4' } }],
}

const MAX_MERCATOR_LATITUDE = 85.0511287798066
let preservedMapView: { center: [number, number]; zoom: number; opacity: number; showPrecipitation: boolean } | null = null
const rasterBounds: [[number, number], [number, number], [number, number], [number, number]] = [
  [-180, MAX_MERCATOR_LATITUDE], [180, MAX_MERCATOR_LATITUDE],
  [180, -MAX_MERCATOR_LATITUDE], [-180, -MAX_MERCATOR_LATITUDE],
]

async function projectRasterToMercator(source: Blob): Promise<Blob> {
  const bitmap = await createImageBitmap(source)
  try {
    const { width, height } = bitmap
    if (Math.abs(width / height - 2) > 0.02) {
      throw new Error(`Expected a global 2:1 latitude/longitude raster, received ${width}×${height}`)
    }
    const sourceCanvas = document.createElement('canvas')
    sourceCanvas.width = width
    sourceCanvas.height = height
    const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true })
    if (!sourceContext) throw new Error('Could not decode precipitation raster pixels')
    sourceContext.drawImage(bitmap, 0, 0)
    const sourcePixels = sourceContext.getImageData(0, 0, width, height)

    // The ECMWF image is regular latitude/longitude. Re-map its rows to
    // uniformly spaced Web Mercator rows using nearest source rows. This
    // preserves source pixel values while avoiding MapLibre's invalid ±90°
    // Mercator coordinates and geographic stretching.
    const target = document.createElement('canvas')
    target.width = width
    target.height = width
    const targetContext = target.getContext('2d')
    if (!targetContext) throw new Error('Could not prepare the map projection')
    const targetPixels = targetContext.createImageData(width, width)
    for (let y = 0; y < width; y += 1) {
      const mercatorY = y / (width - 1)
      const latitude = Math.atan(Math.sinh(Math.PI * (1 - 2 * mercatorY))) * 180 / Math.PI
      const sourceY = Math.max(0, Math.min(height - 1, Math.round((90 - latitude) * (height - 1) / 180)))
      const from = sourceY * width * 4
      targetPixels.data.set(sourcePixels.data.subarray(from, from + width * 4), y * width * 4)
    }
    targetContext.putImageData(targetPixels, 0, 0)
    return await new Promise<Blob>((resolve, reject) => {
      target.toBlob(blob => blob ? resolve(blob) : reject(new Error('Could not encode projected precipitation raster')), 'image/png')
    })
  } finally {
    bitmap.close()
  }
}

type MapCanvasProps = {
  rasterUrl?: string
  onPoint?: (lat: number, lon: number) => void
  height?: number
  center?: [number, number]
}

export default function MapCanvas({ rasterUrl, onPoint, height = 540, center }: MapCanvasProps) {
  const element = useRef<HTMLDivElement>(null)
  const map = useRef<MapLibreMap | null>(null)
  const initialMapView = useRef(preservedMapView)
  const restoreViewOnMount = useRef(false)
  const styleReady = useRef(false)
  const clickHandler = useRef(onPoint)
  const [mapReady, setMapReady] = useState(false)
  const [basemapError, setBasemapError] = useState(!basemapConfigured
    ? 'The configured basemap is missing attribution. Set VITE_BASEMAP_ATTRIBUTION with the provider style URL.'
    : '')
  const [imageUrl, setImageUrl] = useState('')
  const [rasterError, setRasterError] = useState('')
  const [retry, setRetry] = useState(0)
  const [mapRetry, setMapRetry] = useState(0)
  const [opacity, setOpacity] = useState(() => preservedMapView?.opacity ?? 0.84)
  const [showPrecipitation, setShowPrecipitation] = useState(() => preservedMapView?.showPrecipitation ?? true)

  useEffect(() => {
    preservedMapView = {
      center: preservedMapView?.center ?? [0, 20],
      zoom: preservedMapView?.zoom ?? 1.25,
      opacity,
      showPrecipitation,
    }
  }, [opacity, showPrecipitation])

  useEffect(() => { clickHandler.current = onPoint }, [onPoint])

  useEffect(() => {
    const container = element.current
    if (!container) return

    let instance: MapLibreMap
    try {
      const startingView = initialMapView.current
      restoreViewOnMount.current = startingView !== null
      instance = new maplibregl.Map({
        container,
        center: startingView?.center ?? [0, 20],
        zoom: startingView?.zoom ?? 1.25,
        minZoom: 0.8,
        maxZoom: 9,
        style: basemapStyle,
        attributionControl: {
          compact: true,
          customAttribution: configuredStyle && !usingRasterTiles ? configuredAttribution : undefined,
        },
        renderWorldCopies: true,
        keyboard: true,
      })
    } catch (error) {
      setBasemapError(error instanceof Error ? error.message : 'MapLibre could not initialize the map.')
      return
    }

    map.current = instance
    instance.on('moveend', () => {
      const current = instance.getCenter()
      preservedMapView = {
        center: [current.lng, current.lat],
        zoom: instance.getZoom(),
        opacity: preservedMapView?.opacity ?? opacity,
        showPrecipitation: preservedMapView?.showPrecipitation ?? showPrecipitation,
      }
    })
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    instance.on('click', event => clickHandler.current?.(event.lngLat.lat, event.lngLat.lng))
    // style.load is independent of basemap tile completion, so a tile
    // outage cannot block the forecast raster from being attached.
    instance.on('style.load', () => {
      styleReady.current = true
      setMapReady(true)
    })
    instance.on('error', event => {
      const mapError = event as { sourceId?: string; error?: Error }
      const sourceId = mapError.sourceId
      if (sourceId === 'rain-raster') setRasterError(`MapLibre could not load the precipitation image: ${mapError.error?.message ?? 'image source error'}`)
      else if (sourceId) setBasemapError(`Basemap source “${sourceId}” could not load. Check the configured provider and network access.`)
      else if (!styleReady.current) setBasemapError(`MapLibre could not initialize its map style: ${mapError.error?.message ?? 'style load error'}`)
    })

    // MapLibre measures its container at initialization. Re-measure after the
    // route, viewport, or responsive layout changes its actual size.
    const resize = () => {
      requestAnimationFrame(() => {
        if (container.isConnected && map.current === instance) instance.resize()
      })
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    window.addEventListener('resize', resize)
    resize()

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', resize)
      instance.remove()
      styleReady.current = false
      if (map.current === instance) map.current = null
    }
  }, [mapRetry])

  useEffect(() => {
    if (map.current && center) {
      if (restoreViewOnMount.current) {
        restoreViewOnMount.current = false
        return
      }
      map.current.easeTo({ center, zoom: Math.max(map.current.getZoom(), 3.2), duration: 0 })
    }
  }, [center?.[0], center?.[1]])

  useEffect(() => {
    let active = true
    let objectUrl = ''
    const controller = new AbortController()
    setImageUrl('')
    setRasterError('')
    // The selected metadata changes before its image finishes downloading.
    // Hide the previous run's pixels now so a failed replacement can never
    // leave them displayed under the new forecast's timestamp.
    try {
      if (map.current?.getLayer('rain-layer')) map.current.setLayoutProperty('rain-layer', 'visibility', 'none')
    } catch {
      // The base style can still be initializing; a later successful raster
      // load will add the layer with the current visibility preference.
    }
    if (!rasterUrl) return

    apiImage(rasterUrl, controller.signal).then(async blob => {
      objectUrl = URL.createObjectURL(await projectRasterToMercator(blob))
      if (active) setImageUrl(objectUrl)
    }).catch(error => {
      if (active) setRasterError(error instanceof Error ? error.message : 'Could not load the ECMWF precipitation raster.')
    })

    return () => {
      active = false
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [rasterUrl, retry])

  useEffect(() => {
    const current = map.current
    if (!current || !mapReady || !imageUrl) return

    try {
      const source = current.getSource('rain-raster') as maplibregl.ImageSource | undefined
      if (source) {
        source.updateImage({ url: imageUrl, coordinates: rasterBounds })
      } else {
        current.addSource('rain-raster', { type: 'image', url: imageUrl, coordinates: rasterBounds })
        current.addLayer({ id: 'rain-layer', type: 'raster', source: 'rain-raster', paint: { 'raster-opacity': 0.84 } })
      }
      current.setLayoutProperty('rain-layer', 'visibility', showPrecipitation ? 'visible' : 'none')
      current.setPaintProperty('rain-layer', 'raster-opacity', opacity)
      setRasterError('')
    } catch (error) {
      setRasterError(error instanceof Error ? error.message : 'The precipitation raster could not be added to the map.')
    }
  }, [mapReady, imageUrl, opacity, showPrecipitation])

  return <div className="map-frame" style={{ height }}>
    <div className="map-canvas" ref={element} role="application" aria-label="Interactive global precipitation map. Use map controls to zoom and drag to pan. Click a point to inspect rainfall." />
    {!mapReady && !basemapError && <div className="map-feedback" role="status">Loading basemap…</div>}
    {basemapError && <div className="map-feedback map-feedback-error map-basemap-error" role="alert"><strong>Basemap unavailable</strong><span>{basemapError}</span><button type="button" onClick={() => {setBasemapError('');setMapReady(false);setMapRetry(value=>value+1)}}>Retry basemap</button></div>}
    {!rasterUrl && <div className="map-empty">Load a verified forecast to view precipitation.</div>}
    {mapReady && <button type="button" className="map-reset-view" aria-label="Reset map view" onClick={() => map.current?.easeTo({ center: center ?? [0, 20], zoom: center ? 3.2 : 1.25, duration: 0 })}>Reset view</button>}
    {rasterUrl && !imageUrl && !rasterError && <div className="map-feedback map-raster-loading" role="status">Loading validated precipitation raster…</div>}
    {rasterUrl && imageUrl && <fieldset className="map-layer-controls"><legend>Map layers</legend><label><input type="checkbox" checked={showPrecipitation} onChange={event => setShowPrecipitation(event.target.checked)}/> Precipitation</label><label className="map-opacity-control"><span>Layer opacity · {Math.round(opacity * 100)}%</span><input type="range" min="0" max="100" step="5" value={Math.round(opacity * 100)} aria-label="Precipitation layer opacity" onChange={event => setOpacity(Number(event.target.value) / 100)} /></label></fieldset>}
    {rasterError && <div className="map-feedback map-feedback-error map-raster-error" role="alert"><strong>Precipitation layer unavailable</strong><span>{rasterError}</span><button type="button" onClick={() => setRetry(value => value + 1)}>Retry layer</button></div>}
  </div>
}
