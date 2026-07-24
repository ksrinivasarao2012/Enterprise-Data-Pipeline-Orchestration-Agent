import { useCallback, useEffect, useState } from 'react';

export type Route = 'run' | 'incidents' | 'records';

export const ROUTES: { id: Route; label: string }[] = [
  { id: 'run', label: 'Run a test' },
  { id: 'incidents', label: 'Incidents' },
  { id: 'records', label: 'Records' },
];

function parse(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  return ROUTES.some((r) => r.id === h) ? (h as Route) : 'run';
}

/** Tiny hash router: #/run, #/incidents, #/records. */
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
