import { useState } from 'react';
import type { AuditEntry, Incident } from '../api';
import { NODE_NAME } from '../lib/flow';
import { PILL_CLASS, formatTime, humanize, isGuardMessage, incidentStatusMeta, isFinal, isQuarantineOnly } from '../lib/incident';
import { DownloadReport } from './DownloadReport';
import { FlowStrip } from './FlowStrip';
import { ValidationStrip } from './ValidationFlow';
import { WhatChanged } from './WhatChanged';
import { ChevronIcon, ReplayIcon, ShieldXIcon } from './icons';

interface Props {
  incident: Incident;
  audit: AuditEntry[] | null;
  step: number;
  guardReason: string | null;
  onReplay: () => void;
}

export function AuditPanel({ incident, audit, step, guardReason, onReplay }: Props) {
  const [techOpen, setTechOpen] = useState(false);
  const st = incidentStatusMeta(incident);

  return (
    <aside
      id="incident-detail"
      aria-label={`Incident ${incident.incident_id}`}
      className="card scroll-mt-4 space-y-4 p-4 sm:p-5 lg:sticky lg:top-5"
    >
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={PILL_CLASS[st.tone]}>{st.label}</span>
          <span className="pill pill-neutral">{incident.severity}</span>
          <span className="ml-auto font-mono text-xs text-faint">{incident.incident_id}</span>
        </div>
        <h3 className="text-base font-semibold">{humanize(incident.category)}</h3>
        <DownloadReport incidentId={incident.incident_id} />
      </div>

      <WhatChanged incident={incident} audit={audit} blocked={!!guardReason} />

      <div className="space-y-2 rounded-[10px] bg-surface2 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-muted">
            {isQuarantineOnly(incident) ? 'Validation steps · no agents needed' : 'Agent flow · replayed from the audit log'}
          </span>
          {isFinal(incident.status) && (
            <button type="button" className="btn h-8 px-2.5 text-xs" onClick={onReplay} disabled={!audit?.length}>
              <ReplayIcon size={12} />
              Replay
            </button>
          )}
        </div>
        {isQuarantineOnly(incident) ? (
          <ValidationStrip incident={incident} audit={audit} />
        ) : (
          <FlowStrip incident={incident} audit={audit} step={step} />
        )}
      </div>

      <h4 className="text-sm font-semibold">Audit trail</h4>

      {guardReason && (
        <div role="note" className="flex gap-2.5 rounded-[10px] border border-bad bg-bad/10 px-3.5 py-3">
          <ShieldXIcon size={18} className="mt-px shrink-0 text-bad" />
          <div className="min-w-0 space-y-0.5">
            <div className="text-[13.5px] font-semibold text-bad">Blocked by guard</div>
            <p className="break-words text-[13px] leading-snug">{guardReason}</p>
          </div>
        </div>
      )}

      {!audit ? (
        <div className="space-y-3" aria-label="Loading audit trail">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-10" />
          ))}
        </div>
      ) : audit.length === 0 ? (
        <p className="text-sm text-muted">No audit entries yet. They appear here as the agents work.</p>
      ) : (
        <ol className="space-y-3.5">
          {audit.map((a, i) => {
            const shown = i < step;
            const guard = shown && isGuardMessage(a.message);
            return (
              <li
                key={`${a.timestamp}-${i}`}
                className={`grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 transition-opacity duration-300 ${shown ? 'opacity-100' : 'opacity-35'}`}
              >
                <span
                  aria-hidden="true"
                  className={`ml-[5px] mt-[5px] h-2.5 w-2.5 rounded-full transition-colors duration-300 ${
                    guard ? 'bg-bad' : shown ? 'bg-info' : 'bg-line'
                  } ${i === step - 1 ? 'shadow-[0_0_0_4px] shadow-info/20' : ''}`}
                />
                <div className="min-w-0 space-y-0.5">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-[13px] font-semibold">{NODE_NAME[a.component] ?? a.component}</span>
                    <span className="font-mono text-[11px] text-faint">
                      {a.component} · {formatTime(a.timestamp)}
                    </span>
                  </div>
                  <p className={`break-words text-[13px] leading-snug ${guard ? 'text-bad' : 'text-muted'}`}>{a.message}</p>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <div className="space-y-2.5 border-t border-line pt-3">
        <button
          type="button"
          onClick={() => setTechOpen((v) => !v)}
          aria-expanded={techOpen}
          aria-controls="tech-details"
          className="flex items-center gap-1.5 rounded text-[13px] font-medium"
        >
          <ChevronIcon size={14} className={`transition-transform duration-200 ${techOpen ? 'rotate-90' : ''}`} />
          Technical details
        </button>
        {techOpen && (
          <div id="tech-details" className="space-y-1.5">
            <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
              <dt className="text-faint">Error class</dt>
              <dd className="break-all font-mono text-muted">{incident.error_class || '—'}</dd>
              <dt className="text-faint">Pipeline</dt>
              <dd className="break-all font-mono text-muted">{incident.pipeline_id}</dd>
              <dt className="text-faint">Started</dt>
              <dd className="text-muted">{formatTime(incident.created_at)}</dd>
              <dt className="text-faint">Finished</dt>
              <dd className="text-muted">{incident.resolved_at ? formatTime(incident.resolved_at) : '—'}</dd>
            </dl>
            <pre
              tabIndex={0}
              aria-label="Stack trace"
              className="max-h-56 overflow-auto rounded-lg border border-line bg-surface2 p-3 font-mono text-[11.5px] leading-relaxed"
            >
              {incident.stack_trace || 'No stack trace recorded.'}
            </pre>
          </div>
        )}
      </div>
    </aside>
  );
}
