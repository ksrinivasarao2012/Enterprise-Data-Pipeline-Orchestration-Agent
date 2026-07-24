import type { PipelineId } from './api';

export interface Preset {
  label: string;
  testCase: string;
  /** One line: what should happen when you run it. */
  hint: string;
}

export const PIPELINES: Record<PipelineId, { tab: string; accept: string; ext: string; presets: Preset[] }> = {
  a: {
    tab: 'Pipeline A · JSON',
    accept: '.json,application/json',
    ext: '.json',
    presets: [
      { label: 'Healthy file', testCase: 'case_healthy.json', hint: 'Loads cleanly. No incident.' },
      { label: 'Renamed column: country', testCase: 'case_drift_country.json', hint: 'A rule maps the new column name and heals the run, no Gemini needed.' },
      { label: 'Renamed column: email', testCase: 'case_drift_email.json', hint: 'A rule maps the new column name and heals the run, no Gemini needed.' },
      { label: 'Renamed column: id', testCase: 'case_drift_id.json', hint: 'A rule maps the new column name and heals the run, no Gemini needed.' },
      { label: 'Two renamed columns', testCase: 'case_drift_multi.json', hint: 'Verify finds the second rename and sends it back to Recovery (a loop), then the load is verified.' },
      { label: 'Wrong JSON shape', testCase: 'case_invalid_root.json', hint: 'The file is an object instead of a list; agents flatten it and heal the run.' },
      { label: 'Broken JSON syntax', testCase: 'case_malformed_syntax.json', hint: 'A bracket is missing. Gemini adds only the bracket; the row that also lacks a country is quarantined, the other 9 load.' },
      { label: 'Bad email in one row', testCase: 'case_invalid_email.json', hint: 'The bad row is quarantined, the rest load.' },
      { label: 'Duplicate IDs', testCase: 'case_duplicate_ids.json', hint: 'Duplicate rows are quarantined, the rest load.' },
    ],
  },
  b: {
    tab: 'Pipeline B · Database',
    accept: '.db',
    ext: '.db',
    presets: [
      { label: 'Healthy data', testCase: 'healthy.db', hint: 'Loads cleanly. No incident.' },
      { label: 'Missing column', testCase: 'sql_error.db', hint: 'The status_code column is gone. The SQL guard blocks any fix that fakes it, so it goes to a human.' },
      { label: 'All device IDs empty', testCase: 'referential_integrity.db', hint: 'Every row is bad, so all rows are quarantined and nothing is loaded.' },
      { label: 'Missing table', testCase: 'missing_table.db', hint: 'The table does not exist. Gemini explains why, then it goes to a human (nothing to fix).' },
    ],
  },
};
