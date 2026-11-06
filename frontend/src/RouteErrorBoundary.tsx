import { Component, type ReactNode } from 'react'

type RouteErrorProps = { routeName: string; children: ReactNode }
type RouteFailureState = { failed: boolean }

export default class RouteErrorBoundary extends Component<RouteErrorProps, RouteFailureState> {
  state: RouteFailureState = { failed: false }

  static getDerivedStateFromError(): RouteFailureState {
    return { failed: true }
  }

  componentDidCatch(error: Error) {
    // Keep route name and stack in the console so the visible fallback does not hide the cause.
    console.error(`RainScope route render failed: ${this.props.routeName}`, error)
  }

  render() {
    if (this.state.failed) {
      return <div className="route-inline-error" role="alert">
        <span><strong>{this.props.routeName} section paused.</strong> An unexpected display error occurred. Other RainScope pages remain available from the navigation.</span>
        <button type="button" className="quiet-button" onClick={() => this.setState({ failed: false })}>Retry section</button>
      </div>
    }
    return this.props.children
  }
}
