import { useEffect, useId, useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { ApiError, api, type ExecuteResult, type PipelineId } from '../api';
import { PIPELINES } from '../presets';
import { CloseIcon, FileIcon, InfoIcon, PlayIcon, UploadIcon } from './icons';

type Outcome = { tone: 'ok' | 'warn' | 'bad'; title: string; detail: string };

function describe(result: ExecuteResult): Outcome {
  if (result.status === 'failure') {
    return { tone: 'bad', title: 'Sent to a human', detail: result.detail || 'The agents could not fix this safely.' };
  }
  if ('healed' in result && result.healed) {
    return { tone: 'ok', title: 'Healed by agents', detail: 'The run failed, the agents fixed it and the load was verified.' };
  }
  if ('quarantined' in result && result.quarantined) {
    const q = result.quarantined_count === 'all' ? 'All rows were' : `${result.quarantined_count} row${result.quarantined_count === 1 ? ' was' : 's were'}`;
    return {
      tone: 'warn',
      title: 'Some rows quarantined',
      detail: `${q} held back for review. ${typeof result.rows_ingested === 'number' ? `${result.rows_ingested} rows loaded.` : ''}`.trim(),
    };
  }
  const rows = typeof result.rows_ingested === 'number' ? `${result.rows_ingested.toLocaleString()} rows loaded.` : '';
  return { tone: 'ok', title: 'Success', detail: `${rows} No incident was needed.`.trim() };
}

function describeError(err: unknown): Outcome {
  if (err instanceof ApiError) {
    const by: Record<number, string> = {
      0: 'Cannot reach the API. Is the backend running?',
      403: 'Your key is not allowed to run pipelines.',
      404: 'Unknown test case. The server does not have this file.',
      413: 'That file is too large for the server.',
      429: 'Too many runs. Wait a moment and try again.',
    };
    const title =
      err.status === 413 ? 'File too large' : err.status === 429 ? 'Too many runs' : err.status === 404 ? 'Unknown test case' : err.status === 403 ? 'Not allowed' : 'Run failed';
    // Prefer the server's detail text when it has one (required for 429).
    const detail = err.detail && err.status !== 0 ? err.detail : by[err.status] ?? err.detail;
    return { tone: 'bad', title, detail };
  }
  return { tone: 'bad', title: 'Run failed', detail: 'Something went wrong. Try again.' };
}

const TONE_CLASS: Record<Outcome['tone'], string> = {
  ok: 'border-ok/30 bg-ok/10',
  warn: 'border-warn/30 bg-warn/10',
  bad: 'border-bad/30 bg-bad/10',
};
const TONE_TEXT: Record<Outcome['tone'], string> = { ok: 'text-ok', warn: 'text-warn', bad: 'text-bad' };
const TONE_DOT: Record<Outcome['tone'], string> = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad' };

/** Every preset, in the order shown in the panel (Pipeline A first). */
const ALL_PRESETS = (Object.keys(PIPELINES) as PipelineId[]).flatMap((pipe) =>
  PIPELINES[pipe].presets.map((p) => ({ pipe, ...p })),
);
/** Pause between demo runs so the finished flow stays on screen for a moment. */
const DEMO_PAUSE_MS = 1500;

interface DemoState {
  /** Index of the preset running now; equals ALL_PRESETS.length when finished. */
  at: number;
  results: { label: string; outcome: Outcome }[];
  stopped: boolean;
}

interface Props {
  /** scroll: false keeps the page where it is (used by "Try all", so its results list stays in view). */
  onRunStart: (opts?: { scroll?: boolean }) => void;
  /** Called with the API result, or null when the run failed with an HTTP error. */
  onRunFinished: (result: ExecuteResult | null) => void;
}

export function TryItPanel({ onRunStart, onRunFinished }: Props) {
  const [pipe, setPipe] = useState<PipelineId>('a');
  const [preset, setPreset] = useState<Record<PipelineId, string>>({
    a: PIPELINES.a.presets[1].testCase,
    b: PIPELINES.b.presets[1].testCase,
  });
  const [files, setFiles] = useState<Record<PipelineId, File | null>>({ a: null, b: null });
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [demo, setDemo] = useState<DemoState | null>(null);
  const stopDemo = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; // leaving the page stops a running demo
    };
  }, []);
  const inputRef = useRef<HTMLInputElement>(null);
  const tabRefs = useRef<Record<PipelineId, HTMLButtonElement | null>>({ a: null, b: null });
  const uid = useId();

  const cfg = PIPELINES[pipe];
  const file = files[pipe];
  const current = cfg.presets.find((p) => p.testCase === preset[pipe]) ?? cfg.presets[0];

  function switchTab(next: PipelineId) {
    setPipe(next);
    setOutcome(null);
    if (!running) setDemo(null);
    setFileError(null);
  }

  function onTabKey(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'Home' && e.key !== 'End') return;
    e.preventDefault();
    const next: PipelineId = e.key === 'Home' ? 'a' : e.key === 'End' ? 'b' : pipe === 'a' ? 'b' : 'a';
    switchTab(next);
    tabRefs.current[next]?.focus();
  }

  function acceptFile(f: File | undefined | null) {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(cfg.ext)) {
      setFileError(`Please choose a ${cfg.ext} file.`);
      return;
    }
    setFileError(null);
    setFiles((prev) => ({ ...prev, [pipe]: f }));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    acceptFile(e.dataTransfer.files?.[0]);
  }

  async function run() {
    if (running) return;
    setRunning(true);
    setOutcome(null);
    setDemo(null);
    onRunStart();
    let result: ExecuteResult | null = null;
    try {
      result = await api.execute(pipe, file ? { file } : { testCase: current.testCase });
      setOutcome(describe(result));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return; // app shows the login card
      setOutcome(describeError(err));
    } finally {
      setRunning(false);
      onRunFinished(result);
    }
  }

  /** "Try all": runs every preset one after another. The server counts the whole demo as one run. */
  async function runAll() {
    if (running) return;
    stopDemo.current = false;
    setRunning(true);
    setOutcome(null);
    setFiles({ a: null, b: null });
    setDemo({ at: 0, results: [], stopped: false });
    // On phones the results list and the agent flow can't both fit on screen. Bring the list
    // into view once and don't jump to the flow on every run (a single run still does).
    if (window.innerWidth < 1024) {
      window.setTimeout(() => document.getElementById('try-all-results')?.scrollIntoView({ block: 'center' }), 50);
    }
    let token: string | undefined;
    try {
      token = (await api.demoStart()).token;
    } catch (err) {
      setRunning(false);
      setDemo(null);
      if (!(err instanceof ApiError && err.status === 401)) setOutcome(describeError(err));
      return;
    }
    const results: DemoState['results'] = [];
    for (let i = 0; i < ALL_PRESETS.length; i++) {
      if (stopDemo.current || !mounted.current) break;
      const p = ALL_PRESETS[i];
      setPipe(p.pipe);
      setPreset((prev) => ({ ...prev, [p.pipe]: p.testCase }));
      setDemo({ at: i, results: [...results], stopped: false });
      onRunStart({ scroll: false });
      let result: ExecuteResult | null = null;
      let out: Outcome;
      try {
        result = await api.execute(p.pipe, { testCase: p.testCase }, token);
        out = describe(result);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) break; // app shows the login card
        out = describeError(err);
      }
      onRunFinished(result);
      results.push({ label: p.label, outcome: out });
      if (!mounted.current) break;
      setDemo({ at: i + 1, results: [...results], stopped: false });
      // Phones: keep the results list on screen (only scrolls if it has moved out of view).
      if (window.innerWidth < 1024) document.getElementById('try-all-results')?.scrollIntoView({ block: 'nearest' });
      if (i < ALL_PRESETS.length - 1 && !stopDemo.current) await new Promise((r) => window.setTimeout(r, DEMO_PAUSE_MS));
    }
    if (!mounted.current) return;
    setDemo({ at: results.length, results, stopped: results.length < ALL_PRESETS.length });
    setRunning(false);
  }

  const demoRunning = running && demo !== null;
  const counts = demo
    ? demo.results.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.outcome.title]: (acc[r.outcome.title] ?? 0) + 1 }), {})
    : {};

  return (
    <section aria-labelledby="tryit-title" className="card">
      <div className="space-y-4 px-4 pt-5 sm:px-6">
        <div className="space-y-1.5">
          <div className="eyebrow">Step 1 · choose a scenario</div>
          <h2 id="tryit-title" className="font-display text-[30px] leading-none tracking-tight">
            Try it
          </h2>
          <p className="text-[13.5px] text-muted">Each test file is broken in a different way.</p>
        </div>
        <div role="tablist" aria-label="Pipeline" className="grid grid-cols-2 gap-1 rounded-[10px] bg-surface2 p-1">
          {(Object.keys(PIPELINES) as PipelineId[]).map((id) => {
            const on = id === pipe;
            return (
              <button
                key={id}
                ref={(el) => {
                  tabRefs.current[id] = el;
                }}
                type="button"
                role="tab"
                id={`${uid}-tab-${id}`}
                aria-selected={on}
                aria-controls={`${uid}-panel`}
                tabIndex={on ? 0 : -1}
                onClick={() => switchTab(id)}
                onKeyDown={onTabKey}
                className={`h-10 whitespace-nowrap rounded-md px-2 text-[13px] font-medium transition-colors sm:h-9 ${
                  on ? 'bg-surface text-fg shadow-sm ring-1 ring-line' : 'text-muted hover:text-fg'
                }`}
              >
                {PIPELINES[id].tab}
              </button>
            );
          })}
        </div>
      </div>

      <div
        role="tabpanel"
        id={`${uid}-panel`}
        aria-labelledby={`${uid}-tab-${pipe}`}
        className="space-y-6 px-4 pb-6 pt-5 sm:px-6"
      >
        <fieldset className="min-w-0 space-y-2.5" disabled={running}>
          <legend className="eyebrow mb-2.5">Presets</legend>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-2 lg:grid-cols-1">
            {cfg.presets.map((p) => {
              const on = !file && p.testCase === current.testCase;
              return (
                <label
                  key={p.testCase}
                  className={`relative flex cursor-pointer flex-col gap-0.5 rounded-xl border bg-surface px-3.5 py-2.5 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-focus has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-bg ${
                    on ? 'border-fg shadow-[0_0_0_1px] shadow-fg' : 'border-line hover:border-faint'
                  }`}
                >
                  <input
                    type="radio"
                    name={`${uid}-preset-${pipe}`}
                    value={p.testCase}
                    checked={on}
                    onChange={() => {
                      setPreset((prev) => ({ ...prev, [pipe]: p.testCase }));
                      setFiles((prev) => ({ ...prev, [pipe]: null }));
                    }}
                    className="sr-only"
                  />
                  <span className="text-sm font-medium">{p.label}</span>
                  <span className="text-[12.5px] leading-snug text-muted">{p.hint}</span>
                  <span className="font-mono text-[10.5px] text-faint">{p.testCase}</span>
                </label>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-col gap-3 border-t border-line pt-5">
          <div className="eyebrow">Or upload your own</div>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`rounded-[10px] border-[1.5px] border-dashed transition-colors ${
              dragging ? 'border-info bg-info/10' : 'border-line bg-surface2'
            }`}
          >
            {file ? (
              <div className="flex items-center gap-3 p-4">
                <FileIcon size={20} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{file.name}</div>
                  <div className="text-xs text-muted">{(file.size / 1024).toFixed(1)} KB · will be used instead of the preset</div>
                </div>
                <button
                  type="button"
                  className="btn h-9 w-9 shrink-0 px-0"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => setFiles((prev) => ({ ...prev, [pipe]: null }))}
                  disabled={running}
                >
                  <CloseIcon />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={running}
                className="flex min-h-[72px] w-full items-center justify-center gap-3 rounded-[10px] p-4 text-center"
              >
                <UploadIcon size={20} className="shrink-0 text-muted" />
                <span className="text-sm">
                  <span className="font-medium">Drop a {cfg.ext} file here</span>
                  <span className="text-muted"> or click to browse</span>
                </span>
              </button>
            )}
            <input
              ref={inputRef}
              type="file"
              accept={cfg.accept}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                acceptFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
          </div>
          {fileError && (
            <p role="alert" className="text-[13px] text-bad">
              {fileError}
            </p>
          )}
          {pipe === 'a' && (
            <p className="flex gap-2 text-[12.5px] leading-relaxed text-muted">
              <InfoIcon size={14} className="mt-0.5 shrink-0 text-warn" />
              <span>
                If your JSON file has broken syntax, its contents are sent to Google Gemini to be repaired. Do not upload
                real personal data.
              </span>
            </p>
          )}

          <button type="button" className="btn-primary mt-2 w-full" onClick={run} disabled={running}>
            <PlayIcon size={14} />
            {running && !demoRunning ? 'Running…' : 'Run pipeline'}
          </button>
          {demoRunning ? (
            <button type="button" className="btn w-full" onClick={() => (stopDemo.current = true)}>
              Stop after this one
            </button>
          ) : (
            <button type="button" className="btn w-full" onClick={runAll} disabled={running}>
              Try all {ALL_PRESETS.length} presets
            </button>
          )}
          {!demo && (
            <p className="-mt-1 text-center text-[12px] text-faint">Runs every preset one after another, so you see every case.</p>
          )}

          <div aria-live="polite" className="space-y-3">
            {demo && (
              <div id="try-all-results" className="space-y-2 rounded-[10px] border border-line bg-surface2 px-3.5 py-3">
                <div className="text-sm font-semibold">
                  {demoRunning
                    ? `Try all: ${Math.min(demo.at + 1, ALL_PRESETS.length)} of ${ALL_PRESETS.length}`
                    : demo.stopped
                      ? `Try all stopped after ${demo.results.length} of ${ALL_PRESETS.length}`
                      : `Try all finished: ${ALL_PRESETS.length} runs`}
                </div>
                {!demoRunning && demo.results.length > 0 && (
                  <p className="text-[12.5px] text-muted">
                    {Object.entries(counts)
                      .map(([title, n]) => `${n} ${title.toLowerCase()}`)
                      .join(' · ')}
                  </p>
                )}
                <ol className="space-y-1 text-[12.5px]">
                  {ALL_PRESETS.map((p, i) => {
                    const r = demo.results[i];
                    const now = demoRunning && i === demo.at;
                    if (!r && !now) return null;
                    return (
                      <li key={`${p.pipe}-${p.testCase}`} className="flex items-center gap-2">
                        <span
                          aria-hidden="true"
                          className={`h-2 w-2 shrink-0 rounded-full ${r ? TONE_DOT[r.outcome.tone] : 'animate-pulse bg-info'}`}
                        />
                        <span className="min-w-0 flex-1 truncate">{p.label}</span>
                        <span className={`shrink-0 ${r ? TONE_TEXT[r.outcome.tone] : 'text-muted'}`}>{r ? r.outcome.title : 'Running…'}</span>
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}
            {running && !demoRunning && (
              <div className="space-y-1.5">
                <div className="h-[3px] overflow-hidden rounded bg-surface2">
                  <div className="h-full w-2/5 animate-slide rounded bg-info" />
                </div>
                <p className="text-[12.5px] text-muted">
                  Running <span className="font-mono">{file ? file.name : current.testCase}</span>. The agents may take a
                  little while.
                </p>
              </div>
            )}
            {outcome && !running && !demo && (
              <div className={`space-y-1 rounded-[10px] border px-3.5 py-3 ${TONE_CLASS[outcome.tone]}`}>
                <div className={`text-sm font-semibold ${TONE_TEXT[outcome.tone]}`}>{outcome.title}</div>
                {outcome.detail && <p className="break-words text-[12.5px] text-fg/85">{outcome.detail}</p>}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
