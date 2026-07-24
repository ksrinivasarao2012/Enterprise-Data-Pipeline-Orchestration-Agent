import { Fragment, useCallback, useRef, useState, type KeyboardEvent } from 'react';
import { ApiError, api, type Escalation, type QuarantineRecord } from '../api';
import { usePolling } from '../hooks/usePolling';
import { formatTime, pipelineName } from '../lib/incident';
import { same } from '../lib/stable';

type Tab = 'quarantine' | 'escalations';
const TABS: { id: Tab; label: string }[] = [
  { id: 'quarantine', label: 'Quarantine' },
  { id: 'escalations', label: 'Escalations' },
];

function stringify(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2) ?? String(v);
  } catch {
    return String(v);
  }
}

/** Rows per page. Only the open tab loads its rows; the other tab only asks for its count. */
const PAGE_SIZE = 20;

/** Quarantine and escalation records, refreshed while this section is on screen. */
export function RecordsTabs({ refreshKey }: { refreshKey: number }) {
  const [tab, setTab] = useState<Tab>('quarantine');
  const [quarantine, setQuarantine] = useState<QuarantineRecord[] | null>(null);
  const [escalations, setEscalations] = useState<Escalation[] | null>(null);
  const [totals, setTotals] = useState<Record<Tab, number | null>>({ quarantine: null, escalations: null });
  const [limits, setLimits] = useState<Record<Tab, number>>({ quarantine: PAGE_SIZE, escalations: PAGE_SIZE });
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const refs = useRef<Record<Tab, HTMLButtonElement | null>>({ quarantine: null, escalations: null });

  const load = useCallback(async () => {
    try {
      // The open tab gets its page; the other tab asks for 1 row just to read its total.
      const [q, e] = await Promise.all([
        api.quarantine(tab === 'quarantine' ? limits.quarantine : 1),
        api.escalations(tab === 'escalations' ? limits.escalations : 1),
      ]);
      if (tab === 'quarantine') setQuarantine((prev) => same(prev, q.items));
      else setEscalations((prev) => same(prev, e.items));
      setTotals((prev) => same(prev, { quarantine: q.total, escalations: e.total }));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) setError(err.detail);
    }
  }, [tab, limits]);

  usePolling(load, 3000, true, [refreshKey, tab, limits]);

  function onKey(e: KeyboardEvent) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const next: Tab = tab === 'quarantine' ? 'escalations' : 'quarantine';
    setTab(next);
    refs.current[next]?.focus();
  }

  const counts = totals;
  const rows: Record<Tab, number> = { quarantine: quarantine?.length ?? 0, escalations: escalations?.length ?? 0 };
  const showMore = (t: Tab) =>
    totals[t] !== null && rows[t] < (totals[t] ?? 0) ? (
      <div className="flex items-center justify-between gap-3 px-4 py-3 text-[13px] text-muted">
        <span>
          {rows[t]} of {totals[t]}
        </span>
        <button type="button" className="btn h-8 text-[13px]" onClick={() => setLimits((l) => ({ ...l, [t]: l[t] + PAGE_SIZE }))}>
          Show more
        </button>
      </div>
    ) : null;

  return (
    <section aria-label="Records" className="card overflow-hidden">
      <div role="tablist" aria-label="Records" className="flex gap-1 border-b border-line px-3 py-2.5 sm:px-4">
        {TABS.map((t) => {
          const on = t.id === tab;
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`records-tab-${t.id}`}
              aria-selected={on}
              aria-controls="records-panel"
              tabIndex={on ? 0 : -1}
              onClick={() => setTab(t.id)}
              onKeyDown={onKey}
              className={`inline-flex h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors sm:h-8 ${
                on ? 'bg-surface2 text-fg' : 'text-muted hover:text-fg'
              }`}
            >
              {t.label}
              {counts[t.id] !== null && (
                <span className={`pill ${t.id === 'quarantine' ? 'pill-warn' : 'pill-bad'}`}>{counts[t.id]}</span>
              )}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id="records-panel" aria-labelledby={`records-tab-${tab}`} className="relative overflow-x-auto">
        {error && <p className="px-4 py-3 text-sm text-bad">{error}</p>}

        {tab === 'quarantine' &&
          (!quarantine ? (
            <TableSkeleton />
          ) : quarantine.length === 0 ? (
            <Empty text="Nothing quarantined. Try “Bad email in one row” or “Duplicate IDs” above." />
          ) : (
            <table className="w-full min-w-[640px] border-collapse text-left">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th scope="col" className="px-4 py-2.5 font-medium">Record</th>
                  <th scope="col" className="px-4 py-2.5 font-medium">Pipeline / run</th>
                  <th scope="col" className="px-4 py-2.5 font-medium">Why it was held back</th>
                  <th scope="col" className="px-4 py-2.5 font-medium">When</th>
                  <th scope="col" className="px-4 py-2.5"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {quarantine.map((q) => {
                  const isOpen = open === q.quarantine_id;
                  return (
                    <Fragment key={q.quarantine_id}>
                      <tr className="border-b border-line align-top text-[13px]">
                        <td className="px-4 py-3">
                          <div className="font-mono text-xs">{q.quarantine_id}</div>
                          <div className="text-xs text-faint">{q.record_type}</div>
                        </td>
                        <td className="px-4 py-3">
                          <div>{pipelineName(q.pipeline_id)}</div>
                          <div className="font-mono text-xs text-faint">{q.run_id}</div>
                        </td>
                        <td className="px-4 py-3">
                          <ul className="flex flex-wrap gap-1">
                            {q.validation_errors.map((v, i) => (
                              <li key={i} className="rounded-md bg-warn/10 px-2 py-0.5 text-xs font-medium text-warn dark:bg-warn/15">
                                {v}
                              </li>
                            ))}
                          </ul>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-muted">{formatTime(q.created_at)}</td>
                        <td className="px-4 py-3 text-right">
                          <button
                            type="button"
                            className="btn h-8 whitespace-nowrap text-[13px]"
                            aria-expanded={isOpen}
                            aria-controls={`raw-${q.quarantine_id}`}
                            onClick={() => setOpen(isOpen ? null : q.quarantine_id)}
                          >
                            {isOpen ? 'Hide record' : 'View record'}
                          </button>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr id={`raw-${q.quarantine_id}`} className="border-b border-line bg-surface2">
                          <td colSpan={5} className="px-4 py-3">
                            <pre tabIndex={0} className="max-h-72 overflow-auto font-mono text-xs leading-relaxed">
                              {stringify(q.raw_record)}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          ))}
        {tab === 'quarantine' && quarantine && quarantine.length > 0 && showMore('quarantine')}

        {tab === 'escalations' &&
          (!escalations ? (
            <TableSkeleton />
          ) : escalations.length === 0 ? (
            <Empty text="Nothing has been sent to a human yet." />
          ) : (
            <table className="w-full min-w-[560px] border-collapse text-left">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th scope="col" className="px-4 py-2.5 font-medium">When</th>
                  <th scope="col" className="px-4 py-2.5 font-medium">Incident</th>
                  <th scope="col" className="px-4 py-2.5 font-medium">What went wrong</th>
                </tr>
              </thead>
              <tbody>
                {escalations.map((e, i) => (
                  <tr key={`${e.incident_id}-${e.timestamp}-${i}`} className="border-b border-line align-top text-[13px]">
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{formatTime(e.timestamp)}</td>
                    <td className="px-4 py-3 font-mono text-xs">{e.incident_id}</td>
                    <td className="space-y-1 px-4 py-3 [overflow-wrap:anywhere]">
                      <p className="font-medium">{e.summary ?? e.reason}</p>
                      {e.why && <p className="text-muted">{e.why}</p>}
                      {e.summary && (
                        <details className="group pt-0.5">
                          <summary className="w-fit cursor-pointer text-xs text-faint hover:text-fg">Technical error</summary>
                          <p className="mt-1.5 rounded-md bg-surface2 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-muted">{e.reason}</p>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        {tab === 'escalations' && escalations && escalations.length > 0 && showMore('escalations')}
      </div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="px-4 py-10 text-center text-sm text-muted">{text}</p>;
}

function TableSkeleton() {
  return (
    <div className="space-y-3 p-4" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <div key={i} className="skeleton h-9" />
      ))}
    </div>
  );
}
