# Enterprise Data Pipeline Orchestration & Agentic Self-Healing Platform

An advanced, production-grade SRE (Site Reliability Engineering) automation platform that orchestrates, monitors, and **automatically heals** data pipelines when they encounter structural drifts, operational failures, or query syntax anomalies — using a multi-agent cooperative framework powered by **LangGraph** and **Google Gemini**.

> **Live Demo**: [https://enterprise-data-pipeline-orchestrat.vercel.app](https://enterprise-data-pipeline-orchestrat.vercel.app)

---

## Key Features

- **Dual Data Pipelines** — JSON batch ingestion (Pipeline A) and database-to-database ETL (Pipeline B) with full telemetry instrumentation.
- **Multi-Agent Self-Healing** — Four cooperative LangGraph agents (Monitor → Classifier → RCA → Recovery) automatically diagnose and remediate failures without human intervention.
- **Iterative Schema Drift Recovery** — Handles compounding multi-column drifts across successive pipeline runs using persistent configuration overrides.
- **LLM-Powered File Repair** — Gemini automatically repairs malformed JSON syntax on disk and retries ingestion.
- **Hybrid Fault-Tolerance & Quarantine** — Row-level anomalies (bad email formats, duplicate IDs, null device IDs) are isolated per-row into a dedicated `quarantined_records` table. Healthy records are always ingested, and invalid records are logged for operator review — without crashing the pipeline.
- **Pydantic v2 Validation** — All ingested customer records are validated via a typed `CustomerRecord` Pydantic model. Custom field validators handle type coercions (e.g., `5` → `"5"`), null checks, email format rules, and string normalizations before any database write.
- **Dual-Database Architecture** — Runs locally on SQLite, and automatically switches to **Neon Postgres** when deployed to Vercel — zero code changes required.
- **Real-Time Dashboard** — A responsive, dark-themed web dashboard with live metrics, incident tracking, quarantine log, data ingestion rules reference, and pipeline execution sandbox.
- **Vercel-Native Deployment** — Fully serverless deployment on Vercel's free tier with in-process healing (no background task limitations).

---

## System Architecture

The platform is split into three core logical tiers: the **Data Ingestion Layer**, the **Control Plane Telemetry Receiver**, and the **Agentic Remediation Loop**.

```
                       +------------------------------------------+
                       |   Web Operations Dashboard (HTML/JS)     |
                       |   or Streamlit Console (Local Dev)        |
                       +-------------------+-----------------------+
                                           |
                                           v
+-----------------------+       +----------+-----------+       +-----------------------+
| Ingestion Pipeline A  | ----> | Operational DB       |       | Pipeline B (DB-to-DB) |
| (Customer JSON Files) |       | SQLite / Neon Postgres|       | (Upload Local DB File)|
+-----------+-----------+       +-----------------------+       +-----------+-----------+
            |                                                               |
            | Sends Telemetry                                              | Sends Telemetry
            v                                                               v
+-----------+---------------------------------------------------------------+-----------+
|                    Control Plane Telemetry Gateway (FastAPI)                           |
|                Local: localhost:8000  |  Cloud: Vercel Serverless                     |
+----------------------------------------------+----------------------------------------+
                                               | In-Process Trigger
                                               v
+----------------------------------------------+----------------------------------------+
|                    Agentic Remediation Loop (LangGraph)                                |
| [Monitor Agent] --> [Classifier Agent] --> [RCA Agent] --> [Recovery Actuator]         |
+----------------------------------------------+----------------------------------------+
                                               |
                                               v
                                  +------------+-----------+
                                  | Dry-Run Verification   |
                                  | ✓ Commit & Resolve     |
                                  | ✗ Rollback & Escalate  |
                                  +------------------------+
```

### 1. Data Plane Pipelines

#### Pipeline A — Batch Customer Ingestor
- **Source**: Customer records in JSON format uploaded dynamically via the UI sandbox.
  - **Input Schema**: `customer_id`, `name`, `email`, `country`
- **Destination**: Saves parsed data to the `customers` table.
  - **Output Schema**: `customer_id` (PK), `name`, `email` (Unique), `country`, `signup_date`, `status` (Default: `ACTIVE`), `total_orders` (Default: `0`)
- **Self-Healing Scope**: Resilient against multi-column schema drifts (e.g., `country` → `nation`, `email` → `user_email`), malformed JSON syntax, and invalid root data structures. Supports iterative healing of compounding drifts using active schema overrides dynamically applied from the control plane's config tables.

#### Pipeline B — Database ETL Engine
- **Source**: Flat telemetry logs table (`device_telemetry`) from an uploaded SQLite `.db` file.
  - **Table Structure**: `log_id` (PK), `device_id`, `timestamp`, `status_code`, `response_time`
- **Destination**: Saves aggregated system summaries to `system_performance_metrics` table.
  - **Output Schema**: `device_id` (PK), `total_events`, `error_rate`, `avg_response_time`
- **Self-Healing Scope**: Automatically repairs invalid database metrics queries, SQL syntax issues, or structural data-drift constraints using dynamic SQL command injections verified in a clean dry-run retry container.

### 2. Control Plane (Telemetry & Metadata Server)

- **Technology**: FastAPI (runs locally on port `8000` or as Vercel Serverless Functions)
- **Database Tier** (`platform_state.db` locally / Neon Postgres on Vercel):
  - `pipeline_runs` — Logs start/end timestamps and execution status (`RUNNING`, `SUCCESS`, `FAILED`, `HEALED`).
  - `incidents` — Stores telemetry exceptions, error classifications, severity levels, root-cause analyses, and resolution histories.
  - `pipeline_configs` — Maintains active versioned configuration maps (JSON mappings and SQL overrides) to support hot-swapping schemas.
  - `audit_logs` — A secure chronological trace of every telemetry alert and remediation action.

### 3. Agentic Remediation Engine (LangGraph Core)

When a pipeline throws an exception, the telemetry interceptor captures the error and triggers an in-process LangGraph workflow:

1. **Monitor Agent**: Validates telemetry alert footprints and initializes the operational tracking incident status (`INVESTIGATING`).
2. **Classifier Agent**: Examines error signatures and categorizes the blast radius into Severity Levels (`P0`–`P3`) and Incident Categories (`SCHEMA_DRIFT`, `DATABASE_FAILURE`, etc.).
3. **RCA Agent**: Uses Gemini to formulate a detailed technical diagnosis, tracing structural exceptions back to code logic.
4. **Recovery Agent (Actuator)**: Builds a correction directive (e.g., mapping `id_col` to `customer_id` or rewriting SQL queries). It tests the fix in a sandbox:
   - ✅ **Dry-run succeeds** → Configuration committed, incident marked `RESOLVED`.
   - ⚠️ **Dry-run reveals a *new* error** → Current fix committed, subsequent runs heal remaining issues iteratively.
   - ❌ **Original error persists** → Draft config rolled back, incident `ESCALATED` to human operators.

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
    *   **Agentic Actuation**: For failed aggregates, the LangGraph Recovery agent is invoked. If it deploys a `QUARANTINE` directive (isolating the bad records), the actuator updates the run status to `HEALED` post-isolation.
    *   **Infinite Loop Prevention**: If a configuration dry-run during schema-drift recovery encounters the **same missing primary ID/key** error, the actuator rolls back the configurations and escalates the incident directly to a human on-call engineer via `_handle_escalation()`.

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

## 📂 Project Structure

```
├── .env                            # Local API keys & secrets (NOT committed to git)
├── .env.example                    # Template showing required environment variables
├── .gitignore                      # Git exclusion rules for secrets, caches, databases
├── .python-version                 # Python version lock (3.12)
├── pyproject.toml                  # Modern Python project manifest (used by uv & Vercel)
├── requirements.txt                # Pip-compatible dependency list
├── vercel.json                     # Vercel deployment configuration (routes & builds)
├── run_platform.py                 # Unified local application launcher
│
├── configs/
│   └── settings.yaml               # Global system configurations (LLM model, DB paths)
│
├── database/
│   ├── init_db.py                  # Database schema initialization (SQLite + Postgres)
│   ├── platform_state.db           # Telemetry records, incident logs, config overrides
│   ├── operational.db              # Raw ingestion transactional database
│   └── analytics.db                # Target warehouse for aggregated metrics
│
├── public/                         # Static web dashboard (served on Vercel & locally)
│   ├── index.html                  # Dashboard UI (dark-themed, responsive, GitHub link, Data Rules panel)
│   └── app.js                      # Dashboard logic (fetch, render, pipeline controls, quarantine log)
│
├── src/
│   ├── database.py                 # Dual-database adapter (SQLite ↔ Postgres)
│   ├── config.py                   # Centralized config loader (DB paths, LLM settings)
│   │
│   ├── agents/
│   │   ├── monitor.py              # Monitor Agent — telemetry verification
│   │   ├── classifier.py           # Classifier Agent — severity & category assignment
│   │   ├── rca.py                  # RCA Agent — Gemini-powered root cause analysis
│   │   └── recovery.py            # Recovery Agent — remediation directive generator
│   │
│   ├── api/
│   │   └── main.py                 # FastAPI gateway (REST endpoints + Vercel entrypoint)
│   │
│   ├── incidents/
│   │   ├── incident_manager.py     # State transitions and lifecycle control
│   │   ├── incident_repository.py  # Data access layer for incidents
│   │   └── severity_rules.py       # Deterministic severity & category classification
│   │
│   ├── models/
│   │   └── schemas.py              # Pydantic data models & enums
│   │
│   ├── orchestration/
│   │   └── graph.py                # LangGraph multi-agent orchestration graph
│   │
│   ├── pipeline/
│   │   ├── json_pipeline.py        # Pipeline A implementation
│   │   ├── db_pipeline.py          # Pipeline B implementation
│   │   └── etl_engine.py           # Prefect-based ETL pipeline (standalone mode)
│   │
│   ├── services/
│   │   ├── audit_service.py        # Centralized audit logging service
│   │   ├── pipeline_service.py     # Pipeline execution status tracking
│   │   ├── quarantine_service.py   # Quarantine record insert, fetch & clear operations
│   │   └── remediation_service.py  # Actuator with dry-run verification & rollback
│   │
│   ├── telemetry/
│   │   ├── incident_creator.py     # In-process telemetry capture & healing trigger
│   │   └── logger.py               # Structured logging (structlog) configuration
│   │
│   └── ui/
│       └── app.py                  # Streamlit Operations Dashboard (local dev)
│
├── test_data/
│   ├── pipeline_A/                 # Test JSON datasets with drift variations
│   │   ├── case_healthy.json
│   │   ├── case_drift_country.json
│   │   ├── case_drift_email.json
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
└── scratch/
    └── create_test_databases.py    # Script generating SQLite test files
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
  model_name: "gemini-2.5-flash"
  temperature: 0.0
```

#### 3. Initialize the Database & Generate Test Data
```powershell
# Create local SQLite files and seed with default values
venv\Scripts\python -m database.init_db

# Generate Pipeline B test databases
venv\Scripts\python.exe scratch/create_test_databases.py
```

#### 4. Launch the Platform
```powershell
venv\Scripts\python.exe run_platform.py
```
- **FastAPI Control Plane**: Runs on port `8000` (logs routed to `database/fastapi.log`)
- **Web Dashboard**: Opens at `http://localhost:8000` (static files served by FastAPI)
- **Streamlit Console** (optional): Opens at `http://localhost:8501`

> **Port Conflict**: If port `8000` is in use, the launcher will detect it and display an error. Stop the previous process before relaunching.

---

### Option B: Vercel Cloud Deployment (Production)

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
3. Vercel will auto-detect the `vercel.json` configuration and `pyproject.toml` dependencies.
4. Add the following **Environment Variable** in the Vercel project settings:
   ```
   GOOGLE_API_KEY = your-actual-google-api-key-here
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
1. Select `case_drift_email.json` (contains *both* ID column drift `customer_id` → `id` and Email column drift `email` → `user_email`).
2. Click **Execute Ingestion Pipeline A**.
3. The engine heals the ID key first, detects the second drift on Email during dry-run, commits the first fix, and triggers subsequent resolutions to fix both mappings sequentially.
4. Once both incidents transition to `RESOLVED`, the pipeline has successfully processed the file.

### Scenario 3: Database ETL Sandbox (Pipeline B)
1. Under **Database ETL Sandbox**, select `healthy.db`.
2. Click **Execute Ingestion Pipeline B** — the run succeeds, showing the synced records count.
3. Now select `sql_error.db` (where the `status_code` column is missing).
4. Execute. The telemetry captures the query parse failure and files a database priority incident.
5. The LangGraph loop executes and applies SQL query fixes to reconcile the missing metadata.

---

## Expected Outcomes

### Pipeline A: Test File Outcomes

| Test JSON File | Expected Behavior | Explanation | Healing Outcome |
|:---|:---|:---|:---|
| `case_healthy.json` | **Success** | Matches baseline schema exactly. | All 10 records written to `customers` table. |
| `case_drift_country.json` | **Failure → Self-Healed** | Key `country` renamed to `nation`, triggers `KeyError`. | LangGraph matches drift, saves override config, marks `RESOLVED`. |
| `case_drift_email.json` | **Failure → Self-Healed** | Two drifts: `customer_id` → `id`, `email` → `user_email`. | Heals ID key first, catches email drift during dry-run, resolves both. |
| `case_invalid_root.json` | **Failure → Self-Healed** | JSON root is a dictionary instead of a list. | Applies `flatten_root_dict: True` override to extract values as array. |
| `case_malformed_syntax.json` | **Failure → Self-Healed** | Malformed JSON (missing commas/brackets). | Gemini repairs syntax on disk via `REPAIR_FILE` directive, retries. |
| `case_invalid_email.json` | **Partial → Quarantined** | One row has invalid email (`john.doe_example.com` missing `@`). | Pydantic validator catches it; bad row isolated to `quarantined_records`, remaining 9 rows ingested successfully. Run status: `QUARANTINED`. |
| `case_duplicate_ids.json` | **Partial → Quarantined** | One duplicate `customer_id` exists in the batch. | Stateful batch duplicate check catches it; duplicate row quarantined, remaining rows ingested. Run status: `QUARANTINED`. |

> **Note**: Both `case_invalid_email.json` and `case_duplicate_ids.json` previously caused full pipeline failures and escalations. After the Pydantic validation and hybrid quarantine refactoring, these now complete as partial successes — isolating only the bad rows.

### Pipeline B: Test File Outcomes

| Test DB File | Expected Behavior | Explanation |
|:---|:---|:---|
| `healthy.db` | **Success** | Validates table structures and completes aggregation cleanly. |
| `sql_error.db` | **Failure → Escalated** | Missing `status_code` column; LangGraph attempts SQL patch but escalates to on-call if repair bounds are exceeded. |
| `referential_integrity.db` | **Failed → Healed** | All device IDs are NULL; quarantined by Pipeline B. LangGraph `QUARANTINE` directive resolves the incident. Run status: `HEALED`. |
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

## Tech Stack

| Layer | Technology |
|:---|:---|
| Backend API | FastAPI (Python 3.12) |
| Multi-Agent Orchestration | LangGraph |
| LLM Provider | Google Gemini (`gemini-2.5-flash`) |
| Local Database | SQLite 3 |
| Cloud Database | Neon Postgres (via `pg8000`) |
| Frontend Dashboard | HTML5, Vanilla JS, Tailwind CSS |
| Local Dashboard (Alt) | Streamlit |
| Structured Logging | structlog |
| Data Validation | **Pydantic v2** (`CustomerRecord` model with field validators) |
| Cloud Hosting | Vercel (Serverless Python) |
| Dependency Management | `uv` / `pip` |

---

## Environment Variables

| Variable | Required | Description |
|:---|:---|:---|
| `GOOGLE_API_KEY` | ✅ | Google Gemini API key for LLM-powered agents |
| `POSTGRES_URL` | Auto-injected | Vercel Neon Postgres connection string (auto-set by Vercel Storage) |
| `VERCEL` | Auto-injected | Set to `"1"` by Vercel runtime to signal serverless environment |

---

### LLM Quota & Rate Limits

This project utilizes the **Google Gemini 2.5 Flash** (`gemini-2.5-flash`) model for multi-agent reasoning, self-healing runbook execution, and code/SQL repair. Under the Gemini API free tier, the following rate limits apply:
- **Requests Per Minute (RPM)**: 15 to 30 RPM
- **Requests Per Day (RPD)**: 1,500 RPD

If these thresholds are exceeded, the API returns a `RESOURCE_EXHAUSTED` (HTTP 429) error, and the platform will gracefully transition the active incident status to `ESCALATED` to notify human operators.

---

## License

This project is provided for educational and research purposes.
