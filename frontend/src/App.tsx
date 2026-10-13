import { useCallback, useEffect, useState } from 'react';
import { ApiError, api, setUnauthorizedHandler, type AuthConfig, type WhoAmI } from './api';
import { Dashboard } from './components/Dashboard';
import { DemoPage } from './components/DemoPage';
import { API_DOCS_URL, Header } from './components/Header';
import { LoginCard, forgetGoogleChoice } from './components/LoginCard';
import { useRoute } from './hooks/useRoute';
import { useTheme } from './hooks/useTheme';

type Auth =
  | { kind: 'checking' }
  | { kind: 'offline'; message: string }
  | { kind: 'login'; config: AuthConfig }
  | { kind: 'ready'; who: WhoAmI };

export default function App() {
  const { theme, toggle } = useTheme();
  const [route, go] = useRoute();
  const [auth, setAuth] = useState<Auth>({ kind: 'checking' });

  const offline = (err: unknown) =>
    setAuth({ kind: 'offline', message: err instanceof ApiError ? err.detail : 'Cannot reach the API.' });

  /** Show the sign-in page (it needs to know whether Google sign-in is set up). */
  const showLogin = useCallback(async () => {
    try {
      setAuth({ kind: 'login', config: await api.authConfig() });
    } catch (err) {
      offline(err);
    }
  }, []);

  const check = useCallback(async () => {
    setAuth({ kind: 'checking' });
    try {
      setAuth({ kind: 'ready', who: await api.whoami(true) });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) await showLogin();
      else offline(err);
    }
  }, [showLogin]);

  useEffect(() => {
    setUnauthorizedHandler(() => void showLogin());
    void check();
  }, [check, showLogin]);

  const logout = async () => {
    try {
      await api.logout();
    } catch {
      /* already signed out or offline: show the sign-in page anyway */
    }
    forgetGoogleChoice();
    await showLogin();
  };

  if (auth.kind === 'login') {
    return (
      <LoginCard config={auth.config} theme={theme} onToggleTheme={toggle} onLoggedIn={(who) => setAuth({ kind: 'ready', who })} />
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.querySelector<HTMLElement>('main')?.focus();
        }}
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <Header
        theme={theme}
        onToggleTheme={toggle}
        who={auth.kind === 'ready' ? auth.who : null}
        onLogout={() => void logout()}
        route={route}
      />
      <div className="flex-1">
        {auth.kind === 'ready' &&
          (route === 'demo' ? <DemoPage who={auth.who} go={go} /> : <Dashboard who={auth.who} route={route} go={go} />)}
        {auth.kind === 'checking' && (
          <div className="mx-auto max-w-6xl space-y-5 px-4 py-8 sm:px-8" aria-label="Loading">
            <div className="skeleton h-16" />
            <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
              <div className="skeleton h-96" />
              <div className="skeleton h-96" />
            </div>
          </div>
        )}
        {auth.kind === 'offline' && (
          <div className="mx-auto max-w-6xl px-4 py-10 sm:px-8">
            <div role="alert" className="card space-y-3 p-6">
              <h2 className="text-lg font-semibold">The API is not reachable</h2>
              <p className="text-sm text-muted">{auth.message}</p>
              <button type="button" className="btn" onClick={() => void check()}>
                Try again
              </button>
            </div>
          </div>
        )}
      </div>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-[13px] text-muted sm:flex-row sm:items-center sm:px-8">
          <span className="flex-1">Built with FastAPI, LangGraph, Google Gemini, sqlglot</span>
          <a href={API_DOCS_URL} target="_blank" rel="noreferrer noopener" className="underline-offset-4 hover:text-fg hover:underline">
            API docs (FastAPI)
          </a>
        </div>
      </footer>
    </div>
  );
}
