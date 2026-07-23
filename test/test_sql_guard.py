"""
Unit tests for the SQL guard (src/agents/sql_guard.py).

Purpose: Gemini is TOLD not to invent values when it repairs SQL, but an LLM does
not always obey. The guard checks the repaired query itself, before it runs.
"""
from src.agents.sql_guard import check_sql_repair, extract_repair_context

ORIGINAL = """
    SELECT
        device_id,
        COUNT(log_id) as total_events,
        SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) as error_rate,
        AVG(response_time) as avg_response_time
    FROM device_telemetry
    GROUP BY device_id
"""
# status_code is missing from this table
AVAILABLE = ["log_id", "device_id", "timestamp", "response_time"]
# status_code was renamed to http_status in this table
AVAILABLE_RENAMED = ["log_id", "device_id", "timestamp", "http_status", "response_time"]
# approved replacement names (in the app these come from configs/settings.yaml)
ALIASES = {"status_code": ["http_status", "status", "response_code"]}

RENAMED = ORIGINAL.replace("status_code", "http_status")


def check(fixed, available=AVAILABLE, aliases=ALIASES):
    return check_sql_repair(ORIGINAL, fixed, available, table="device_telemetry", column_aliases=aliases)


# ------------------------------------------------------------ must be REJECTED

def test_rejects_constant_in_place_of_missing_column():
    ok, reason = check("""SELECT device_id, COUNT(log_id) AS total_events,
        SUM(CASE WHEN 0 >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) AS error_rate,
        AVG(response_time) AS avg_response_time FROM device_telemetry GROUP BY device_id""")
    assert not ok and "constant" in reason


def test_rejects_null_in_place_of_missing_column():
    ok, _ = check("""SELECT device_id, COUNT(log_id), NULL, AVG(response_time)
        FROM device_telemetry GROUP BY device_id""")
    assert not ok


def test_rejects_zero_literal_output():
    ok, _ = check("""SELECT device_id, COUNT(log_id), 0.0 AS error_rate, AVG(response_time)
        FROM device_telemetry GROUP BY device_id""")
    assert not ok


def test_rejects_dropped_output():
    ok, reason = check("SELECT device_id, COUNT(log_id), AVG(response_time) FROM device_telemetry GROUP BY device_id")
    assert not ok and "outputs" in reason


def test_rejects_unknown_column():
    ok, reason = check("""SELECT device_id, COUNT(log_id), SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id),
        AVG(response_time) FROM device_telemetry GROUP BY device_id""")
    assert not ok and "status" in reason


def test_rejects_delete():
    ok, _ = check("DELETE FROM device_telemetry")
    assert not ok


def test_rejects_attach():
    ok, _ = check("ATTACH DATABASE 'other.db' AS other")
    assert not ok


def test_rejects_two_statements():
    ok, _ = check("SELECT 1; DROP TABLE device_telemetry")
    assert not ok


def test_rejects_other_table():
    ok, reason = check("""SELECT device_id, COUNT(log_id), SUM(status_code), AVG(response_time)
        FROM secret_table GROUP BY device_id""", AVAILABLE_RENAMED)
    assert not ok and "table" in reason


def test_rejects_garbage():
    ok, _ = check("this is not sql at all (((")
    assert not ok


# ------------------------------------------------------------ must be ACCEPTED

def test_accepts_real_rename():
    ok, reason = check("""SELECT device_id, COUNT(log_id) AS total_events,
        SUM(CASE WHEN http_status >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) AS error_rate,
        AVG(response_time) AS avg_response_time FROM device_telemetry GROUP BY device_id""", AVAILABLE_RENAMED)
    assert ok, reason


def test_accepts_rename_with_different_spacing_and_capitals():
    ok, reason = check(RENAMED.replace("as ", "AS ").replace("\n", " "), AVAILABLE_RENAMED)
    assert ok, reason


# ------------------------------------------------------------ rename-only rule

def test_rejects_times_zero_trick():
    # uses a real column, but "* 0" makes every error rate 0 -> fake data
    ok, reason = check(ORIGINAL.replace("status_code >= 400", "http_status * 0 >= 400"), AVAILABLE_RENAMED)
    assert not ok and "rename" in reason


def test_rejects_any_other_formula_change():
    ok, _ = check(ORIGINAL.replace("status_code >= 400", "http_status >= 500"), AVAILABLE_RENAMED)
    assert not ok


def test_rejects_extra_order_by():
    ok, _ = check(RENAMED + " ORDER BY error_rate DESC", AVAILABLE_RENAMED)
    assert not ok


def test_rejects_real_but_wrong_column():
    # 'timestamp' exists, but it is not an approved name for status_code
    ok, reason = check(ORIGINAL.replace("status_code", "timestamp"), AVAILABLE_RENAMED)
    assert not ok and "approved" in reason


def test_rejects_rename_when_no_aliases_configured():
    ok, _ = check(RENAMED, AVAILABLE_RENAMED, aliases={})
    assert not ok


def test_known_false_alarm_dropped_label_is_rejected():
    # a correct rename that also drops "as error_rate" is blocked: safe, goes to a human
    ok, _ = check(RENAMED.replace(" as error_rate", ""), AVAILABLE_RENAMED)
    assert not ok


# ------------------------------------------------------------ reading the error message

def test_extracts_query_and_columns_from_pipeline_error():
    msg = ("Database Query Parse Error: no such column: status_code. "
           "Available columns in device_telemetry: ['log_id', 'device_id', 'timestamp', 'response_time']. "
           "Query: SELECT device_id FROM device_telemetry")
    ctx = extract_repair_context(msg)
    assert ctx["table"] == "device_telemetry"
    assert ctx["available"] == AVAILABLE
    assert ctx["original"].strip() == "SELECT device_id FROM device_telemetry"
