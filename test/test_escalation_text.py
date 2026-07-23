"""
Escalations in plain words (CHANGES.md section 16): each escalation gets a short
"what went wrong" and "why a person is needed"; the raw technical error is kept.
"""
from src.incidents.escalation_text import explain

SQL_ERR = ("Database Query Parse Error: no such column: status_code. Available columns in device_telemetry: "
           "['log_id', 'device_id']. Query: SELECT device_id, COUNT(log_id) FROM device_telemetry GROUP BY device_id")


def test_missing_column():
    e = explain(SQL_ERR)
    assert e["summary"] == "The column 'status_code' is missing from the table 'device_telemetry'."
    assert "SELECT" not in e["summary"] + e["why"]


def test_sql_guard_block_is_the_why():
    guard = ("SQL guard rejected the LLM repair: 'error_rate' needed missing column(s) ['status_code'] and the repair "
             "replaced them with a constant. Escalating instead of running it.")
    e = explain(SQL_ERR, recovery_action=guard)
    assert e["why"].startswith("A safety check blocked the AI fix: 'error_rate' needed missing column(s) status_code")
    assert "Escalating instead" not in e["why"]


def test_unresolvable_means_gemini_refused_to_guess():
    e = explain(SQL_ERR, recovery_action="LLM reported the SQL error as UNRESOLVABLE (required column has no equivalent).")
    assert "refused to guess" in e["why"]


def test_broken_json_hides_the_error_signature():
    raw = ("[ErrorSignature: PIPELINE_A|JSONDecodeError|malformed_syntax|7af2047ff05ce062] Invalid JSON Syntax: "
           "Expecting ',' delimiter: line 7 column 3 (char 527)")
    e = explain(raw, audit_messages=["LLM file repair rejected: the repaired file is still not valid JSON. The file was not changed."])
    assert e["summary"] == "The JSON file is broken near line 7 (a bracket, comma or value is missing)."
    assert e["why"] == "A safety check blocked the AI fix: the repaired file is still not valid JSON."
    assert "ErrorSignature" not in e["summary"]


def test_missing_table_and_unmatched_column():
    assert explain("Operational source table 'device_telemetry' does not exist on disk.")["summary"] == \
        "The source table 'device_telemetry' does not exist."
    e = explain("[ErrorSignature: PIPELINE_A|KeyError|country|abc] Expected: ['country'] | Found: ['zzz']")
    assert e["summary"] == "The column 'country' is missing and no matching column was found."


def test_unknown_error_is_kept_short():
    e = explain("Weird failure " + "x" * 400 + " Query: SELECT 1")
    assert len(e["summary"]) <= 165 and "SELECT" not in e["summary"]


def test_api_returns_plain_words_and_keeps_the_raw_error(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})
    esc = client.get("/api/escalations").json()[0]  # newest first
    assert esc["summary"] == "The source table 'device_telemetry' does not exist."
    assert esc["why"] and esc["reason"].startswith("Operational source table")

