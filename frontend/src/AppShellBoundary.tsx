import { Component, type ReactNode } from 'react'

const routes = [
  ['/', 'Overview'], ['/map', 'Explore map'], ['/compare', 'Compare models'],
  ['/planner', 'Rain planner'], ['/locations', 'Locations'], ['/history', 'Forecast changes'],
  ['/data', 'Data & method'], ['/settings', 'Settings'],
] as const

type State = { failed: boolean }

/** Last-resort guard for errors in App-level render logic; keep navigation usable. */
export default class AppShellBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error) {
    console.error('RainScope application render failed', error)
  }

  render() {
    if (!this.state.failed) return this.props.children
    const query = window.location.search
    return <div className="app-shell">
      <aside className="sidebar">
        <a className="wordmark" href={`/${query}`}><span className="brand-symbol">R</span><span>RainScope<small>PRECIPITATION DESK</small></span></a>
        <nav aria-label="Main navigation">
          {routes.map(([path, label]) => <a key={path} href={`${path}${query}`} aria-current={window.location.pathname === path ? 'page' : undefined} className={window.location.pathname === path ? 'active' : ''}>{label}</a>)}
        </nav>
        <div className="side-note">ECMWF Open Data<br/><small>Forecasts, not observations</small></div>
      </aside>
      <main className="main-content">
        <header className="mobile-header"><a className="wordmark" href={`/${query}`}><span className="brand-symbol">R</span><span>RainScope</span></a><select aria-label="Navigate to a RainScope page" value={window.location.pathname} onChange={event => window.location.assign(`${event.target.value}${query}`)}>{routes.map(([path, label]) => <option key={path} value={path}>{label}</option>)}</select></header>
        <div className="route-inline-error" role="alert">
          <span><strong>This view encountered a display error.</strong> The error was logged to the browser console. Your saved places and preferences were left untouched.</span>
          <button type="button" className="quiet-button" onClick={() => this.setState({ failed: false })}>Retry view</button>
        </div>
      </main>
    </div>
  }
}
