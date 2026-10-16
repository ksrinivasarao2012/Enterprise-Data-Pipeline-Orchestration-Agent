import type { AuditEntry, Incident } from '../api';
import { humanize, isFinal, timeTaken } from './incident';

/**
 * "What did this agent do in this run?" for the circle the user clicked in the flow graph.
 * Built only from data we already have: the incident row and its audit log.
 */

export interface AgentDetail {
  title: string;
  /** What this agent does in general, in one line. */
  role: string;
  /** Key facts for this run (e.g. severity for the Classifier, Gemini's full text for RCA). */
  facts: { label: string; value: string; long?: boolean }[];
  /** This agent's own audit lines for this run. */
  lines: AuditEntry[];
  /** Why there is nothing to show (agent did not run, or has not run yet). */
  empty: string | null;
}

const ROLE: Record<string, { title: string; role: string }> = {
  MONITOR_AGENT: { title: 'Monitor', role: 'Spots the failed run and cleans up the raw error logs.' },
  CLASSIFIER_AGENT: { title: 'Classifier', role: 'Decides how serious the problem is (P0–P3) and what kind it is.' },
  RCA_AGENT: { title: 'RCA (Gemini)', role: 'Asks Gemini to explain the root cause of the failure.' },
  RECOVERY_AGENT: { title: 'Recovery', role: 'Chooses a fix: map a renamed column, repair the file or SQL, quarantine rows, or escalate.' },
  ACTUATOR: { title: 'Verify', role: 'Applies the fix, re-runs the pipeline and checks real rows were saved.' },
  AI_FIX_AGENT: {
    title: 'AI fix (Gemini)',
    role: 'When no rule works, Gemini proposes one fix from a fixed list of data steps. It is tried on a copy first and used only if every check passes.',
  },
  FINAL: { title: 'Healed', role: 'The run saved real rows after the fix.' },
  ESCALATION: { title: 'Escalate', role: 'Hands the incident to a person, with the reason.' },
};

export function agentDetail(key: string, incident: Incident, audit: AuditEntry[] | null): AgentDetail {
  const meta = ROLE[key] ?? { title: humanize(key), role: '' };
  const lines = (audit ?? []).filter((a) => a.component === key);
  const routeLine = (audit ?? []).find((a) => a.component === 'CLASSIFIER_AGENT' && a.message.startsWith('Route: '))?.message ?? '';
  const facts: AgentDetail['facts'] = [];
  const finished = isFinal(incident.status);

  switch (key) {
    case 'MONITOR_AGENT':
      if (incident.error_class) facts.push({ label: 'Error', value: incident.error_class });
      if (incident.error_message) facts.push({ label: 'Message', value: incident.error_message, long: true });
      break;
    case 'CLASSIFIER_AGENT':
      if (lines.length) {
        facts.push({ label: 'Severity', value: incident.severity });
        facts.push({ label: 'Category', value: humanize(incident.category) });
        if (routeLine) facts.push({ label: 'Next step', value: routeLine.replace(/^Route: /, ''), long: true });
      }
      break;
    case 'RCA_AGENT':
      if (incident.root_cause) {
        facts.push({ label: "Gemini's explanation", value: incident.root_cause, long: true });
        // The audit line is a 60-character cut of the same text; the full text above replaces it.
        lines.splice(0, lines.length, ...lines.filter((a) => !a.message.startsWith('Analysis completed:')));
      }
      break;
    case 'RECOVERY_AGENT':
      if (lines.length && incident.recovery_action) facts.push({ label: 'Chosen fix', value: incident.recovery_action, long: true });
      break;
    case 'AI_FIX_AGENT': {
      const proposed = lines.find((a) => a.message.startsWith('Gemini proposed'));
      const result = lines.find((a) => /^(Dry run (passed|rejected)|No AI fix was applied)/.test(a.message));
      if (proposed) facts.push({ label: "Gemini's proposal", value: proposed.message.replace(/^Gemini proposed:? ?/, ''), long: true });
      if (result) facts.push({ label: 'Dry run on a copy', value: result.message, long: true });
      break;
    }
    case 'ESCALATION':
      if (incident.status === 'ESCALATED') facts.push({ label: 'Status', value: 'Handed to a person' });
      break;
    case 'FINAL':
      if (incident.status === 'ESCALATED') break;
      facts.push({
        label: 'Status',
        value: incident.status === 'RESOLVED' ? 'Healed' : 'Still running',
      });
      if (finished) facts.push({ label: 'Time taken', value: timeTaken(incident) });
      break;
  }

  const hasContent = lines.length > 0 || facts.length > 0;
  // The graph branches (CHANGES.md section 23), so say WHY a step was skipped.
  let skippedWhy = 'This agent did not run in this incident (an earlier step ended it).';
  if (key === 'RCA_AGENT' && routeLine.startsWith('Route: Recovery'))
    skippedWhy = 'Skipped: a safe rule already knew the fix, so no Gemini call was needed.';
  else if (key === 'RECOVERY_AGENT' && routeLine.includes('then a person'))
    skippedWhy = 'Skipped: there was no automatic fix to try, so the incident went straight to a person.';
  else if (key === 'ACTUATOR' && incident.status === 'ESCALATED')
    skippedWhy = 'Skipped: no fix was applied, so there was nothing to verify.';
  else if (key === 'AI_FIX_AGENT' && incident.status === 'RESOLVED')
    skippedWhy = 'Skipped: the first fix worked, so no AI fix was needed.';
  else if (key === 'AI_FIX_AGENT' && incident.blocked_by_guard)
    skippedWhy = 'Skipped: a safety guard blocked an earlier fix, so the AI does not get a second try.';
  else if (key === 'AI_FIX_AGENT')
    skippedWhy = 'Skipped: an AI fix only reshapes Pipeline A files that exist (not a missing file or table, not a database).';
  else if (key === 'FINAL')
    skippedWhy = 'Not healed: the incident went to a person.';
  else if (key === 'ESCALATION' && incident.status === 'RESOLVED')
    skippedWhy = 'Skipped: the incident was healed, so no person was needed.';
  const empty = (key === 'FINAL' && incident.status !== 'ESCALATED') || hasContent ? null : finished ? skippedWhy : 'This agent has not run yet.';
  return { ...meta, facts, lines, empty };
}
