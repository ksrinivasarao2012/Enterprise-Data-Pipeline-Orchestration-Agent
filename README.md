# Enterprise Data Pipeline Orchestration & Agentic Self-Healing Platform

An advanced, production-grade SRE (Site Reliability Engineering) automation platform. The system orchestrates, monitors, and automatically heals data pipelines when they encounter structural drifts, operational failures, or query syntax anomalies using a multi-agent cooperative framework powered by LangGraph and Gemini.

---

## System Architecture

The platform is split into three core logical tiers: the **Data Ingestion Layer**, the **Control Plane Telemetry Receiver**, and the **Agentic Remediation Loop**.

```
                           +----------------------------------------+
                           |  Streamlit Operations Dashboard (UI)   |
                           +-------------------+--------------------+
                                               |
                                               v
+-----------------------+          +-----------+------------+          +-----------------------+
| Ingestion Pipeline A  |  ---->   | Operational DB (SQLite)|          | Pipeline B (DB‑to‑DB) |
|  (Customer JSON Files)|          |      `operational.db`  |          | (Upload Local DB File)|
+-----------+-----------+          +------------------------+          +-----------+-----------+
            |                                                                      |
            | Sends Telemetry Logs                                                 | Sends Telemetry Logs
            v                                                                      v
+-----------+----------------------------------------------------------------------+-----------+
|                      Control Plane Telemetry Gateway (FastAPI)                                |
|                                   `localhost:8000`                                           |
+----------------------------------------------+-----------------------------------------------+
                                               | Offloads Telemetry Logs
                                               v
+----------------------------------------------+-----------------------------------------------+
|                      Agentic Remediation Loop (LangGraph)                                    |
|  [Monitor Agent] --> [Classifier Agent] --> [RCA Agent] --> [Recovery Actuator]              |
+----------------------------------------------------------------------------------------------+
```

### 1. Data Plane Pipelines
*   **Pipeline A (Batch Customer Ingestor)**:
    *   **Source**: Customer records in JSON format uploaded dynamically via the UI sandbox.
        *   **Input JSON Structure (`case_healthy.json`)**:
            *   `customer_id` (`string`) - Unique text identifier for the customer record.
            *   `name` (`string`) - Customer's full name.
            *   `email` (`string`) - Email address (validated via regex patterns).
            *   `country` (`string`) - Geographic region or country name.
    *   **Destination**: Saves parsed data directly to the `customers` table in `database/operational.db`.
        *   **Output Schema (`customers` table)**:
            *   `customer_id` (`TEXT`, Primary Key) - Matches input id.
            *   `name` (`TEXT`, Not Null) - Customer's name.
            *   `email` (`TEXT`, Unique, Not Null) - Customer's validated email address.
            *   `country` (`TEXT`) - Standardized location string.
            *   `signup_date` (`TEXT`) - Current ISO timestamp populated on ingestion.
            *   `status` (`TEXT`, Default 'ACTIVE') - Customer operational status.
            *   `total_orders` (`INTEGER`, Default 0) - Active order counter.
    *   **Self-Healing Scope**: Resilient against multi-column schema drifts (e.g., column renaming anomalies like `country` $\rightarrow$ `nation` or `email` $\rightarrow$ `user_email`). It supports iterative healing of compounding drifts using active schema overrides dynamically applied from the control plane's config tables.
*   **Pipeline B (Independent DB‑to‑DB ETL)**:
    *   **Source**: Flat telemetry logs table (`device_telemetry`) located inside a local or uploaded SQLite database file (`.db`).
        *   **Table Structure (`device_telemetry`)**:
            *   `log_id` (`TEXT`, Primary Key) - Unique identifier for the log entry.
            *   `device_id` (`TEXT`) - The ID of the device generating log telemetry.
            *   `timestamp` (`TEXT`) - Date and time string of the log event.
            *   `status_code` (`INTEGER`) - Request execution status response (e.g. `200`, `404`, `500`).
            *   `response_time` (`REAL`) - Operational latency in milliseconds.
    *   **Destination**: Saves aggregated system summaries directly to the `system_performance_metrics` table inside `database/analytics.db`.
        *   **Output Schema (`system_performance_metrics`)**:
            *   `device_id` (`TEXT`, Primary Key) - Device target.
            *   `total_events` (`INTEGER`) - Total request events registered.
            *   `error_rate` (`REAL`) - Percentage ratio of error status codes ($\ge 400$) relative to total runs.
            *   `avg_response_time` (`REAL`) - Average latency across all records.
    *   **Self-Healing Scope**: Automatically repairs invalid database metrics queries, syntax issues, or structural data-drift constraints using dynamic SQL command injections verified in a clean dry-run retry container.


