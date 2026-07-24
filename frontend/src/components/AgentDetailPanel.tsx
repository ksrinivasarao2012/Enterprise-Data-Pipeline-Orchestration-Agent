import type { AuditEntry, Incident } from '../api';
import { agentDetail } from '../lib/agentDetail';
import { formatTime } from '../lib/incident';

/** Shown under the flow graph after a circle is clicked: what that agent did in this run. */
export function AgentDetailPanel({
  nodeKey,
  incident,
  audit,
  onClose,
}: {
  nodeKey: string;
  incident: Incident;
  audit: AuditEntry[] | null;
  onClose: () => void;
}) {
  const d = agentDetail(nodeKey, incident, audit);
  return (
    <section id="agent-detail" aria-label={`What ${d.title} did`} className="space-y-3 rounded-xl border border-line bg-surface px-4 py-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold">{d.title}</h3>
          <p className="text-[13px] text-muted">{d.role}</p>
        </div>
        <button type="button" className="btn h-8 px-2.5 text-xs" onClick={onClose}>
          Close
        </button>
      </div>

      {d.facts.length > 0 && (
        <dl className="grid grid-cols-[minmax(0,1fr)] gap-2 text-[13px] sm:grid-cols-[140px_minmax(0,1fr)]">
          {d.facts.map((f) => (
            <div key={f.label} className="contents">
              <dt className="font-medium text-muted">{f.label}</dt>
              <dd className={`[overflow-wrap:anywhere] ${f.long ? 'whitespace-pre-wrap' : ''}`}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {d.lines.length > 0 && (
        <ol className="space-y-1.5 border-t border-line pt-2.5 font-mono text-[12px]">
          {d.lines.map((a, i) => (
            <li key={i} className="flex gap-2">
              <span className="shrink-0 text-faint">{formatTime(a.timestamp)}</span>
              <span className="min-w-0 [overflow-wrap:anywhere]">{a.message}</span>
            </li>
          ))}
        </ol>
      )}

      {d.empty && <p className="text-[13px] text-muted">{d.empty}</p>}
    </section>
  );
}
