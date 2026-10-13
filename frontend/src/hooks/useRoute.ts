import { useCallback, useEffect, useState } from 'react';

export type Route = 'run' | 'incidents' | 'records' | 'demo';

export const ROUTES: { id: Route; label: string }[] = [
  { id: 'run', label: 'Run a test' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'records', label: 'Records' },
  { id: 'demo', label: 'Demo' },
];

function parse(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  return ROUTES.some((r) => r.id === h) ? (h as Route) : 'run';
}

/** Tiny hash router: #/run, #/incidents, #/records, #/demo. */
export function useRoute() {
  const [route, setRoute] = useState<Route>(parse);

  useEffect(() => {
    const onHash = () => setRoute(parse());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = useCallback((to: Route) => {
    if (parse() !== to) window.location.hash = `/${to}`;
  }, []);

  return [route, go] as const;
}
