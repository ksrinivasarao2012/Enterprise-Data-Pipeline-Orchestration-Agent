/**
 * Reuse objects from `prev` that are deep-equal to their counterpart in `next`
 * (matched by key) so memoized list items do not re-render on every poll.
 * Returns `prev` itself when nothing changed.
 */
export function reconcile<T>(prev: T[] | null, next: T[], key: (item: T) => string): T[] {
  if (!prev) return next;
  const old = new Map(prev.map((p) => [key(p), p]));
  let changed = prev.length !== next.length;
  const out = next.map((n, i) => {
    const o = old.get(key(n));
    if (o && JSON.stringify(o) === JSON.stringify(n)) {
      if (prev[i] !== o) changed = true;
      return o;
    }
    changed = true;
    return n;
  });
  return changed ? out : prev;
}

/** Keep the previous object when the new one is deep-equal. */
export function same<T>(prev: T | null, next: T): T {
  return prev !== null && JSON.stringify(prev) === JSON.stringify(next) ? prev : next;
}