### 2. Control Plane (Telemetry & Metadata Server)
*   **Technology**: FastAPI server running locally on port `8000`.
*   **Database Tier (`database/platform_state.db`)**:
    *   `pipeline_runs`: Logs start/end timestamps and execution status (`RUNNING`, `SUCCESS`, `FAILED`, `HEALED`).
    *   `incidents`: Stores telemetry exceptions, error classifications, severity levels, root-cause analyses, and resolution histories.
    *   `pipeline_configs`: Maintains active versioned configuration maps (JSON mappings and SQL overrides) to support hot-swapping schemas.
    *   `audit_logs`: A secure chronological trace of every telemetry alert and remediation action.

### 3. Agentic Remediation Engine (LangGraph Core)
When a pipeline throws an exception, it POSTs the metadata payload to the FastAPI `/telemetry/incident` endpoint. This starts an out-of-band LangGraph workflow:
*   **Monitor Agent**: Validates telemetry alert footprints and initializes the operational tracking incident status (`INVESTIGATING`).
*   **Classifier Agent**: Examines error signatures and categorizes the blast radius into Severity Levels (`P0` to `P3`) and Incident Categories (`SCHEMA_DRIFT`, `DATABASE_FAILURE`, `NETWORK_TIMEOUT`, etc.).
*   **RCA Agent**: Formulates a detailed technical diagnosis, tracing structural exceptions back to code logic.
*   **Recovery Agent (Actuator)**: Builds a correction directive (e.g. mapping `id_col` to `customer_id` or updating SQL queries). It tests the fix in a sandbox environment:
    *   If the dry-run succeeds, the configuration is marked as **verified** and committed to `platform_state.db`.
    *   If the dry-run raises a *new* error (e.g. a second schema drift), it commits the current fix and allows subsequent pipeline runs to heal the remaining issues iteratively.
    *   If the original error persists, it rolls back the draft config and marks the incident as `ESCALATED` to notify human operators.

### 4. Incident Classification & Severity Routing

Incidents are dynamically classified and prioritized using a combination of deterministic rules and agent-based calibrations.

#### Incident Categories
*   **`SCHEMA_DRIFT`**: Structural changes, key errors, or target schema mismatches (e.g., column renames).
*   **`DATA_QUALITY`**: Row value anomalies, null constraint failures, or negative value check failures.
*   **`NETWORK_TIMEOUT`**: Outages or socket timeouts related to network requests.
*   **`API_FAILURE`**: Authentication failures, rate limits, or external endpoint synchronization errors.
*   **`DATABASE_FAILURE`**: Database locking issues, foreign key constraint failures, or syntax query errors.
*   **`INFRASTRUCTURE_FAILURE`**: Core system-level errors, SQLite connection exceptions, or operational failures.
*   **`UNKNOWN`**: Unclassified fallback exceptions.

#### Severity Routing Tiers
*   **`P0` (Critical Outage)**: Immediately blocks all operations. Triggered by `INFRASTRUCTURE_FAILURE` exceptions, authentication errors (`401`/`403`) in critical syncs, or **automatically escalated** by the Classifier Agent if there are multiple unresolved active incidents on the same pipeline.
*   **`P1` (High)**: Blocks a data pipeline completely (e.g., schema drifts or database failures on production pathways).
*   **`P2` (Medium)**: Degraded performance, rate limits (`429`), or non-blocking network anomalies.
*   **`P3` (Low)**: Minor row-level anomalies (e.g., data quality quarantine alerts).

---

## 📂 Project Structure

