"""
Login (authentication) and permissions (authorization) tests. See CHANGES.md section 7.

Roles:
    viewer (e.g. an interviewer) - see everything, run presets, upload own files (smaller limit, run limit)
    admin  (you)                 - everything, including Clear History and the telemetry endpoints

Keys come from EHA_VIEWER_KEY / EHA_ADMIN_KEY and are sent in the X-Access-Key header.
No keys set: open locally (development), fully locked on Vercel.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTHY = json.load(open(os.path.join(ROOT, "test_data", "pipeline_A", "case_healthy.json")))

ADMIN = "admin-test-key-123"
VIEWER = "viewer-test-key-456"
READ_ENDPOINTS = ["/api/metrics", "/api/incidents", "/api/quarantine", "/api/escalations", "/api/incidents/x/audit"]


@pytest.fixture
def keys_on(monkeypatch):
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN)
    monkeypatch.setenv("EHA_VIEWER_KEY", VIEWER)


def h(key):
    return {"X-Access-Key": key}


def run_preset(client, key=None):
    return client.post("/api/pipelines/a/execute", data={"test_case": "case_healthy.json"},
                       headers=h(key) if key else {})


# ---------------------------------------------------------------- no key / wrong key

def test_no_key_is_refused_everywhere(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    for url in READ_ENDPOINTS:
        assert client.get(url).status_code == 401, url
    assert run_preset(client).status_code == 401
    assert client.post("/api/history/clear").status_code == 401
    assert client.post("/telemetry/incident", json={}).status_code == 401


def test_wrong_key_is_refused(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    assert client.get("/api/metrics", headers=h("guess")).status_code == 401
    assert run_preset(client, "guess").status_code == 401


# ---------------------------------------------------------------- viewer

def test_viewer_can_see_everything(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    for url in READ_ENDPOINTS:
        assert client.get(url, headers=h(VIEWER)).status_code == 200, url


def test_viewer_can_run_presets_and_upload(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    assert run_preset(client, VIEWER).json()["status"] == "success"
    r = client.post("/api/pipelines/a/execute", headers=h(VIEWER),
                    files={"file": ("mine.json", json.dumps(HEALTHY), "application/json")})
    assert r.json()["status"] == "success"


def test_viewer_cannot_clear_history(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    run_preset(client, VIEWER)
    assert client.post("/api/history/clear", headers=h(VIEWER)).status_code == 403
    from src.database import get_db_connection
    with get_db_connection("state_db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 1, "nothing may be deleted"


def test_viewer_cannot_use_telemetry_endpoints(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    assert client.post("/telemetry/incident", json={}, headers=h(VIEWER)).status_code == 403
    assert client.post("/runs/register", json={}, headers=h(VIEWER)).status_code == 403


def test_viewer_upload_limit_is_smaller(client, fake_llm, keys_on, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.main as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 5000)
    monkeypatch.setattr(api, "VIEWER_MAX_UPLOAD_BYTES", 1000)
    body = json.dumps(HEALTHY)  # about 1 KB+, between the two limits
    assert 1000 < len(body) < 5000
    up = lambda key: client.post("/api/pipelines/a/execute", headers=h(key),
                                 files={"file": ("f.json", body, "application/json")})
    assert up(VIEWER).status_code == 413
    assert up(ADMIN).json()["status"] == "success"


def test_viewer_run_limit(client, fake_llm, keys_on, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    assert run_preset(client, VIEWER).status_code == 200
    assert run_preset(client, VIEWER).status_code == 200
    assert run_preset(client, VIEWER).status_code == 429
    # reading is never limited, and the admin is never limited
    assert client.get("/api/metrics", headers=h(VIEWER)).status_code == 200
    assert run_preset(client, ADMIN).status_code == 200
    auth.reset_rate_limit()


# ---------------------------------------------------------------- admin

def test_admin_can_do_everything(client, fake_llm, keys_on):
    fake_llm("UNRESOLVED")
    for url in READ_ENDPOINTS:
        assert client.get(url, headers=h(ADMIN)).status_code == 200, url
    assert run_preset(client, ADMIN).json()["status"] == "success"
    assert client.post("/api/history/clear", headers=h(ADMIN)).status_code == 200


def test_whoami_reports_the_role(client, keys_on):
    assert client.get("/api/whoami", headers=h(VIEWER)).json() == {"role": "viewer", "auth_enabled": True}
    assert client.get("/api/whoami", headers=h(ADMIN)).json() == {"role": "admin", "auth_enabled": True}
    assert client.get("/api/whoami").status_code == 401


# ---------------------------------------------------------------- no keys configured

def test_no_keys_locally_means_open_mode(client, fake_llm):
    fake_llm("UNRESOLVED")
    assert client.get("/api/whoami").json() == {"role": "admin", "auth_enabled": False}
    assert run_preset(client).json()["status"] == "success"


def test_no_keys_on_vercel_means_locked(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    monkeypatch.setenv("VERCEL", "1")
    assert client.get("/api/metrics").status_code == 401
    assert run_preset(client).status_code == 401
    assert client.post("/api/history/clear").status_code == 401


def test_only_admin_key_set_viewers_are_refused(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN)
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers=h("")).status_code == 401
    assert client.get("/api/metrics", headers=h(ADMIN)).status_code == 200


# ---------------------------------------------------------------- dashboard stays loadable

def test_dashboard_page_itself_is_public(client, keys_on):
    # the page itself contains no data, so the login box can be shown (old or new dashboard)
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


# ---------------------------------------------------------------- real browser: login box

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_login_box_in_real_browser():
    """Old HTML dashboard (public/): starts the server with keys set, opens it in Chromium, logs in as viewer."""
    playwright = pytest.importorskip("playwright.sync_api")
    import requests

    port = _free_port()
    env = dict(os.environ, EHA_DB_DIR=tempfile.mkdtemp(prefix="eha_auth_"), PYTHONPATH=ROOT,
               EHA_ADMIN_KEY=ADMIN, EHA_VIEWER_KEY=VIEWER, EHA_UI="legacy")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "src.api.main:app", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                requests.get(f"{base}/", timeout=1)
                break
            except Exception:
                time.sleep(0.2)

        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(base)

            # 1. Login box is shown, data is not loaded
            page.wait_for_selector("#login-overlay", state="visible", timeout=10000)

            # 2. Wrong key -> error, box stays
            page.fill("#login-key", "wrong")
            page.click("#login-submit")
            page.wait_for_selector("#login-error:has-text('Wrong key')", timeout=10000)

            # 3. Viewer key -> dashboard loads, Clear History hidden, role shown
            page.fill("#login-key", VIEWER)
            page.click("#login-submit")
            page.wait_for_selector("#login-overlay", state="hidden", timeout=10000)
            page.wait_for_selector("#role-badge:has-text('viewer')", timeout=10000)
            assert not page.is_visible("#btn-clear-history")

            # 4. Viewer can run a preset from the dashboard
            page.select_option("#preset-pipeline-a", "case_healthy.json")
            page.click("#btn-pipeline-a")
            page.wait_for_selector("#result-a:has-text('successful')", timeout=20000)

            # 5. Log out -> login box again
            page.click("#btn-logout")
            page.wait_for_selector("#login-overlay", state="visible", timeout=10000)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


# ---------------------------------------------------------------- "Try all" demo (CHANGES.md section 14)

def _demo(client, key):
    return client.post("/api/demo/start", headers=h(key)).json()


def test_try_all_counts_as_one_run(client, fake_llm, keys_on, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    demo = _demo(client, VIEWER)
    assert len(demo["presets"]) == 13
    dh = {**h(VIEWER), "X-Demo-Token": demo["token"]}
    for p in demo["presets"]:  # 13 runs, far above the limit of 2
        r = client.post(f"/api/pipelines/{p['pipeline']}/execute", headers=dh, data={"test_case": p["test_case"]})
        assert r.status_code == 200, (p, r.text)
    # the demo itself used 1 of the 2 runs, so exactly one normal run is left
    assert run_preset(client, VIEWER).status_code == 200
    assert run_preset(client, VIEWER).status_code == 429
    auth.reset_rate_limit()


def test_demo_token_cannot_be_reused_or_faked(client, fake_llm, keys_on, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    dh = {**h(VIEWER), "X-Demo-Token": _demo(client, VIEWER)["token"]}   # 1 run used
    preset = lambda headers: client.post("/api/pipelines/a/execute", headers=headers, data={"test_case": "case_healthy.json"})
    assert preset(dh).status_code == 200      # free (part of the demo)
    assert preset(dh).status_code == 200      # same preset again: counted -> 2 of 2 used
    assert preset(dh).status_code == 429      # so now blocked
    fake = {**h(VIEWER), "X-Demo-Token": "made-up"}
    assert preset(fake).status_code == 429
    # uploads are never free, even with a valid token
    auth.reset_rate_limit()
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 1)
    dh = {**h(VIEWER), "X-Demo-Token": _demo(client, VIEWER)["token"]}   # uses the only run
    up = client.post("/api/pipelines/a/execute", headers=dh,
                     files={"file": ("x.json", json.dumps(HEALTHY), "application/json")})
    assert up.status_code == 429
    auth.reset_rate_limit()


def test_demo_needs_a_key(client, keys_on):
    assert client.post("/api/demo/start").status_code == 401
