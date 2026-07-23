"""
End-to-end regression tests for the self-healing loop.

They run the real FastAPI endpoints and the real LangGraph workflow against an
isolated temp database (see conftest.py). Gemini is replaced by a fake model
whose reply each test controls, so the tests are fast, free and deterministic.
"""
import json
import os

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import langchain_google_genai
from src.agents import rca as rca_mod
from src.agents import recovery as recovery_mod
from src.api.main import app
from src.database import get_db_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# FakeLLM, fake_llm and client fixtures live in conftest.py (shared with test_security.py).


def _one(sql, params=(), db="state_db"):
    with get_db_connection(db) as conn:
        return conn.execute(sql, params).fetchone()


def _run_status():
    return _one("SELECT status FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")[0]


def _incident_rows():
    with get_db_connection("state_db") as conn:
        return conn.execute("SELECT run_id, status FROM incidents WHERE error_class != 'DataQualityAnomaly'").fetchall()


def _customer_count():
    return _one("SELECT COUNT(*) FROM customers", db="operational_db")[0]


# ---------------------------------------------------------------- Bug 1: fake HEALED

def test_multi_drift_is_healed_and_rows_are_really_saved(client, fake_llm):
    fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_multi.json"}).json()

    assert _customer_count() == 10, "run claimed healed but no customers were saved"
    assert _run_status() == "HEALED"
    assert r.get("healed") is True


def test_single_drift_still_heals(client, fake_llm):
    fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})
    assert _run_status() == "HEALED"
    assert _customer_count() == 10


def test_unfixable_drift_is_escalated_not_healed(client, fake_llm, tmp_path):
    fake_llm("UNRESOLVED")  # the LLM also cannot find a match
    rows = [{"customer_id": str(i), "name": "n", "email": f"u{i}@x.com", "zzz_unknown": "IN"} for i in range(3)]
    r = client.post("/api/pipelines/a/execute",
                    files={"file": ("bad.json", json.dumps(rows), "application/json")}).json()

    assert r["status"] == "failure"
    assert _run_status() != "HEALED"
    assert _customer_count() == 0
    assert [s for _, s in _incident_rows()] == ["ESCALATED"]


# ---------------------------------------------------------------- Bug 2: fake SQL

def test_sql_repair_unresolvable_escalates(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    r = client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"}).json()

    assert r["status"] == "failure"
    assert _run_status() != "HEALED"
    assert _one("SELECT COUNT(*) FROM system_performance_metrics", db="analytics_db")[0] == 0
    assert [s for _, s in _incident_rows()] == ["ESCALATED"]


def test_sql_prompt_no_longer_asks_for_fake_values(client, fake_llm):
    calls = fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    prompt_text = " ".join(str(c) for c in calls)
    assert "0.0" not in prompt_text
    assert "UNRESOLVABLE" in prompt_text


# ---------------------------------------------------------------- Bug 3: nested healing loops

def test_pipeline_b_failed_fix_does_not_start_nested_healing(client, fake_llm):
    # LLM returns SQL that is still broken -> verification fails
    fake_llm("SELECT device_id, COUNT(log_id), SUM(status_code), AVG(response_time) FROM device_telemetry GROUP BY device_id")
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})

    incidents = _incident_rows()
    assert len(incidents) == 1, f"nested healing created {len(incidents)} incidents"
    assert incidents[0][1] == "ESCALATED"


def test_file_repair_failure_does_not_start_nested_healing(client, fake_llm):
    # LLM "repairs" the file into something still invalid
    fake_llm("{ still : broken ")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"})

    incidents = _incident_rows()
    assert len(incidents) == 1, f"nested healing created {len(incidents)} incidents"
    assert incidents[0][1] == "ESCALATED"


def test_heal_attempt_cap_applies_before_any_agent_runs(client, fake_llm):
    calls = fake_llm("UNRESOLVED")
    from src.incidents.incident_manager import IncidentManager
    from src.services.pipeline_service import PipelineService
    from src.services.remediation_service import RemediationService
    from src.api.main import run_agentic_healing_workflow

    PipelineService.start_run("run_cap", "PIPELINE_B")
    incs = [IncidentManager.initialize_incident("run_cap", "PIPELINE_B", "ValueError", "boom", "trace")
            for _ in range(RemediationService.MAX_HEAL_ATTEMPTS + 1)]
    run_agentic_healing_workflow(incs[-1].incident_id)

    status = _one("SELECT status FROM incidents WHERE incident_id = ?", (incs[-1].incident_id,))[0]
    assert status == "ESCALATED"
    assert calls == [], "the LLM should not be called once the cap is exceeded"


