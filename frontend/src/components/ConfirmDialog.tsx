import { useEffect, useRef } from 'react';

interface Props {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Native <dialog>: focus is trapped and Escape closes it. */
export function ConfirmDialog({ open, title, body, confirmLabel, busy, error, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onCancel();
      }}
      aria-labelledby="confirm-title"
      aria-describedby="confirm-body"
      className="w-[calc(100%-32px)] max-w-md rounded-xl border border-line bg-surface p-0 text-fg shadow-xl backdrop:bg-black/40"
    >
      <div className="space-y-4 p-6">
        <h2 id="confirm-title" className="text-lg font-semibold">
          {title}
        </h2>
        <p id="confirm-body" className="text-sm text-muted">
          {body}
        </p>
        {error && (
          <p role="alert" className="text-sm text-bad">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onCancel} disabled={busy} autoFocus>
            Cancel
          </button>
          <button
            type="button"
            className="btn border-bad bg-bad text-surface hover:bg-bad hover:opacity-90"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
