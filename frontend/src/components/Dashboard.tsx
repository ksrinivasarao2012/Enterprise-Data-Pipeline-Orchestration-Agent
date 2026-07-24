import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, NO_FILTERS, api, type AuditEntry, type ExecuteResult, type Incident, type IncidentFilters, type Metrics, type WhoAmI } from '../api';
import { usePolling } from '../hooks/usePolling';
import type { Route } from '../hooks/useRoute';
import { guardReason } from '../lib/incident';
import { reconcile, same } from '../lib/stable';
import { ConfirmDialog } from './ConfirmDialog';
import { IncidentTimeline } from './IncidentTimeline';
import { IntroCard } from './IntroCard';
import { MetricsStrip } from './MetricsStrip';
import { MissionControl } from './MissionControl';
import { RecordsTabs } from './RecordsTabs';
import { TryItPanel } from './TryItPanel';
import { AlertIcon } from './icons';

const POLL_MS = 3000;
/** Incidents per page. The list polls only what is on screen; "Show more" adds a page. */
const PAGE_SIZE = 20;
const STEP_MS = 400;
/** While a run is healing, check for new agent steps this often (live flow). */
const LIVE_POLL_MS = 500;
const INTRO_KEY = 'introDismissed';

type AuditCache = Record<string, { status: string; entries: AuditEntry[] }>;
/** What the Run page's agent flow is showing. */
type RunPhase = 'idle' | 'running' | 'clean' | 'incident';

const byNewest = (a: Incident, b: Incident) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0);
const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function readIntroDismissed() {
  try {
    return localStorage.getItem(INTRO_KEY) === '1';
  } catch {
    return false;
  }
}

interface Props {
  who: WhoAmI;
  route: Route;
  go: (to: Route) => void;
}

