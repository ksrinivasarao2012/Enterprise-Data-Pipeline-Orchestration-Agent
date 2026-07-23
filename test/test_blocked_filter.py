"""
"Blocked by guard" is stored in the database and filtered on the server (CHANGES.md section 26).

Before, the dashboard's "Show only blocked fixes" button only looked at the 20 incidents already on
screen, so an older blocked fix was missed and the badge count was wrong.
"""
import importlib.util
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.database import get_db_connection
from src.incidents.incident_repository import IncidentRepository
from src.models.schemas import IncidentSchema, IncidentStatus, SeverityLevel, IncidentCategory
from src.services.pipeline_service import PipelineService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHEATING_SQL = ("SELECT device_id, COUNT(log_id) AS total_events, "
                "SUM(CASE WHEN 0 >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) AS error_rate, "
                "AVG(response_time) AS avg_response_time FROM device_telemetry GROUP BY device_id")


def _make_incident(n: int, minutes_ago: int, blocked: bool):
    run_id = f"run_blk_{n}"
    PipelineService.start_run(run_id, "PIPELINE_B")
    IncidentRepository.create_incident(IncidentSchema(
        incident_id=f"inc_blk_{n:02d}", run_id=run_id, pipeline_id="PIPELINE_B",
        severity=SeverityLevel.P1, category=IncidentCategory.SCHEMA_DRIFT, status=IncidentStatus.ESCALATED,
        error_class="OperationalError", error_message="no such column: status_code", stack_trace="-",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    ))
    if blocked:
        IncidentRepository.mark_blocked_by_guard(f"inc_blk_{n:02d}")


def _get(client, query):
    r = client.get(f"/api/incidents?{query}")
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


# ---------------------------------------------------------------- the main bug: page 2

def test_blocked_incident_on_page_two_is_still_found(client):
    # 25 incidents. The ONLY blocked one is the oldest, so it is on page 2 (the page size is 20).
    for n in range(25):
        _make_incident(n, minutes_ago=n, blocked=(n == 24))

    page1, total = _get(client, "limit=20")
    assert total == 25 and len(page1) == 20
    assert "inc_blk_24" not in [i["incident_id"] for i in page1]   # the old code only looked at this page

    items, blocked_total = _get(client, "limit=20&blocked=true")
    assert blocked_total == 1
    assert [i["incident_id"] for i in items] == ["inc_blk_24"]
    assert items[0]["blocked_by_guard"] is True

    # the badge number comes from the server, not from the loaded page
    assert client.get("/api/metrics").json()["blocked_by_guard"] == 1


def test_blocked_false_and_combined_filters_and_bad_value(client):
    for n in range(5):
        _make_incident(n, minutes_ago=n, blocked=(n in (1, 3)))
    assert _get(client, "blocked=true")[1] == 2
    assert _get(client, "blocked=false")[1] == 3
    assert _get(client, "blocked=true&pipeline=PIPELINE_B&outcome=escalated")[1] == 2
    assert _get(client, "blocked=true&pipeline=PIPELINE_A")[1] == 0
    assert _get(client, "blocked=true&limit=1&offset=1")[0][0]["blocked_by_guard"] is True
    assert client.get("/api/incidents?blocked=maybe").status_code == 422


# ---------------------------------------------------------------- real runs mark the incident

def _only_incident(client):
    items = client.get("/api/incidents").json()
    return [i for i in items if i["error_class"] not in ("DataQualityAnomaly", "ReferentialIntegrityAnomaly")]


def test_unresolvable_marks_the_incident(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    [inc] = _only_incident(client)
    assert inc["status"] == "ESCALATED" and inc["blocked_by_guard"] is True


def test_sql_guard_marks_the_incident(client, fake_llm):
    fake_llm(CHEATING_SQL)
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    [inc] = _only_incident(client)
    assert "SQL guard" in inc["recovery_action"] and inc["blocked_by_guard"] is True


def test_json_repair_check_marks_the_incident(client, fake_llm):
    raw = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
    invented = raw.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com", "country": "Japan"},\n')
    fake_llm(invented)   # Gemini invents a missing country: the repair check refuses it
    client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"})
    [inc] = _only_incident(client)
    assert inc["status"] == "ESCALATED" and inc["blocked_by_guard"] is True


def test_other_outcomes_are_not_marked(client, fake_llm):
    fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})   # healed
    client.post("/api/pipelines/a/execute", data={"test_case": "case_invalid_email.json"})   # quarantined
    client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})          # escalated, no guard
    items = client.get("/api/incidents").json()
    assert len(items) == 3 and not any(i["blocked_by_guard"] for i in items)
    assert _get(client, "blocked=true")[1] == 0


# ---------------------------------------------------------------- old incidents are filled in once

def _init_db_module():
    spec = importlib.util.spec_from_file_location("eha_init_db_blk", os.path.join(ROOT, "database", "init_db.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_marks_old_incidents_once(tmp_path):
    init_db = _init_db_module()
    conn = sqlite3.connect(tmp_path / "old.db")
    cur = conn.cursor()
    # the OLD layout: no blocked_by_guard column
    cur.execute("CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, recovery_action TEXT)")
    cur.execute("CREATE TABLE audit_logs (ref_id TEXT, component TEXT, message TEXT)")
    cur.executemany("INSERT INTO incidents VALUES (?, ?)", [
        ("sql", "Action: ESCALATE | Rationale: SQL guard rejected the LLM repair: fake data"),
        ("json", None), ("unres", None), ("rca_only", None), ("plain", "Action: RECONFIGURE | Rationale: alias matched"),
    ])
    cur.executemany("INSERT INTO audit_logs VALUES (?, ?, ?)", [
        ("json", "ACTUATOR", "LLM file repair rejected: the repair changed the data. The file was not changed."),
        ("unres", "RECOVERY_AGENT", "Remediation generated: ESCALATE. LLM reported the SQL error as UNRESOLVABLE (...)"),
        ("rca_only", "RCA_AGENT", "Analysis completed: the word UNRESOLVABLE in Gemini's analysis must not count"),
        ("plain", "ACTUATOR", "Configuration version 1 verified."),
    ])

    assert init_db._ensure_blocked_by_guard_column(cur) is True      # column was new
    init_db._backfill_blocked_by_guard(cur)
    marked = {r[0] for r in cur.execute("SELECT incident_id FROM incidents WHERE blocked_by_guard = 1")}
    assert marked == {"sql", "json", "unres"}

    assert init_db._ensure_blocked_by_guard_column(cur) is False     # second start: nothing to do
    conn.close()


def test_initialize_database_adds_column_to_an_existing_database(client):
    # the shared test database already has the column; dropping it is not possible in old SQLite,
    # so check the real table has it and new rows default to "not blocked"
    with get_db_connection("state_db") as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(incidents)").fetchall()]
    assert "blocked_by_guard" in cols
    _make_incident(99, 0, blocked=False)
    assert IncidentRepository.get_incident("inc_blk_99").blocked_by_guard is False
