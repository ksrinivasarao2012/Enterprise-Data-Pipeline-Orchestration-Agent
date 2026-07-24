import { NO_FILTERS, type AuditEntry, type Incident, type IncidentFilters } from '../api';
import { AuditPanel } from './AuditPanel';
import { IncidentCard } from './IncidentCard';
import { ShieldIcon } from './icons';

const OUTCOME_CHIPS: { label: string; value: IncidentFilters['outcome'] }[] = [
  { label: 'All', value: '' },
  { label: 'Healed', value: 'healed' },
  { label: 'Quarantined', value: 'quarantined' },
  { label: 'Escalated', value: 'escalated' },
  { label: 'In progress', value: 'in_progress' },
];

interface Props {
  incidents: Incident[] | null;
  /** How many incidents exist in total (the list shows only the newest page). */
  total: number;
  onShowMore: () => void;
  /** Search / pipeline / outcome filters (applied on the server). */
  filters: IncidentFilters;
  onFiltersChange: (f: IncidentFilters) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
  blockedReasons: Map<string, string>;
  /** Total blocked fixes in the whole database (from the server), not just on this page. */
  blockedCount: number;
  selectedAudit: AuditEntry[] | null;
  step: number;
  onReplay: () => void;
  isAdmin: boolean;
  onClearHistory: () => void;
  onGoRun: () => void;
}

export function IncidentTimeline({
  incidents,
  total,
  onShowMore,
  filters,
  onFiltersChange,
  selectedId,
  onSelect,
  blockedReasons,
  blockedCount,
  selectedAudit,
  step,
  onReplay,
  isAdmin,
  onClearHistory,
  onGoRun,
}: Props) {
  // Every filter, including "blocked", is applied by the server, so the list, the paging and the
  // "X of Y" total are right even when the match is older than the newest page.
  const list = incidents;
  const blockedOnly = filters.blocked;
  const selected = incidents?.find((i) => i.incident_id === selectedId) ?? null;
  const filtered = filters.pipeline !== '' || filters.outcome !== '' || filters.q.trim() !== '' || filters.blocked;
  const set = (patch: Partial<IncidentFilters>) => onFiltersChange({ ...filters, ...patch });

  return (
    <section aria-labelledby="timeline-title" className="space-y-3.5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="timeline-title" className="flex-1 text-lg font-semibold">
          Incident timeline
          {incidents && (
            <span className="ml-2 text-sm font-normal text-muted">
              {incidents.length} of {Math.max(total, incidents.length)}
              {filtered ? ' matching' : ''}
            </span>
          )}
        </h2>
        <button
          type="button"
          onClick={() => set({ blocked: !filters.blocked })}
          aria-pressed={blockedOnly}
          className={`inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-colors ${
            blockedOnly ? 'border-bad bg-bad/10 text-bad' : 'border-line bg-surface hover:bg-surface2'
          }`}
        >
          <ShieldIcon size={13} />
          Show only blocked fixes
          {blockedCount > 0 && <span className="pill pill-bad">{blockedCount}</span>}
        </button>
        {isAdmin && (
          <button type="button" className="btn text-bad" onClick={onClearHistory}>
            Clear history
          </button>
        )}
      </div>

      <div role="search" aria-label="Filter incidents" className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={filters.q}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="Search id, error or root cause"
          aria-label="Search incidents"
          maxLength={100}
          className="h-9 min-w-0 flex-1 basis-56 rounded-lg border border-line bg-surface px-3 text-[13px] placeholder:text-faint"
        />
        <select
          value={filters.pipeline}
          onChange={(e) => set({ pipeline: e.target.value as IncidentFilters['pipeline'] })}
          aria-label="Pipeline"
          className="h-9 rounded-lg border border-line bg-surface px-2.5 text-[13px]"
        >
          <option value="">All pipelines</option>
          <option value="PIPELINE_A">Pipeline A · JSON</option>
          <option value="PIPELINE_B">Pipeline B · Database</option>
        </select>
        <div role="group" aria-label="Outcome" className="flex flex-wrap gap-1">
          {OUTCOME_CHIPS.map((o) => {
            const on = filters.outcome === o.value;
            return (
              <button
                key={o.label}
                type="button"
                aria-pressed={on}
                onClick={() => set({ outcome: o.value })}
                className={`h-9 rounded-full border px-3 text-[13px] font-medium transition-colors ${
                  on ? 'border-fg bg-fg text-bg' : 'border-line bg-surface text-muted hover:text-fg'
                }`}
              >
                {o.label}
              </button>
            );
          })}
        </div>
        {filtered && (
          <button type="button" className="h-9 px-2 text-[13px] text-muted underline-offset-4 hover:text-fg hover:underline" onClick={() => onFiltersChange(NO_FILTERS)}>
            Clear filters
          </button>
        )}
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="space-y-2.5">
          {!list ? (
            [0, 1, 2].map((i) => (
              <div key={i} className="card space-y-3 p-4" aria-hidden="true">
                <div className="skeleton h-5 w-40" />
                <div className="skeleton h-4 w-full" />
                <div className="skeleton h-4 w-2/3" />
              </div>
            ))
          ) : list.length === 0 ? (
            <div className="space-y-3 rounded-xl border border-dashed border-line px-6 py-10 text-center text-sm text-muted">
              <p>
                {blockedOnly && blockedCount === 0 ? (
                  <>
                    No blocked fixes yet. Try <span className="font-medium text-fg">Broken JSON syntax</span> or{' '}
                    <span className="font-medium text-fg">Missing column</span>.
                  </>
                ) : filtered ? (
                  'No incidents match these filters.'
                ) : (
                  'No incidents yet. Run a preset to create one.'
                )}
              </p>
              {filtered ? (
                <button type="button" className="btn" onClick={() => onFiltersChange(NO_FILTERS)}>
                  Clear filters
                </button>
              ) : (
                <button type="button" className="btn" onClick={onGoRun}>
                  Go to Run a test
                </button>
              )}
            </div>
          ) : (
            list.map((inc) => (
              <IncidentCard
                key={inc.incident_id}
                incident={inc}
                selected={inc.incident_id === selectedId}
                blocked={blockedReasons.has(inc.incident_id)}
                onSelect={onSelect}
              />
            ))
          )}
          {incidents && incidents.length < total && (
            <button type="button" className="btn w-full justify-center" onClick={onShowMore}>
              Show more
            </button>
          )}
        </div>

        {selected ? (
          <AuditPanel
            key={selected.incident_id}
            incident={selected}
            audit={selectedAudit}
            step={step}
            onReplay={onReplay}
            guardReason={blockedReasons.get(selected.incident_id) ?? null}
          />
        ) : (
          list &&
          list.length > 0 && (
            <div className="hidden rounded-xl border border-dashed border-line p-6 text-sm text-muted lg:block">
              Select an incident to see its audit trail.
            </div>
          )
        )}
      </div>
    </section>
  );
}
