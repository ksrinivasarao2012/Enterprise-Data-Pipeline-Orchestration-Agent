// All calls to the FastAPI backend live here. Every request carries the
// X-Access-Key header; any 401 notifies the app so it can show the login card.

export type Role = 'admin' | 'viewer';

export interface WhoAmI {
  role: Role;
  auth_enabled: boolean;
}

export interface Metrics {
  resolved: number;
  investigating: number;
  escalated: number;
  runs_without_incident: number;
  quarantined: number;
  /** Incidents where a guard blocked the fix, counted on the server (the "blocked fixes" badge). */
  blocked_by_guard: number;
}

export type Severity = 'P0' | 'P1' | 'P2' | 'P3';

export type IncidentStatus =
  | 'OPEN'
  | 'INVESTIGATING'
  | 'RCA_COMPLETED'
  | 'RECOVERY_IN_PROGRESS'
  | 'RESOLVED'
  | 'ESCALATED';

export interface Incident {
  incident_id: string;
  run_id: string;
  pipeline_id: string;
  severity: Severity;
  category: string;
  status: IncidentStatus;
  error_class: string | null;
  error_message: string | null;
  stack_trace: string | null;
  root_cause: string | null;
  recovery_action: string | null;
  /** True when a guard (SQL guard, JSON repair check or UNRESOLVABLE) stopped the AI fix. Stored by the server. */
  blocked_by_guard?: boolean;
  created_at: string;
  resolved_at: string | null;
}

export type AuditComponent =
  | 'MONITOR_AGENT'
  | 'CLASSIFIER_AGENT'
  | 'RCA_AGENT'
  | 'RECOVERY_AGENT'
  | 'ACTUATOR'
  | (string & {});

export interface AuditEntry {
  component: AuditComponent;
  message: string;
  timestamp: string;
}

export interface QuarantineRecord {
  quarantine_id: string;
  pipeline_id: string;
  run_id: string;
  record_type: string;
  raw_record: unknown;
  validation_errors: string[];
  created_at: string;
}

export interface Escalation {
  timestamp: string;
  incident_id: string;
  /** The raw technical error. */
  reason: string;
  /** Plain words: what went wrong. */
  summary?: string;
  /** Plain words: why a person has to decide. */
  why?: string;
}

export type PipelineId = 'a' | 'b';

export type ExecuteResult =
  | { status: 'success'; rows_ingested: number; healed?: false; quarantined?: false }
  | { status: 'success'; rows_ingested: 'Auto-healed'; healed: true }
  | { status: 'success'; rows_ingested: number; quarantined: true; quarantined_count: number | 'all' }
  | { status: 'failure'; detail: string };

/** Endpoint used by the admin-only "Clear history" button. Change here if the backend differs. */
const CLEAR_HISTORY = { method: 'POST', path: '/api/history/clear' } as const;

const KEY_STORAGE = 'accessKey';

export function getAccessKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? '';
  } catch {
    return '';
  }
}

export function setAccessKey(key: string): void {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable: the key lives only for this page load */
  }
}

/** A failed request. status 0 means the API could not be reached at all. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

let onUnauthorized: () => void = () => {};

/** Register the handler the app runs on any 401. */
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

interface RequestOptions {
  method?: string;
  body?: BodyInit;
  signal?: AbortSignal;
  /** Skip the global 401 handler (used by the login form itself). */
  quiet401?: boolean;
  /** Extra headers (e.g. the "Try all" demo token). */
  headers?: Record<string, string>;
}

async function readDetail(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === 'object' && 'detail' in data) {
      const d = (data as { detail: unknown }).detail;
      if (typeof d === 'string') return d;
      return JSON.stringify(d);
    }
  } catch {
    /* not JSON */
  }
  return res.statusText || `Request failed (${res.status})`;
}

async function send(path: string, opts: RequestOptions = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? 'GET',
      body: opts.body,
      signal: opts.signal,
      headers: { ...opts.headers, 'X-Access-Key': getAccessKey(), Accept: 'application/json' },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(0, 'Cannot reach the API. Is the backend running?');
  }

  if (res.status === 401) {
    if (!opts.quiet401) onUnauthorized();
    throw new ApiError(401, await readDetail(res));
  }
  if (!res.ok) throw new ApiError(res.status, await readDetail(res));
  return res;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** One page of rows (newest first) plus the total count from the X-Total-Count header. */
export interface Page<T> {
  items: T[];
  total: number;
}
export type IncidentPage = Page<Incident>;

export type Outcome = 'healed' | 'quarantined' | 'escalated' | 'in_progress';
/** Incident list filters; empty string = no filter. Applied on the server, so paging stays right. */
export interface IncidentFilters {
  pipeline: '' | 'PIPELINE_A' | 'PIPELINE_B';
  outcome: '' | Outcome;
  q: string;
  /** Only incidents where a guard blocked the fix (also applied on the server). */
  blocked: boolean;
}
export const NO_FILTERS: IncidentFilters = { pipeline: '', outcome: '', q: '', blocked: false };

async function page<T>(path: string, limit: number, extra: Record<string, string> = {}): Promise<Page<T>> {
  const qs = new URLSearchParams({ limit: String(limit) });
  for (const [k, v] of Object.entries(extra)) if (v) qs.set(k, v);
  const res = await send(`${path}?${qs}`);
  const items = (await res.json()) as T[];
  const total = Number(res.headers.get('X-Total-Count') ?? items.length);
  return { items, total: Number.isFinite(total) ? total : items.length };
}

export const api = {
  whoami: (quiet401 = true) => request<WhoAmI>('/api/whoami', { quiet401 }),
  metrics: () => request<Metrics>('/api/metrics'),
  incidents: (limit = 20, filters: IncidentFilters = NO_FILTERS) =>
    page<Incident>('/api/incidents', limit, { pipeline: filters.pipeline, outcome: filters.outcome, q: filters.q.trim(), blocked: filters.blocked ? 'true' : '' }),
  audit: (incidentId: string) =>
    request<AuditEntry[]>(`/api/incidents/${encodeURIComponent(incidentId)}/audit`),
  /** One incident + its audit trail as a file (JSON or PDF), ready to save. */
  report: async (incidentId: string, format: 'json' | 'pdf'): Promise<Blob> => {
    const res = await send(`/api/incidents/${encodeURIComponent(incidentId)}/report?format=${format}`);
    return res.blob();
  },
  quarantine: (limit = 20) => page<QuarantineRecord>('/api/quarantine', limit),
  escalations: (limit = 20) => page<Escalation>('/api/escalations', limit),
  clearHistory: () => request<unknown>(CLEAR_HISTORY.path, { method: CLEAR_HISTORY.method }),

  /** Run a pipeline with either a preset test case or an uploaded file. */
  execute: (pipeline: PipelineId, input: { testCase: string } | { file: File }, demoToken?: string) => {
    const form = new FormData();
    if ('file' in input) form.append('file', input.file);
    else form.append('test_case', input.testCase);
    return request<ExecuteResult>(`/api/pipelines/${pipeline}/execute`, {
      method: 'POST',
      body: form,
      headers: demoToken ? { 'X-Demo-Token': demoToken } : undefined,
    });
  },

  /** "Try all": counts as one run; the token lets each preset run once without counting again. */
  demoStart: () => request<{ token: string }>('/api/demo/start', { method: 'POST' }),
};
