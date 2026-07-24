import { memo, useState } from 'react';
import type { Incident } from '../api';
import { PILL_CLASS, humanize, pipelineName, incidentStatusMeta, isQuarantineOnly, timeTaken, rootCauseText } from '../lib/incident';
import { ShieldIcon } from './icons';

interface Props {
  incident: Incident;
  selected: boolean;
  blocked: boolean;
  onSelect: (id: string) => void;
}

const LONG = 140;

export const IncidentCard = memo(function IncidentCard({ incident, selected, blocked, onSelect }: Props) {
  const [expanded, setExpanded] = useState(false);
  const st = incidentStatusMeta(incident);
  const message = incident.error_message || incident.error_class || 'No error message recorded.';
  const isLong = message.length > LONG;

  return (
    <article
      className={`card transition-colors ${selected ? 'border-fg shadow-[0_0_0_1px] shadow-fg' : 'hover:border-faint'}`}
    >
      <button
        type="button"
        onClick={() => {
          onSelect(incident.incident_id);
          if (isLong) setExpanded(true);
        }}
        aria-pressed={selected}
        className="flex w-full flex-col gap-2.5 rounded-xl px-4 pb-2 pt-4 text-left sm:px-[18px]"
      >
        <span className="flex w-full flex-wrap items-center gap-1.5">
          <span className={PILL_CLASS[st.tone]}>{st.label}</span>
          <span className="pill pill-neutral">{incident.severity}</span>
          {blocked && (
            <span className="pill pill-bad">
              <ShieldIcon size={12} strokeWidth={2.4} />
              Blocked by guard
            </span>
          )}
          <span className="ml-1 text-[13px] text-muted">{humanize(incident.category)}</span>
          <span className="flex-1" />
          <span className="font-mono text-xs text-faint">
            {incident.incident_id} · {pipelineName(incident.pipeline_id)}
          </span>
        </span>
        <span className={`block break-words [overflow-wrap:anywhere] text-[16px] font-semibold leading-snug ${expanded ? '' : 'line-clamp-2'}`}>
          {message}
        </span>
      </button>

      <div className="space-y-2 px-4 pb-4 sm:px-[18px]">
        {isLong && (
          <button
            type="button"
            className="text-[13px] font-medium text-muted underline-offset-4 hover:text-fg hover:underline"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? 'Show less' : 'Show full error'}
          </button>
        )}
        <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
          <dt className="text-faint">Root cause</dt>
          <dd className="break-words text-muted">{rootCauseText(incident)}</dd>
          <dt className="text-faint">Action taken</dt>
          <dd className="break-words text-muted">{incident.recovery_action || (isQuarantineOnly(incident) ? 'Bad rows quarantined, the rest loaded. No agent fix needed.' : 'None yet.')}</dd>
          <dt className="text-faint">Time taken</dt>
          <dd className="text-muted">{timeTaken(incident)}</dd>
        </dl>
      </div>
    </article>
  );
});
