import { memo } from 'react';
import type { Metrics } from '../api';

const ITEMS: { key: keyof Metrics; label: string; dot: string }[] = [
  { key: 'resolved', label: 'Healed', dot: 'bg-ok' },
  { key: 'investigating', label: 'In progress', dot: 'bg-info' },
  { key: 'escalated', label: 'Sent to a human', dot: 'bg-bad' },
  { key: 'runs_without_incident', label: 'Clean runs', dot: 'bg-faint' },
  { key: 'quarantined', label: 'Rows quarantined', dot: 'bg-warn' },
];

/** Editorial numbers: big serif figures separated by hairlines (a 1px-gap grid over the line colour). */
export const MetricsStrip = memo(function MetricsStrip({ metrics }: { metrics: Metrics | null }) {
  return (
    <section aria-label="Metrics">
      <dl className="grid grid-cols-2 gap-px border-y border-line bg-line sm:grid-cols-3 lg:grid-cols-5">
        {ITEMS.map((m, i) => (
          <div
            key={m.key}
            className={`flex flex-col-reverse gap-1 bg-bg px-3 py-4 sm:px-5 ${i === ITEMS.length - 1 ? 'col-span-2 lg:col-span-1' : ''}`}
          >
            <dt className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
              <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} aria-hidden="true" />
              {m.label}
            </dt>
            <dd className="font-display text-[52px] leading-none tracking-tight tabular-nums sm:text-[64px]">
              {metrics ? (
                metrics[m.key].toLocaleString()
              ) : (
                <span className="skeleton inline-block h-12 w-14 align-middle" aria-label="Loading" />
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
});
