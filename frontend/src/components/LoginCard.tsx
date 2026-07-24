import { useState, type FormEvent, type ReactNode } from 'react';
import { ApiError, api, setAccessKey, type WhoAmI } from '../api';
import type { Theme } from '../hooks/useTheme';
import { DemoLoop } from './DemoLoop';
import { API_DOCS_URL, GITHUB_URL, ThemeToggle } from './Header';
import { FileXIcon, FlowIcon, GitHubIcon, LogoIcon, ShieldIcon } from './icons';

interface Props {
  onLoggedIn: (who: WhoAmI) => void;
  theme: Theme;
  onToggleTheme: () => void;
}

const STEPS: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <FileXIcon size={14} />, title: 'Pick a broken test file', text: 'Renamed columns, bad JSON, missing tables and more.' },
  { icon: <FlowIcon size={14} />, title: 'Watch the agents fix it', text: 'They detect, diagnose with Gemini, try a fix and verify it.' },
  { icon: <ShieldIcon size={14} />, title: 'See what was blocked and why', text: 'Guards stop unsafe fixes and hand them to a human.' },
];

const STACK = ['FastAPI', 'LangGraph', 'Google Gemini', 'sqlglot'];

/** Login page: a short pitch of the project beside the access-key form. */
export function LoginCard({ onLoggedIn, theme, onToggleTheme }: Props) {
  const [key, setKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !key.trim()) return;
    setBusy(true);
    setError(null);
    setAccessKey(key.trim());
    try {
      onLoggedIn(await api.whoami(true));
    } catch (err) {
      setAccessKey('');
      if (err instanceof ApiError && err.status === 401) setError('Wrong key. Try again.');
      else if (err instanceof ApiError) setError(err.detail);
      else setError('Something went wrong. Try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center gap-3 px-4 py-4 sm:px-8">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-onprimary">
          <LogoIcon size={18} />
        </span>
        <span className="flex-1 text-[15px] font-semibold">Self-Healing Data Pipeline Platform</span>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        <a className="btn h-11 sm:h-9" href={GITHUB_URL} target="_blank" rel="noreferrer noopener">
          <GitHubIcon />
          <span className="hidden sm:inline">GitHub</span>
          <span className="sr-only sm:hidden">GitHub repository</span>
        </a>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 content-center gap-8 px-4 py-8 sm:px-8 lg:grid-cols-[minmax(0,1fr)_380px] lg:gap-x-14 lg:py-12">
        <section aria-labelledby="pitch-title" className="space-y-4 lg:col-start-1">
          <div className="eyebrow">Portfolio project · Self-healing data pipelines</div>
          <h1 id="pitch-title" className="max-w-3xl font-display text-[44px] leading-[0.98] tracking-tight sm:text-[64px] lg:text-[76px]">
            Pipelines that heal themselves, <span className="italic text-muted">and know when not to.</span>
          </h1>
          <p className="max-w-xl text-[15px] leading-relaxed text-muted">
            When a data load breaks, a team of AI agents finds the failure, works out why, tries a safe fix and checks it.
            If the fix is not safe, a guard blocks it and a person is asked to step in.
          </p>
        </section>

        <section aria-label="How it works" className="order-3 space-y-5 lg:order-none lg:col-start-1">
          <DemoLoop />
          <ol className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="space-y-1 border-t border-line pt-3">
                <span className="flex items-center gap-2 text-sm font-medium">
                  <span className="text-muted">{s.icon}</span>
                  <span className="font-mono text-[11px] text-faint">0{i + 1}</span>
                  {s.title}
                </span>
                <span className="block text-[13px] leading-snug text-muted">{s.text}</span>
              </li>
            ))}
          </ol>
          <ul aria-label="Built with" className="flex flex-wrap gap-1.5">
            {STACK.map((t) => (
              <li key={t} className="pill pill-neutral font-mono text-[11px]">
                {t}
              </li>
            ))}
          </ul>
        </section>

        <form
          onSubmit={submit}
          noValidate
          className="card order-2 w-full space-y-5 self-start p-6 shadow-sm sm:p-7 lg:order-none lg:sticky lg:top-8 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:mt-10"
        >
          <div className="space-y-1.5">
            <div className="eyebrow">Restricted demo</div>
            <h2 className="font-display text-[34px] leading-none tracking-tight">Log in</h2>
            <p className="text-sm text-muted">Ask the owner for a viewer key.</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="access-key" className="block text-[13px] font-medium">
              Access key
            </label>
            <input
              id="access-key"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={key}
              onChange={(e) => {
                setKey(e.target.value);
                if (error) setError(null);
              }}
              aria-invalid={!!error}
              aria-describedby={error ? 'access-key-error' : undefined}
              className={`h-11 w-full rounded-lg border bg-bg px-3 text-sm text-fg ${error ? 'border-bad' : 'border-line'}`}
            />
            {error && (
              <p id="access-key-error" role="alert" className="text-[13px] text-bad">
                {error}
              </p>
            )}
          </div>
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? 'Checking…' : 'Log in'}
          </button>
          <p className="border-t border-line pt-4 text-[13px] text-muted">
            Viewers can run tests and read results. Admins can also clear history.
          </p>
        </form>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-5 text-[13px] text-muted sm:flex-row sm:items-center sm:px-8">
          <span className="flex-1">Built with FastAPI, LangGraph, Google Gemini, sqlglot</span>
          <a href={API_DOCS_URL} target="_blank" rel="noreferrer noopener" className="underline-offset-4 hover:text-fg hover:underline">
            API docs (FastAPI)
          </a>
        </div>
      </footer>
    </div>
  );
}
