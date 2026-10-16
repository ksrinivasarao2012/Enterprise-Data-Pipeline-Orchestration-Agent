# Changes

Fixes made to the self-healing agents. Each entry says what was wrong, why it mattered, and what changed.

---

## 1. "HEALED" now means the data was really saved

**Problem:** A file with two renamed columns (`customer_id` → `id` and `email` → `user_email`) was reported as healed, but **0 rows** were saved. The system fixed the first column, saw a different error, and assumed it was done.

**Why it matters:** A self-healing tool that reports false successes can't be trusted.

**Fix:**
- The actuator keeps fixing columns one by one and re-runs the pipeline until the data saves (max 4 attempts).
- A run is marked `HEALED` only when rows were actually written.
- If every row is bad and gets quarantined, the run ends as `QUARANTINED`, not `HEALED`.

**Files:** `src/services/remediation_service.py`, `src/agents/recovery.py`, `src/api/main.py`

---

## 2. Gemini is no longer told to invent values

**Problem:** The SQL-repair prompt told Gemini to replace a missing column with `0.0` or `NULL`. For `sql_error.db` this set every device's error rate to 0 and marked it as healed: fake data.

**Why it matters:** Fake numbers in analytics are worse than a failed run, because nobody notices them.

**Fix:**
- The prompt now forbids constants. If a column is missing with no real replacement, Gemini must answer `UNRESOLVABLE`.
- `UNRESOLVABLE` escalates the incident to a human, and nothing is written.

**Files:** `src/agents/recovery.py`

---

## 3. No more endless healing loops

**Problem:** When a fix failed, the verification run opened a *new* incident, which started a *new* healing loop inside the current one. One broken JSON file created **157 incidents** and kept calling Gemini.

**Why it matters:** It wastes the Gemini quota (about 20 requests/day on the free tier) and floods the dashboard.

**Fix:**
- Verification and retry runs (both pipelines) report errors back to the actuator instead of opening new incidents.
- If one run already has more than 4 incidents, it is escalated before any agent or Gemini call.

**Files:** `src/services/remediation_service.py`, `src/pipeline/db_pipeline.py`, `src/api/main.py`, `src/incidents/incident_repository.py`

---

## 4. SQL guard: checks Gemini's SQL before it runs

**Problem:** Fix 2 only *asks* Gemini not to invent values. LLMs don't always obey, so a fake query could still run.

**Why it matters:** The safety of the data shouldn't depend on the LLM following instructions.

**Fix:** A new check (`src/agents/sql_guard.py`) sits between Gemini and the database:

```
Gemini writes SQL  →  SQL guard checks it  →  database runs it
```