# ---------------------------------------------------------------- Unchanged behaviour

def test_healthy_files_still_succeed(client, fake_llm):
    calls = fake_llm("UNRESOLVED")
    assert client.post("/api/pipelines/a/execute", data={"test_case": "case_healthy.json"}).json()["status"] == "success"
    assert client.post("/api/pipelines/b/execute", data={"test_case": "healthy.db"}).json()["status"] == "success"
    assert calls == []


def test_all_rows_quarantined_is_not_reported_as_healed(client, fake_llm):
    fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/b/execute", data={"test_case": "referential_integrity.db"}).json()
    assert _run_status() == "QUARANTINED"
    assert r.get("healed") is not True
    assert r.get("quarantined") is True


# ---------------------------------------------------------------- SQL guard (end to end)

CHEATING_SQL = ("SELECT device_id, COUNT(log_id) AS total_events, "
                "SUM(CASE WHEN 0 >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) AS error_rate, "
                "AVG(response_time) AS avg_response_time FROM device_telemetry GROUP BY device_id")


def test_gemini_ignoring_the_prompt_is_caught(client, fake_llm):
    # Gemini "disobeys": returns a query that fakes error_rate with the constant 0
    fake_llm(CHEATING_SQL)
    r = client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"}).json()

    assert r["status"] == "failure"
    assert _run_status() != "HEALED"
    assert _one("SELECT COUNT(*) FROM system_performance_metrics", db="analytics_db")[0] == 0
    assert [s for _, s in _incident_rows()] == ["ESCALATED"]
    action = _one("SELECT recovery_action FROM incidents")[0]
    assert "SQL guard" in action


def test_real_column_rename_is_still_healed(client, fake_llm, tmp_path):
    import sqlite3
    db = tmp_path / "renamed.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE device_telemetry (log_id TEXT PRIMARY KEY, device_id TEXT, "
                     "timestamp TEXT, http_status INTEGER, response_time REAL)")
        conn.executemany("INSERT INTO device_telemetry VALUES (?,?,?,?,?)", [
            ("1", "dev_a", "t", 200, 10.0), ("2", "dev_a", "t", 500, 30.0), ("3", "dev_b", "t", 200, 5.0)])
    fake_llm(CHEATING_SQL.replace("0 >= 400", "http_status >= 400"))

    r = client.post("/api/pipelines/b/execute",
                    files={"file": ("renamed.db", db.read_bytes(), "application/octet-stream")}).json()

    assert r.get("healed") is True
    assert _run_status() == "HEALED"
    with get_db_connection("analytics_db") as conn:
        rates = dict(conn.execute("SELECT device_id, error_rate FROM system_performance_metrics").fetchall())
    assert rates == {"dev_a": 0.5, "dev_b": 0.0}   # real numbers, computed from http_status


def test_times_zero_trick_is_caught(client, fake_llm, tmp_path):
    import sqlite3
    db = tmp_path / "renamed.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE device_telemetry (log_id TEXT PRIMARY KEY, device_id TEXT, "
                     "timestamp TEXT, http_status INTEGER, response_time REAL)")
        conn.execute("INSERT INTO device_telemetry VALUES ('1', 'dev_a', 't', 500, 30.0)")
    # uses the real column, but "* 0" would make the error rate 0 (it should be 1.0)
    fake_llm(CHEATING_SQL.replace("0 >= 400", "http_status * 0 >= 400"))

    r = client.post("/api/pipelines/b/execute",
                    files={"file": ("renamed.db", db.read_bytes(), "application/octet-stream")}).json()

    assert r["status"] == "failure"
    assert _one("SELECT COUNT(*) FROM system_performance_metrics", db="analytics_db")[0] == 0
    assert [s for _, s in _incident_rows()] == ["ESCALATED"]


# ---------------------------------------------------------------- quarantine-only runs (no agents)

@pytest.mark.parametrize("test_case", ["case_invalid_email.json", "case_duplicate_ids.json"])
def test_quarantine_only_incident_is_data_quality_and_not_counted_as_healed(client, fake_llm, test_case):
    calls = fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/a/execute", data={"test_case": test_case}).json()
    assert r["quarantined"] is True and r["rows_ingested"] == 9
    inc = client.get("/api/incidents").json()[0]
    assert inc["category"] == "DATA_QUALITY" and inc["severity"] == "P3"
    metrics = client.get("/api/metrics").json()
    assert metrics["resolved"] == 0, "a quarantine warning is not an agent heal"
    assert metrics["quarantined"] == 1
    assert calls == [], "no agent (and no Gemini) runs for a quarantine-only warning"
