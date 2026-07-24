import { CloseIcon } from './icons';

const STEPS = ['Pick a broken test file', 'Watch the agents fix it', 'See what was blocked and why'];

/** Editorial page opener for the Run page, with a dismissible "what is this?" row. */
export function IntroCard({ showSteps, onDismiss }: { showSteps: boolean; onDismiss: () => void }) {
  return (
    <section aria-labelledby="intro-title" className="space-y-4 pb-1 pt-2">
      <h2 id="intro-title" className="max-w-3xl font-display text-[40px] leading-[1.02] tracking-tight sm:text-[56px]">
        Break a pipeline. <span className="italic text-muted">Watch it heal.</span>
      </h2>
      {showSteps && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="eyebrow">What is this?</span>
          <ol className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13.5px]">
            {STEPS.map((s, i) => (
              <li key={s} className="flex items-center gap-2">
                <span className="flex h-5 w-5 items-center justify-center rounded-full border border-line font-mono text-[10px] text-muted">
                  {i + 1}
                </span>
                {s}
              </li>
            ))}
          </ol>
          <button type="button" className="btn h-8 w-8 px-0" onClick={onDismiss} aria-label="Dismiss the what-is-this steps">
            <CloseIcon size={13} />
          </button>
        </div>
      )}
    </section>
  );
}