It uses `sqlglot` to read both queries (the original and Gemini's) and rejects the repair if:
- it isn't exactly one `SELECT` (e.g. `DELETE`, `DROP`, `ATTACH`);
- it reads from any other table;
- it uses a column that doesn't exist;
- it drops or adds an output;
- an output that needed a missing column now uses no replacement column, meaning it was swapped for a constant.

A rejected repair is escalated to a human. A real rename (e.g. `status_code` → `http_status`) still passes and heals normally.

**Files:** `src/agents/sql_guard.py` (new), `src/agents/recovery.py`, `requirements.txt`, `pyproject.toml` (adds `sqlglot`)

---

## 5. SQL guard: "rename only" rule

**Problem:** Fix 4 checked *which* columns Gemini used, not *how*. A sneaky formula like `http_status * 0 >= 400` uses a real column, so it passed, but anything × 0 is 0, so every error rate became 0 (fake). Gemini could also pick a real but wrong column (e.g. `timestamp`).

**Why it matters:** Fake data could still slip through a clever or confused LLM.

**Fix:** The only honest repair for a missing column is a **rename**, so that's the only one allowed:
- The guard takes your original query, swaps the missing name for the new one, and compares it with Gemini's query.
- If Gemini changed anything else (added `* 0`, changed `400` to `500`, added `ORDER BY`), it's blocked.
- The new name must be on an approved list in `configs/settings.yaml` (`sql_column_aliases`). For example, `status_code` may only become `http_status`, `status`, `response_code` or `http_code`.

| Gemini's query | Result |
|:---|:---|
| `http_status >= 400` (real rename) | Allowed |
| `HTTP_STATUS >= 400`, different spacing | Allowed |
| `http_status * 0 >= 400` | Blocked |
| `timestamp >= 400` (real but wrong column) | Blocked |

**Cost:** A correct rename that also changes something cosmetic (e.g. drops the label `AS error_rate`) is blocked and goes to a human. Renames not on the list also need a human, or you add them to `settings.yaml`.

**Files:** `src/agents/sql_guard.py`, `src/agents/recovery.py`, `configs/settings.yaml`

---

## 6. Security fixes

Six holes closed. Each one was first shown to be a real problem by a test.

| # | Problem (simple words) | Fix | File |
|:---|:---|:---|:---|
| 2 | **File path trick.** The app opened any file name it was sent, e.g. `../../.env` (your API key file) or `/etc/passwd`, and could pass it to Gemini. | Only names of real files in `test_data/pipeline_A` or `pipeline_B` are accepted. Anything else gets an error before any file is opened. | `src/api/main.py` |
| 3 | **Script injection.** Code hidden in an uploaded file (e.g. in an email field) **ran in the browser** of anyone viewing the dashboard. Confirmed in a real Chrome test. | Every value is escaped before it is shown, so it appears as plain text. The "Audit" buttons no longer build JavaScript from data. | `public/app.js` |
| 4 | **No upload size limit.** A huge upload could slow down or crash the app. | Uploads over 5 MB are refused before they are read (`EHA_MAX_UPLOAD_MB` to change it). | `src/api/main.py` |
| 5 | **Hidden instructions in file repair.** A broken file could hide text like "change every email", and Gemini's repaired file was saved without any check. | After Gemini repairs a file, every key and value must be exactly the same as in the broken file. Only brackets and commas may change. Otherwise it goes to a person. | `src/services/remediation_service.py` |
| 6 | **Private data sent to Gemini.** Users weren't told broken files leave the server. | A warning next to the upload box, plus a note in the README. | `public/index.html` |
| 7 | **Weak SQL word list.** Blocked harmless names (`last_updated`, `updated_email`) and missed risky SQL (`ATTACH`). | Word list removed. Column names may only use letters, numbers and `_`. SQL must be exactly one `SELECT`. | `src/services/remediation_service.py` |

**Side effect (on purpose):** the preset `case_malformed_syntax.json` is missing a bracket **and** row 5's `country`. It used to "heal" only because Gemini invented a country. Now it's sent to a person, because inventing values is fake data. A file with only a syntax problem still heals.

**#1 login:** done in section 7.

---

## 7. Login and permissions

**Problem:** There was no login. Anyone who opened the dashboard could do everything, including **Clear History**, which deletes all data.

**Why it matters:** An interviewer (or a stranger) could wipe your demo with one click.

**Fix:** Every API request now needs a key. There are two keys, and **you create them yourself** (see README → Security → "Setting up the keys"):

| Action | Viewer key (e.g. interviewer) | Admin key (you) |
|:---|:---|:---|
| See dashboard, incidents, audit, quarantine | ✅ | ✅ |
| Run preset test files | ✅ (max 10 runs/minute) | ✅ |
| Upload own file | ✅ (max 1 MB) | ✅ (max 5 MB) |
| Clear History | ❌ | ✅ |
| Telemetry API (`/telemetry/incident`, `/runs/...`) | ❌ | ✅ |

- The dashboard shows a **login box**. The key is kept only for that browser tab and sent with every request.
- Wrong or missing key → `401`. Viewer trying an admin action → `403`. Too many runs → `429`.
- Keys are compared with `secrets.compare_digest`, which can't be guessed letter by letter.
- **No keys set:** locally the app runs **open** (like before, for easy development). On Vercel it is **fully locked**, so a public site is never left open by accident.
- The run limit protects your Gemini quota.

**Limits:**
- The run counter is kept in memory, so on Vercel each server instance counts separately.
- The Streamlit dashboard had no login; it was removed in section 15.
- There's no limit on wrong-key attempts yet.

**Files:** `src/api/auth.py` (new), `src/api/main.py`, `public/index.html`, `public/app.js`, `.env.example`

---

## 8. New React dashboard

**What:** A new dashboard in `frontend/` (React + Vite + TypeScript + Tailwind), designed with Claude Design and then checked and fixed here.

**Why:** The old page was one long HTML file. The new one explains the project in 30 seconds to an interviewer: a login screen with a short pitch, a "Try it" panel, an **agent flow** that replays each incident step by step, an **incident timeline**, and clear **"Blocked by guard"** badges when a safety check stopped Gemini.

**Checked and fixed before adding it:**

| Problem found | Fix |
|:---|:---|
| "Clear history" called `DELETE /api/history`, which doesn't exist | Now calls `POST /api/history/clear` (`frontend/src/api.ts`) |
| Gemini's `UNRESOLVABLE` answer and SQL-guard blocks weren't shown as "blocked" in the flow, because the reason was missing from the audit log | Recovery agent now writes its reason into the audit log (`src/agents/recovery.py`); the frontend also checks `recovery_action` |
| Blocked reason read "The LLM repair: …" | Cleaner cut of the reason text (`frontend/src/lib/incident.ts`) |
| 5 preset hints didn't match what the backend really does (e.g. "Wrong JSON shape goes to a human", but it heals) | Hints rewritten to the real outcomes (`frontend/src/presets.ts`) |
| Heading said "in real time", but the flow is replayed from the audit log | Now "step by step" |
| Pages scrolled sideways on phones (long error text, and a hidden table label escaping its box) | Layout columns may shrink, long words wrap, the table's scroll box contains its labels |

**How it's served:** FastAPI serves `frontend/dist/` when it's built, otherwise the old `public/` page. `EHA_UI=legacy` forces the old one. Build with `cd frontend && npm install && npm run build`.

**Not done yet:** Vercel still serves the old `public/` page. Deploying the React app needs a build step in `vercel.json`.

**Files:** `frontend/` (new), `src/api/main.py`, `src/agents/recovery.py`, `.gitignore` (ignores `frontend/node_modules` and `frontend/dist`), `test/test_frontend_react.py` (new)

---

## 9. Live agent flow

**Problem:** The agent flow only started *after* a run had finished, so it was a replay, not live. The cause was a backend bug: the "run pipeline" endpoint did all its work inside the server's main loop, so **the whole server froze** during a run. It couldn't answer the dashboard or any other user until the run ended (measured: 2.7 s frozen with a 4 s Gemini call).

**Why it matters:** Watching agents work step by step is the main thing the dashboard shows off. A frozen server is also a real bug for anyone else using the app at the same time.

**Fix:**
- **Backend:** pipeline runs now execute in a worker thread (`run_in_threadpool`), so the server keeps answering during a run. Runs share working files, so they still take turns through a lock (`PIPELINE_RUN_LOCK`). Clear History waits for a running pipeline instead of cutting it in half.
- **Frontend:** when you click Run, the dashboard checks for new steps every 0.5 s, shows the new incident as soon as it appears, and lights up each agent as it finishes. While the agents wait (e.g. for Gemini), the **next** step pulses. The caption says "Live" during a run and "Replayed from the audit log" afterwards.

**Limit:** simple fixes finish in about 0.2 s, faster than the eye can follow, so each step is still shown for about 0.4 s. Steps that call Gemini take seconds and visibly appear live.

**Files:** `src/api/main.py`, `frontend/src/components/Dashboard.tsx`, `frontend/src/lib/flow.ts`, `frontend/src/components/MissionControl.tsx`, `test/test_live.py` (new)

---

## 10. Quarantine-only runs shown honestly

**Problem:** For "Bad email in one row" and "Duplicate IDs", no agent runs: the validation rules set the bad row aside, load the other 9 rows and close the incident at once. But the dashboard drew an empty agent graph ending in **"Healed"**, the incident was labelled **"Unknown" / P2**, and it was counted in the **Healed** number.

**Why it matters:** It claimed an agent heal that never happened.

**Fix:**
- **Backend:** these warnings (`DataQualityAnomaly`, `ReferentialIntegrityAnomaly`) are now categorised as **Data quality** (P3 for Pipeline A), and the **Healed** metric counts only incidents the agents actually fixed.
- **Dashboard:** instead of an agent graph with unused steps, these runs get their own complete graph of what really happened: **Check rows → Bad rows found → Quarantined → Rest saved**, with the real counts (e.g. "1 row breaks a rule", "9 rows loaded") and the heading **"Caught by validation. No agents needed."** The incident card and "What changed" panel say the rest of the data loaded and no fix was needed.

**Files:** `src/incidents/severity_rules.py`, `src/api/main.py`, `frontend/src/lib/incident.ts`, `frontend/src/lib/flow.ts`, `frontend/src/components/` (ValidationFlow (new), MissionControl, FlowGraph, FlowStrip, WhatChanged, IncidentCard, AuditPanel)

---

## 11. Incident paging

**Purpose:** The dashboard asked for every incident every 3 seconds. After hundreds of runs that gets slow. Now it asks only for what is on screen.

- `GET /api/incidents?limit=20&offset=0` returns one page, newest first. The `X-Total-Count` header says how many exist. No `limit` = 50 (max 200).
- The dashboard shows the newest **20**, with "**20 of 135**" and a **Show more** button (+20 each click).
- The classifier used to load every incident just to count open ones for a pipeline. It now asks the database for that count directly.

**Files:** `src/api/main.py`, `src/incidents/incident_repository.py` (`get_incidents_page`, `count_unresolved_for_pipeline`), `src/agents/classifier.py`, `frontend/src/api.ts`, `frontend/src/components/Dashboard.tsx`, `frontend/src/components/IncidentTimeline.tsx`, `test/test_paging.py` (new)

---

## 12. Two small dashboard fixes

**Purpose:** Remove two things that looked wrong.

- **No blue box around the page after switching tabs.** The page gets focus on a tab switch (so screen readers jump to it). It no longer draws a focus ring around the whole page. Buttons and links still show their ring.
- **Replay hidden during a live run.** The steps are already appearing live, so Replay only shows once the run has finished.

**Files:** `frontend/src/index.css`, `frontend/src/components/MissionControl.tsx`, `frontend/src/components/AuditPanel.tsx`, `test/test_frontend_react.py`, `test/test_live.py`

---

## 13. Explain each agent + download report

**Purpose:** Let an interviewer see *what each agent did* and keep a copy of an incident.

- **Click a circle** in the agent flow (e.g. "RCA") to see what that agent did in this run: its log lines plus the key result. Monitor shows the error, Classifier the severity, RCA **Gemini's full explanation**, Recovery the chosen fix, and the last circle the outcome and time taken. Works with the keyboard too. Click the same circle again to close it.
- **Download report** buttons in the incident panel: **PDF** or **JSON**, with the incident and its whole audit trail.
- New endpoint: `GET /api/incidents/{id}/report?format=pdf|json` (viewers allowed). The PDF is made with **fpdf2** (new dependency). Characters the PDF font can't show (e.g. emoji) are replaced, so the export never crashes.

**Files:** `src/services/report_service.py` (new), `src/api/main.py`, `requirements.txt`, `pyproject.toml`, `frontend/src/lib/agentDetail.ts` (new), `frontend/src/components/AgentDetailPanel.tsx` (new), `frontend/src/components/DownloadReport.tsx` (new), `FlowGraph.tsx`, `MissionControl.tsx`, `AuditPanel.tsx`, `frontend/src/api.ts`, `test/test_report.py` (new), `test/test_frontend_react.py`

---

## 14. "Try all" demo button

**Purpose:** An interviewer sees every case (heal, quarantine, guard block, escalation) with one click.

- **Try all 13 presets** button under "Run pipeline". It runs every preset one after another. Mission control follows each run live, and a list shows each result. A short summary appears at the end. **Stop after this one** ends it early.
- **Counts as one run.** Viewers may start 10 runs a minute, and 13 presets would break that. Starting the demo counts as 1 run and returns a token (`POST /api/demo/start`). With the token (`X-Demo-Token` header), each preset may run **once** without counting. Tokens expire after 10 minutes, can't be reused for the same preset, and never make uploads free.
- It still calls Gemini for the cases that need the agents: about 11 calls per demo (counted with the test model; real Gemini can differ slightly).

**Files:** `src/api/auth.py`, `src/api/main.py`, `frontend/src/api.ts`, `frontend/src/components/TryItPanel.tsx`, `test/test_auth.py`, `test/test_frontend_react.py`

---

## 15. Streamlit dashboard removed

**Purpose:** One dashboard, not three. The React app replaces Streamlit.

- Deleted `src/ui/` (Streamlit) and removed `streamlit` and `watchdog` from `pyproject.toml`.
- `run_platform.py` now starts only the server. It prints `http://localhost:8000` and opens it in your browser (`--no-browser` to skip). Logs show in the terminal.
- Added the missing `pyyaml` to `pyproject.toml` (it was only in `requirements.txt`, so `uv sync` could miss it).
- The old HTML dashboard (`public/`) is **kept for now**, because the live Vercel site still serves it. Remove it once Vercel serves the React app.

**Files:** `src/ui/` (deleted), `run_platform.py`, `pyproject.toml`, `README.md`

---

## 16. Escalations in plain words

**Purpose:** The Escalations list showed raw errors (error signatures, whole SQL queries). Now each row says what went wrong and why a person is needed.

- Each escalation has a **summary** (e.g. "The column 'status_code' is missing from the table 'device_telemetry'.") and a **why** (e.g. "A safety check blocked the AI fix: ..." or "Gemini found no safe replacement for the missing data, so it refused to guess.").
- The raw error is still there, under **Technical error** (click to open).
- The "why" comes from the incident's own log when a guard blocked the fix. Otherwise it comes from the kind of error.
- **Bug fix:** the escalation list file now has one shared path (`get_escalation_log_path`). Before, running the tests added fake escalations to your real list, and **Clear history in the tests deleted it**. On Vercel the file was written where it can't be, so the list stayed empty there.

**Files:** `src/incidents/escalation_text.py` (new), `src/config.py`, `src/api/main.py`, `src/services/remediation_service.py`, `frontend/src/api.ts`, `frontend/src/components/RecordsTabs.tsx`, `test/test_escalation_text.py` (new), `test/test_frontend_react.py`

---

## 17. Records paging

**Purpose:** The Records page asked for every quarantined row and every escalation every 3 seconds. Escalations also look up their incident for the plain-English text. Both get slow as history grows.

- `GET /api/quarantine` and `GET /api/escalations` now take `limit` and `offset`, return **newest first**, and send the total in `X-Total-Count` (no `limit` = 50, max 200).
- Escalations: only the rows on the page get their plain-English text, so the extra lookups stay small.
- Dashboard: each tab shows the newest **20**, with "20 of 57" and **Show more** (+20). Only the open tab loads its rows. The other tab asks for 1 row, just to keep its count badge right.
- Escalations are now shown newest first (before, oldest first).

**Files:** `src/services/quarantine_service.py`, `src/api/main.py`, `frontend/src/api.ts`, `frontend/src/components/RecordsTabs.tsx`, `test/test_records_paging.py` (new), `test/test_escalation_text.py`, `test/test_frontend_react.py`

---

## 18. Incident search and filters + Try all on phones

**Purpose:** Find incidents quickly once there are many runs, and let phone users watch Try all.

- **Filter bar** on the Incident log: a **search box** (id, run, error text, root cause), a **pipeline** menu (A or B) and **outcome** chips: Healed, Quarantined, Escalated, In progress. The count shows "3 of 12 matching". **Clear filters** resets them.
- Filters run on the **server** (`GET /api/incidents?pipeline=&outcome=&q=`), so paging and totals stay right. Values are checked (unknown values get `422`) and always passed as SQL parameters. `%` and `_` in the search are treated as normal letters.
- Search waits 300 ms after you stop typing. Filters only apply on the Incident log; the Run page always follows the newest incident.
- **Try all on phones:** the page no longer jumps to the agent flow on every run. It brings the results list into view once and keeps it there. A single "Run pipeline" still scrolls to the flow.

**Files:** `src/incidents/incident_repository.py`, `src/api/main.py`, `frontend/src/api.ts`, `frontend/src/components/Dashboard.tsx`, `frontend/src/components/IncidentTimeline.tsx`, `frontend/src/components/TryItPanel.tsx`, `test/test_incident_filters.py` (new), `test/test_frontend_react.py`

---

## 19. Wrong-key lockout + limits stored in the database

**Purpose:** Stop key guessing, and make the limits real on Vercel.

- **Lockout:** 10 wrong keys from one visitor in 15 minutes → `429 Too many wrong keys. Try again in N minutes.` (with `Retry-After`). While locked, **even the right key is refused**, so guessing can't carry on. A request with no key (the login screen checking) is not counted. Change with `EHA_MAX_FAILED_KEYS` / `EHA_LOCKOUT_MINUTES`.
- **Limits in the database:** the viewer run limit, the lockout counter and the Try all tokens moved from memory into 3 small tables (`auth_failures`, `run_events`, `demo_grants`). A restart no longer resets them, and all server copies on Vercel share them. Using a Try all token is one `DELETE`, so two copies can't both use it.
- **Fewer database reads:** a visitor with a correct key is re-checked at most every 30 seconds per server copy. Wrong keys are always checked.
- **Visitor address fix:** locally `x-forwarded-for` is now ignored. Before, anyone could send a made-up address in that header to dodge the limits. On Vercel it is still used, because Vercel sets it.
- If the database can't be reached, the limits are skipped (and logged) instead of locking everyone out. The keys themselves are still required.

**Files:** `src/api/security_store.py` (new), `src/api/auth.py`, `database/init_db.py`, `test/conftest.py`, `test/test_lockout_and_shared_limits.py` (new), `README.md`

---

## 20. Escalations read from the database

**Purpose:** The Escalations tab read a text file (`escalated_incidents.log`). On Vercel that file lived in `/tmp`, which is wiped and differs per server copy, so escalations could vanish and the tab could disagree with the Incident log.

- The Escalations tab now lists incidents whose status is **ESCALATED**, straight from the incidents table: newest first, paged, with the plain-English text. No new table was needed.
- Nothing writes the text file any more. `get_escalation_log_path` and the file clean-up in Clear history are removed. Clear history empties the list because it deletes the incidents.
- Escalations made without any agent (the heal-attempt cap) now show up too. Before, only some paths wrote to the file.
- Old escalations still show, because their incidents are already in the database. The old `escalated_incidents.log` in the project folder is no longer used and can be deleted.

**Files:** `src/api/main.py`, `src/services/remediation_service.py`, `src/config.py`, `test/test_escalations_from_db.py` (new), `test/test_escalation_text.py`, `test/test_records_paging.py`, `README.md`

---

## 21. Clear history shown in Open mode

**Purpose:** Locally with no keys set ("Open mode"), the server lets everyone act as admin, but the dashboard hid **Clear history**, because it only showed it when login keys were on. Now it shows for any admin, including Open mode. Viewers still never see it.

**Files:** `frontend/src/components/Dashboard.tsx`, `test/test_frontend_react.py`

---

## 22. Crashed heal is escalated, not left OPEN

**Purpose:** Never lose an incident. If the healing code itself crashed halfway (e.g. a missing package like `sqlglot`, or a bug), the incident stayed OPEN / INVESTIGATING for ever. It never reached Escalations, so no person was told.

- The agents and the fix now run inside a safety net. On a crash the incident becomes **ESCALATED**, and the audit log gets "Healing crashed: ModuleNotFoundError: ... Sent to a person."
- The Escalations tab explains it in plain words: "The healing itself crashed (ModuleNotFoundError), so a person was asked to look at it instead."
- An incident that had already finished (healed or escalated) before a late crash is left as it is.
- A Gemini outage is **not** a crash. The agents already handle that themselves: RCA falls back to a basic analysis, and Recovery escalates. A test checks this too.

**Files:** `src/api/main.py`, `src/incidents/escalation_text.py`, `test/test_crash_escalates.py` (new)

---

## 23. Branching agent graph + Recovery uses RCA

**Purpose:** The LangGraph graph was a straight line (Monitor → Classifier → RCA → Recovery). Every incident took every step, and the real decisions (apply, verify, retry) were hidden in plain Python afterwards. So "multi-agent" was partly for show. Now the graph makes the decisions.

**The new graph** (`src/orchestration/graph.py`):
- **Classifier chooses a route** and writes it in the audit log:
  - *a safe rule knows the fix* → Recovery, **no Gemini call**;
  - *no rule fits* → RCA (Gemini) → Recovery;
  - *nothing to fix* → RCA explains it → Escalate.
- **Recovery → Apply fix → Verify** are graph steps. If Verify finds another renamed column, the graph **loops back to Recovery** (max 4 tries). Otherwise it ends Healed / Quarantined, or goes to **Escalate**.
- **Recovery uses RCA:** the SQL rewrite, column-mapping and JSON-repair prompts all include the RCA analysis.
- **Broken JSON can now heal:** Gemini adds only the missing `},` (the JSON repair check still blocks any invented value). A field missing from **some** rows now quarantines those rows ("Missing field 'country'"). A field missing from **every** row is still schema drift. Result: 9 rows loaded, 1 held back.
- **Escalate** says why in plain words, and is logged as its own step, so the dashboard no longer lights "Verify" when no fix was tried.

**Result per preset** (agreed before building; each one is tested):

| Preset | Path | Gemini | Result |
|:---|:---|:---:|:---|
| Renamed column ×3, wrong JSON shape | Monitor → Classifier → Recovery → Apply → Verify | 0 (was 1) | Healed |
| Two renamed columns | … → Verify → back to Recovery → Apply → Verify | 0 (was 1) | Healed |
| Broken JSON syntax | Monitor → Classifier → RCA → Recovery → Apply → Verify | 2 | **Healed + 1 row quarantined** (was: human) |
| All device IDs empty | Monitor → Classifier → Recovery → Quarantine | 0 (was 1) | Quarantined |
| Missing column | Monitor → Classifier → RCA → Recovery → Escalate | 2 | Human |
| Missing table | Monitor → Classifier → RCA → Escalate | 1 | Human |
| Healthy ×2, bad email, duplicate IDs | no agents | 0 | Success / Quarantined |

Try all now uses about **5** Gemini calls instead of about 11.

**Dashboard:** skipped steps are drawn grey and the lit path passes over them. A dashed **↺ retry** arc shows the loop. Clicking a skipped circle says why it was skipped (e.g. "a safe rule already knew the fix, so no Gemini call was needed"). The Classifier panel shows the route it chose. The preset hints are updated.

**Files:** `src/orchestration/graph.py` (rewritten), `src/agents/recovery.py`, `src/agents/classifier.py`, `src/services/remediation_service.py` (the loop moved to the graph; `apply_fix` / `finish_healed` / `finish_quarantined`), `src/incidents/incident_manager.py`, `src/pipeline/json_pipeline.py`, `src/api/main.py`, `frontend/src/lib/flow.ts`, `frontend/src/lib/agentDetail.ts`, `frontend/src/components/FlowGraph.tsx`, `frontend/src/components/MissionControl.tsx`, `frontend/src/presets.ts`, `test/test_agent_paths.py` (new), `test/test_live.py`, `test/test_crash_escalates.py`, `test/test_report.py`, `test/test_frontend_react.py`

---

## 24. "Root cause: Not found yet" explained

**Purpose:** Since section 23, RCA is skipped on purpose when a safe rule already knows the fix. The dashboard still said "Root cause: Not found yet.", as if Gemini were still working.

- Healed without RCA: "Not needed: a safe rule already knew the fix, so Gemini wasn't asked."
- Escalated without RCA (e.g. heal-attempt cap): "Not analysed: the incident was handed to a person before RCA ran."
- "Not found yet." now only shows while an incident is still running.

**Files:** `frontend/src/lib/incident.ts` (`rootCauseText`), `frontend/src/components/IncidentCard.tsx`, `frontend/src/components/WhatChanged.tsx`, `test/test_frontend_react.py`

---

## 25. Tests

**Purpose:** Prove each bug existed, prove it's fixed, and stop it coming back.

- **239 tests** in total, all passing: `venv\Scripts\python -m pytest test`
- Each new test was first run on the old code and failed. It passes now.
- Gemini is replaced by a fake model, so the tests are free, need no internet, and give the same result every time.
- Tests use a temporary database (`EHA_DB_DIR`), so your real data is never touched.

| File | What it checks |
|:---|:---|
| `test/test_healing_e2e.py` | Full runs through the API and agents: real rows saved, no fake SQL, no nested loops, guard catches a disobedient Gemini and the `* 0` trick, real renames still heal with correct numbers |
| `test/test_sql_guard.py` | The SQL guard's rules, one test per rule, including the rename-only rule and its known false alarm |
| `test/test_security.py` | Security fixes: path trick, upload limit, JSON repair check, Gemini warning, SQL word list, and a **real browser test** that the script-injection hole is closed (skipped if Playwright is not installed) |
| `test/test_auth.py` | Login and permissions: no/wrong key refused, viewer vs admin, smaller viewer upload limit, run limit, open locally and locked on Vercel when no keys are set, plus a **real browser test** of the login box |
| `test/test_frontend_react.py` | The React dashboard in a real browser: login, viewer vs admin, a drift that heals, cheating SQL shown as blocked, no script injection, no sideways scroll on phones, no focus box around the page after a tab switch, clicking agent circles, PDF/JSON download, Try all, escalations in plain words, Records "Show more", incident filters, Try all on a phone, Clear history in Open mode (skipped if not built or no Playwright) |
| `test/test_live.py` | With a slow fake Gemini: the API keeps answering during a run, two runs at once take turns safely, and the dashboard lights up steps (and pulses the RCA step) while the run is still going, with Replay hidden until it ends |
| `test/test_paging.py` | Incident paging: page size, offset, total header, limits, the classifier count, and "Show more" in the browser |
| `test/test_report.py` | Report download: JSON content, real PDF, odd characters, 404/422, login rules |
| `test/test_escalation_text.py` | Escalations in plain words: missing column/table, broken JSON, guard block and UNRESOLVABLE reasons, raw error kept, tests use their own escalation file |
| `test/test_records_paging.py` | Quarantine and escalations paging: page size, offset, total, newest first, only the page is explained, bad values refused |
| `test/test_incident_filters.py` | Incident filters: outcome, pipeline, search, combined with paging, `%`/`_` escaped, bad values refused |
| `test/test_lockout_and_shared_limits.py` | Wrong-key lockout, and two real server copies on one database sharing the run limit, lockout and Try all tokens (also after a restart) |
| `test/test_escalations_from_db.py` | Escalations come from the incidents table: same list as the Incident log, no file written, shared by server copies |
| `test/test_crash_escalates.py` | A crash during healing (missing package, broken actuator) escalates the incident instead of leaving it OPEN; finished incidents are left alone; a Gemini outage is handled by the agents |
| `test/test_agent_paths.py` | Each of the 13 presets: exact path through the graph (with the retry loop), Gemini calls, final result; Recovery prompts include RCA; missing field in some rows vs every row |
| `test/test_blocked_filter.py` | "Blocked by guard" saved in the database, old incidents marked once, filter and badge count from the server |
| `test/test_measure.py` | The measure script: expected table, README block written between the markers, failed Gemini calls flagged, Windows file lock handled |
| `test/test_per_user_data.py` | Two viewers never see each other's incidents, audit trails, reports, quarantined rows, escalations or counters; the admin sees all; Clear only removes your own runs |
| `test/test_demo_snapshot.py` | Demo recording: only for signed-in users, reports download, never mixes with your runs, the recording script refuses a broken run |
| `test/test_vercel_config.py` | Vercel uses the FastAPI preset, builds the React app, and there is no `public/` folder to hide it |
| `test/test_recovery.py` | Existing tests for column matching and fix validation |

**Files:** `test/conftest.py` (shared test setup), `test/test_healing_e2e.py`, `test/test_sql_guard.py`, `test/test_security.py`, `test/test_auth.py` (new), `src/config.py` (`EHA_DB_DIR` option)

---

## 26. "Show only blocked fixes" asks the server

**Purpose:** The button only looked at the 20 incidents already on screen, so an older blocked fix was missed and the badge count was wrong.

- Each incident now has a `blocked_by_guard` flag in the database, set when the SQL guard, the JSON repair check or an UNRESOLVABLE answer blocks an AI fix.
- Old incidents are marked once, the first time the app starts, by searching their audit log for the same sentences.
- The filter (`?blocked=true`) and the badge count both come from the server.

**Files:** `src/incidents/guard_messages.py` (new), `database/init_db.py`, `src/incidents/incident_repository.py`, `src/models/schemas.py`, `src/api/main.py`, `frontend/src/api.ts`, `frontend/src/components/Dashboard.tsx`, `test/test_blocked_filter.py` (new)

---

## 27. PDF report prints every language

**Purpose:** The PDF used built-in fonts that only know basic Latin, so names like "José" or "Москва" printed as "?".

- The report now embeds Noto Sans and Noto Sans Telugu (free fonts, licence in `src/assets/fonts/OFL.txt`, about 1.6 MB).
- `uharfbuzz` joins Indian-script letters correctly (Telugu, Hindi). It is in `requirements.txt`.

**Files:** `src/services/report_service.py`, `src/assets/fonts/` (new), `requirements.txt`, `pyproject.toml`, `test/test_report.py`

---

## 28. Measured results, written by a script

**Purpose:** The README numbers were typed by hand and could go out of date.

- `scripts/measure.py` starts a local server with a throw-away database, runs all 13 presets with real Gemini, and checks each one's result, path, Gemini calls and time against the design.
- It writes the table into the README by itself (between the `MEASURED-RESULTS` markers) and saves the raw numbers in `scripts/measure_last_run.json`.
- If a Gemini call fails, it says so on that row, so a bad run can't pass as a real measurement.
- On Windows the stopped server can keep its temporary database locked for a moment; the script now retries and never crashes before writing the README.

**Files:** `scripts/measure.py` (new), `scripts/measure_last_run.json` (new), `README.md`, `test/test_measure.py` (new)

---

## 29. New Gemini model

**Purpose:** Google stopped offering `gemini-2.5-flash` to new API keys (404 "no longer available"), so every Gemini call failed.

- The model is now `gemini-3.5-flash-lite`. It was picked over `gemini-3.8-flash` because its free tier is reported to allow far more requests (15 per minute and 500 per day, against 5 and 20 for the other 3.x Flash models).
- The new model returns its reply as a list of blocks, not plain text. `llm_text()` reads both shapes; Recovery (column and SQL) and the JSON repair use it.
- Measured with the new model: 13/13 presets as expected, 5 Gemini calls in total.

**Files:** `configs/settings.yaml`, `src/config.py`, `src/agents/recovery.py`, `src/services/remediation_service.py`, `README.md`, `test/test_agent_paths.py`

---

## 30. Vercel serves the React app

**Purpose:** Vercel still served the old HTML dashboard (`public/`).

- `vercel.json` now uses Vercel's **FastAPI** preset (the old `builds` list is gone). The deploy builds the React app (`cd frontend && npm ci && npm run build`) and FastAPI serves `frontend/dist`, locally and on Vercel.
- The old HTML dashboard (`public/`) is **removed**: Vercel serves a `public/` folder before the app, so it would have hidden the React app. Its two tests now check the React app instead.
- Checked with `vercel build` locally: the build succeeds, the bundle is about 0.5 MB, and `/` returns the React app.

**Files:** `vercel.json`, `pyproject.toml`, `src/api/main.py`, `public/` (deleted), `test/test_security.py`, `test/test_vercel_config.py` (new)

---

## 31. Sign in with Google, and each viewer sees only their own runs

**Purpose:** Everyone shared one viewer key and saw everyone's runs. Now each person signs in with their own Google account and has their own space.

- **Sign in:** a "Sign in with Google" button. The server checks Google's token (right app, not expired, email verified), then sets its own login cookie: signed, HttpOnly, 7 days. No passwords are stored.
- **Admin** = the Google emails in `EHA_ADMIN_EMAILS`. Everyone else is a viewer. The viewer key is gone; `EHA_ADMIN_KEY` stays for scripts only.
- **Per-person data:** every run is tagged with its owner (`run_owners` table). Viewers see only their own incidents, audit trails, reports, quarantined rows, escalations and counters, filtered on the server; someone else's incident answers `404`. The admin sees all.
- **Clear history:** viewers clear only their own runs ("Clear my history"); the admin clears everything.
- **Limits:** the viewer run limit counts per person, not per address.
- **CSRF:** requests that change something must send `X-Requested-With: eha`, which another site can't add.
- **Stored about people:** only Google id, email and name (`users` table).
- **New settings:** `GOOGLE_CLIENT_ID`, `EHA_ADMIN_EMAILS`, `EHA_SESSION_SECRET` (all three needed on Vercel). Nothing set locally = open mode, as before.

**Files:** `src/api/auth.py` (rewritten), `src/api/ownership.py` (new), `src/api/main.py`, `database/init_db.py`, `src/incidents/incident_repository.py`, `src/services/quarantine_service.py`, `.env.example`, `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/LoginCard.tsx`, `Header.tsx`, `Dashboard.tsx`, `IncidentTimeline.tsx`, `test/conftest.py`, `test/test_auth.py` (rewritten), `test/test_per_user_data.py` (new), `test/test_lockout_and_shared_limits.py`, `test/test_report.py`, `test/test_live.py`, `test/test_frontend_react.py`

---

## 32. Demo tab

**Purpose:** Let an interviewer see the app after all 13 test cases without running anything.

- A **Demo** tab for signed-in users: the same incident log, agent flow, reports and records, from a frozen recording. Read-only: nothing can be run or cleared, and it never mixes with anyone's own runs.
- The recording (`demo/snapshot.json`) is made with real Gemini by `python scripts/make_demo_snapshot.py`. If any preset doesn't behave as designed (e.g. the quota ran out), nothing is written.
- The frontend reads live data or the recording through one data source (`frontend/src/data.ts`), so both look exactly the same.

**Files:** `scripts/make_demo_snapshot.py` (new), `demo/snapshot.json` (made by the script), `src/api/main.py`, `frontend/src/data.ts` (new), `frontend/src/components/DemoPage.tsx` (new), `Dashboard.tsx`, `RecordsTabs.tsx`, `DownloadReport.tsx`, `frontend/src/hooks/useRoute.ts`, `test/test_demo_snapshot.py` (new)

---

## 33. Privacy page

**Purpose:** Google only lets a sign-in app be published (so anyone can sign in) once it has a public privacy policy link.

- New page at `/privacy.html`: what is stored (Google id, email, name, your test runs, one login cookie), what is shared (Gemini for broken files, Vercel and Postgres for hosting), and how to delete it.
- Linked from the footer of the sign-in page and the app. It needs no sign-in.

**Files:** `frontend/public/privacy.html` (new), `frontend/src/App.tsx`, `frontend/src/components/LoginCard.tsx`, `test/test_vercel_config.py`

---

## 34. AI fix with a dry run, and a branching flow diagram

**Purpose:** Before, a new kind of error went straight to a person, even when Gemini could have fixed it. Now Gemini gets one chance, but its fix is tested on a copy first, so it can never damage the data.

- **When:** a Pipeline A file, when no rule knows the fix or a rule's fix failed at Verify. Only once per incident. Never for a missing file or table, and never after a guard already blocked a fix.
- **What Gemini may do:** propose up to 5 steps from a fixed list: `unwrap`, `flatten_root`, `unnest`, `rename_field`, `lowercase_keys`, `trim_values`. No code, no SQL, no value edits.
- **Dry run on a copy:** the steps must pass every check: same number of records, no value added, changed or removed, the pipeline's schema and row checks pass, at most a quarter of rows held back.
- **Then:** passed → the fixed file is used for a real run and Verify checks it as usual. Failed → nothing is changed, the incident shows **Blocked by guard** and goes to a person with Gemini's proposal and the reason.
- **New preset:** "Records wrapped in an object" (`case_wrapped_records.json`). The flatten rule fails, the AI fix unwraps the list, 10 rows saved, 1 Gemini call.
- **Flow diagram:** now drawn as the real branching graph (Recovery, RCA, AI fix, Verify, Healed, Escalate). Only the arrows this incident took light up. The incident panel's step chips follow the same path.
- Small fix: a failed verification run no longer writes junk rows to the quarantine table when no row was valid.

**Files:** `src/agents/ai_fix.py` (new), `src/agents/recovery.py`, `src/agents/classifier.py`, `src/orchestration/graph.py`, `src/pipeline/json_pipeline.py`, `src/services/remediation_service.py`, `src/incidents/escalation_text.py`, `src/incidents/guard_messages.py`, `test_data/pipeline_A/case_wrapped_records.json` (new), `scripts/measure.py`, `scripts/make_demo_snapshot.py`, `frontend/src/lib/flow.ts`, `frontend/src/components/FlowGraph.tsx`, `FlowStrip.tsx`, `MissionControl.tsx`, `DemoLoop.tsx`, `frontend/src/lib/agentDetail.ts`, `frontend/src/lib/incident.ts`, `frontend/src/presets.ts`, `test/test_ai_fix.py` (new), `test_agent_paths.py`, `test_measure.py`, `test_auth.py`, `test_demo_snapshot.py`, `test_frontend_react.py`

---

## Results before and after

Same 13 test files, same fake Gemini (before section 34 added the 14th):

| Test file | Before | After |
|:---|:---|:---|
| `case_drift_multi.json` | "Healed", **0 rows** saved | Healed, **10 rows** saved |
| `sql_error.db` | "Healed" with **fake error rate 0** | Escalated, nothing written |
| `referential_integrity.db` | "Healed", 0 rows | Quarantined, 0 rows |
| `case_malformed_syntax.json` | "Healed" (Gemini invented a missing country) | Escalated, nothing written |
| Other 9 files | unchanged | unchanged |

---

## Still open

- **To do once:** create the Google sign-in client id, set the three new settings on Vercel, connect Postgres, and record the demo (`scripts/make_demo_snapshot.py`). See the README.
