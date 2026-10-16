"""
The branching agent graph (CHANGES.md section 23): one test per preset.

For each of the 14 presets this checks the three things agreed before building:
    1. the exact path through the graph (which agents ran, in order, including the loop),
    2. how many times Gemini was called,
    3. the final result.

Rule behind the paths: try safe rules first; call Gemini only when no rule knows the fix,
or to explain a failure to the person who takes over.
"""
import json
import os

import pytest

from src.database import get_db_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROKEN = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
# An honest repair: only the missing "}," is added. Yuki's row still has no country.
BRACKET_ONLY = BROKEN.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com"},\n')

T, M, C, RCA, R, A, E = ("TELEMETRY_RECEIVER", "MONITOR_AGENT", "CLASSIFIER_AGENT", "RCA_AGENT",
                         "RECOVERY_AGENT", "ACTUATOR", "ESCALATION")
AI = "AI_FIX_AGENT"
UNWRAP = '{"steps": [{"op": "unwrap", "key": "customers"}], "reason": "the records are under customers"}'
START = [T, M, C, C]  # the Classifier writes two lines: severity, then the route it chose

# (pipeline, preset, Gemini's reply, expected path, Gemini calls, expected result)
CASES = [
    ("a", "case_healthy.json", "", None, 0, "success"),
    ("a", "case_drift_country.json", "", START + [R, A, A], 0, "healed"),
    ("a", "case_drift_email.json", "", START + [R, A, A], 0, "healed"),
    ("a", "case_drift_id.json", "", START + [R, A, A], 0, "healed"),
    ("a", "case_drift_multi.json", "", START + [R, A, A, R, A, A], 0, "healed"),  # the loop
    ("a", "case_invalid_root.json", "", START + [R, A, A], 0, "healed"),
    ("a", "case_malformed_syntax.json", BRACKET_ONLY, START + [RCA, R, A, A, A], 2, "healed_with_quarantine"),
    ("a", "case_wrapped_records.json", UNWRAP, START + [R, A, A, AI, AI, A, A], 1, "healed"),  # rule fails -> AI fix
    ("a", "case_invalid_email.json", "", None, 0, "quarantined"),
    ("a", "case_duplicate_ids.json", "", None, 0, "quarantined"),
    ("b", "healthy.db", "", None, 0, "success"),
    ("b", "sql_error.db", "UNRESOLVABLE", START + [RCA, R, E], 2, "human"),
    ("b", "referential_integrity.db", "", START + [R, A, A], 0, "quarantined_all"),
    ("b", "missing_table.db", "", START + [RCA, E], 1, "human"),
]


def _count(sql, db="state_db"):
    with get_db_connection(db) as conn:
        return conn.execute(sql).fetchone()[0]


def _agent_incident(client):
    """The incident the agents worked on (not the automatic quarantine warning)."""
    incs = [i for i in client.get("/api/incidents").json() if i["error_class"] != "DataQualityAnomaly"]
    return incs[0] if incs else None


@pytest.mark.parametrize("pipe,case,reply,path,gemini_calls,result", CASES, ids=[c[1] for c in CASES])
def test_preset_path_calls_and_result(client, fake_llm, pipe, case, reply, path, gemini_calls, result):
    calls = fake_llm(reply or "UNRESOLVED")
    r = client.post(f"/api/pipelines/{pipe}/execute", data={"test_case": case}).json()

    # 1. path
    inc = _agent_incident(client)
    if path is None:
        assert inc is None, "no agent should run for this case"
    else:
        audit = client.get(f"/api/incidents/{inc['incident_id']}/audit").json()
        assert [a["component"] for a in audit] == path, [(a["component"], a["message"][:60]) for a in audit]

    # 2. Gemini calls
    assert len(calls) == gemini_calls

    # 3. result
    rows = _count("SELECT COUNT(*) FROM customers", "operational_db")
    held = _count("SELECT COUNT(*) FROM quarantined_records")
    if result == "success":
        assert r["status"] == "success" and not r.get("healed") and not r.get("quarantined")
    elif result == "healed":
        assert r.get("healed") is True and inc["status"] == "RESOLVED" and rows == 10 and held == 0
    elif result == "healed_with_quarantine":
        assert r.get("healed") is True and inc["status"] == "RESOLVED"
        assert rows == 9 and held == 1, "9 rows loaded, Yuki's row (no country) held back"
    elif result == "quarantined":
        assert r.get("quarantined") is True and rows == 9 and held == 1
    elif result == "quarantined_all":
        assert r.get("quarantined") is True and r.get("rows_ingested") == 0
    elif result == "human":
        assert r["status"] == "failure" and inc["status"] == "ESCALATED"
        assert inc["root_cause"], "RCA explained it for the person who takes over"


