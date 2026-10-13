import { useEffect, useMemo, useState } from 'react';
import { ApiError, api, type WhoAmI } from '../api';
import { DataContext, demoSource, type DemoSnapshot } from '../data';
import type { Route } from '../hooks/useRoute';
import { formatTime } from '../lib/incident';
import { Dashboard } from './Dashboard';

type State = { kind: 'loading' } | { kind: 'missing' } | { kind: 'error'; message: string } | { kind: 'ready'; snap: DemoSnapshot };

/**
 * Demo tab (CHANGES.md section 32): the app as it looks after all 13 test cases, from a frozen
 * recording made with real Gemini. Read-only: nothing here runs, clears or changes anything,
 * and it never mixes with anyone's own runs.
 */
export function DemoPage({ who, go }: { who: WhoAmI; go: (to: Route) => void }) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    let alive = true;
    api
      .demoSnapshot()
      .then((snap) => alive && setState({ kind: 'ready', snap }))
      .catch((err) => {
        if (!alive || (err instanceof ApiError && err.status === 401)) return;
        if (err instanceof ApiError && err.status === 404) setState({ kind: 'missing' });
        else setState({ kind: 'error', message: err instanceof ApiError ? err.detail : 'Could not load the demo.' });
      });
    return () => {
      alive = false;
    };
  }, []);

  const source = useMemo(() => (state.kind === 'ready' ? demoSource(state.snap) : null), [state]);

  if (state.kind !== 'ready' || !source) {
    return (
      <main className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6 sm:px-8 sm:py-8">
        {state.kind === 'loading' && <div className="skeleton h-40" aria-label="Loading the demo" />}
        {state.kind === 'missing' && (
          <div role="status" className="card space-y-2 p-6">
            <h2 className="text-lg font-semibold">No demo recorded yet</h2>
            <p className="text-sm text-muted">
              The owner records it once with <code className="font-mono">python scripts/make_demo_snapshot.py</code>. Until then,
              try a test yourself on the Run a test tab.
            </p>
          </div>
        )}
        {state.kind === 'error' && (
          <div role="alert" className="card p-6 text-sm text-bad">
            {state.message}
          </div>
        )}
      </main>
    );
  }

  const m = state.snap.meta;
  const banner = (
    <div role="note" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-line bg-surface2 px-4 py-3 text-sm">
      <span className="pill pill-info">Read-only demo</span>
      <span className="text-muted">
        Recorded {formatTime(m.recorded_at)} with <span className="font-mono">{m.model}</span> · {m.as_expected}/{m.presets} test
        cases as designed · {m.gemini_calls} Gemini calls in total.
      </span>
      <a href="#/run" className="ml-auto font-medium underline-offset-4 hover:underline">
        Run one yourself →
      </a>
    </div>
  );

  return (
    <DataContext.Provider value={source}>
      <Dashboard who={who} route="demo" go={go} demoBanner={banner} />
    </DataContext.Provider>
  );
}
