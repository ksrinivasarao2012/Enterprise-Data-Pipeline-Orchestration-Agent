import type { AuditEntry, Incident } from '../api';
import { isGuardMessage, isQuarantineOnly } from './incident';

/**
 * The agent graph as it really runs (CHANGES.md sections 23 and 34): it branches.
 *   Monitor → Classifier → (rule knows the fix) Recovery, or (new error) RCA
 *   RCA → Recovery, or an AI fix (no rule fits), or a person (nothing to fix)
 *   Recovery → Verify, or an AI fix (no fix found), or a person (guard blocked it)
 *   Verify → Healed, back to Recovery (another drift), an AI fix (re-run failed), or a person
 *   AI fix → Verify (dry run passed on a copy), or a person (rejected)
 */
export const FLOW_NODES = [
  { key: 'MONITOR_AGENT', label: 'Monitor', sub: 'Spots the failed run', num: '1' },
  { key: 'CLASSIFIER_AGENT', label: 'Classifier', sub: 'Picks the route', num: '2' },
  { key: 'RCA_AGENT', label: 'RCA (Gemini)', sub: 'Finds the root cause', num: '3' },
  { key: 'RECOVERY_AGENT', label: 'Recovery', sub: 'Tries a known fix', num: '4' },
  { key: 'ACTUATOR', label: 'Verify', sub: 'Re-runs and checks', num: '5' },
  { key: 'AI_FIX_AGENT', label: 'AI fix', sub: 'Dry run on a copy', num: 'AI' },
] as const;

export const NODE_NAME: Record<string, string> = {
  MONITOR_AGENT: 'Monitor',
  CLASSIFIER_AGENT: 'Classifier',
  RCA_AGENT: 'RCA',
  RECOVERY_AGENT: 'Recovery',
  ACTUATOR: 'Verify',
  AI_FIX_AGENT: 'AI fix',
  ESCALATION: 'Escalate',
};

/** Every arrow the graph can take. `label` is a short hint drawn next to branch arrows. */
export const FLOW_EDGES: { from: string; to: string; label?: string }[] = [
  { from: 'MONITOR_AGENT', to: 'CLASSIFIER_AGENT' },
  { from: 'CLASSIFIER_AGENT', to: 'RECOVERY_AGENT', label: 'a rule knows' },
  { from: 'CLASSIFIER_AGENT', to: 'RCA_AGENT', label: 'new error' },
  { from: 'RCA_AGENT', to: 'RECOVERY_AGENT', label: 'Gemini repair' },
  { from: 'RCA_AGENT', to: 'AI_FIX_AGENT', label: 'no rule fits' },
  { from: 'RCA_AGENT', to: 'ESCALATION', label: 'nothing to fix' },
  { from: 'RECOVERY_AGENT', to: 'ACTUATOR' },
  { from: 'RECOVERY_AGENT', to: 'AI_FIX_AGENT' },
  { from: 'RECOVERY_AGENT', to: 'ESCALATION' },
  { from: 'ACTUATOR', to: 'FINAL' },
  { from: 'ACTUATOR', to: 'AI_FIX_AGENT', label: 'failed' },
  { from: 'AI_FIX_AGENT', to: 'ACTUATOR', label: 'passed' },
  { from: 'AI_FIX_AGENT', to: 'ESCALATION', label: 'rejected' },
  { from: 'ACTUATOR', to: 'ESCALATION' },
];

const KNOWN = new Set<string>([...FLOW_NODES.map((n) => n.key), 'ESCALATION']);

