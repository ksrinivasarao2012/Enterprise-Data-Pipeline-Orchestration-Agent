# Enterprise Data Pipeline Orchestration & Agentic Self-Healing Platform

[![GitHub Repository](https://img.shields.io/badge/GitHub-Repository-blue?logo=github)](https://github.com/KSrinivasarao2012/Enterprise-Data-Pipeline-Orchestration-Agent)
[![Deployment Status](https://img.shields.io/badge/Vercel-Deployed%20Live-brightgreen?logo=vercel)](https://enterprise-data-pipeline-orchestrat.vercel.app/)

An advanced, production-grade SRE (Site Reliability Engineering) automation platform that orchestrates, monitors, and **automatically heals** data pipelines when they encounter structural drifts, operational failures, or query syntax anomalies — using a multi-agent cooperative framework powered by **LangGraph** and **Google Gemini**.

> 🚀 **Live Cloud Deployment**: The platform is deployed live on Vercel at **[https://enterprise-data-pipeline-orchestrat.vercel.app/](https://enterprise-data-pipeline-orchestrat.vercel.app/)**

---

## Key Features

- **Dual Data Pipelines** — JSON batch ingestion (Pipeline A) and database-to-database ETL (Pipeline B) with full telemetry instrumentation.
- **Branching Agent Graph** — A LangGraph graph (Monitor → Classifier → RCA → Recovery → Apply → Verify → Escalate) that makes real decisions: safe rules first, Gemini RCA only when no rule knows the fix (or to explain a failure to a person), Recovery reads the RCA analysis, and Verify loops back to Recovery when it finds another problem.
- **Iterative Schema Drift Recovery** — Heals compounding multi-column drifts (e.g. `customer_id` → `id` **and** `email` → `user_email`) inside a single run: each verification run that reveals another drift resolves it and retries, up to 4 attempts.
- **Honest Healing Status** — A run is only marked `HEALED` when a verification run actually saves rows. Partial fixes, 0-row runs and unresolvable errors are escalated instead of being reported as successes. If the healing code itself crashes, the incident is escalated too, never left open.
- **SQL Guard** — Every SQL query repaired by Gemini is checked with `sqlglot` before it runs. Only a pure column **rename** to an approved name is allowed; fake constants, formula tricks (e.g. `* 0`), wrong columns, other tables and non-`SELECT` statements are rejected and escalated.
- **LLM-Powered File Repair** — Gemini repairs malformed JSON syntax and the pipeline retries. The repair is only accepted if every key and value is unchanged (only brackets and commas may differ); otherwise it is escalated.
- **Security Hardening** — Sign in with Google; every viewer sees only their own runs (the admin sees all), only real test file names are accepted, uploads are capped at 5 MB, all dashboard data is shown as plain text (no script injection), and repaired SQL/JSON is checked before use. See [Security](#security).
- **Hybrid Fault-Tolerance & Quarantine** — Row-level anomalies (bad email formats, duplicate IDs, null device IDs) are isolated per-row into a dedicated `quarantined_records` table. Healthy records are always ingested, and invalid records are logged for operator review — without crashing the pipeline.
- **Pydantic v2 Validation** — All ingested customer records are validated via a typed `CustomerRecord` Pydantic model. Custom field validators handle type coercions (e.g., `5` → `"5"`), null checks, email format rules, and string normalizations before any database write.
- **Dual-Database Architecture** — Runs locally on SQLite, and automatically switches to **Neon Postgres** when deployed to Vercel — zero code changes required.
- **React Dashboard** — A React + Vite + TypeScript dashboard (`frontend/`) with light and dark themes, **Sign in with Google**, a read-only **Demo** tab (the app after all the test cases, recorded with real Gemini), a **live, branching** agent flow (it lights up only the path this incident took, as each agent finishes), an incident timeline with "Blocked by guard" badges, and a records view. Click any agent circle to see what it did in that run (e.g. Gemini's full root-cause text), download any incident as a **PDF or JSON report**, press **Try all** to run all 15 presets one after another (counts as one run for the viewer limit), read every escalation in plain words (what went wrong, why a person is needed), and search or filter the incident log by pipeline and outcome. Works on phones. Vercel builds and serves it too.
- **Vercel-Native Deployment** — Fully serverless deployment on Vercel's free tier with in-process healing (no background task limitations).

---

## System Architecture

The platform is split into three core logical tiers: the **Data Ingestion Layer**, the **Control Plane Telemetry Receiver**, and the **Agentic Remediation Loop**.

```
                       +------------------------------------------+
                       |   Web Operations Dashboard (HTML/JS/CSS) |
                       |   Served directly via FastAPI Web Server  |
                       +--------------------+---------------------+
                                            |
                                            v
+-----------------------+       +-----------+-----------+       +-----------------------+
| Ingestion Pipeline A  | ----> | Operational DB        |       | Pipeline B (DB-to-DB) |
| (Customer JSON Files) |       | SQLite / Neon Postgres|       | (Upload Local DB File)|
+-----------+-----------+       +-----------------------+       +-----------+-----------+
            |                                                               |
            | Sends Telemetry                                               | Sends Telemetry
            v                                                               v
+-----------+---------------------------------------------------------------+-----------+
|                    Control Plane Telemetry Gateway (FastAPI)                          |
|                Local: localhost:8000  |  Cloud: Vercel Serverless                    |
+-------------------------------------------+-------------------------------------------+
                                            | In-Process Trigger
                                            v
+-------------------------------------------+-------------------------------------------+
|                    Branching Agent Graph (LangGraph)                                  |
|  Monitor -> Classifier --rule knows fix-----------------> Recovery                    |
|                        --no rule---> RCA (Gemini) ------> Recovery (reads RCA)        |
|                        --no fix----> RCA (Gemini) ------> Escalate (with explanation) |
|  Recovery --escalate--> Escalate                                                      |
|  Recovery --fix-------> Apply -> Verify --rows saved------------> HEALED              |
|                                         --another drift, tries--> back to Recovery    |
|                                         --anything else---------> Escalate            |
+---------------------------------------------------------------------------------------+
```

### 1. Data Plane Pipelines

#### Pipeline A — Batch Customer Ingestor
- **Source**: Customer records in JSON format uploaded dynamically via the UI sandbox.
  - **Input Schema**: `customer_id`, `name`, `email`, `country`
- **Destination**: Saves parsed data to the `customers` table.
  - **Output Schema**: `customer_id` (PK), `name`, `email` (Unique), `country`, `signup_date`, `status` (Default: `ACTIVE`), `total_orders` (Default: `0`)
- **Self-Healing Scope**: Resilient against multi-column schema drifts (e.g., `country` → `nation`, `email` → `user_email`), malformed JSON syntax, and invalid root data structures. Compounding drifts are healed within the same run: the actuator collects one mapping per drift and re-runs the pipeline with all of them until the data saves (max 4 attempts).

#### Pipeline B — Database ETL Engine
- **Source**: Flat telemetry logs table (`device_telemetry`) from an uploaded SQLite `.db` file.
  - **Table Structure**: `log_id` (PK), `device_id`, `timestamp`, `status_code`, `response_time`
- **Destination**: Saves aggregated system summaries to `system_performance_metrics` table.
  - **Output Schema**: `device_id` (PK), `total_events`, `error_rate`, `avg_response_time`
- **Self-Healing Scope**: Gemini rewrites the failing metrics query to fix SQL syntax errors or map a renamed column to its existing equivalent; the rewrite is then verified by re-running the pipeline. If a column the query needs has **no** equivalent (e.g. `status_code` is missing from `sql_error.db`), the model must answer `UNRESOLVABLE` and the incident is escalated. The platform never substitutes constants such as `0.0` or `NULL`, which would silently produce fake metrics. As a second safety net, the **SQL guard** (`src/agents/sql_guard.py`) checks Gemini's query before it runs and rejects it if it is not a single `SELECT` on the same table, uses columns that do not exist, changes the outputs, or replaces a missing column with a constant. It also enforces **rename only**: Gemini's query must be the original query with the missing column swapped for a name from `sql_column_aliases` in `configs/settings.yaml`, and nothing else changed. This blocks tricks such as `http_status * 0` and real-but-wrong columns such as `timestamp`.

### 2. Control Plane (Telemetry & Metadata Server)

- **Technology**: FastAPI (runs locally on port `8000` or as Vercel Serverless Functions)
- **Database Tier** (`platform_state.db` locally / Neon Postgres on Vercel):
  - `pipeline_runs` — Logs start/end timestamps and execution status (`RUNNING`, `SUCCESS`, `FAILED`, `HEALED`, `QUARANTINED`). `HEALED` means a remediation ran **and** rows were saved.
  - `incidents` — Stores telemetry exceptions, error classifications, severity levels, root-cause analyses, and resolution histories.
  - `pipeline_configs` — Maintains active versioned configuration maps (JSON mappings and SQL overrides) to support hot-swapping schemas.
  - `audit_logs` — A secure chronological trace of every telemetry alert and remediation action.

### 3. Agentic Remediation Engine (LangGraph Core)

When a pipeline throws an exception, the telemetry interceptor captures the error and runs the agent graph (`src/orchestration/graph.py`). The graph branches, so each incident only takes the steps it needs:

1. **Monitor Agent**: opens the investigation (`INVESTIGATING`).
2. **Classifier Agent**: sets the severity (`P0`–`P3`) and **chooses the route** (it writes it in the audit log, e.g. "Route: Recovery (no Gemini needed), because a known column alias matches"):
   - **a safe rule knows the fix** (renamed column with a known alias, object instead of list, every row bad, temporary failure) → straight to Recovery, **no Gemini call**;
   - **no rule fits** (SQL needs rewriting, broken JSON syntax, unknown column name) → RCA first;
   - **a new kind of error on a Pipeline A file** → RCA, then an **AI fix with a dry run** (see below);
   - **no automatic fix exists** (e.g. the source file or table is missing) → RCA explains it, then Escalate.
3. **RCA Agent** (Gemini): writes the root-cause analysis. It runs only on the last three routes.
4. **Recovery Agent**: chooses the fix. Every Gemini prompt it sends (SQL rewrite, column mapping, JSON repair) **includes the RCA analysis**. Guards still check every AI fix (SQL guard, JSON repair check).
5. **Apply fix → Verify**: the fix is applied and the pipeline re-run for real:
   - **rows saved** → configuration committed, run `HEALED`, incident `RESOLVED`;
   - **another column is missing** (e.g. two columns were renamed) → the graph **loops back to Recovery**, which adds the next fix; up to `MAX_HEAL_ATTEMPTS = 4`. The dashboard draws this as a retry arc;
   - **the re-run still fails** on a Pipeline A file → one **AI fix with a dry run** (below);
   - **anything else** → Escalate.
6. **AI fix with a dry run** (Pipeline A only, at most once per incident): when no rule knows the fix, or a rule's fix failed, Gemini proposes **one** fix as a short list of steps from a fixed set (`unwrap`, `flatten_root`, `unnest`, `rename_field`, `lowercase_keys`, `trim_values`; at most 5). It never writes code or SQL and never edits values. The steps run on a **copy** first, which must pass every check: same number of records, no value added, changed or removed, the pipeline's schema and row checks pass, and at most a quarter of the rows held back. Only then is the fixed file used for a real run, which Verify checks as usual. If any check fails, nothing is changed, the incident is shown as **Blocked by guard** and goes to a person with Gemini's proposal and the reason. Never tried for a missing file or table, or after a guard already blocked a fix.
7. **Escalate**: the incident is handed to a person, with a plain-English reason.

| Preset | Path | Gemini calls | Result |
|:---|:---|:---:|:---|
| Renamed column (country / email / id), wrong JSON shape | Monitor → Classifier → Recovery → Apply → Verify | 0 | Healed |
| Two renamed columns | … → Verify → **back to Recovery** → Apply → Verify | 0 | Healed |
| Broken JSON + wrapped records | Monitor → Classifier → RCA → Recovery (Gemini adds the missing `}`) → Verify fails (still wrapped) → **AI fix** (unwrap `customers`, dry run passes) → Apply → Verify | 3 | Healed |
| Records wrapped in an object | … → Recovery (flatten rule) → Verify fails → **AI fix** (Gemini: unwrap `customers`, dry run passes) → Apply → Verify | 1 | Healed |
| Broken JSON syntax | Monitor → Classifier → RCA → Recovery → Apply (Gemini adds only the missing `},`) → Verify | 2 | Healed: 9 rows loaded, the row with no country quarantined |
| All device IDs empty | Monitor → Classifier → Recovery → Quarantine | 0 | Quarantined |
| Missing column | Monitor → Classifier → RCA → Recovery (SQL guard blocks) → Escalate | 2 | Human |
| Missing table | Monitor → Classifier → RCA → Escalate | 1 | Human |
| Healthy / bad email / duplicate IDs | no agents (validation only) | 0 | Success / Quarantined |

**Loop safety**: verification runs raise their errors back to the graph (`throw_on_error=True`) instead of opening new incidents, so healing can never start another healing loop inside itself. Once a single run has raised more than 4 incidents, the workflow escalates before any agent runs. If the healing code itself crashes, the incident is escalated too.

### 4. Incident Classification & Severity Routing

#### Incident Categories
| Category | Description |
|:---|:---|
| `SCHEMA_DRIFT` | Structural changes, key errors, or target schema mismatches |
| `DATA_QUALITY` | Row value anomalies, null constraints, or validation failures |
| `NETWORK_TIMEOUT` | Outages or socket timeouts on network requests |
| `API_FAILURE` | Authentication failures, rate limits, or endpoint errors |
| `DATABASE_FAILURE` | Database locking, foreign key constraints, or SQL syntax errors |
| `INFRASTRUCTURE_FAILURE` | Core system-level errors or SQLite connection exceptions |
| `UNKNOWN` | Unclassified fallback exceptions |

#### Severity Tiers
| Tier | Name | Trigger |
|:---|:---|:---|
| **P0** | Critical Outage | Infrastructure failures, auth errors, multiple unresolved incidents |
| **P1** | High | Complete pipeline blocks (schema drifts, database failures) |
| **P2** | Medium | Degraded performance, rate limits, non-blocking network anomalies |
| **P3** | Low | Minor row-level anomalies (data quality quarantine alerts) |

### 5. Hybrid Quarantine & Fault-Tolerance Model

To prevent minor row-level anomalies (like invalid formatting or single duplicate entries) from blocking entire pipeline ingestion runs, the platform implements a hybrid fault-tolerance model.

*   **Granular Quarantine Isolation**: Individual row failures (e.g., email format errors or duplicate IDs in Pipeline A, or NULL device IDs in Pipeline B) are written to a dedicated `quarantined_records` table in the state database.
*   **Partial Success Flow**: Healthy records are successfully loaded into the destination tables, while the run completes under a terminal status of `QUARANTINED` (with warning metrics displayed on the dashboard).
*   **Warning Incident Logging**: Each quarantined run registers a low-priority (`P3`) warning incident categorized as `DATA_QUALITY`. Since valid data was successfully ingested, this incident auto-resolves immediately (`RESOLVED` using `force_override=True` to bypass state machine rules) for clean auditing.
*   **Complete Escalation Guardrails**:
    *   **All Rows Invalid**: If 100% of the ingestion payload is quarantined (resulting in `0` valid records loaded), the pipeline throws a `ValueError`. This registers the run as `FAILED` and raises an open incident.
    *   **Agentic Actuation**: For failed aggregates, the LangGraph Recovery agent is invoked. If it deploys a `QUARANTINE` directive (isolating the bad records), the incident is `RESOLVED` and the run ends as `QUARANTINED`. It is **not** marked `HEALED`, because no valid rows were left to load.
    *   **Infinite Loop Prevention**: If a verification run during schema-drift recovery fails on a column that was **already fixed**, the actuator stops and escalates to a human via `_handle_escalation()`. Verification runs never open nested incidents, and a per-run cap of 4 incidents escalates anything that slips through.

### 6. Pydantic v2 Validation Layer

All customer records ingested via Pipeline A are validated through a typed `CustomerRecord` Pydantic BaseModel before any database write:

| Field | Type | Rule |
|:---|:---|:---|
| `customer_id` | `str` (Required) | Coerced from any type (e.g. `5` → `"5"`), must not be null or empty. Duplicates in batch or DB are quarantined. |
| `email` | `str` (Required) | Must contain `"@"`. Coerced to string. Malformed values are quarantined, not crashed. |
| `name` | `str` (Optional) | Coerced to string, defaults to `""` if missing or null. |
| `country` | `str` (Optional) | Coerced to string, defaults to `""` if missing or null. |

Validation errors are extracted per-row from `ValidationError.errors()`, stripped of Pydantic's `"Value error, "` prefix, and added to the row's quarantine log entry. This ensures clean, human-readable error records in the Quarantine Log dashboard.

---

## Project Structure

```
├── .env                            # Local API keys & secrets (NOT committed to git)
├── .env.example                    # Template showing required environment variables
├── .gitignore                      # Git exclusion rules for secrets, caches, databases
├── .python-version                 # Python version lock (3.12)
├── pyproject.toml                  # Modern Python project manifest (used by uv & Vercel)
├── requirements.txt                # Pip-compatible dependency list
├── vercel.json                     # Vercel: FastAPI preset, builds the React app, function settings
├── run_platform.py                 # Unified local application launcher
├── CHANGES.md                      # What was fixed and why, in simple words
│
├── configs/
│   └── settings.yaml               # Global configs (LLM model, DB paths, column aliases, SQL guard rename list)
│
├── database/
│   ├── init_db.py                  # Database schema initialization (SQLite + Postgres)
│   ├── platform_state.db           # Telemetry records, incident logs, config overrides
│   ├── operational.db              # Raw ingestion transactional database
│   └── analytics.db                # Target warehouse for aggregated metrics
│
├── frontend/                       # React + Vite + TypeScript dashboard (build: npm run build)
│   ├── src/api.ts                  # All API calls, sign-in, response types
│   ├── src/data.ts                 # Live data or the Demo tab's recording, same components
│   ├── src/components/             # Sign-in, Try it, agent flow, incident timeline, records, Demo
│   └── dist/                       # Built app (not committed; FastAPI serves it when present)
│
├── demo/snapshot.json              # Demo tab recording (made by scripts/make_demo_snapshot.py)
├── scripts/
│   ├── measure.py                  # Measures the 15 presets with real Gemini, writes the README table
│   └── make_demo_snapshot.py       # Records the 15 presets for the Demo tab
│
├── src/
│   ├── database.py                 # Dual-database adapter (SQLite ↔ Postgres)
│   ├── config.py                   # Centralized config loader (DB paths, LLM settings)
│   │
│   ├── agents/
│   │   ├── monitor.py              # Monitor Agent — telemetry verification
│   │   ├── classifier.py           # Classifier Agent — severity & category assignment
│   │   ├── rca.py                  # RCA Agent — Gemini-powered root cause analysis
│   │   ├── recovery.py             # Recovery Agent — remediation directive generator
│   │   └── sql_guard.py            # Checks Gemini's repaired SQL before it runs (sqlglot)
│   │
│   ├── api/
│   │   ├── main.py                 # FastAPI gateway (REST endpoints + Vercel entrypoint)
│   │   ├── auth.py                 # Google sign-in, login cookie, roles, run limit, script key lockout
│   │   ├── ownership.py            # Who owns which run (viewers see only their own)
│   │   └── security_store.py       # Stores limits, lockouts and Try all tokens in the database
│   │
│   ├── incidents/
│   │   ├── incident_manager.py     # State transitions and lifecycle control
│   │   ├── escalation_text.py      # Turns raw escalation errors into plain words
│   │   ├── incident_repository.py  # Data access layer for incidents
│   │   └── severity_rules.py       # Deterministic severity & category classification
│   │
│   ├── models/
│   │   └── schemas.py              # Pydantic data models & enums
│   │
│   ├── orchestration/
│   │   └── graph.py                # Branching LangGraph agent graph (routes, verify loop, escalate)
│   │
│   ├── pipeline/
│   │   ├── json_pipeline.py        # Pipeline A implementation
│   │   └── db_pipeline.py          # Pipeline B implementation
│   │
│   ├── services/
│   │   ├── audit_service.py        # Centralized audit logging service
│   │   ├── pipeline_service.py     # Pipeline execution status tracking
│   │   ├── quarantine_service.py   # Quarantine record insert, fetch & clear operations
│   │   ├── remediation_service.py  # Actuator: verification re-runs, multi-drift loop, escalation
│   │   └── report_service.py       # Incident report as JSON or PDF (fpdf2)
│   │
│   └── telemetry/
│       ├── incident_creator.py     # In-process telemetry capture & healing trigger
│       └── logger.py               # Structured logging (structlog) configuration
│
├── test_data/
│   ├── pipeline_A/                 # Test JSON datasets with drift variations
│   │   ├── case_healthy.json
│   │   ├── case_drift_country.json
│   │   ├── case_drift_email.json
│   │   ├── case_drift_id.json
│   │   ├── case_drift_multi.json   # Two drifts at once (id + email)
│   │   ├── case_invalid_root.json
│   │   ├── case_malformed_syntax.json
│   │   ├── case_invalid_email.json
│   │   └── case_duplicate_ids.json
│   └── pipeline_B/                 # Test SQLite databases for various failure modes
│       ├── healthy.db
│       ├── sql_error.db
│       ├── referential_integrity.db
│       └── missing_table.db
│
├── test/
│   ├── conftest.py                 # Temporary test database + shared fake-Gemini fixtures
│   ├── test_recovery.py            # Unit tests: alias matching, fix validation
│   ├── test_sql_guard.py           # Unit tests: one per SQL guard rule
│   ├── test_healing_e2e.py         # End-to-end healing tests (fake LLM, real API + LangGraph)
│   ├── test_security.py            # Security tests (path trick, upload limit, repair checks)
│   ├── test_auth.py                # Google sign-in, login cookie, roles and limits
│   ├── test_per_user_data.py       # Viewers see only their own runs
│   ├── test_paging.py              # Incident paging (limit/offset, X-Total-Count)
│   ├── test_report.py              # Incident report download (JSON / PDF)
│   ├── test_escalation_text.py     # Escalations in plain words
│   ├── test_escalations_from_db.py # Escalations read from the incidents table
│   ├── test_crash_escalates.py     # A crash during healing escalates instead of staying OPEN
│   ├── test_agent_paths.py         # Each preset's path through the graph, Gemini calls and result
│   ├── test_records_paging.py      # Quarantine / escalations paging
│   ├── test_incident_filters.py    # Incident search and filters
│   ├── test_demo_snapshot.py       # Demo tab recording: access, reports, recording script
│   ├── test_vercel_config.py       # Vercel serves the React app
│   └── test_lockout_and_shared_limits.py  # Script-key lockout; limits shared by server copies
│
└── scratch/
    ├── create_test_databases.py    # Script generating SQLite test files
    └── populate_db.py              # Generates sample data for the dashboard
```

---

## Deployment

### Option A: Local Development

#### 1. Prepare Your Environment

We recommend **`uv`** for faster dependency management. Standard `venv` + `pip` also works.

**Using `uv` (Recommended):**
```powershell
uv sync --all-extras
.venv\Scripts\activate.ps1
```

**Using Standard `venv` + `pip`:**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> If execution policies block activation on PowerShell, run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process`

#### 2. Configure Your API Key

The platform uses Google Gemini for intelligent root cause analysis and schema recovery.

1. Copy the example environment file:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and replace the placeholder with your actual API key:
   ```
   GOOGLE_API_KEY=your-actual-google-api-key-here
   ```
   > You can obtain a free API key from [Google AI Studio](https://aistudio.google.com/apikey).

The LLM model and temperature are configured centrally in `configs/settings.yaml`:
```yaml
llm:
  model_name: "gemini-3.5-flash-lite"
  temperature: 0.0
```

#### 3. Initialize the Database & Generate Test Data
```powershell
# Create local SQLite files and seed with default values
venv\Scripts\python -m database.init_db

# Generate Pipeline B test databases
venv\Scripts\python.exe scratch/create_test_databases.py
```

#### 4. Build the React Dashboard (once, and after frontend changes)

Needs [Node.js](https://nodejs.org) 18 or newer.
```powershell
cd frontend
npm install
npm run build
cd ..
```
This creates `frontend/dist/`, which FastAPI serves at `http://localhost:8000`.

For live editing of the UI, run `npm run dev` inside `frontend/` while the backend is running; Vite opens the app at `http://localhost:5173` and forwards `/api` to port 8000.

#### 5. Launch the Platform
```powershell
venv\Scripts\python.exe run_platform.py
```
- **FastAPI Control Plane**: Runs on port `8000`; logs are shown in the terminal. Stop with `Ctrl+C`.
- **Web Dashboard**: The launcher prints `http://localhost:8000` and opens it in your browser (add `--no-browser` to skip). FastAPI serves the React app from `frontend/dist/`

> **Port Conflict**: If port `8000` is in use, the launcher will detect it and display an error. Stop the previous process before relaunching.

---

### Option B: Vercel Cloud Deployment (Production)

> 🚀 **Live Deployment Status**: The platform is deployed and live at **[https://enterprise-data-pipeline-orchestrat.vercel.app/](https://enterprise-data-pipeline-orchestrat.vercel.app/)**.

The platform is fully deployable to **Vercel's free tier** with zero configuration changes. The dual-database adapter (`src/database.py`) automatically detects the environment and switches between SQLite (local) and Neon Postgres (Vercel).


#### 1. Push to GitHub

Ensure your repository is pushed to GitHub:
```powershell
git add .
git commit -m "Initial commit"
git push origin main
```

#### 2. Import Project on Vercel

1. Go to [vercel.com](https://vercel.com) and sign in with your GitHub account.
2. Click **Add New Project** → Import your GitHub repository.
3. Vercel reads `vercel.json`: the **FastAPI** preset, a build step that builds the React app
   (`cd frontend && npm ci && npm run build`), and the function settings. FastAPI then serves the app.
4. Add these **Environment Variables** in the Vercel project settings:
   ```
   GOOGLE_API_KEY     = your Gemini API key
   GOOGLE_CLIENT_ID   = your Google sign-in client id (see "Setting up Google sign-in")
   EHA_ADMIN_EMAILS   = your-google-email@gmail.com
   EHA_SESSION_SECRET = a long random string
   ```
5. Click **Deploy**.

#### 3. Connect Neon Postgres Storage

Since Vercel serverless functions are stateless, SQLite databases are lost between invocations. Neon Postgres provides persistent storage on the free tier.

1. In your Vercel project dashboard, navigate to the **Storage** tab.
2. Click **Create Database** → Select **Neon Postgres** → Choose the **Free** plan.
3. Vercel will automatically inject the `POSTGRES_URL` environment variable into your project.
4. Redeploy your project (or push a new commit) to activate the database connection.

#### 4. Query Your Live Data

To inspect data in your production database:
1. Go to **Storage** → Click on your Neon database instance.
2. In the left sidebar, click **Query** (under DATABASE).
3. Toggle off **Read-Only** mode to enable writes.
4. Run SQL queries:
   ```sql
   SELECT * FROM customers;
   SELECT * FROM incidents;
   SELECT * FROM audit_logs ORDER BY timestamp DESC;
   ```

#### How the Dual-Database Adapter Works

The `src/database.py` module provides a transparent adapter layer:

| Feature | Local (SQLite) | Cloud (Neon Postgres) |
|:---|:---|:---|
| Connection | `sqlite3.connect(path)` | `pg8000.connect(...)` with SSL |
| Detection | No `POSTGRES_URL` env var | `POSTGRES_URL` present |
| Placeholders | `?` | Auto-translated to `%s` |
| Upserts | `INSERT OR REPLACE` | Auto-translated to `ON CONFLICT ... DO UPDATE` |
| Schema Init | `database/init_db.py` creates tables | Same script, Postgres-compatible DDL |

No application code changes are required. The adapter wraps `pg8000` connections with a SQLite-compatible API (`PostgresConnectionWrapper`) so all service modules work identically in both environments.

---

## Testing Scenarios

Open the dashboard and run these testing scenarios:

### Scenario 1: Basic Ingestion & Auto-Remediation (Pipeline A)
1. In the **Pipeline Ingestion Sandbox**, select `case_drift_country.json` (where `country` was renamed to `nation`).
2. Click **Execute Ingestion Pipeline A**.
3. The pipeline will crash, register an incident, and trigger the self-healing loop.
4. Within seconds, the dashboard will display the incident shifting from `OPEN` → `INVESTIGATING` → `RCA_COMPLETED` → `RESOLVED`.
5. The API will return an auto-healed success response to the UI.

### Scenario 2: Iterative Multi-Step Healing (Pipeline A)
1. Upload `test_data/pipeline_A/case_drift_multi.json` (contains *both* ID column drift `customer_id` → `id` and Email column drift `email` → `user_email`).
2. Click **Execute Ingestion Pipeline A**.
3. The engine maps `id` first. The verification run then fails on the missing `email`, so the actuator maps `user_email` and re-runs with both fixes.
4. The second verification run saves all 10 rows. One incident is `RESOLVED`, the run is `HEALED`, and the audit trail shows both fixes.

### Scenario 3: Database ETL Sandbox (Pipeline B)
1. Under **Database ETL Sandbox**, select `healthy.db`.
2. Click **Execute Ingestion Pipeline B** — the run succeeds, showing the synced records count.
3. Now select `sql_error.db` (where the `status_code` column is missing).
4. Execute. The telemetry captures the query parse failure and files a database priority incident.
5. The LangGraph loop asks Gemini to repair the query. Because `status_code` has no equivalent column, the correct outcome is `UNRESOLVABLE` → incident `ESCALATED`, and nothing is written to the analytics table.

---

## Expected Outcomes

### Pipeline A: Test File Outcomes

| Test JSON File | Expected Behavior | Explanation | Healing Outcome |
|:---|:---|:---|:---|
| `case_healthy.json` | **Success** | Matches baseline schema exactly. | All 10 records written to `customers` table. |
| `case_drift_country.json` | **Failure → Self-Healed** | Key `country` renamed to `nation`, triggers `KeyError`. | Deterministic alias matcher maps `country_col` → `nation`, run `HEALED`, 10 rows saved. |
| `case_drift_email.json` | **Failure → Self-Healed** | Key `email` renamed to `user_email`, triggers `KeyError`. | Deterministic alias matcher maps `email_col` → `user_email`, run `HEALED`, 10 rows saved. |
| `case_drift_id.json` | **Failure → Self-Healed** | Primary key `customer_id` renamed to `id`, triggers `KeyError`. | Deterministic alias matcher maps `id` → `customer_id`, run `HEALED`, 10 rows saved. |
| `case_drift_multi.json` | **Failure → Self-Healed** | Two drifts: `customer_id` → `id` **and** `email` → `user_email`. | Both mappings resolved in one run (two verification runs), run `HEALED`, 10 rows saved. *(Upload it; it is not in the dashboard preset list.)* |
| `case_invalid_root.json` | **Failure → Self-Healed** | JSON root is a dictionary instead of a list. | Applies `flatten_root_dict: True` override to extract values as array. |
| `case_broken_and_wrapped.json` | **Failure → Self-Healed (repair, then AI fix)** | One record lacks its closing `}` **and** the records sit inside `{"customers": [...]}`. | RCA explains it; Gemini adds only the `}`. The re-run fails because the records are still wrapped, so one AI fix unwraps them (dry run on a copy first). 10 rows saved, 3 Gemini calls. |
| `case_wrapped_records.json` | **Failure → Self-Healed by an AI fix** | The 10 records sit inside `{"customers": [...]}`. | The flatten rule fails (it gives one wrong record). Verify then asks for an AI fix: Gemini proposes `unwrap 'customers'`, the dry run on a copy passes (10 records, no value changed, schema OK), the real re-run saves 10 rows. If Gemini proposes anything that loses or changes data, it is rejected and the incident is `ESCALATED`. |
| `case_malformed_syntax.json` | **Failure → Healed (1 row quarantined)** | Row 5 is missing its closing `}` **and** its `country` field. | Gemini adds only the missing `},` (the JSON repair check blocks any invented value). The re-run then quarantines row 5 ("Missing field 'country'") and loads the other 9. If Gemini invents a country anyway, the repair is rejected and the incident is `ESCALATED`. |
| `case_invalid_email.json` | **Partial → Quarantined** | One row has invalid email (`john.doe_example.com` missing `@`). | Pydantic validator catches it; bad row isolated to `quarantined_records`, remaining 9 rows ingested successfully. Run status: `QUARANTINED`. No agents run; the dashboard shows "Quarantined, no agents needed" (P3 data-quality warning, not counted as healed). |
| `case_duplicate_ids.json` | **Partial → Quarantined** | One duplicate `customer_id` exists in the batch. | Stateful batch duplicate check catches it; duplicate row quarantined, remaining rows ingested. Run status: `QUARANTINED`. No agents run; shown as "Quarantined, no agents needed". |

> **Note**: Both `case_invalid_email.json` and `case_duplicate_ids.json` previously caused full pipeline failures and escalations. After the Pydantic validation and hybrid quarantine refactoring, these now complete as partial successes — isolating only the bad rows.

### Pipeline B: Test File Outcomes

| Test DB File | Expected Behavior | Explanation |
|:---|:---|:---|
| `healthy.db` | **Success** | Validates table structures and completes aggregation cleanly. |
| `sql_error.db` | **Failure → Escalated** | Missing `status_code` column with no equivalent. Gemini is instructed to answer `UNRESOLVABLE` instead of inventing values; if it ignores that and returns a faked query, the SQL guard rejects it. Either way the incident is escalated and no metrics are written. |
| `referential_integrity.db` | **Failed → Quarantined** | All device IDs are NULL; every row is quarantined. LangGraph `QUARANTINE` directive resolves the incident. Run status: `QUARANTINED` (0 rows loaded, so not `HEALED`). |
| `missing_table.db` | **Failure → Escalated** | Table `device_telemetry` not found; requires manual infrastructure setup. |

---

## Technical Details: Why Specific Cases Work vs. Escalate

### Why `case_invalid_root` Works (Auto-Healed)
- **Structured Error Signature**: The pipeline throws a structured error (`ValueError|root_structure`) containing a parseable `ErrorSignature` pattern.
- **Defined Remediation Pathway**: The Recovery Agent handles `root_structure` by triggering a `RECONFIGURE` action with `flatten_root_dict: True`.
- **System Actuation**: The validator whitelists `flatten_root_dict` as a valid boolean override. The parser converts dictionary values into a standard array using `list(raw_data.values())`.

### Why `case_invalid_email` is Quarantined (Not Escalated)
- **Row-Level Isolation**: The Pydantic `CustomerRecord` validator catches the missing `@` character in the email field and raises a `ValidationError` for that specific row.
- **Partial Success**: Only the invalid row is written to `quarantined_records`. All remaining valid rows are successfully ingested.
- **No Run Crash**: Since valid data was loaded, the run ends with status `QUARANTINED` (not `FAILED`), and a P3 auto-resolved warning incident is logged.
- **When it Escalates**: Only if **all rows** in the batch fail validation does the pipeline raise a `ValueError` (0 valid records), triggering the LangGraph healing loop.

### Why `case_duplicate_ids` is Quarantined (Not Escalated)
- **Stateful Batch Check**: After Pydantic validates the format of the record, a batch-level `seen_ids_in_batch` set detects the duplicate `customer_id`.
- **Selective Quarantine**: The duplicate row is isolated. The original (first-seen) and all other unique rows are ingested successfully.
- **Run Status**: `QUARANTINED` with a P3 auto-resolved warning incident logged.

---

## Automated Tests

```powershell
venv\Scripts\python -m pytest test
```

The suite has **188 tests** and needs no Gemini key or network:

- **Unit tests** (`test_recovery.py`): alias and fuzzy matching, fix-validation whitelist.
- **SQL guard tests** (`test_sql_guard.py`): one test per rule. Rejects constants, `NULL`, dropped outputs, unknown columns, other tables, `DELETE`/`ATTACH`/multiple statements, the `* 0` trick, any other formula change, and real-but-wrong columns; accepts a real rename even with different spacing or capitals.
- **End-to-end tests** (`test_healing_e2e.py`): run the real FastAPI endpoints and LangGraph workflow, with Gemini replaced by a fake model whose answer each test controls. `conftest.py` sets `EHA_DB_DIR` to a temporary folder, so your real `database/` files are never touched. They check that:
  - a multi-drift file is `HEALED` **and** saves 10 rows;
  - an unresolvable drift or an `UNRESOLVABLE` SQL answer is escalated and saves nothing;
  - the SQL-repair prompt no longer asks for substitute constants;
  - a failed SQL fix or file repair creates exactly **one** incident (no nested healing);
  - the per-run cap escalates before any LLM call;
  - an all-rows-quarantined run ends as `QUARANTINED`, not `HEALED`;
  - if Gemini **ignores the prompt** and returns a faked query, the SQL guard escalates it and nothing is written;
  - the `http_status * 0` trick is escalated and nothing is written;
  - a real column rename (`status_code` → `http_status`) still heals, with correct error rates.
- **Security tests** (`test_security.py`): path-trick names are refused before any file is opened; uploads over the limit get `413`; a JSON repair that changes, adds or invents a value is escalated; the Gemini warning is on the dashboard; harmless column names are allowed and anything but one `SELECT` is blocked. One test starts the real server, uploads a file with a hidden script, and opens the dashboard in Chromium to check the script never runs. It needs Playwright and is skipped if Playwright is not installed.

- **Sign-in tests** (`test_auth.py`): signed out gets `401`; a Google sign-in sets a safe cookie (HttpOnly, SameSite); a bad token, an edited or expired cookie and a missing CSRF header are refused; the admin is chosen by email; a viewer gets `403` on the telemetry API, a smaller upload limit and a run limit per person; **Try all** counts as one run and its token can't be reused or faked; the script key still works; nothing set means open locally and locked on Vercel. Google itself is replaced by a fake in tests.
- **Per-person data tests** (`test_per_user_data.py`): two viewers never see each other's incidents, audit trails, reports, quarantined rows, escalations or counters (someone else's incident answers `404`); the admin sees all; a viewer's Clear only removes their own runs.
- **Demo tests** (`test_demo_snapshot.py`, `test_vercel_config.py`): the recording is only for signed-in users, its reports download, it never mixes with your own runs, the recording script refuses a broken run; Vercel uses the FastAPI preset, builds the React app and has no `public/` folder that would hide it.
- **React dashboard tests** (`test_frontend_react.py`): start the real backend with Google sign-in on and a fake Gemini that tries to fake a SQL value, then click through the built app in Chromium: the sign-in page, a viewer's "Clear my history" and sign out, admin-only Clear history (also shown in Open mode), one viewer never seeing another's runs, the read-only **Demo** tab (filters, report download, records, nothing to run), a two-column drift that heals, the branching graph lighting only the AI-fix path, the cheating SQL shown as **Blocked by guard**, an uploaded script shown as text and never run, no sideways scrolling on a 390 px phone screen, no focus box around the whole page after switching tabs, clicking an agent circle shows what it did, the PDF and JSON report buttons, **Try all** without hitting the run limit, escalations in plain words, the Records tabs with **Show more**, the incident filters, and the Try all results staying in view on a phone. Skipped if Playwright is not installed or `frontend/dist` is not built.
- **Lockout and shared-limit tests** (`test_lockout_and_shared_limits.py`): wrong keys lock a visitor out (even the right key is refused until the window passes), a missing key is not counted, a faked `x-forwarded-for` can't dodge it locally; and with **two real server copies on one database** the run limit, lockout and Try all tokens are shared, and survive a restart.
- **Incident filter tests** (`test_incident_filters.py`): filter by outcome (healed, quarantined, escalated, in progress), by pipeline, and by search text, together with paging; `%` and `_` are searched as normal letters; unknown values get `422`.
- **Records paging tests** (`test_records_paging.py`): `/api/quarantine` and `/api/escalations` return one page newest first with the total in `X-Total-Count`; only the escalations on the page get their plain-English text; bad values get `422`.
- **Agent path tests** (`test_agent_paths.py`): for each of the 15 presets, the exact path through the graph (including the retry loop), the number of Gemini calls, and the final result; Recovery's prompts include the RCA analysis; a field missing in some rows quarantines those rows, missing in every row is schema drift.
- **Crash tests** (`test_crash_escalates.py`): if healing crashes (a missing package, a broken actuator), the incident becomes ESCALATED with a "Healing crashed" audit line and a plain-English reason, instead of staying OPEN for ever; an already finished incident is left alone; a Gemini outage is handled by the agents themselves.
- **Escalations-from-database tests** (`test_escalations_from_db.py`): the Escalations tab lists exactly the incidents with status ESCALATED (same as the Incident log's Escalated filter), no text file is written, escalations made without any agent are listed too, Clear history empties the list, and **two server copies on one database** show the same escalations.
- **AI fix tests** (`test_ai_fix.py`): each allowed step reshapes without touching values, steps outside the list are refused, the original data is never changed; the dry run rejects a fix that loses records, drops or changes any value, leaves the schema broken or makes most rows invalid; through the real agents: a failed rule then a good AI fix heals with no junk rows, a rejected fix changes nothing and goes to a person, no usable answer goes to a person, the AI fix runs only once, a missing file never gets one, and a guard block is never followed by one.
- **Escalation text tests** (`test_escalation_text.py`): each escalation gets a plain summary and a "why" (missing column or table, broken JSON, guard block, Gemini refusing to guess); the raw error is kept.
- **Report tests** (`test_report.py`): `GET /api/incidents/{id}/report?format=json|pdf` returns the incident and its audit trail; the PDF is a real PDF even with emoji or curly quotes in Gemini's text; unknown incident `404`, bad format `422`, no key `401`, viewers may download.
- **Paging tests** (`test_paging.py`): `/api/incidents` returns one page (`limit`, `offset`) newest first with the total in `X-Total-Count`; bad values get `422`; the dashboard shows "20 of N" and **Show more** loads 20 more.
- **Live flow tests** (`test_live.py`): with a fake Gemini that takes 4 seconds, the API still answers mid-run (it used to freeze), two runs at once take turns without mixing files, and the dashboard shows the new incident and lights up agent steps while the run is still in progress (Replay stays hidden until the run ends).

See [`CHANGES.md`](CHANGES.md) for what each fix does and why.

---

## Security

| Protection | What it does |
|:---|:---|
| Sign in with Google | Every API request needs a signed-in person. Google's token is checked by the server (right app, not expired, verified email), then a signed, HttpOnly login cookie keeps you signed in for 7 days. No passwords are stored. |
| Per-person data | Every run is tagged with its owner. A **viewer** sees only their own incidents, quarantined rows, escalations, counters and reports, filtered on the server (someone else's incident answers `404`), and can clear only their own history. The **admin** (emails in `EHA_ADMIN_EMAILS`) sees everyone's and can clear all. Viewers also get a 1 MB upload limit and 10 runs per minute, counted per person. |
| CSRF protection | With a login cookie, every request that changes something must carry the `X-Requested-With: eha` header, which another website can't add. |
| Script key | `EHA_ADMIN_KEY` in the `X-Access-Key` header gives admin rights to scripts (e.g. `scripts/measure.py --url`). The dashboard never asks for it. |
| Wrong-key lockout | 10 wrong script keys from one visitor within 15 minutes (`EHA_MAX_FAILED_KEYS`, `EHA_LOCKOUT_MINUTES`) locks that visitor out with `429` until the 15 minutes pass, even if they then send the right key. A missing key is not counted. |
| Shared limits | The viewer run limit, the lockout and the **Try all** tokens are stored in the database, so a restart does not reset them and every server copy on Vercel counts together. |
| Test file allowlist | `test_case` must be the name of a real file in `test_data/pipeline_A` or `pipeline_B`. Paths such as `../../.env` or `/etc/passwd` are refused before anything is opened. |
| Upload size limit | Uploads over 5 MB (`EHA_MAX_UPLOAD_MB`) are refused with `413` before they are read. |
| Plain-text dashboard | React shows every value from the server or an uploaded file as text, so hidden scripts never run (checked in a real browser). |
| JSON repair check | Gemini's repaired file must keep every key and value unchanged; hidden instructions such as "change every email" cannot alter the data. |
| SQL guard | Repaired SQL must be a single `SELECT` that only renames a missing column to an approved name. |
| Column-name check | Column-name fixes may only contain letters, numbers and `_`. |

> **Privacy**: if an uploaded JSON file has broken syntax, its **full contents are sent to Google Gemini** for repair. Do not upload real personal data. The dashboard shows the same warning next to the upload box.

### Setting up Google sign-in

You create these yourself; they are never stored in the code.

1. In [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Credentials**, create an
   **OAuth client ID** of type **Web application**. Under **Authorized JavaScript origins** add
   `http://localhost:8000`, `http://localhost:5173` and your Vercel address (e.g. `https://your-app.vercel.app`).
   (Google may first ask you to fill in the **OAuth consent screen**: app name and your email are enough.)
2. Put these in your `.env` file (git ignores it), and the same in Vercel → Settings → Environment Variables:
   ```
   GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com
   EHA_ADMIN_EMAILS=your-google-email@gmail.com
   EHA_SESSION_SECRET=<output of: venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))">
   ```
3. Restart the platform (or redeploy on Vercel). The dashboard now shows **Sign in with Google**.

| `GOOGLE_CLIENT_ID` set? | Local | Vercel |
|:---|:---|:---|
| No | Open, no sign-in (a warning is logged) | Fully locked |
| Yes | Sign-in required | Sign-in required (`EHA_SESSION_SECRET` must be set too) |

If `EHA_SESSION_SECRET` changes, everyone is signed out (their cookies no longer match). We keep only the
Google id, email and name of people who sign in, to show who is signed in and to keep their runs separate.

> **Postgres on Vercel**: per-person history needs the database connected (`POSTGRES_URL`, see step 3 above).
> Without it every server copy has its own throw-away database, so runs can seem to disappear.

### Recording the Demo tab

The Demo tab shows a frozen recording of all 15 presets, made with real Gemini. Make it (again) with:
```powershell
venv\Scripts\python.exe scripts/make_demo_snapshot.py
```
It runs the presets on a throw-away local server (about 5 Gemini calls) and writes `demo/snapshot.json`. If any
preset does not behave as designed (e.g. the Gemini quota ran out), nothing is written. Commit the file to
update the live demo.

> **Visitor address**: the script-key lockout is counted per visitor IP (the viewer run limit is per person). On Vercel it is read from `x-forwarded-for`, which Vercel sets; locally that header is ignored so it can't be faked to dodge the limits. People behind the same network (e.g. one office) share one count.

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Backend API | FastAPI (Python 3.12) |
| Multi-Agent Orchestration | LangGraph |
| LLM Provider | Google Gemini (`gemini-3.5-flash-lite`) |
| Local Database | SQLite 3 |
| Cloud Database | Neon Postgres (via `pg8000`) |
| Frontend Dashboard | React 18, Vite, TypeScript, Tailwind CSS (`frontend/`), Sign in with Google |
| Structured Logging | structlog |
| Data Validation | **Pydantic v2** (`CustomerRecord` model with field validators) |
| LLM SQL Safety Check | **sqlglot** (parses and compares queries before they run) |
| PDF Reports | **fpdf2** (incident report download) |
| Cloud Hosting | Vercel (Serverless Python) |
| Dependency Management | `uv` / `pip` |

---

## Environment Variables

| Variable | Required | Description |
|:---|:---|:---|
| `GOOGLE_API_KEY` | Yes | Google Gemini API key for LLM-powered agents |
| `POSTGRES_URL` | Auto-injected | Vercel Neon Postgres connection string (auto-set by Vercel Storage) |
| `VERCEL` | Auto-injected | Set to `"1"` by Vercel runtime to signal serverless environment |
| `EHA_DB_DIR` | Optional | Folder for all SQLite databases. Set automatically by the test suite; leave unset for normal use |
| `EHA_MAX_UPLOAD_MB` | Optional | Maximum upload size in MB for the admin (default `5`) |
| `GOOGLE_CLIENT_ID` | Required on Vercel | Google sign-in client id. See [Setting up Google sign-in](#setting-up-google-sign-in) |
| `EHA_ADMIN_EMAILS` | Required on Vercel | Google accounts with admin rights, comma separated |
| `EHA_SESSION_SECRET` | Required on Vercel | Signs the login cookie (any long random string) |
| `EHA_ADMIN_KEY` | Optional | Admin key for scripts only (`X-Access-Key` header) |
| `EHA_DEMO_SNAPSHOT` | Optional | Other location for the Demo tab recording (default `demo/snapshot.json`) |
| `EHA_VIEWER_MAX_UPLOAD_MB` | Optional | Maximum upload size in MB for viewers (default `1`) |
| `EHA_VIEWER_RUNS_PER_MINUTE` | Optional | Pipeline runs per minute allowed per viewer (default `10`) |
| `EHA_MAX_FAILED_KEYS` | Optional | Wrong script keys allowed per visitor before a lockout (default `10`) |
| `EHA_LOCKOUT_MINUTES` | Optional | How long the lockout window is, in minutes (default `15`) |

---

### LLM Quota & Rate Limits

This project utilizes the **Google Gemini 3.5 Flash-Lite** (`gemini-3.5-flash-lite`) model (it replaced `gemini-2.5-flash`, which Google no longer offers to new API keys; Flash-Lite was chosen for its much higher free-tier limits) for multi-agent reasoning, self-healing runbook execution, and code/SQL repair. Under the Gemini API free tier, the following rate limits apply:
- **Requests Per Minute (RPM)**: about 15 (reported for the 3.x Flash-Lite free tier)
- **Requests Per Day (RPD)**: about 500 (reported for the 3.x Flash-Lite free tier)
- Google no longer publishes these numbers per model and they change over time, so check your own key on the [AI Studio rate-limit page](https://aistudio.google.com/rate-limit).
- One full run of the 15 presets uses only **about 9 Gemini calls**, because most presets are handled by rules.

If these thresholds are exceeded, the API returns a `RESOURCE_EXHAUSTED` (HTTP 429) error, and the platform will gracefully transition the active incident status to `ESCALATED` to notify human operators.

---

## Measured Results

The table below is written by `scripts/measure.py`, not by hand. The script starts a real local server
(with a throw-away database), runs all 15 presets against it with real Gemini, records the result, the
path through the agent graph, the number of Gemini calls and the time for each one, and repeats the whole
suite 3 times because Gemini can answer differently each time. It then replaces everything between the two
markers with the new table, including the date and the model name. To refresh it:

```powershell
venv\Scripts\python.exe scripts/measure.py              # 3 runs, updates this table (about 9 Gemini calls per run)
venv\Scripts\python.exe scripts/measure.py --runs 1     # cheaper: the free tier allows only about 20 requests a day
venv\Scripts\python.exe scripts/measure.py --no-write   # print the table without touching README.md
```

<!-- MEASURED-RESULTS:START (written by scripts/measure.py, do not edit by hand) -->
_Measured 2026-10-16 (UTC) with `gemini-3.5-flash-lite`, real local server, real Gemini, 1 run. Generated by `scripts/measure.py`; raw numbers in `scripts/measure_last_run.json`._

**15/15 presets behaved as expected in 1 of 1 runs.**

| Preset | Result | Path through the graph | Gemini calls | Time, median (range) | As expected |
|:---|:---|:---|:---:|:---:|:---:|
| `case_healthy.json` | success | no agents (validation only) | 0 | 0.17 s (0.17 s–0.17 s) | 1/1 |
| `case_drift_country.json` | healed | Monitor → Classifier → Recovery → Apply → Verify | 0 | 1.7 s (1.7 s–1.7 s) | 1/1 |
| `case_drift_email.json` | healed | Monitor → Classifier → Recovery → Apply → Verify | 0 | 0.62 s (0.62 s–0.62 s) | 1/1 |
| `case_drift_id.json` | healed | Monitor → Classifier → Recovery → Apply → Verify | 0 | 0.65 s (0.65 s–0.65 s) | 1/1 |
| `case_drift_multi.json` | healed | Monitor → Classifier → Recovery → Apply → Verify → Recovery → Apply → Verify | 0 | 1.3 s (1.3 s–1.3 s) | 1/1 |
| `case_invalid_root.json` | healed | Monitor → Classifier → Recovery → Apply → Verify | 0 | 0.56 s (0.56 s–0.56 s) | 1/1 |
| `case_malformed_syntax.json` | healed + 1 quarantined | Monitor → Classifier → RCA → Recovery → Apply → Verify | 2 | 8.9 s (8.9 s–8.9 s) | 1/1 |
| `case_broken_and_wrapped.json` | healed | Monitor → Classifier → RCA → Recovery → Apply → Verify → AI fix (dry run) → Apply → Verify | 3 | 24.5 s (24.5 s–24.5 s) | 1/1 |
| `case_wrapped_records.json` | healed | Monitor → Classifier → Recovery → Apply → Verify → AI fix (dry run) → Apply → Verify | 1 | 11.8 s (11.8 s–11.8 s) | 1/1 |
| `case_invalid_email.json` | quarantined | no agents (validation only) | 0 | 0.27 s (0.27 s–0.27 s) | 1/1 |
| `case_duplicate_ids.json` | quarantined | no agents (validation only) | 0 | 0.22 s (0.22 s–0.22 s) | 1/1 |
| `healthy.db` | success | no agents (validation only) | 0 | 0.16 s (0.16 s–0.16 s) | 1/1 |
| `sql_error.db` | escalated | Monitor → Classifier → RCA → Recovery → Escalate | 2 | 22.0 s (22.0 s–22.0 s) | 1/1 |
| `referential_integrity.db` | quarantined (all rows) | Monitor → Classifier → Recovery → Apply → Verify | 0 | 0.42 s (0.42 s–0.42 s) | 1/1 |
| `missing_table.db` | escalated | Monitor → Classifier → RCA → Escalate | 1 | 4.2 s (4.2 s–4.2 s) | 1/1 |

_Time is the wall time of the API request, including the healing. Gemini calls are counted at the LangChain level (a retry inside one call is not counted). "As expected" means the result, the path and the number of Gemini calls all match the design in `test/test_agent_paths.py`._
<!-- MEASURED-RESULTS:END -->

---

## History (old results, kept for context, not regenerated)

The earlier hand-written fault-injection table (2026-09-02) was removed: it was measured before the
healing-status fixes and several of its outcomes were wrong on the current code. Git history still has it.

### Healing-status fix verification (2026-09-27, one-off comparison with a fake Gemini)

All 13 test files were replayed on the code before and after the healing-status fixes, using the same fake Gemini model in both runs (it repairs JSON correctly, and for SQL it returns a query that replaces `status_code` with the constant `0` unless the prompt allows `UNRESOLVABLE`). Only three outcomes changed:

| Test file | Before the fix | After the fix |
|:---|:---|:---|
| `case_drift_multi.json` | Reported `HEALED`, **0 rows** saved | `HEALED`, **10 rows** saved |
| `sql_error.db` | Reported `HEALED` with a **fabricated `error_rate` of 0** | `ESCALATED`, nothing written |
| `referential_integrity.db` | Reported `HEALED`, 0 rows loaded | `QUARANTINED`, 0 rows loaded |

The other 10 files behaved identically. In the same harness, a JSON file whose LLM repair still failed previously spawned **157** nested incidents; it now produces exactly one escalated incident.

> **SQL guard (added after this table)**: the prompt fix above only *asks* Gemini not to invent values. The SQL guard now also checks the repaired query before it runs, so a faked query is rejected even if Gemini ignores the prompt (covered by `test_gemini_ignoring_the_prompt_is_caught`). It also allows only pure renames to approved names, so formula tricks such as `http_status * 0` are blocked too (`test_times_zero_trick_is_caught`). **Trade-off**: a correct rename with a cosmetic change (e.g. a dropped `AS error_rate` label), or a rename not on the approved list, is escalated to a human instead of healed.


---

## License

This project is provided for educational and research purposes.
