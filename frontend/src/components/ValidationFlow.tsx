import { useEffect, useState } from 'react';
import type { AuditEntry, Incident } from '../api';

/**
 * The path a run takes when the validation rules catch bad rows by themselves:
 * check rows → bad rows found → quarantined → the rest saved. No agent runs here,
 * so instead of an agent graph with unused steps, this shows what really happened.
 */

type Tone = 'info' | 'warn' | 'ok';

const CIRCLE: Record<Tone, string> = {
  info: 'border-info bg-info/15 text-info',
  warn: 'border-warn bg-warn/15 text-warn',
  ok: 'border-ok bg-ok/15 text-ok',
};
const CHIP: Record<Tone, string> = {
  info: 'border-info/40 bg-info/10 text-info',
  warn: 'border-warn/50 bg-warn/10 text-warn',
  ok: 'border-ok/50 bg-ok/10 text-ok',
};

/** Reads "9 saved, 1 quarantined" (Pipeline A) or "aggregated 2 devices, quarantined 1" (Pipeline B). */
function counts(audit: AuditEntry[] | null): { saved: number | null; bad: number | null } {
  const text = (audit ?? []).map((a) => a.message).join(' ');
  const a = text.match(/(\d+)\s+saved,\s*(\d+)\s+quarantined/i);
  if (a) return { saved: Number(a[1]), bad: Number(a[2]) };
  const b = text.match(/aggregated\s+(\d+)\s+devices?,\s*quarantined\s+(\d+)/i);
  if (b) return { saved: Number(b[1]), bad: Number(b[2]) };
  return { saved: null, bad: null };
}

function steps(incident: Incident, audit: AuditEntry[] | null) {
  const { saved, bad } = counts(audit);
  const rows = (n: number | null, one: string, many: string) => (n === null ? many : `${n} ${n === 1 ? one : many}`);
  const isB = incident.pipeline_id === 'PIPELINE_B';
  return [
    { label: 'Check rows', sub: 'Validation rules run on every row', tone: 'info' as Tone },
    { label: 'Bad rows found', sub: `${rows(bad, 'row breaks', 'rows break')} a rule`, tone: 'warn' as Tone },
    { label: 'Quarantined', sub: 'Set aside for review', tone: 'warn' as Tone },
    { label: 'Rest saved', sub: `${rows(saved, isB ? 'device' : 'row', isB ? 'devices' : 'rows')} loaded`, tone: 'ok' as Tone },
  ];
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Big version for Mission control. `step` restarts the animation when it goes back to 0 (Replay). */
export function ValidationFlow({ incident, audit, step }: { incident: Incident; audit: AuditEntry[] | null; step: number }) {
  const list = steps(incident, audit);
  const [shown, setShown] = useState(reducedMotion() ? list.length : 0);

  useEffect(() => {
    if (step === 0) setShown(reducedMotion() ? list.length : 0);
  }, [step, incident.incident_id, list.length]);

  useEffect(() => {
    if (shown >= list.length) return;
    const t = window.setTimeout(() => setShown((s) => s + 1), shown === 0 ? 150 : 400);
    return () => window.clearTimeout(t);
  }, [shown, list.length]);

  return (
    <ol aria-label="Validation steps" className="grid grid-cols-[minmax(0,1fr)] gap-3 py-4 sm:grid-cols-4 sm:gap-0">
      {list.map((s, i) => {
        const on = i < shown;
        return (
          <li key={s.label} className="relative flex items-center gap-3 sm:flex-col sm:gap-2 sm:text-center">
            {i < list.length - 1 && (
              <span
                aria-hidden="true"
                className={`absolute hidden h-0.5 transition-colors duration-300 sm:block ${on && i + 1 < shown ? 'bg-fg/30' : 'bg-line'}`}
                style={{ left: 'calc(50% + 30px)', right: 'calc(-50% + 30px)', top: 28 }}
              />
            )}
            <span
              className={`relative z-10 flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2 font-mono text-sm transition-all duration-300 ${
                on ? CIRCLE[s.tone] : 'border-line bg-surface text-faint'
              } ${on && i === shown - 1 ? 'animate-pop' : ''}`}
            >
              {s.tone === 'ok' && on ? '✓' : `0${i + 1}`}
            </span>
            <span className="min-w-0">
              <span className={`block text-[15px] font-semibold ${on ? '' : 'text-faint'}`}>{s.label}</span>
              <span className="block text-xs text-muted">{s.sub}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Compact chip version for the incident detail panel. */
export function ValidationStrip({ incident, audit }: { incident: Incident; audit: AuditEntry[] | null }) {
  const list = steps(incident, audit);
  return (
    <ol aria-label="Validation steps" className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
      {list.map((s, i) => (
        <li key={s.label} className="flex items-center gap-1">
          <span className={`inline-flex h-7 items-center rounded-md border px-2 text-xs font-medium ${CHIP[s.tone]}`}>{s.label}</span>
          {i < list.length - 1 && (
            <span aria-hidden="true" className="text-faint">
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
