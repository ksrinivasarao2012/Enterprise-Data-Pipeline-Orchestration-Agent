import type { AuditEntry, Incident } from '../api';
import { isQuarantineOnly, rootCauseText } from '../lib/incident';

interface Props {
  incident: Incident;
  audit: AuditEntry[] | null;
  blocked: boolean;
}

/** Finds renames written as "old → new" or "old -> new" in the fix description and audit log. */
function findMappings(texts: string[]): { from: string; to: string }[] {
  const re = /([\w.$"'`]+)\s*(?:→|->)\s*([\w.$"'`]+)/g;
  const seen = new Set<string>();
  const out: { from: string; to: string }[] = [];
  const clean = (s: string) => s.replace(/^["'`]+|["'`.,;]+$/g, '');
  for (const t of texts) {
    for (const m of t.matchAll(re)) {
      const from = clean(m[1]);
      const to = clean(m[2]);
      const key = `${from}>${to}`;
      if (from && to && from !== to && !seen.has(key)) {
        seen.add(key);
        out.push({ from, to });
      }
    }
  }
  return out;
}

type After = { label: string; box: string; tag: string; body: string; detail: string | null };

function describeAfter(incident: Incident, audit: AuditEntry[] | null, blocked: boolean): After {
  const verify = [...(audit ?? [])].reverse().find((a) => a.component === 'ACTUATOR')?.message ?? null;
  const action = incident.recovery_action?.trim() || null;

  if (incident.status === 'RESOLVED' && isQuarantineOnly(incident)) {
    return {
      label: 'After · quarantined',
      box: 'border-warn/35 bg-warn/[0.06]',
      tag: 'text-warn',
      body: 'The bad rows were set aside for review and the rest of the data loaded. Nothing failed, so no agent fix was needed.',
      detail: verify ?? [...(audit ?? [])].reverse()[0]?.message ?? null,
    };
  }
  if (incident.status === 'RESOLVED') {
    return {
      label: 'After · healed',
      box: 'border-ok/35 bg-ok/[0.06]',
      tag: 'text-ok',
      body: action ?? 'The agents applied a fix.',
      detail: verify,
    };
  }
  if (incident.status === 'ESCALATED' && blocked) {
    return {
      label: 'After · nothing changed',
      box: 'border-line bg-surface2',
      tag: 'text-muted',
      body: 'The proposed fix was blocked by a guard, so the original data was kept as it was.',
      detail: 'A person has been asked to decide what to do.',
    };
  }
  if (incident.status === 'ESCALATED') {
    return {
      label: 'After · sent to a human',
      box: 'border-line bg-surface2',
      tag: 'text-muted',
      body: action ?? 'No automatic fix was applied.',
      detail: verify,
    };
  }
  return {
    label: 'After · in progress',
    box: 'border-info/35 bg-info/[0.06]',
    tag: 'text-info',
    body: action ?? 'The agents are still working on a fix.',
    detail: null,
  };
}

/** Before → cause → after summary for the selected incident. */
export function WhatChanged({ incident, audit, blocked }: Props) {
  const after = describeAfter(incident, audit, blocked);
  const mappings =
    incident.status === 'RESOLVED'
      ? findMappings([incident.recovery_action ?? '', ...(audit ?? []).filter((a) => a.component !== 'MONITOR_AGENT').map((a) => a.message)])
      : [];

  return (
    <section aria-labelledby="changes-title" className="space-y-2">
      <h4 id="changes-title" className="text-sm font-semibold">
        What changed
      </h4>

      <div className="rounded-[10px] border border-bad/35 bg-bad/[0.06] p-3">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-bad">
          {isQuarantineOnly(incident) ? 'Before · bad rows found' : 'Before · broken'}
        </div>
        <p className="mt-1.5 break-words text-[14.5px] font-medium leading-snug">
          {incident.error_message || incident.error_class || 'The run failed.'}
        </p>
      </div>

      <div className="flex gap-2.5 pl-3">
        <span aria-hidden="true" className="flex w-4 flex-col items-center">
          <span className="w-px flex-1 bg-line" />
          <span className="text-xs leading-none text-faint">↓</span>
        </span>
        <p className="min-w-0 py-1.5 text-[12.5px] leading-snug text-muted">
          <span className="font-medium text-fg">Cause: </span>
          {rootCauseText(incident)}
        </p>
      </div>

      <div className={`rounded-[10px] border p-3 ${after.box}`}>
        <div className={`text-[11px] font-semibold uppercase tracking-wider ${after.tag}`}>{after.label}</div>
        <p className="mt-1.5 break-words text-[14.5px] font-medium leading-snug">{after.body}</p>
        {mappings.length > 0 && (
          <div className="mt-2.5 overflow-x-auto rounded-md border border-line bg-surface font-mono text-xs" aria-label="Renamed fields">
            {mappings.map((m) => (
              <div key={`${m.from}>${m.to}`} className="grid grid-cols-2 divide-x divide-line">
                <span className="bg-bad/[0.07] px-2.5 py-1.5">
                  <span aria-hidden="true" className="text-bad">− </span>
                  <span className="sr-only">Before: </span>
                  <span className="line-through decoration-bad/60">{m.from}</span>
                </span>
                <span className="bg-ok/[0.07] px-2.5 py-1.5">
                  <span aria-hidden="true" className="text-ok">+ </span>
                  <span className="sr-only">After: </span>
                  {m.to}
                </span>
              </div>
            ))}
          </div>
        )}
        {after.detail && <p className="mt-2 break-words text-[12.5px] text-muted">{after.detail}</p>}
      </div>
    </section>
  );
}