```
├── .env                          # Local API keys & secrets (NOT committed to git)
├── .env.example                  # Template showing required environment variables
├── .gitignore                    # Git exclusion rules for secrets, caches, databases
├── configs/
│   └── settings.yaml             # Global system configurations (LLM model, database URL)
├── database/
│   ├── init_db.py                # Database schema initialization script
│   ├── platform_state.db         # Telemetry records, incident logs, and config overrides
│   ├── operational.db            # Raw ingestion transactional database
│   └── analytics.db              # Target database warehouse for aggregated metrics
├── scratch/
│   └── create_test_databases.py  # Script generating SQLite test files
├── src/
│   ├── agents/
│   │   ├── monitor.py            # Monitor Agent - telemetry verification
│   │   ├── classifier.py         # Classifier Agent - severity & category assignment
│   │   ├── rca.py                # RCA Agent - Gemini-powered root cause analysis
│   │   └── recovery.py           # Recovery Agent - remediation directive generator
│   ├── api/
│   │   └── main.py               # FastAPI telemetry gateway
│   ├── config.py                 # Centralized config loader (DB paths, LLM settings)
│   ├── incidents/
│   │   ├── incident_manager.py   # State transitions and lifecycle control
│   │   ├── incident_repository.py# Data access layer for platform_state.db
│   │   └── severity_rules.py     # Deterministic severity & category classification engine
│   ├── models/
│   │   └── schemas.py            # Pydantic data models & enums
│   ├── orchestration/
│   │   └── graph.py              # LangGraph multi-agent orchestration code
│   ├── pipeline/
│   │   ├── json_pipeline.py       # Pipeline A implementation
│   │   ├── db_pipeline.py        # Pipeline B implementation
│   │   └── etl_engine.py         # Prefect-based ETL pipeline (standalone mode)
│   ├── services/
│   │   ├── audit_service.py      # Centralized audit logging service
│   │   ├── pipeline_service.py   # Pipeline execution status records
│   │   └── remediation_service.py# Actuator containing dry-run verification
│   ├── telemetry/
│   │   ├── incident_creator.py   # Telemetry event shipper to Control Plane API
│   │   └── logger.py             # Structured logging (structlog) configuration
│   └── ui/
│       └── app.py                # Streamlit Operations Dashboard
├── test_data/
│   ├── pipeline_A/               # Test JSON datasets with drift variations
│   └── pipeline_B/               # Test SQLite databases matching database failures
├── requirements.txt              # Project dependencies
└── run_platform.py               # Unified application launcher (with port check)
```

---

## Execution & Getting Started

### 1. Prepare Your Environment
Create and activate your Python virtual environment, then install dependencies. We highly recommend using **`uv`** as the project is configured with a modern `pyproject.toml` and `uv.lock` setup.

#### Method A: Using `uv` (Recommended & Faster)
If you have `uv` installed, run:
```powershell
# Create the environment and install all dependencies (including local dashboard/ETL/test extras)
uv sync --all-extras
```
To activate the virtual environment:
* **For PowerShell**:
  ```powershell
  .venv\Scripts\activate.ps1
  ```
* **For Command Prompt (cmd.exe)**:
  ```cmd
  .venv\Scripts\activate.bat
  ```

#### Method B: Using Standard `venv` & `pip` (Fallback)
If you prefer standard Python tools:

##### Create the Virtual Environment:
```powershell
python -m venv venv
```
This creates a folder named `venv` in your project root containing the isolated environment.