def test_the_classifier_says_which_way_it_routed(client, fake_llm):
    fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})
    inc = _agent_incident(client)
    msgs = [a["message"] for a in client.get(f"/api/incidents/{inc['incident_id']}/audit").json()]
    assert any(m.startswith("Route: Recovery (no Gemini needed)") for m in msgs), msgs


def test_the_loop_says_why_it_went_back(client, fake_llm):
    fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_multi.json"})
    inc = _agent_incident(client)
    msgs = [a["message"] for a in client.get(f"/api/incidents/{inc['incident_id']}/audit").json()]
    assert "Verification run 1 found another drift: column 'email' is missing. Back to Recovery." in msgs


# ---------------------------------------------------------------- Recovery uses RCA

def test_recovery_prompts_include_the_rca_analysis(client, fake_llm):
    calls = fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    sql_prompt = str(calls[-1])
    assert "Root cause analysis from the RCA agent" in sql_prompt and "fake rca" in sql_prompt

    calls = fake_llm(BRACKET_ONLY)
    client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"})
    repair_prompt = str(calls[-1])
    assert "fake rca" in repair_prompt
    assert "Never add, remove or change any key or value" in repair_prompt


def test_gemini_inventing_the_missing_country_still_goes_to_a_person(client, fake_llm):
    invented = BROKEN.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com", "country": "Japan"},\n')
    fake_llm(invented)
    r = client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"}).json()
    assert r["status"] == "failure"
    assert _agent_incident(client)["status"] == "ESCALATED"
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 0


# ---------------------------------------------------------------- missing field: some rows vs every row

def _upload(client, rows):
    return client.post("/api/pipelines/a/execute",
                       files={"file": ("f.json", json.dumps(rows), "application/json")}).json()


def test_field_missing_in_some_rows_quarantines_those_rows(client, fake_llm):
    calls = fake_llm("UNRESOLVED")
    rows = [{"customer_id": str(i), "name": "n", "email": f"u{i}@x.com", "country": "IN"} for i in range(5)]
    del rows[2]["country"]
    r = _upload(client, rows)
    assert r.get("quarantined") is True and r["rows_ingested"] == 4
    with get_db_connection("state_db") as conn:
        errors = conn.execute("SELECT validation_errors FROM quarantined_records").fetchone()[0]
    assert "Missing field 'country'" in errors
    assert calls == [], "a row problem is not schema drift: no agents"


def test_field_missing_in_every_row_is_still_schema_drift(client, fake_llm):
    fake_llm("UNRESOLVED")
    rows = [{"customer_id": str(i), "name": "n", "email": f"u{i}@x.com", "nation": "IN"} for i in range(5)]
    r = _upload(client, rows)
    assert r.get("healed") is True  # 'nation' is a known alias for country
    assert _agent_incident(client)["error_class"] == "KeyError"


# ---------------------------------------------------------------- newer Gemini reply shape (section 29)

def test_reply_text_works_for_old_and_new_gemini_models():
    from types import SimpleNamespace
    from src.config import llm_text
    assert llm_text(SimpleNamespace(content="OK")) == "OK"  # gemini-2.5: a string
    new = [{"type": "text", "text": "SELECT 1", "extras": {"signature": "abc"}}, {"type": "thinking", "thinking": "x"}]
    assert llm_text(SimpleNamespace(content=new)) == "SELECT 1"  # gemini-3.x: a list of blocks
