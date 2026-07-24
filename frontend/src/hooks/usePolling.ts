import { useEffect, useRef } from 'react';

/**
 * Calls `fn` now and then every `intervalMs` while the tab is visible.
 * Pauses when the tab is hidden and runs once immediately on return.
 * Pass `enabled = false` to stop.
 */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, enabled = true, deps: unknown[] = []) {
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (cancelled || document.hidden || inFlight) return;
      inFlight = true;
      try {
        await fnRef.current();
      } finally {
        inFlight = false;
      }
    };
    const start = () => {
      window.clearInterval(timer);
      timer = window.setInterval(tick, intervalMs);
    };
    const onVisibility = () => {
      if (document.hidden) {
        window.clearInterval(timer);
      } else {
        void tick();
        start();
      }
    };

    void tick();
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled, ...deps]);
}
