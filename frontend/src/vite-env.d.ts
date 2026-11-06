/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_BASEMAP_TILE_URL?: string
  readonly VITE_BASEMAP_STYLE_URL?: string
  readonly VITE_BASEMAP_ATTRIBUTION?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
