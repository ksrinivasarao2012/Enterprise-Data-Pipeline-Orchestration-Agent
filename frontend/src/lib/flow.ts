import type { AuditEntry, Incident } from '../api';
import { isGuardMessage, isQuarantineOnly } from './incident';

export const FLOW_NODES = [
  { key: 'MONITOR_AGENT', label: 'Monitor', sub: 'Spots the failed run' },
  { key: 'CLASSIFIER_AGENT', label: 'Classifier', sub: 'Names the kind of problem' },
  { key: 'RCA_AGENT', label: 'RCA (Gemini)', sub: 'Finds the root cause' },
  { key: 'RECOVERY_AGENT', label: 'Recovery', sub: 'Tries a fix' },
  { key: 'ACTUATOR', label: 'Verify', sub: 'Checks the fix worked' },
] as const;

export const NODE_NAME: Record<string, string> = {
  MONITOR_AGENT: 'Monitor',
  CLASSIFIER_AGENT: 'Classifier',
  RCA_AGENT: 'RCA',
  RECOVERY_AGENT: 'Recovery',
  ACTUATOR: 'Verify',
  ESCALATION: 'Escalate',
};

/**
 * Which agent the graph runs after `last` (CHANGES.md section 23). The graph branches, so this
 * is not always the next circle: a rule-based fix skips RCA, an escalation skips Verify, and
 * Verify can send the incident back to Recovery.
 */
function nextAfter(last: AuditEntry | null): string | null {
  if (!last) return null;
  const m = last.message;
  if (last.component === 'CLASSIFIER_AGENT' && m.startsWith('Route: Recovery')) return 'RECOVERY_AGENT';
  if (last.component === 'CLASSIFIER_AGENT' && m.startsWith('Route: RCA')) return 'RCA_AGENT';
  if (last.component === 'CLASSIFIER_AGENT') return null; // severity line: the route line follows
  if (last.component === 'RECOVERY_AGENT' && /Remediation generated: ESCALATE/.test(m)) return null;
  if (last.component === 'ACTUATOR' && /Back to Recovery/.test(m)) return 'RECOVERY_AGENT';
  const i = FLOW_NODES.findIndex((n) => n.key === last.component);
  return i >= 0 && i < FLOW_NODES.length - 1 ? FLOW_NODES[i + 1].key : null;
}

export type NodeState = 'idle' | 'lit' | 'blocked' | 'skipped' | 'healed' | 'escalated' | 'quarantined';

export interface FlowNode {
  key: string;
  label: string;
  sub: string;
  num: string;
  state: NodeState;
  active: boolean;
}

/** Node states for an incident after `step` audit entries have been replayed. */
export function computeFlow(incident: Incident | null, audit: AuditEntry[] | null, step: number) {
  const entries = audit ?? [];
  const shown = entries.slice(0, step);
  // Done only when every step is shown AND the incident has really finished (live runs keep going).
  const done = !!incident && !!audit && step >= entries.length && (incident.status === 'RESOLVED' || incident.status === 'ESCALATED');
  const final: NodeState | null = !incident ? null
    : incident.status === 'RESOLVED' ? (isQuarantineOnly(incident) ? 'quarantined' : 'healed')
    : incident.status === 'ESCALATED' ? 'escalated' : null;
  const last = shown[shown.length - 1] ?? null;

  const seen = new Map<string, NodeState>();
  for (const a of shown) {
    const prev = seen.get(a.component);
    seen.set(a.component, prev === 'blocked' || isGuardMessage(a.message) ? 'blocked' : 'lit');
  }

  const finalState: NodeState = done && final ? final : 'idle';
  // Which node is "working" right now. While a live run waits (e.g. for Gemini) after all
  // steps so far are shown, the NEXT agent is the one working, so it pulses.
  const caughtUp = !!audit && step >= entries.length;
  const running = !!incident && !final;
  const activeKey = running && caughtUp ? nextAfter(last) ?? last?.component : last?.component;
  // How many times Verify sent the incident back to Recovery (e.g. two renamed columns).
  const loops = shown.filter((a) => a.component === 'ACTUATOR' && /Back to Recovery/.test(a.message)).length;
  const nodes: FlowNode[] = FLOW_NODES.map((n, i) => ({
    key: n.key,
    label: n.label,
    sub: n.sub,
    num: String(i + 1),
    state: seen.get(n.key) ?? (done && final ? 'skipped' : 'idle'),
    active: !done && activeKey === n.key,
  }));
  nodes.push({
    key: 'FINAL',
    label: finalState === 'healed' ? 'Healed' : finalState === 'escalated' ? 'Escalated' : finalState === 'quarantined' ? 'Quarantined' : 'Healed / Escalated',
    sub: finalState === 'healed' ? 'Run finished cleanly' : finalState === 'escalated' ? 'A person takes over' : finalState === 'quarantined' ? 'Bad rows set aside, no agents needed' : 'Final outcome',
    num: finalState === 'escalated' ? '!' : '6',
    state: finalState,
    active: finalState !== 'idle',
  });

  return { nodes, last, done, live: running, loops };
}

export const isOn = (s: NodeState) => s !== 'idle' && s !== 'skipped';
