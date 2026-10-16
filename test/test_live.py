"""
Live agent flow (CHANGES.md section 9).

Purpose: the dashboard should show each agent step WHILE a run is healing, not only
after it has finished. That needs two things, both tested here with a real server and
a fake Gemini that takes a few seconds to answer (like a real call):

1. The API keeps answering during a run (before, the whole server froze until the run ended).
2. The React dashboard lights up agent steps while the run is still in progress.
"""
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

import pytest

from conftest import ADMIN_EMAIL, browser_sign_in, server_env_for_google

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist", "index.html")
ADMIN = "admin-live-key"
DELAY = 8  # seconds the fake Gemini takes for the RCA step (long enough for slow machines)

SERVER = textwrap.dedent('''
    import os, sys, time, importlib.util
    root, port, delay = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    os.chdir(root); sys.path.insert(0, root)
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    import langchain_google_genai
    def slow_rca(schema):
        def run(_):
            time.sleep(delay)
            return schema(root_cause_analysis="slow fake rca", confidence_score=0.5)
        return run
    # Broken JSON goes through RCA (slow here) and then a Gemini repair that adds only the missing "},".
    raw = open(os.path.join(root, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
    FIXED = raw.replace('"yuki.tanaka@example.com"\\n', '"yuki.tanaka@example.com"},\\n')
    class Fake(RunnableLambda):
        def __init__(self):
            super().__init__(lambda p: AIMessage(content=FIXED if "JSON syntax repair" in str(p) else "UNRESOLVED"))
        def with_structured_output(self, s):
            return RunnableLambda(slow_rca(s))
    f = lambda *a, **k: Fake()
    import src.agents.rca as r, src.agents.recovery as rc
    r.ChatGoogleGenerativeAI = f; rc.ChatGoogleGenerativeAI = f; langchain_google_genai.ChatGoogleGenerativeAI = f
    spec = importlib.util.spec_from_file_location("i", os.path.join(root, "database", "init_db.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); m.initialize_database()
    import uvicorn
    from src.api.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
''')


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    requests = pytest.importorskip("requests")
    pytest.importorskip("uvicorn")  # the test starts a real server
    port = _free_port()
    script = os.path.join(tempfile.mkdtemp(), "server.py")
    open(script, "w").write(SERVER)
    env = dict(os.environ, EHA_DB_DIR=tempfile.mkdtemp(prefix="eha_live_"), PYTHONPATH=ROOT,
               EHA_ADMIN_KEY=ADMIN, EHA_VIEWER_KEY="", EHA_UI="", GOOGLE_API_KEY="x", **server_env_for_google())
    proc = subprocess.Popen([sys.executable, script, ROOT, str(port), str(DELAY)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            requests.get(base + "/api/whoami", headers={"X-Access-Key": ADMIN}, timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def _start_run(base, test_case="case_malformed_syntax.json"):  # a case that calls Gemini (RCA)
    import requests
    out = {}

    def go():
        out["result"] = requests.post(base + "/api/pipelines/a/execute", headers={"X-Access-Key": ADMIN},
                                      data={"test_case": test_case}, timeout=60).json()
    t = threading.Thread(target=go)
    t.start()
    return t, out


def test_api_answers_while_a_run_is_healing(server):
    import requests
    h = {"X-Access-Key": ADMIN}
    t, out = _start_run(server)
    # Wait (up to a few seconds, slow machines need longer) until the run is inside the slow Gemini call:
    # the Classifier has written its route, RCA has not answered yet.
    steps = []
    for _ in range(60):
        time.sleep(0.1)
        found = requests.get(server + "/api/incidents", headers=h, timeout=10).json()
        if found:
            audit = requests.get(f"{server}/api/incidents/{found[0]['incident_id']}/audit", headers=h, timeout=10).json()
            steps = [a["component"] for a in audit]
            if "CLASSIFIER_AGENT" in steps:
                break
    assert "MONITOR_AGENT" in steps and "CLASSIFIER_AGENT" in steps, steps  # already visible mid-run

    start = time.monotonic()
    incidents = requests.get(server + "/api/incidents", headers=h, timeout=10).json()
    took = time.monotonic() - start
    assert took < 1.5, f"API was frozen by the run ({took:.1f}s)"
    assert t.is_alive(), "the run should still be in progress"

    assert len(incidents) == 1 and incidents[0]["status"] not in ("RESOLVED", "ESCALATED")

    t.join(timeout=30)
    assert out["result"].get("healed") is True
    final = requests.get(server + "/api/incidents", headers=h, timeout=10).json()[0]
    assert final["status"] == "RESOLVED"


def test_two_runs_at_once_do_not_mix_files(server):
    # Runs share one working file, so they must take turns (a lock), not overwrite each other.
    t1, out1 = _start_run(server, "case_malformed_syntax.json")
    time.sleep(0.3)
    t2, out2 = _start_run(server, "case_healthy.json")
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert out1["result"].get("healed") is True
    assert out2["result"] == {"status": "success", "rows_ingested": 10}


@pytest.mark.skipif(not os.path.exists(DIST), reason="React app not built (frontend/dist missing)")
def test_dashboard_lights_up_steps_during_the_run(server):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1400, "height": 1000})
        browser_sign_in(context, server, ADMIN_EMAIL)
        page = context.new_page()
        page.goto(server)
        page.get_by_role("button", name="Sign out").wait_for(timeout=10000)
        page.get_by_text("Broken JSON syntax").click()
        page.get_by_role("button", name="Run pipeline").click()

        # While the fake Gemini is still thinking (DELAY seconds), the flow must already
        # show this incident and its first agent steps.
        page.wait_for_function(
            "(document.querySelector('#mission-control')?.innerText || '').includes('Route: RCA')", timeout=6000)
        mc = page.inner_text("#mission-control")
        assert "inc_" in mc, "the new incident should appear during the run"
        assert "[CLASSIFIER_AGENT]" in mc or "[MONITOR_AGENT]" in mc, mc
        assert page.get_by_text("Healed by agents").count() == 0, "run should not have finished yet"
        assert "Live: each step appears" in mc
        replay = page.locator("#mission-control").get_by_role("button", name="Replay")
        assert replay.count() == 0, "Replay should be hidden while the run is live"
        working = page.evaluate("""() => { const r = document.querySelector('#mission-control circle.animate-ring');
            return r ? r.closest('g').textContent : ''; }""")
        assert "RCA (Gemini)" in working, f"the RCA step should pulse while Gemini works, got: {working!r}"

        # And it finishes as healed.
        page.get_by_text("Healed by agents").first.wait_for(timeout=30000)
        replay.wait_for(timeout=10000)  # and Replay comes back once the run is finished
        browser.close()
