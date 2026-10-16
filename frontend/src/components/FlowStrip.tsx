import type { AuditEntry, Incident } from '../api';
import { computeFlow, type NodeState } from '../lib/flow';

const CHIP: Record<NodeState, string> = {
  idle: 'border-line text-faint',
  lit: 'border-info/40 bg-info/10 text-info',
  blocked: 'border-bad/50 bg-bad/10 text-bad',
  skipped: 'border-dashed border-line text-faint opacity-60',
  healed: 'border-ok/50 bg-ok/10 text-ok',
  escalated: 'border-bad/50 bg-bad/10 text-bad',
  quarantined: 'border-warn/50 bg-warn/10 text-warn',
};

/** Compact one-line version of the agent flow for the incident detail panel. */
export function FlowStrip({ incident, audit, step }: { incident: Incident; audit: AuditEntry[] | null; step: number }) {
  const { nodes, path } = computeFlow(incident, audit, step);
  // The path this incident took (the graph branches), or the main path before anything ran.
  const order = path.length ? path : ['MONITOR_AGENT', 'CLASSIFIER_AGENT', 'RECOVERY_AGENT', 'ACTUATOR', 'FINAL'];
  const shown = order
    .filter((k, i) => order.indexOf(k) === i)
    .map((k) => nodes.find((n) => n.key === k))
    .filter((n): n is (typeof nodes)[number] => !!n);
  return (
    <ol aria-label="Agent steps" className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
      {shown.map((n, i) => (
        <li key={n.key} className="flex items-center gap-1">
          <span
            className={`inline-flex h-7 items-center rounded-md border px-2 text-xs font-medium transition-colors duration-300 ${CHIP[n.state]} ${
              n.active ? 'animate-pop' : ''
            }`}
          >
            {n.key === 'RCA_AGENT' ? 'RCA' : n.label}
            {n.state === 'blocked' && <span className="sr-only"> (blocked by guard)</span>}
            {n.state === 'skipped' && <span className="sr-only"> (skipped)</span>}
          </span>
          {i < shown.length - 1 && (
            <span aria-hidden="true" className="text-faint">
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
