// Where the dashboard reads its data from (CHANGES.md section 32).
//
//   live -> the API: your own runs (or everyone's, for the admin)
//   demo -> a frozen recording of all the presets (demo/snapshot.json, made with real Gemini by
//           scripts/make_demo_snapshot.py), filtered and paged here in the browser
//
// The same components show both, so the Demo tab looks exactly like the real app.
import { createContext, useContext } from 'react';
import {
  NO_FILTERS,
  api,
  type AuditEntry,
  type Escalation,
  type Incident,
  type IncidentFilters,
  type IncidentPage,
  type Metrics,
  type Page,
  type QuarantineRecord,
} from './api';

export interface DataSource {
  /** True for the Demo tab: nothing can be run or cleared. */
  readOnly: boolean;
  metrics: () => Promise<Metrics>;
  incidents: (limit?: number, filters?: IncidentFilters) => Promise<IncidentPage>;
  audit: (incidentId: string) => Promise<AuditEntry[]>;
  report: (incidentId: string, format: 'json' | 'pdf') => Promise<Blob>;
  quarantine: (limit?: number) => Promise<Page<QuarantineRecord>>;
  escalations: (limit?: number) => Promise<Page<Escalation>>;
}

export const liveSource: DataSource = {
  readOnly: false,
  metrics: api.metrics,
  incidents: api.incidents,
  audit: api.audit,
  report: api.report,
  quarantine: api.quarantine,
  escalations: api.escalations,
};

export interface DemoSnapshot {
  meta: { recorded_at: string; model: string; presets: number; as_expected: number; gemini_calls: number };
  metrics: Metrics;
  incidents: Incident[];
  audits: Record<string, AuditEntry[]>;
  quarantine: QuarantineRecord[];
  escalations: Escalation[];
}

// Same rules as the server's incident filters (src/incidents/incident_repository.py).
const QUARANTINE_ONLY = new Set(['DataQualityAnomaly', 'ReferentialIntegrityAnomaly']);
const SEARCHED: (keyof Incident)[] = ['incident_id', 'run_id', 'error_class', 'error_message', 'category', 'root_cause'];

export function matches(inc: Incident, f: IncidentFilters): boolean {
  if (f.pipeline && inc.pipeline_id !== f.pipeline) return false;
  const qOnly = QUARANTINE_ONLY.has(inc.error_class ?? '');
  if (f.outcome === 'healed' && !(inc.status === 'RESOLVED' && !qOnly)) return false;
  if (f.outcome === 'quarantined' && !(inc.status === 'RESOLVED' && qOnly)) return false;
  if (f.outcome === 'escalated' && inc.status !== 'ESCALATED') return false;
  if (f.outcome === 'in_progress' && (inc.status === 'RESOLVED' || inc.status === 'ESCALATED')) return false;
  if (f.blocked && !inc.blocked_by_guard) return false;
  const q = f.q.trim().toLowerCase();
  if (q && !SEARCHED.some((k) => String(inc[k] ?? '').toLowerCase().includes(q))) return false;
  return true;
}

const pageOf = <T,>(rows: T[], limit: number): Page<T> => ({ items: rows.slice(0, limit), total: rows.length });

export function demoSource(snap: DemoSnapshot): DataSource {
  return {
    readOnly: true,
    metrics: async () => snap.metrics,
    incidents: async (limit = 20, filters = NO_FILTERS) => pageOf(snap.incidents.filter((i) => matches(i, filters)), limit),
    audit: async (id) => snap.audits[id] ?? [],
    report: api.demoReport,
    quarantine: async (limit = 20) => pageOf(snap.quarantine, limit),
    escalations: async (limit = 20) => pageOf(snap.escalations, limit),
  };
}

export const DataContext = createContext<DataSource>(liveSource);
export const useData = () => useContext(DataContext);
