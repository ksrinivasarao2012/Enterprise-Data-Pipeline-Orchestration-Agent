// All calls to the FastAPI backend live here. People sign in with Google; the server then
// keeps them signed in with an HttpOnly cookie the page can't read (CHANGES.md section 31).
// Every request sends the X-Requested-With header, which the server requires for anything that
// changes data (protection against cross-site request forgery). Any 401 notifies the app so it
// can show the sign-in page.

export type Role = 'admin' | 'viewer';

/** 'google': sign in with Google · 'open': local development, no sign-in · 'locked': not set up · 'key': scripts only */
export type AuthMode = 'google' | 'open' | 'locked' | 'key';

export interface WhoAmI {
  role: Role;
  auth_enabled: boolean;
  mode: AuthMode;
  email: string;
  name: string;
}

export interface AuthConfig {
  mode: AuthMode;
  google_client_id: string | null;
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

/** "Clear history": viewers clear their own runs, the admin clears everything. */
const CLEAR_HISTORY = { method: 'POST', path: '/api/history/clear' } as const;

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
  /** Skip the global 401 handler (used by the sign-in page itself). */
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
      credentials: 'same-origin',
      headers: { ...opts.headers, 'X-Requested-With': 'eha', Accept: 'application/json' },
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
  /** Public: is sign-in set up, and with which Google app. */
  authConfig: () => request<AuthConfig>('/api/auth/config', { quiet401: true }),
  /** Sends Google's ID token; the server checks it and sets the login cookie. */
  googleLogin: (credential: string) =>
    request<WhoAmI>('/api/auth/google', {
      method: 'POST',
      quiet401: true,
      body: JSON.stringify({ credential }),
      headers: { 'Content-Type': 'application/json' },
    }),
  logout: () => request<unknown>('/api/auth/logout', { method: 'POST', quiet401: true }),
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

  /** The Demo tab's frozen recording of all 13 presets (404 until one is recorded). */
  demoSnapshot: () => request<import('./data').DemoSnapshot>('/api/demo/snapshot'),
  /** Report download for an incident in the recording. */
  demoReport: async (incidentId: string, format: 'json' | 'pdf'): Promise<Blob> => {
    const res = await send(`/api/demo/incidents/${encodeURIComponent(incidentId)}/report?format=${format}`);
    return res.blob();
  },

  /** "Try all": counts as one run; the token lets each preset run once without counting again. */
  demoStart: () => request<{ token: string }>('/api/demo/start', { method: 'POST' }),
};