/** Which agent runs after `last`, used to pulse the "working" circle during a live run. */
function nextAfter(last: AuditEntry | null, routeLine: string): string | null {
  if (!last) return null;
  const m = last.message;
  switch (last.component) {
    case 'MONITOR_AGENT':
      return 'CLASSIFIER_AGENT';
    case 'CLASSIFIER_AGENT':
      if (m.startsWith('Route: Recovery')) return 'RECOVERY_AGENT';
      if (m.startsWith('Route: RCA')) return 'RCA_AGENT';
      return null; // severity line: the route line follows
    case 'RCA_AGENT':
      if (routeLine.includes('AI fix')) return 'AI_FIX_AGENT';
      if (routeLine.includes('then a person')) return 'ESCALATION';
      return 'RECOVERY_AGENT';
    case 'RECOVERY_AGENT':
      return /Remediation generated: ESCALATE/.test(m) ? null : 'ACTUATOR';
    case 'ACTUATOR':
      if (/Back to Recovery/.test(m)) return 'RECOVERY_AGENT';
      if (/Trying an AI fix/.test(m)) return 'AI_FIX_AGENT';
      return null;
    case 'AI_FIX_AGENT':
      if (/^Dry run passed/.test(m)) return 'ACTUATOR';
      if (/^Gemini proposed: /.test(m)) return 'AI_FIX_AGENT';
      return 'ESCALATION';
    default:
      return null;
  }
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

export interface FlowEdge {
  from: string;
  to: string;
  label?: string;
  lit: boolean;
  /** Colour of a lit edge. */
  state: NodeState;
}

/** Node and edge states for an incident after `step` audit entries have been replayed. */
export function computeFlow(incident: Incident | null, audit: AuditEntry[] | null, step: number) {
  const entries = audit ?? [];
  const shown = entries.slice(0, step);
  // Done only when every step is shown AND the incident has really finished (live runs keep going).
  const done = !!incident && !!audit && step >= entries.length && (incident.status === 'RESOLVED' || incident.status === 'ESCALATED');
  const final: NodeState | null = !incident ? null
    : incident.status === 'RESOLVED' ? (isQuarantineOnly(incident) ? 'quarantined' : 'healed')
    : incident.status === 'ESCALATED' ? 'escalated' : null;
  const last = shown[shown.length - 1] ?? null;
  const routeLine = entries.find((a) => a.component === 'CLASSIFIER_AGENT' && a.message.startsWith('Route: '))?.message ?? '';

  const seen = new Map<string, NodeState>();
  for (const a of shown) {
    const prev = seen.get(a.component);
    seen.set(a.component, prev === 'blocked' || isGuardMessage(a.message) ? 'blocked' : 'lit');
  }

  // The path the incident really took: the agents in audit order, repeats collapsed.
  const path: string[] = [];
  for (const a of shown) {
    if (KNOWN.has(a.component) && path[path.length - 1] !== a.component) path.push(a.component);
  }
  if (done && final === 'escalated' && path[path.length - 1] !== 'ESCALATION') path.push('ESCALATION');
  if (done && (final === 'healed' || final === 'quarantined')) path.push('FINAL');
  const taken = new Set(path.slice(1).map((k, i) => `${path[i]}>${k}`));

  const caughtUp = !!audit && step >= entries.length;
  const running = !!incident && !final;
  const activeKey = running && caughtUp ? nextAfter(last, routeLine) ?? last?.component : last?.component;
  // How many times Verify sent the incident back to Recovery (e.g. two renamed columns).
  const loops = shown.filter((a) => a.component === 'ACTUATOR' && /Back to Recovery/.test(a.message)).length;

  const nodes: FlowNode[] = FLOW_NODES.map((n) => ({
    key: n.key,
    label: n.label,
    sub: n.sub,
    num: n.num,
    state: seen.get(n.key) ?? (done ? 'skipped' : 'idle'),
    active: !done && activeKey === n.key,
  }));
  const healedState: NodeState = done && (final === 'healed' || final === 'quarantined') ? final : done ? 'skipped' : 'idle';
  nodes.push({
    key: 'FINAL',
    label: final === 'quarantined' ? 'Quarantined' : 'Healed',
    sub: final === 'quarantined' ? 'Bad rows set aside' : 'Run saved real rows',
    num: '✓',
    state: healedState,
    active: healedState === 'healed' || healedState === 'quarantined',
  });
  const escState: NodeState = done ? (final === 'escalated' ? 'escalated' : 'skipped') : seen.has('ESCALATION') ? 'lit' : 'idle';
  nodes.push({
    key: 'ESCALATION',
    label: 'Escalate',
    sub: 'A person takes over',
    num: '!',
    state: escState,
    active: escState === 'escalated' || (!done && activeKey === 'ESCALATION'),
  });

  const stateOf = new Map(nodes.map((n) => [n.key, n.state]));
  const edges: FlowEdge[] = FLOW_EDGES.map((e) => {
    const lit = taken.has(`${e.from}>${e.to}`);
    const to = stateOf.get(e.to);
    const state: NodeState = stateOf.get(e.from) === 'blocked' || e.to === 'ESCALATION' ? (e.to === 'ESCALATION' ? 'escalated' : 'blocked')
      : to === 'healed' || to === 'quarantined' ? to : 'lit';
    return { ...e, lit, state };
  });

  return { nodes, edges, path, last, done, live: running, loops };
}

export const isOn = (s: NodeState) => s !== 'idle' && s !== 'skipped';
