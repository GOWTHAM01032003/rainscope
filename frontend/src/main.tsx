import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AppShellBoundary from './AppShellBoundary'
import './style.css'
import 'maplibre-gl/dist/maplibre-gl.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AppShellBoundary><App /></AppShellBoundary></React.StrictMode>,
)
