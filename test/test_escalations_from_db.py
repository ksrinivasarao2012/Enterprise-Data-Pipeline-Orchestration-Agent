"""
Escalations are read from the database (CHANGES.md section 20).

Before, the Escalations tab read a text file. On Vercel that file lived in /tmp, which is
wiped and is different on every server copy, so escalations could vanish and the
Escalations tab could disagree with the Incident log. Now it lists incidents whose status
is ESCALATED, straight from the incidents table.
"""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ids(rows):
    return [r["incident_id"] for r in rows]


def test_escalations_tab_matches_the_incident_log(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})  # healed, not listed
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    esc = client.get("/api/escalations").json()
    log = client.get("/api/incidents?outcome=escalated").json()
    assert len(esc) == 2 and _ids(esc) == _ids(log), "the two tabs must list the same incidents"
    assert esc[0]["summary"].startswith("The column 'status_code' is missing"), "newest first"
    assert esc[1]["reason"].startswith("Operational source table")  # raw error still there


def test_no_text_file_is_written(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    places = [os.path.join(ROOT, "escalated_incidents.log"), os.path.join(os.environ["EHA_DB_DIR"], "escalated_incidents.log")]
    before = {p: os.path.getmtime(p) if os.path.exists(p) else None for p in places}
    client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})
    assert len(client.get("/api/escalations").json()) == 1
    after = {p: os.path.getmtime(p) if os.path.exists(p) else None for p in places}
    assert before == after, "nothing should be written to an escalations file any more"


def test_heal_cap_escalations_are_listed_too(client, fake_llm):
    # Escalated without any agent running (too many incidents in one run) -> still listed.
    fake_llm("UNRESOLVED")
    from src.api.main import run_agentic_healing_workflow
    from src.incidents.incident_manager import IncidentManager
    from src.services.pipeline_service import PipelineService
    from src.services.remediation_service import RemediationService
    PipelineService.start_run("run_cap_esc", "PIPELINE_B")
    incs = [IncidentManager.initialize_incident("run_cap_esc", "PIPELINE_B", "ValueError", "boom", "t")
            for _ in range(RemediationService.MAX_HEAL_ATTEMPTS + 1)]
    run_agentic_healing_workflow(incs[-1].incident_id)
    assert incs[-1].incident_id in _ids(client.get("/api/escalations").json())


def test_clear_history_clears_escalations(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})
    assert client.get("/api/escalations").headers["X-Total-Count"] == "1"
    client.post("/api/history/clear")
    assert client.get("/api/escalations").json() == []


def test_every_server_copy_sees_the_same_escalations():
    pytest.importorskip("requests")
    pytest.importorskip("uvicorn")
    import requests
    from test_lockout_and_shared_limits import ADMIN, Copies
    copies = Copies()
    try:
        a, _ = copies.start()
        b, _ = copies.start()
        requests.post(a + "/api/pipelines/b/execute", headers={"X-Access-Key": ADMIN},
                      data={"test_case": "missing_table.db"}, timeout=60)
        on_b = requests.get(b + "/api/escalations", headers={"X-Access-Key": ADMIN}, timeout=10).json()
        assert len(on_b) == 1 and on_b[0]["summary"] == "The source table 'device_telemetry' does not exist."
    finally:
        copies.close()
