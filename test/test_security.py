"""
Security tests (fixes 2-7 in CHANGES.md).

2. File path trick      - only real test file names are accepted
3. Script injection     - uploaded data shows as plain text in the dashboard (real browser test)
4. Upload size limit    - big uploads are refused before they are read
5. JSON repair check    - Gemini may fix brackets/commas, but may not change the data
6. Gemini warning       - the dashboard tells users broken files are sent to Gemini
7. SQL word list        - replaced: harmless names allowed, only one SELECT allowed
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from src.database import get_db_connection
from src.services.remediation_service import RemediationService

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTHY = json.load(open(os.path.join(ROOT, "test_data", "pipeline_A", "case_healthy.json")))


def _count(sql, db="state_db"):
    with get_db_connection(db) as conn:
        return conn.execute(sql).fetchone()[0]


def _broken(rows):
    """Valid data with one comma removed between two rows -> a pure syntax error."""
    text = "[\n" + ",\n".join(json.dumps(r) for r in rows) + "\n]"
    return text.replace("},\n", "}\n", 1)


# ---------------------------------------------------------------- 2. file path trick

@pytest.mark.parametrize("bad_name", ["/etc/hostname", "../../.env", "../pipeline_B/healthy.db",
                                      "..\\..\\.env", "case_healthy.json/../../.env", ""])
def test_pipeline_a_rejects_anything_but_a_real_test_file(client, fake_llm, bad_name):
    calls = fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/a/execute", data={"test_case": bad_name})
    assert r.status_code in (400, 404)
    assert _count("SELECT COUNT(*) FROM pipeline_runs") == 0, "the file must not even be opened"
    assert calls == [], "nothing may be sent to Gemini"


@pytest.mark.parametrize("bad_name", ["/etc/hostname", "../pipeline_A/case_healthy.json", "../../database/platform_state.db"])
def test_pipeline_b_rejects_anything_but_a_real_test_file(client, fake_llm, bad_name):
    fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/b/execute", data={"test_case": bad_name})
    assert r.status_code in (400, 404)
    assert _count("SELECT COUNT(*) FROM pipeline_runs") == 0


def test_real_test_file_names_still_work(client, fake_llm):
    fake_llm("UNRESOLVED")
    assert client.post("/api/pipelines/a/execute", data={"test_case": "case_healthy.json"}).json()["status"] == "success"
    assert client.post("/api/pipelines/b/execute", data={"test_case": "healthy.db"}).json()["status"] == "success"


# ---------------------------------------------------------------- 4. upload size limit

def test_big_upload_is_refused(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.main as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1000)
    big = json.dumps(HEALTHY * 5)  # > 1000 bytes
    for pipe in ("a", "b"):
        r = client.post(f"/api/pipelines/{pipe}/execute", files={"file": ("big.json", big, "application/json")})
        assert r.status_code == 413
    assert _count("SELECT COUNT(*) FROM pipeline_runs") == 0


def test_normal_upload_still_works(client, fake_llm):
    fake_llm("UNRESOLVED")
    r = client.post("/api/pipelines/a/execute", files={"file": ("ok.json", json.dumps(HEALTHY), "application/json")})
    assert r.json()["status"] == "success"


# ---------------------------------------------------------------- 5. JSON repair must not change the data

def _upload_broken(client):
    return client.post("/api/pipelines/a/execute",
                       files={"file": ("broken.json", _broken(HEALTHY), "application/json")}).json()


def test_honest_repair_is_healed(client, fake_llm):
    fake_llm(json.dumps(HEALTHY, indent=2))  # only the missing comma is fixed
    r = _upload_broken(client)
    assert r.get("healed") is True
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 10


def test_repair_that_changes_a_value_is_blocked(client, fake_llm):
    tampered = [dict(row) for row in HEALTHY]
    tampered[0]["email"] = "attacker@evil.com"   # e.g. hidden instructions in the file told Gemini to do this
    fake_llm(json.dumps(tampered))
    r = _upload_broken(client)
    assert r["status"] == "failure"
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 0
    assert _count("SELECT COUNT(*) FROM incidents WHERE status = 'ESCALATED'") == 1


def test_repair_that_adds_a_row_is_blocked(client, fake_llm):
    fake_llm(json.dumps(HEALTHY + [{"customer_id": "x", "name": "x", "email": "x@x.com", "country": "x"}]))
    assert _upload_broken(client)["status"] == "failure"
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 0


def test_repair_that_invents_a_missing_value_is_blocked(client, fake_llm):
    # case_malformed_syntax.json row 5 is missing its closing bracket AND its country.
    # A "repair" that invents the country is fake data, so it must go to a person.
    raw = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
    invented = raw.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com", "country": "Japan"},\n')
    json.loads(invented)  # sanity: this "repair" is valid JSON
    fake_llm(invented)
    r = client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"}).json()
    assert r["status"] == "failure"
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 0


# ---------------------------------------------------------------- 6. Gemini warning

def test_dashboard_warns_that_broken_files_go_to_gemini():
    html = open(os.path.join(ROOT, "public", "index.html"), encoding="utf-8").read()
    assert 'id="gemini-warning"' in html
    assert "Google Gemini" in html


# ---------------------------------------------------------------- 7. SQL word list replaced

def test_harmless_sql_column_names_are_allowed():
    assert RemediationService.validate_fix_schema(
        "PIPELINE_B", {"sql_override": "SELECT device_id, MAX(last_updated), SUM(deleted_flag) FROM t GROUP BY device_id"})


def test_harmless_json_column_names_are_allowed():
    assert RemediationService.validate_fix_schema("PIPELINE_A", {"email_col": "updated_email"})
    assert RemediationService.validate_fix_schema("PIPELINE_A", {"country_col": "selected_country"})


@pytest.mark.parametrize("sql", ["ATTACH DATABASE 'other.db' AS o", "DELETE FROM device_telemetry",
                                 "SELECT 1; DROP TABLE device_telemetry", "PRAGMA table_info(x)", "not sql ((("])
def test_anything_but_one_select_is_blocked(sql):
    assert not RemediationService.validate_fix_schema("PIPELINE_B", {"sql_override": sql})


def test_sql_in_a_column_name_is_still_blocked():
    assert not RemediationService.validate_fix_schema("PIPELINE_A", {"country_col": "select * from users"})


# ---------------------------------------------------------------- 3. script injection (real browser)

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uploaded_code_is_shown_as_text_not_run():
    """Starts the real server, uploads a file with a script hidden in the email field,
    opens the dashboard in Chromium and checks the script never runs."""
    playwright = pytest.importorskip("playwright.sync_api")
    import requests

    port = _free_port()
    env = dict(os.environ, EHA_DB_DIR=tempfile.mkdtemp(prefix="eha_xss_"), PYTHONPATH=ROOT, EHA_UI="legacy")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.api.main:app", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if requests.get(f"{base}/api/metrics", timeout=1).ok:
                    break
            except Exception:
                time.sleep(0.2)

        payload = '<img src=x onerror="window.__hacked=1">@evil.com'
        rows = [dict(r) for r in HEALTHY]
        rows[0]["email"] = "not-an-email " + payload.replace("@evil.com", "")  # fails validation -> quarantined
        rows[1]["name"] = payload
        requests.post(f"{base}/api/pipelines/a/execute",
                      files={"file": ("xss.json", json.dumps(rows), "application/json")}, timeout=60)

        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(base)
            page.wait_for_selector("#quarantine-body td:has-text('qnt_')", timeout=15000)
            page.wait_for_timeout(1500)
            hacked = page.evaluate("window.__hacked")
            quarantine_text = page.inner_text("#quarantine-body")
            browser.close()

        assert hacked is None, "the script hidden in the uploaded data ran in the browser"
        assert "<img" in quarantine_text, "the data should be visible as plain text"
    finally:
        server.terminate()
        server.wait(timeout=10)
