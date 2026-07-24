import { useEffect, useState } from 'react';
import type { AuditEntry, Incident } from '../api';
import { useReducedMotion } from '../hooks/useMediaQuery';
import { computeFlow } from '../lib/flow';
import { FlowGraph } from './FlowGraph';

// Two illustrative runs for the login page: one the agents heal, one a guard blocks.
const base = {
  run_id: 'sample',
  pipeline_id: 'pipeline_a',
  error_class: null,
  error_message: null,
  stack_trace: null,
  root_cause: null,
  recovery_action: null,
  created_at: '',
  resolved_at: null,
} as const;

const SAMPLES: { incident: Incident; audit: AuditEntry[] }[] = [
  {
    incident: { ...base, incident_id: 'sample-1', severity: 'P2', category: 'SCHEMA_DRIFT', status: 'RESOLVED' },
    audit: [
      { component: 'MONITOR_AGENT', message: 'Run failed: column "country" not found.', timestamp: '' },
      { component: 'CLASSIFIER_AGENT', message: 'Classified as SCHEMA_DRIFT.', timestamp: '' },
      { component: 'RCA_AGENT', message: 'Upstream renamed "country" to "country_code".', timestamp: '' },
      { component: 'RECOVERY_AGENT', message: 'Mapped country_code → country.', timestamp: '' },
      { component: 'ACTUATOR', message: 'Re-ran the load: 0 errors. Healed.', timestamp: '' },
    ],
  },
  {
    incident: { ...base, incident_id: 'sample-2', severity: 'P1', category: 'SQL_ERROR', status: 'ESCALATED' },
    audit: [
      { component: 'MONITOR_AGENT', message: 'Run failed: no such column.', timestamp: '' },
      { component: 'CLASSIFIER_AGENT', message: 'Classified as SQL_ERROR.', timestamp: '' },
      { component: 'RCA_AGENT', message: 'Table was created without the column.', timestamp: '' },
      { component: 'RECOVERY_AGENT', message: 'SQL guard rejected: the fix would drop a table.', timestamp: '' },
      { component: 'ACTUATOR', message: 'No change made. Sent to a human.', timestamp: '' },
    ],
  },
];

const STEP_MS = 650;
const HOLD_STEPS = 4; // pause on the outcome before the next sample

/** A looping, illustrative agent run shown behind the login form. */
export function DemoLoop() {
  const reduced = useReducedMotion();
  const [sample, setSample] = useState(0);
  const [step, setStep] = useState(0);
  const { incident, audit } = SAMPLES[sample];

  useEffect(() => {
    if (reduced) {
      setStep(audit.length);
      return;
    }
    const t = window.setTimeout(() => {
      if (step >= audit.length + HOLD_STEPS) {
        setSample((s) => (s + 1) % SAMPLES.length);
        setStep(0);
      } else {
        setStep((s) => s + 1);
      }
    }, STEP_MS);
    return () => window.clearTimeout(t);
  }, [step, audit.length, reduced]);

  const { nodes, last } = computeFlow(incident, audit, Math.min(step, audit.length));

  return (
    <figure className="card relative overflow-hidden">
      <div aria-hidden="true" className="dot-grid pointer-events-none absolute inset-0" />
      <div className="relative space-y-2 p-4 sm:p-5">
        <figcaption className="eyebrow flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-info opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-info" />
          </span>
          Sample run · replayed on a loop
        </figcaption>
        <FlowGraph nodes={nodes} label="Illustration of the agent flow: Monitor, Classifier, RCA, Recovery, Verify, then Healed or Escalated." />
        <p aria-hidden="true" className="min-h-[2.5rem] rounded-xl border border-line bg-bg/70 px-3.5 py-2.5 font-mono text-[12px]">
          <span className="text-info">[{last ? last.component : 'MONITOR_AGENT'}]</span> {last ? last.message : 'Watching the pipeline…'}
        </p>
      </div>
    </figure>
  );
}
