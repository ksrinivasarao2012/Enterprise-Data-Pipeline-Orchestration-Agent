import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, api, type AuthConfig, type WhoAmI } from '../api';
import type { Theme } from '../hooks/useTheme';
import { DemoLoop } from './DemoLoop';
import { API_DOCS_URL, GITHUB_URL, ThemeToggle } from './Header';
import { FileXIcon, FlowIcon, GitHubIcon, LogoIcon, ShieldIcon } from './icons';

interface Props {
  config: AuthConfig;
  onLoggedIn: (who: WhoAmI) => void;
  theme: Theme;
  onToggleTheme: () => void;
}

// ---- Google Identity Services ("Sign in with Google" button), loaded only on this page.
interface GoogleId {
  initialize: (opts: { client_id: string; callback: (r: { credential?: string }) => void; ux_mode?: 'popup'; auto_select?: boolean; itp_support?: boolean }) => void;
  renderButton: (el: HTMLElement, opts: Record<string, string | number>) => void;
  disableAutoSelect: () => void;
}
declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleId } };
  }
}

const GIS_SRC = 'https://accounts.google.com/gsi/client';
let gisLoading: Promise<GoogleId> | null = null;

function loadGoogle(): Promise<GoogleId> {
  const ready = window.google?.accounts?.id;
  if (ready) return Promise.resolve(ready);
  gisLoading ??= new Promise<GoogleId>((resolve, reject) => {
    const s = document.createElement('script');
    s.src = GIS_SRC;
    s.async = true;
    s.onload = () => {
      const id = window.google?.accounts?.id;
      if (id) resolve(id);
      else reject(new Error('Google sign-in did not start.'));
    };
    s.onerror = () => {
      gisLoading = null;
      s.remove();
      reject(new Error('Could not load Google sign-in.'));
    };
    document.head.appendChild(s);
  });
  return gisLoading;
}

/** After signing out, stop Google from signing the same account in again by itself. */
export function forgetGoogleChoice(): void {
  try {
    window.google?.accounts?.id?.disableAutoSelect();
  } catch {
    /* not loaded: nothing to forget */
  }
}

function GoogleButton({ clientId, theme, onCredential }: { clientId: string; theme: Theme; onCredential: (c: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const callback = useRef(onCredential);
  callback.current = onCredential;

  useEffect(() => {
    let cancelled = false;
    loadGoogle()
      .then((id) => {
        if (cancelled || !box.current) return;
        id.initialize({
          client_id: clientId,
          callback: (r) => r.credential && callback.current(r.credential),
          ux_mode: 'popup',
          auto_select: false,
          itp_support: true,
        });
        box.current.innerHTML = '';
        id.renderButton(box.current, {
          type: 'standard',
          theme: theme === 'dark' ? 'filled_black' : 'outline',
          size: 'large',
          text: 'signin_with',
          shape: 'pill',
          logo_alignment: 'left',
          width: Math.min(320, box.current.clientWidth || 320),
        });
      })
      .catch((e: Error) => !cancelled && setFailed(e.message));
    return () => {
      cancelled = true;
    };
  }, [clientId, theme]);

  return (
    <div className="space-y-2">
      <div ref={box} data-testid="google-button" className={failed ? 'hidden' : 'flex min-h-[44px] w-full justify-center'} />
      {failed && (
        <p role="alert" className="text-[13px] text-bad">
          {failed} Check your connection and reload the page.
        </p>
      )}
    </div>
  );
}

const STEPS: { icon: ReactNode; title: string; text: string }[] = [
  { icon: <FileXIcon size={14} />, title: 'Pick a broken test file', text: 'Renamed columns, bad JSON, missing tables and more.' },
  { icon: <FlowIcon size={14} />, title: 'Watch the agents fix it', text: 'They detect, diagnose with Gemini, try a fix and verify it.' },
  { icon: <ShieldIcon size={14} />, title: 'See what was blocked and why', text: 'Guards stop unsafe fixes and hand them to a human.' },
];

const STACK = ['FastAPI', 'LangGraph', 'Google Gemini', 'sqlglot'];

/** Sign-in page: a short pitch of the project beside the "Sign in with Google" card. */
export function LoginCard({ config, onLoggedIn, theme, onToggleTheme }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function signIn(credential: string) {
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await api.googleLogin(credential));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Sign-in failed. Try again.');
    } finally {
      setBusy(false);
    }
  }

  const clientId = config.mode === 'google' ? config.google_client_id : null;

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

        <section
          aria-labelledby="signin-title"
          className="card order-2 w-full space-y-5 self-start p-6 shadow-sm sm:p-7 lg:order-none lg:sticky lg:top-8 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:mt-10"
        >
          <div className="space-y-1.5">
            <div className="eyebrow">Live demo</div>
            <h2 id="signin-title" className="font-display text-[34px] leading-none tracking-tight">
              Sign in
            </h2>
            <p className="text-sm text-muted">Use your Google account. You get your own space: only you see your test runs.</p>
          </div>
          {clientId ? (
            <GoogleButton clientId={clientId} theme={theme} onCredential={(c) => void signIn(c)} />
          ) : (
            <p role="status" className="rounded-lg border border-line bg-bg px-3 py-2.5 text-sm text-muted">
              Sign-in is not set up on this server yet. Ask the owner to add a Google client id.
            </p>
          )}
          {busy && (
            <p role="status" className="text-[13px] text-muted">
              Signing you in…
            </p>
          )}
          {error && (
            <p role="alert" className="text-[13px] text-bad">
              {error}
            </p>
          )}
          <p className="border-t border-line pt-4 text-[13px] text-muted">
            We keep only your name and email, to show who is signed in and to keep your runs separate. After signing in, the
            Demo tab shows the app after all the test cases.
          </p>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-x-5 gap-y-2 px-4 py-5 text-[13px] text-muted sm:flex-row sm:items-center sm:px-8">
          <span className="flex-1">Built with FastAPI, LangGraph, Google Gemini, sqlglot</span>
          <a href={API_DOCS_URL} target="_blank" rel="noreferrer noopener" className="underline-offset-4 hover:text-fg hover:underline">
            API docs (FastAPI)
          </a>
          <a href="/privacy.html" className="underline-offset-4 hover:text-fg hover:underline">
            Privacy
          </a>
        </div>
      </footer>
    </div>
  );
}