export function Dashboard({ who, route, go }: Props) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [incidentTotal, setIncidentTotal] = useState(0);
  const [limit, setLimit] = useState(PAGE_SIZE);
  // Incident log filters. The search box waits 300 ms after typing before it asks the server.
  const [filters, setFilters] = useState<IncidentFilters>(NO_FILTERS);
  const [applied, setApplied] = useState<IncidentFilters>(NO_FILTERS);
  useEffect(() => {
    const t = window.setTimeout(() => setApplied(filters), filters.q === applied.q ? 0 : 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);
  // Filters only apply on the Incident log; the Run page always follows the newest incident.
  const activeFilters = route === 'incidents' ? applied : NO_FILTERS;
  const changeFilters = useCallback((next: IncidentFilters) => {
    setFilters(next);
    setLimit(PAGE_SIZE);
  }, []);
  const [audits, setAudits] = useState<AuditCache>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [apiError, setApiError] = useState<string | null>(null);
  const [introHidden, setIntroHidden] = useState(readIntroDismissed);
  const [runPhase, setRunPhase] = useState<RunPhase>('idle');
  // True while a pipeline request is in flight: poll fast so agent steps appear live.
  const [liveRun, setLiveRun] = useState(false);
  const liveRunRef = useRef(liveRun);
  liveRunRef.current = liveRun;
  const [refreshKey, setRefreshKey] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);

  // Tracks a started run so the incident it creates can be auto-selected as soon as it appears.
  const pendingRun = useRef<{ prevTop: string | null; since: number } | null>(null);
  const userPicked = useRef(false);
  const fetchingAudit = useRef(new Set<string>());
  const mainRef = useRef<HTMLElement>(null);
  const firstRoute = useRef(true);

  // Open mode (no keys set, local only) gives everyone admin rights on the server, so the
  // admin-only buttons (Clear history) must show there too (CHANGES.md section 21).
  const isAdmin = who.role === 'admin';

  // Move focus to the new page for keyboard and screen-reader users.
  useEffect(() => {
    if (firstRoute.current) {
      firstRoute.current = false;
      return;
    }
    window.scrollTo({ top: 0 });
    mainRef.current?.focus({ preventScroll: true });
  }, [route]);

  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) return; // app swaps to the login page
    if (err instanceof ApiError && (err.status === 0 || err.status >= 500)) {
      setApiError(err.status === 0 ? 'Cannot reach the API. Retrying every few seconds…' : `The API returned an error (${err.status}). Retrying…`);
    }
  }, []);

  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const incidentsRef = useRef(incidents);
  incidentsRef.current = incidents;

  const select = useCallback((id: string) => {
    userPicked.current = true;
    if (selectedIdRef.current !== id) {
      setSelectedId(id);
      setStep(0);
    }
    // On narrow screens the detail panel sits below the list.
    if (window.innerWidth < 1024) {
      window.setTimeout(() => document.getElementById('incident-detail')?.scrollIntoView({ behavior: 'smooth' }), 50);
    }
  }, []);

  const auditsRef = useRef(audits);
  auditsRef.current = audits;

  const loadAudit = useCallback(
    async (inc: Incident) => {
      if (fetchingAudit.current.has(inc.incident_id)) return;
      fetchingAudit.current.add(inc.incident_id);
      try {
        const entries = await api.audit(inc.incident_id);
        setAudits((prev) => {
          const old = prev[inc.incident_id];
          if (old && old.status === inc.status && JSON.stringify(old.entries) === JSON.stringify(entries)) return prev;
          return { ...prev, [inc.incident_id]: { status: inc.status, entries } };
        });
      } catch (err) {
        handleError(err);
      } finally {
        fetchingAudit.current.delete(inc.incident_id);
      }
    },
    [handleError],
  );

  const poll = useCallback(async () => {
    try {
      const [m, page] = await Promise.all([api.metrics(), api.incidents(limit, activeFilters)]);
      const sorted = [...page.items].sort(byNewest);
      setIncidentTotal(page.total);
      setMetrics((prev) => same(prev, m));
      setIncidents((prev) => reconcile(prev, sorted, (i) => i.incident_id));
      setApiError(null);

      const top = sorted[0]?.incident_id ?? null;
      const pending = pendingRun.current;
      if (pending) {
        if (top && top !== pending.prevTop) {
          pendingRun.current = null;
          setSelectedId(top);
          setStep(0);
          setRunPhase('incident');
        } else if (!liveRunRef.current && Date.now() - pending.since > 20000) {
          pendingRun.current = null;
          setRunPhase('idle');
        }
      }
      // First load: show the newest incident so the agent flow is not empty.
      if (!userPicked.current && top) setSelectedId((prev) => prev ?? top);

      // Fetch audits for the other incidents (once, and again when their status changes)
      // so guard badges can show on every card. A few per tick keeps each poll light.
      const stale = sorted
        .filter((i) => i.incident_id !== selectedIdRef.current && auditsRef.current[i.incident_id]?.status !== i.status)
        .slice(0, 6);
      await Promise.all(stale.map(loadAudit));
    } catch (err) {
      handleError(err);
    }
  }, [handleError, loadAudit, limit, activeFilters]);

  usePolling(poll, liveRun ? LIVE_POLL_MS : POLL_MS, true, [limit, activeFilters]);

  // Poll the selected incident's audit trail.
  const selected = incidents?.find((i) => i.incident_id === selectedId) ?? null;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  usePolling(
    async () => {
      if (selectedRef.current) await loadAudit(selectedRef.current);
    },
    liveRun ? LIVE_POLL_MS : POLL_MS,
    !!selectedId,
    [selectedId],
  );

  const selectedAudit = selectedId ? audits[selectedId]?.entries ?? null : null;
  const auditLen = selectedAudit?.length ?? 0;

  // Replay: reveal one audit entry every STEP_MS. New entries from polling animate in too.
  useEffect(() => {
    if (!selectedAudit) return;
    if (prefersReducedMotion()) {
      if (step !== auditLen) setStep(auditLen);
      return;
    }
    if (step >= auditLen) return;
    const t = window.setTimeout(() => setStep((s) => Math.min(s + 1, auditLen)), step === 0 ? 150 : STEP_MS);
    return () => window.clearTimeout(t);
  }, [step, auditLen, selectedAudit]);

  // Which incidents were blocked comes from the server (incident.blocked_by_guard). The reason text
  // is read from the incident / its audit log when it is available, else a general sentence.
  const blockedReasons = useMemo(() => {
    const map = new Map<string, string>();
    incidents?.forEach((inc) => {
      if (!inc.blocked_by_guard) return;
      map.set(inc.incident_id, guardReason(inc, audits[inc.incident_id]?.entries) ?? 'A guard blocked the proposed fix, so it was not run.');
    });
    return map;
  }, [incidents, audits]);

  const onRunStart = useCallback((opts: { scroll?: boolean } = {}) => {
    // Remember the newest incident before this run: the first NEW one to appear is this run's,
    // and it is shown immediately (while the agents are still working).
    pendingRun.current = { prevTop: incidentsRef.current?.[0]?.incident_id ?? null, since: Date.now() };
    setRunPhase('running');
    setLiveRun(true);
    // On narrow screens the graph sits below the scenario list: bring it into view.
    if (opts.scroll !== false && window.innerWidth < 1024) {
      document.getElementById('mission-control')?.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    }
  }, []);

  const onRunFinished = useCallback(
    (result: ExecuteResult | null) => {
      const clean =
        result?.status === 'success' && !('healed' in result && result.healed) && !('quarantined' in result && result.quarantined);
      setLiveRun(false);
      if (clean) {
        pendingRun.current = null;
        setRunPhase('clean');
      } else if (pendingRun.current) {
        // The incident has not shown up yet: keep waiting a little after the run ends.
        pendingRun.current = { ...pendingRun.current, since: Date.now() };
      }
      void poll();
      setRefreshKey((k) => k + 1);
    },
    [poll],
  );

  async function clearHistory() {
    setClearing(true);
    setClearError(null);
    try {
      await api.clearHistory();
      setConfirmOpen(false);
      setIncidents([]);
      setAudits({});
      setSelectedId(null);
      setRunPhase('idle');
      userPicked.current = false;
      setRefreshKey((k) => k + 1);
      void poll();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return;
      setClearError(err instanceof ApiError ? err.detail : 'Could not clear history.');
    } finally {
      setClearing(false);
    }
  }

  const replay = useCallback(() => setStep(0), []);

  // Run page: the flow follows the run you just started.
  const flowIncident = runPhase === 'running' || runPhase === 'clean' ? null : selected;
  const flowNote =
    runPhase === 'running'
      ? 'Running your test. The agents start when the pipeline fails.'
      : runPhase === 'clean'
        ? 'That run was clean, so the agents had nothing to fix.'
        : runPhase === 'idle' && selected
          ? (
              <>
                Latest incident · <span className="font-mono">{selected.incident_id}</span>
              </>
            )
          : undefined;

  return (
    <main ref={mainRef} tabIndex={-1} className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6 outline-none sm:px-8 sm:py-8">
      {apiError && (
        <div role="alert" className="flex items-start gap-2.5 rounded-xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm">
          <AlertIcon size={16} className="mt-0.5 shrink-0 text-bad" />
          <span>{apiError}</span>
        </div>
      )}

      {route === 'run' && (
        <>
          <IntroCard
            showSteps={!introHidden}
            onDismiss={() => {
              setIntroHidden(true);
              try {
                localStorage.setItem(INTRO_KEY, '1');
              } catch {
                /* ignore */
              }
            }}
          />
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-5 lg:grid-cols-[380px_minmax(0,1fr)]">
            <TryItPanel onRunStart={onRunStart} onRunFinished={onRunFinished} />
            <div id="mission-control" className="scroll-mt-4 lg:sticky lg:top-5">
              <MissionControl
                incident={flowIncident}
                audit={flowIncident ? selectedAudit : null}
                step={step}
                onReplay={replay}
                running={runPhase === 'running'}
                note={flowNote}
                footer={
                  flowIncident && (
                    <a href="#/incidents" className="text-[13px] font-medium underline-offset-4 hover:underline">
                      See the full incident →
                    </a>
                  )
                }
              />
            </div>
          </div>
        </>
      )}

      {route === 'incidents' && (
        <>
          <PageTitle title="Incident log" accent="Every failure, and what happened next." />
          <MetricsStrip metrics={metrics} />
          <IncidentTimeline
            incidents={incidents}
            total={incidentTotal}
            onShowMore={() => setLimit((l) => l + PAGE_SIZE)}
            filters={filters}
            onFiltersChange={changeFilters}
            selectedId={selectedId}
            onSelect={select}
            blockedReasons={blockedReasons}
            blockedCount={metrics?.blocked_by_guard ?? 0}
            selectedAudit={selectedAudit}
            step={step}
            onReplay={replay}
            isAdmin={isAdmin}
            onClearHistory={() => {
              setClearError(null);
              setConfirmOpen(true);
            }}
            onGoRun={() => go('run')}
          />
        </>
      )}

      {route === 'records' && (
        <>
          <PageTitle title="Records" accent="Held back, or handed over." />
          <p className="-mt-2 text-sm text-muted">Rows held back for review, and incidents the agents handed to a human.</p>
          <RecordsTabs refreshKey={refreshKey} />
        </>
      )}

      {isAdmin && (
        <ConfirmDialog
          open={confirmOpen}
          title="Clear all history?"
          body="This deletes every incident, audit entry, quarantined row and escalation. It cannot be undone."
          confirmLabel="Clear history"
          busy={clearing}
          error={clearError}
          onConfirm={clearHistory}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </main>
  );
}

function PageTitle({ title, accent }: { title: string; accent: string }) {
  return (
    <h2 className="pt-2 font-display text-[40px] leading-[1.02] tracking-tight sm:text-[56px]">
      {title}. <span className="italic text-muted">{accent}</span>
    </h2>
  );
}
