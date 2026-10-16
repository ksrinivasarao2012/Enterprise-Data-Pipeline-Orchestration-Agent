import { useEffect, useState, type ReactNode } from 'react';
import type { AuditEntry, Incident } from '../api';
import { computeFlow } from '../lib/flow';
import { humanize, isFinal, isQuarantineOnly } from '../lib/incident';
import { AgentDetailPanel } from './AgentDetailPanel';
import { FlowGraph } from './FlowGraph';
import { ValidationFlow } from './ValidationFlow';
import { ReplayIcon } from './icons';

interface Props {
  incident: Incident | null;
  audit: AuditEntry[] | null;
  /** How many audit entries have been replayed so far. */
  step: number;
  onReplay: () => void;
  /** A run is in progress: the monitor is "listening". */
  running?: boolean;
  /** Shown instead of the incident line when there is nothing to replay. */
  note?: ReactNode;
  footer?: ReactNode;
}

/** The hero: the agent loop as a glowing graph, replayed from the audit log. */
export function MissionControl({ incident, audit, step, onReplay, running, note, footer }: Props) {
  const { nodes, edges, last, done, live, loops } = computeFlow(incident, audit, step);
  // The circle the user clicked. Cleared when a different incident is shown.
  const [picked, setPicked] = useState<string | null>(null);
  const incidentId = incident?.incident_id ?? null;
  useEffect(() => setPicked(null), [incidentId]);
  if (running) nodes[0] = { ...nodes[0], active: true };

  const outcome = done && incident
    ? incident.status === 'RESOLVED'
      ? isQuarantineOnly(incident) ? 'quarantined' : 'healed'
      : incident.status === 'ESCALATED' ? 'escalated' : null
    : null;
  const liveDot = running || (!!incident && !done) ? 'bg-info' : outcome === 'healed' ? 'bg-ok' : outcome === 'escalated' ? 'bg-bad' : outcome === 'quarantined' ? 'bg-warn' : 'bg-faint';

  const ticker = running
    ? { tag: 'MONITOR_AGENT', text: 'Watching the run…' }
    : last
      ? { tag: last.component, text: last.message }
      : { tag: incident ? 'WAITING' : 'IDLE', text: incident ? (audit ? 'Starting replay…' : 'Loading the audit log…') : 'Pick a scenario and run it.' };

  const summary = incident
    ? `Agent flow for incident ${incident.incident_id}. ${nodes
        .filter((n) => n.state !== 'idle')
        .map((n) => `${n.label}: ${n.state === 'lit' ? 'done' : n.state}`)
        .join(', ') || 'Not started.'}`
    : 'Agent flow. No incident selected.';

  return (
    <section aria-labelledby="mc-title" className="card relative overflow-hidden">
      <div aria-hidden="true" className="dot-grid pointer-events-none absolute inset-0" />
      <div className="relative space-y-2 p-4 sm:p-6">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="eyebrow flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                {(running || (incident && !done)) && (
                  <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${liveDot}`} />
                )}
                <span className={`relative inline-flex h-2 w-2 rounded-full ${liveDot}`} />
              </span>
              Mission control
            </div>
            <h2 id="mc-title" className="font-display text-[28px] leading-none tracking-tight sm:text-[34px]">
              {outcome === 'healed' ? (
                <>
                  Healed <span className="italic text-muted">by the agents.</span>
                </>
              ) : outcome === 'escalated' ? (
                <>
                  Handed <span className="italic text-muted">to a human.</span>
                </>
              ) : outcome === 'quarantined' ? (
                <>
                  Caught by validation. <span className="italic text-muted">No agents needed.</span>
                </>
              ) : (
                <>
                  Agent flow <span className="italic text-muted">step by step.</span>
                </>
              )}
            </h2>
            <p className="truncate text-[13px] text-muted">
              {note ??
                (incident ? (
                  <>
                    <span className="font-mono">{incident.incident_id}</span> · {humanize(incident.category)}
                  </>
                ) : (
                  'Nothing to replay yet.'
                ))}
            </p>
          </div>
          {/* Replay only makes sense once the run has finished; during a live run the steps are already appearing. */}
          {incident && isFinal(incident.status) && !running && (
            <button type="button" className="btn shrink-0" onClick={onReplay} disabled={!audit?.length}>
              <ReplayIcon size={14} />
              Replay
            </button>
          )}
        </div>

        <div className="mx-auto max-w-[880px] px-1 py-2 sm:px-4">
          {incident && isQuarantineOnly(incident) ? (
            <ValidationFlow incident={incident} audit={audit} step={step} />
          ) : (
            <FlowGraph
              nodes={nodes}
              edges={edges}
              label={summary}
              picked={picked}
              loops={loops}
              onPick={incident ? (k) => setPicked((p) => (p === k ? null : k)) : undefined}
            />
          )}
        </div>

        {incident && !isQuarantineOnly(incident) && (picked ? (
          <AgentDetailPanel nodeKey={picked} incident={incident} audit={audit} onClose={() => setPicked(null)} />
        ) : (
          <p className="text-center text-xs text-faint">Click a circle to see what that agent did in this run.</p>
        ))}

        <div
          aria-live="polite"
          className="flex min-h-12 items-start gap-3 rounded-xl border border-line bg-bg/70 px-4 py-3 font-mono text-[12.5px] backdrop-blur-sm"
        >
          <span className="shrink-0 text-info">[{ticker.tag}]</span>
          <span className="min-w-0 break-words">
            {ticker.text}
            <span aria-hidden="true" className="ml-1 inline-block h-3.5 w-[7px] translate-y-0.5 animate-blink bg-fg/70" />
          </span>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <span className="text-xs text-faint">
            {live ? 'Live: each step appears as the agent finishes it.' : 'Replayed from the audit log.'}
            {loops > 0 && (
              <span className="ml-2 font-medium text-info">
                ↺ Verify sent it back to Recovery {loops === 1 ? 'once' : `${loops} times`}
              </span>
            )}
          </span>
          {footer}
        </div>
      </div>
    </section>
  );
}
