import { useState } from 'react';
import { ApiError, api } from '../api';

/** "Download report" buttons: fetch with the access key, then hand the file to the browser to save. */
export function DownloadReport({ incidentId }: { incidentId: string }) {
  const [busy, setBusy] = useState<'json' | 'pdf' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const download = async (format: 'json' | 'pdf') => {
    setBusy(format);
    setError(null);
    try {
      const blob = await api.report(incidentId, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${incidentId}_report.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Download failed.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs font-medium text-muted">Download report</span>
      {(['pdf', 'json'] as const).map((f) => (
        <button
          key={f}
          type="button"
          className="btn h-8 px-2.5 text-xs"
          aria-label={`Download ${f.toUpperCase()} report`}
          disabled={busy !== null}
          onClick={() => download(f)}
        >
          {busy === f ? 'Preparing…' : f.toUpperCase()}
        </button>
      ))}
      {error && (
        <span role="alert" className="text-xs text-bad">
          {error}
        </span>
      )}
    </div>
  );
}
