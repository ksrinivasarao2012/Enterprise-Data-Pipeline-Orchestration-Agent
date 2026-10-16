import type { AuditEntry, Incident, IncidentStatus } from '../api';

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral';

/** Written out in full so Tailwind can find the class names. */
export const PILL_CLASS: Record<Tone, string> = {
  ok: 'pill pill-ok',
  warn: 'pill pill-warn',
  bad: 'pill pill-bad',
  info: 'pill pill-info',
  neutral: 'pill pill-neutral',
};

export const STATUS_META: Record<IncidentStatus, { label: string; tone: Tone }> = {
  OPEN: { label: 'Open', tone: 'info' },
  INVESTIGATING: { label: 'Investigating', tone: 'info' },
  RCA_COMPLETED: { label: 'Diagnosed', tone: 'info' },
  RECOVERY_IN_PROGRESS: { label: 'Fixing', tone: 'info' },
  RESOLVED: { label: 'Healed', tone: 'ok' },
  ESCALATED: { label: 'Sent to a human', tone: 'bad' },
};

export function statusMeta(status: string): { label: string; tone: Tone } {
  return STATUS_META[status as IncidentStatus] ?? { label: status, tone: 'neutral' };
}

/**
 * Warnings raised when bad rows were quarantined and the rest of the data loaded.
 * Nothing failed, so no agent ran: these are NOT agent heals.
 */
const QUARANTINE_ONLY = new Set(['DataQualityAnomaly', 'ReferentialIntegrityAnomaly']);
export const isQuarantineOnly = (incident: Incident) => QUARANTINE_ONLY.has(incident.error_class ?? '');

/** Status pill for an incident: quarantine-only warnings show as "Quarantined", not "Healed". */
export function incidentStatusMeta(incident: Incident): { label: string; tone: Tone } {
  return isQuarantineOnly(incident) ? { label: 'Quarantined', tone: 'warn' } : statusMeta(incident.status);
}

export const isFinal = (status: string) => status === 'RESOLVED' || status === 'ESCALATED';

const GUARD_PATTERNS: RegExp[] = [/SQL guard rejected/i, /LLM file repair rejected/i, /UNRESOLVABLE/, /Dry run rejected the fix/i];

/** Text after "rejected" (or after UNRESOLVABLE), cleaned up for display. */
function reasonFrom(text: string): string {
  const m = text.match(/rejected(?:\s+the\s+LLM\s+repair|\s+the\s+fix)?\s*[:\-–—]?\s*(.*)$/is) ?? text.match(/UNRESOLVABLE\s*[:\-–—]?\s*(.*)$/s);
  const reason = (m?.[1] ?? '').trim() || text.trim();
  return reason.charAt(0).toUpperCase() + reason.slice(1);
}

/**
 * The "Root cause" text shown for an incident. RCA does not always run (CHANGES.md section 23:
 * a safe rule may already know the fix), so an empty root cause is explained, not left as "Not found yet".
 */
export function rootCauseText(incident: Incident): string {
  if (incident.root_cause) return incident.root_cause;
  if (isQuarantineOnly(incident)) return 'Some rows failed the validation rules (see Records).';
  if (incident.status === 'RESOLVED') return "Not needed: a safe rule already knew the fix, so Gemini wasn't asked.";
  if (incident.status === 'ESCALATED') return 'Not analysed: the incident was handed to a person before RCA ran.';
  return 'Not found yet.';
}

export function isGuardMessage(text: string | null | undefined): boolean {
  return !!text && GUARD_PATTERNS.some((re) => re.test(text));
}

/**
 * If a guard blocked an AI fix for this incident, return the reason; otherwise null.
 * Checks recovery_action for "SQL guard rejected" and every audit message for
 * "LLM file repair rejected" or "UNRESOLVABLE".
 */
export function guardReason(incident: Incident, audit?: AuditEntry[]): string | null {
  if (isGuardMessage(incident.recovery_action)) {
    return reasonFrom(incident.recovery_action as string);
  }
  const hit = audit?.find((a) => /LLM file repair rejected/i.test(a.message) || /UNRESOLVABLE/.test(a.message) || /Dry run rejected the fix/i.test(a.message));
  return hit ? reasonFrom(hit.message) : null;
}

function parseTime(s: string | null | undefined): number | null {
  if (!s) return null;
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : t;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s % 60);
  if (m < 60) return rest ? `${m} min ${rest} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

export function timeTaken(incident: Incident): string {
  const start = parseTime(incident.created_at);
  const end = parseTime(incident.resolved_at);
  if (start === null) return '—';
  if (end === null) return isFinal(incident.status) ? '—' : 'Still running';
  return formatDuration(end - start);
}

export function formatTime(s: string | null | undefined): string {
  const t = parseTime(s);
  if (t === null) return s ?? '—';
  return new Date(t).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function pipelineName(id: string): string {
  const v = id.toLowerCase();
  if (v === 'a' || v.includes('pipeline_a') || v.endsWith('_a')) return 'Pipeline A';
  if (v === 'b' || v.includes('pipeline_b') || v.endsWith('_b')) return 'Pipeline B';
  return id;
}

/** Turns SCHEMA_DRIFT into "Schema drift". */
export function humanize(s: string | null | undefined): string {
  if (!s) return '';
  const t = s.replace(/_/g, ' ').toLowerCase().trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}