##### Activate the Virtual Environment:
Depending on your terminal environment:
* **For PowerShell**:
  ```powershell
  venv\Scripts\Activate.ps1
  ```
  *(If execution policies block you, run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` first)*
* **For Command Prompt (cmd.exe)**:
  ```cmd
  venv\Scripts\activate.bat
  ```

##### Install the Dependencies:
Once activated (indicated by `(venv)` prepended to your command prompt), upgrade `pip` and install the package requirements:
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure Your API Key
The platform uses Google Gemini for intelligent root cause analysis and schema recovery. You must provide a valid Google API key.

1. Copy the example environment file:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and replace the placeholder with your actual API key:
   ```
   GOOGLE_API_KEY=your-actual-google-api-key-here
   ```
   > **Note**: You can obtain a free API key from [Google AI Studio](https://aistudio.google.com/apikey).

The LLM model and temperature are configured centrally in `configs/settings.yaml`:
```yaml
llm:
  model_name: "gemini-2.5-flash"
  temperature: 0.0
```
All agents automatically read from this config — no need to edit individual Python files.

### 3. Initialize the Database Schema & Generate Test Data
Run the database initialization script. This creates the local SQLite files and seeds `operational.db` with default values:
```powershell
venv\Scripts\python -m database.init_db
```

Next, generate the 4 distinct SQLite files used for Pipeline B testing:
```powershell
venv\Scripts\python.exe scratch/create_test_databases.py
```
This generates the following files inside `test_data/pipeline_B/`:
*   `healthy.db` - Clean, standard telemetry database schema.
*   `missing_table.db` - Telemetry table missing entirely.
*   `referential_integrity.db` - Telemetry logs with orphaned/null device IDs.
*   `sql_error.db` - Telemetry table missing the expected `status_code` column, causing query failures.

### 4. Launch the Platform
Start both the control plane API server and the front-end dashboard simultaneously using the unified launcher:
```powershell
venv\Scripts\python.exe run_platform.py
```
*   **FastAPI Control Plane**: Runs on port `8000` (FastAPI stdout/stderr logs are routed to `database/fastapi.log` to keep your console output clean).
*   **Streamlit Operations Console**: Opens automatically in your browser at `http://localhost:8501`.

> **Port Conflict**: If port `8000` is already in use from a previous run, the launcher will detect it and display an error message. Stop the previous process before relaunching.

---

## Detailed Ingestion & Execution Testing Scenarios

Open the dashboard (`http://localhost:8501`) and run these testing scenarios:

### Scenario 1: Basic Ingestion & Auto-Remediation (Pipeline A)
1.  In the **Pipeline Ingestion Sandbox**, upload `test_data/pipeline_A/case_drift_country.json` (where the `country` key has drifted to `nation`).
2.  Click **Execute Ingestion Pipeline A**.
3.  The pipeline will crash, register an incident, and trigger the self-healing loop.
4.  Within seconds, the dashboard's **Active Incident Workflow** table will display the incident shifting from `OPEN` $\rightarrow$ `INVESTIGATING` $\rightarrow$ `RCA_COMPLETED` $\rightarrow$ `RESOLVED`.
5.  Re-execute the ingestion — it will succeed automatically using the committed override database config mapping.

### Scenario 2: Iterative Multi-Step Healing (Pipeline A)
1.  Upload `test_data/pipeline_A/case_drift_email.json` (contains *both* ID column drift and Email column drift).
2.  Click **Execute Ingestion Pipeline A**.
3.  The pipeline will fail on ID drift, apply the fix, detect the second drift on Email during the dry-run, commit the first fix, and trigger subsequent resolutions to fix both mappings sequentially.
4.  Once both incidents transition to `RESOLVED`, the pipeline will successfully process the file.

### Scenario 3: Database ETL Sandbox (Pipeline B)
1.  Under the **Database ETL Sandbox** section, drag and drop `test_data/pipeline_B/healthy.db`.
2.  Click **Execute Ingestion Pipeline B**. The run will succeed, showing the synced records count.
3.  Now, drag and drop `test_data/pipeline_B/sql_error.db` (where the `status_code` column is missing).
4.  Execute.
5.  Observe the telemetry capture the query parse failure and file a database priority incident.
6.  The LangGraph loop will execute and attempt to apply SQL query fixes to reconcile the missing metadata.

---

## Expected Outcomes & Destination Logs

When a pipeline completes successfully, the platform stores its parsed dataset or aggregated insights directly into local database files:

### Data Destinations
* **Pipeline A Successful Run**: Saves the mapped customer dataset directly to the **`database/operational.db`** file in the `customers` table.
* **Pipeline B Successful Run**: Saves the compiled system insights directly to the **`database/analytics.db`** file in the `system_performance_metrics` table.

### Pipeline A: Ingestion Test File Outcomes

Below is the expected outcome of running Pipeline A with specific JSON test datasets from `test_data/pipeline_A/`:

| Test JSON File | Expected Behavior | Operational Explanation | Healing Outcome |
| :--- | :--- | :--- | :--- |
| `case_healthy.json` | **Success** | Matches the baseline schema exactly: `customer_id`, `name`, `email`, `country`. | Runs cleanly; writes records to the `customers` table in `operational.db`. |
| `case_drift_country.json` | **Failure** $\rightarrow$ **Self-Healed** | The key `country` was renamed to `nation`. This triggers a `KeyError: 'country'` crash. | LangGraph matches the drift (`country` $\rightarrow$ `nation`), saves the override configuration, and succeeding runs complete cleanly. |
| `case_drift_email.json` | **Failure** $\rightarrow$ **Self-Healed** | Contains two drifts: `customer_id` $\rightarrow$ `id`, and `email` $\rightarrow$ `user_email`. | The engine heals the ID key first, dry-runs and catches the secondary email key drift, resolves both sequentially, and succeeds. |
| `case_invalid_root.json` | **Failure** $\rightarrow$ **Self-Healed** | The JSON root is a dictionary/object instead of a list. Triggers a `root_structure` error. | Automatically heals by applying a `RECONFIGURE` override of `{"flatten_root_dict": True}`, which extracts dictionary values into a list. |
| `case_malformed_syntax.json` | **Failure** $\rightarrow$ **Self-Healed** | The JSON file is malformed (missing commas/brackets). Triggers a `JSONDecodeError`. | Automatically heals via a `REPAIR_FILE` directive. The Gemini LLM repairs the syntax and rewrites the file on disk. |
| `case_invalid_email.json` | **Failure** | A data validation error is triggered because some rows contain invalid email patterns. | **Escalated**: Pre-validation rules check email format. Because it is a raw data value error rather than a structural drift, it escalates to a human. |
| `case_duplicate_ids.json` | **Failure** | Multiple rows in the batch contain the same customer ID. | **Escalated**: Batch ID duplication is a logic or data issue that cannot be resolved via configuration overrides, so it escalates. |

### Pipeline B: Database Test File Outcomes

Below is the expected outcome of running Pipeline B with specific SQLite database files from `test_data/pipeline_B/`:

| Test DB File | Expected Behavior | Operational Explanation & Target Outcome |
| :--- | :--- | :--- |
| `healthy.db` | **Success** | Validates table structures and completes aggregation cleanly. Outputs results to `analytics.db`. |
| `sql_error.db` | **Failure** $\rightarrow$ **Self-Healed** | The query fails due to missing columns or syntax mismatch. Instantly recognized as `SCHEMA_DRIFT`; Gemini rewrites the SQL query to bypass the error and runs it successfully. |
| `referential_integrity.db` | **Failure** $\rightarrow$ **Self-Healed** | Database rows contain null or unregistered device identifiers. Triggers `DATA_QUALITY` exception; automatically quarantined, allowing the rest of the batch to run. |
| `missing_table.db` | **Failure** | Table `device_telemetry` is not found, throwing `OperationalError`. Escales as missing table schemas require manual setup. |

---

## Technical Details: Why Specific Cases Work vs. Escalate

### Why `case_invalid_root` Works (Auto-Healed)
- **Structured Error Signature**: The pipeline throws a structured error message (`ValueError|root_structure`) containing the `ErrorSignature` pattern.
- **Defined Remediation Pathway**: The Recovery Agent contains code to specifically handle the `root_structure` signature under `IncidentCategory.DATA_QUALITY` by triggering a `RECONFIGURE` action with `flatten_root_dict: True`.
- **System Actuation**: The remediation validator parses and whitelists `flatten_root_dict` as a valid boolean override. The parser converts the dictionary keys/values into a standard array using `list(raw_data.values())`, resolving the crash.

### Why `case_invalid_email` Escalates (Does Not Auto-Heal)
- **No Error Signature**: Row-level verification checks are performed inline inside a loop. Since there is no config-level column mapping that can fix a malformed string value (e.g. `john.doe_example.com` missing the `@` symbol), the code raises a generic `ValueError` without an `ErrorSignature` token.
- **No Safe Automated Guessing**: The healing engine cannot automatically guess or fabricate valid email strings. Resolving invalid emails requires human SRE lookup or data provider feedback, making `ESCALATED` the correct safety behavior.


