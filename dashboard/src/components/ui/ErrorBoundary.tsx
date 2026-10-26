import { Component, type ReactNode } from "react";
import { Alert } from "./Alert";
import { Button } from "./Button";

interface State { error: Error | null }

/** Keeps a render error inside the page it happened on, with a retry, instead of blanking the app. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }
  componentDidUpdate(prev: { resetKey?: string }) { if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null }); }
  render() {
    if (this.state.error) {
      return (
        <Alert tone="danger" title="This page hit an error" actions={<Button size="sm" onClick={() => this.setState({ error: null })}>Try again</Button>}>
          {this.state.error.message}
        </Alert>
      );
    }
    return this.props.children;
  }
}
