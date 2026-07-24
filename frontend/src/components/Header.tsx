import type { WhoAmI } from '../api';
import { ROUTES, type Route } from '../hooks/useRoute';
import type { Theme } from '../hooks/useTheme';
import { GitHubIcon, LogoIcon, MoonIcon, SunIcon } from './icons';

export const GITHUB_URL = 'https://github.com/KSrinivasarao2012/Enterprise-Data-Pipeline-Orchestration-Agent';
/** FastAPI's interactive API docs (Swagger UI), served by the backend on the same origin. */
export const API_DOCS_URL = '/docs';

interface Props {
  theme: Theme;
  onToggleTheme: () => void;
  who: WhoAmI | null;
  onLogout: () => void;
  route: Route;
}

export function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <button
      type="button"
      className="btn h-11 w-11 px-0 sm:h-9 sm:w-9"
      onClick={onToggle}
      aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
      title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
    >
      {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

export function Header({ theme, onToggleTheme, who, onLogout, route }: Props) {
  const roleLabel = !who ? null : !who.auth_enabled ? 'Open mode' : who.role === 'admin' ? 'Admin' : 'Viewer';

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-3 px-4 pt-4 sm:px-8">
        <a href="#/run" className="flex min-w-0 flex-1 items-center gap-3 rounded-lg">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[9px] bg-primary text-onprimary">
            <LogoIcon size={20} />
          </span>
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold leading-tight tracking-tight sm:text-base">
              Self-Healing Data Pipeline Platform
            </span>
            <span className="hidden text-[13px] text-muted md:block">
              AI agents catch broken data runs, fix what is safe to fix, and hand the rest to a human.
            </span>
          </span>
        </a>

        <div className="flex w-full items-center gap-2 sm:w-auto">
          {roleLabel && (
            <span className={`pill ${who?.role === 'admin' && who.auth_enabled ? 'pill-info' : 'pill-neutral'}`}>
              {roleLabel}
            </span>
          )}
          <span className="flex-1 sm:hidden" />
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <a className="btn h-11 sm:h-9" href={GITHUB_URL} target="_blank" rel="noreferrer noopener">
            <GitHubIcon />
            <span className="hidden sm:inline">GitHub</span>
            <span className="sr-only sm:hidden">GitHub repository</span>
          </a>
          {who?.auth_enabled && (
            <button type="button" className="btn h-11 sm:h-9" onClick={onLogout}>
              Log out
            </button>
          )}
        </div>
      </div>

      <nav aria-label="Main" className="mx-auto mt-3 max-w-6xl px-4 sm:px-8">
        <ul className="-mb-px flex gap-1 overflow-x-auto">
          {ROUTES.map((r) => {
            const on = r.id === route;
            return (
              <li key={r.id}>
                <a
                  href={`#/${r.id}`}
                  aria-current={on ? 'page' : undefined}
                  className={`flex h-11 items-center whitespace-nowrap border-b-2 px-3 text-sm font-medium transition-colors ${
                    on ? 'border-fg text-fg' : 'border-transparent text-muted hover:text-fg'
                  }`}
                >
                  {r.label}
                </a>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
