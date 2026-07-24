"""
Browser tests for the React dashboard in frontend/ (see frontend/README.md).

They start the real backend with login keys and a fake Gemini that tries to cheat on SQL,
then click through the built app in Chromium. Skipped if Playwright is not installed or
the app has not been built yet (cd frontend && npm install && npm run build).
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist", "index.html")
ADMIN, VIEWER = "admin-test-key-123", "viewer-test-key-456"

pytestmark = pytest.mark.skipif(not os.path.exists(DIST), reason="React app not built (frontend/dist missing)")

# Server started in a subprocess: real API + React app, Gemini replaced by a fake that
# returns a SQL "repair" that fakes the missing column with the constant 0.
SERVER = textwrap.dedent('''
    import os, sys, importlib.util
    root, port = sys.argv[1], int(sys.argv[2])
    os.chdir(root); sys.path.insert(0, root)
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    import langchain_google_genai
    CHEAT = ("SELECT device_id, COUNT(log_id) AS total_events, SUM(CASE WHEN 0 >= 400 THEN 1 ELSE 0 END) * 1.0 / "
             "COUNT(log_id) AS error_rate, AVG(response_time) AS avg_response_time FROM device_telemetry GROUP BY device_id")
    class Fake(RunnableLambda):
        def __init__(self):
            super().__init__(lambda p: AIMessage(content=CHEAT if "SQL repair" in str(p) else "UNRESOLVED"))
        def with_structured_output(self, s):
            return RunnableLambda(lambda _: s(root_cause_analysis="fake rca", confidence_score=0.5))
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


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("playwright.sync_api")
    import requests
    port = _free_port()
    script = os.path.join(tempfile.mkdtemp(), "server.py")
    open(script, "w").write(SERVER)
    env = dict(os.environ, EHA_DB_DIR=tempfile.mkdtemp(prefix="eha_react_"), PYTHONPATH=ROOT,
               EHA_ADMIN_KEY=ADMIN, EHA_VIEWER_KEY=VIEWER, EHA_UI="", GOOGLE_API_KEY="x")
    proc = subprocess.Popen([sys.executable, script, ROOT, str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            requests.get(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def page(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1400, "height": 1000})
        pg.goto(server)
        yield pg
        browser.close()


def _login(page, key):
    page.fill("input[type=password]", key)
    page.keyboard.press("Enter")


def test_wrong_key_then_viewer_login_and_logout(page):
    _login(page, "wrong")
    page.get_by_text("Wrong key").wait_for(timeout=10000)
    _login(page, VIEWER)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.get_by_text("Viewer", exact=True).wait_for()
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_text("Incident timeline").wait_for(timeout=10000)
    assert page.get_by_role("button", name="Clear history").count() == 0, "viewers must not see Clear history"
    page.get_by_role("button", name="Log out").click()
    page.locator("input[type=password]").wait_for(timeout=10000)


def test_admin_sees_clear_history(page):
    _login(page, ADMIN)
    page.get_by_text("Admin", exact=True).wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_role("button", name="Clear history").wait_for(timeout=10000)


def test_viewer_runs_two_renamed_columns_and_it_heals(page):
    _login(page, VIEWER)
    page.get_by_text("Two renamed columns").click()
    page.get_by_role("button", name="Run pipeline").click()
    page.get_by_text("Healed by agents").first.wait_for(timeout=30000)


def test_cheating_sql_is_shown_as_blocked_by_guard(page):
    _login(page, VIEWER)
    page.get_by_role("tab", name="Pipeline B · Database").click()
    page.get_by_text("sql_error.db").click()
    page.get_by_role("button", name="Run pipeline").click()
    page.get_by_text("Sent to a human").first.wait_for(timeout=30000)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_text("Blocked by guard").first.wait_for(timeout=15000)
    assert "constant instead of a real column" in page.inner_text("main")


def test_uploaded_script_is_shown_as_text_not_run(page, server):
    import requests
    rows = json.load(open(os.path.join(ROOT, "test_data", "pipeline_A", "case_healthy.json")))
    rows[0]["email"] = 'bad <img src=x onerror="window.__hacked=1">'
    rows[1]["name"] = '<img src=x onerror="window.__hacked=2">'
    requests.post(server + "/api/pipelines/a/execute", headers={"X-Access-Key": ADMIN},
                  files={"file": ("x.json", json.dumps(rows), "application/json")}, timeout=60)
    _login(page, VIEWER)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/records")
    page.get_by_text("onerror").first.wait_for(timeout=15000)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.wait_for_timeout(2000)
    assert page.evaluate("window.__hacked") is None


@pytest.mark.parametrize("route", ["#/run", "#/incidents", "#/records"])
def test_no_sideways_scroll_on_a_phone(server, route):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        pg.goto(server)
        _login(pg, VIEWER)
        pg.get_by_role("button", name="Log out").wait_for(timeout=10000)
        pg.goto(server + "/" + route)
        pg.wait_for_timeout(2500)
        width = pg.evaluate("document.documentElement.scrollWidth")
        browser.close()
    assert width <= 390, f"page is {width}px wide on a 390px phone"


def test_quarantine_only_run_is_not_shown_as_healed(page):
    _login(page, VIEWER)
    page.get_by_text("Bad email in one row").click()
    page.get_by_role("button", name="Run pipeline").click()
    page.get_by_text("Some rows quarantined").first.wait_for(timeout=20000)
    page.wait_for_timeout(2500)
    mc = page.inner_text("#mission-control")
    assert "Caught by validation" in mc and "no agents needed" in mc.lower(), mc
    for step in ["Check rows", "Bad rows found", "Quarantined", "Rest saved", "9 rows loaded"]:
        assert step in mc, f"missing validation step {step!r}"
    assert "RCA (Gemini)" not in mc, "no unused agent steps should be drawn"
    assert "Healed by the agents" not in mc
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_text("Incident timeline").wait_for(timeout=10000)
    page.wait_for_timeout(1500)
    main = page.inner_text("main").lower()   # some labels are shown in capitals
    assert "quarantined" in main and "after · quarantined" in main, main[:800]


def test_incident_list_shows_a_page_and_loads_more(page, server):
    import requests
    h = {"X-Access-Key": ADMIN}
    for _ in range(22):  # make sure there are more than one page (20) of incidents
        requests.post(server + "/api/pipelines/a/execute", headers=h, data={"test_case": "case_drift_country.json"}, timeout=60)
    total = int(requests.get(server + "/api/incidents?limit=1", headers=h).headers["X-Total-Count"])
    assert total > 20

    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_text(f"20 of {total}").wait_for(timeout=10000)
    page.get_by_role("button", name="Show more").click()
    shown = min(40, total)
    page.get_by_text(f"{shown} of {total}").wait_for(timeout=10000)


def test_no_focus_ring_around_the_page_after_switching_tabs(page):
    # Switching tabs moves focus to <main> (for screen readers); it must not draw a blue box around the page.
    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.locator('a[href="#/incidents"]').first.focus()
    page.keyboard.press("Enter")  # keyboard use is when browsers show focus rings
    page.wait_for_function("document.activeElement && document.activeElement.tagName === 'MAIN'", timeout=10000)
    assert page.evaluate("getComputedStyle(document.querySelector('main')).boxShadow") == "none"
    # buttons and links still get their ring
    page.keyboard.press("Tab")
    assert page.evaluate("getComputedStyle(document.activeElement).boxShadow") != "none"


def _heal_country_drift(page):
    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.get_by_text("Renamed column: country").click()
    page.get_by_role("button", name="Run pipeline").click()
    page.get_by_text("Healed by agents").first.wait_for(timeout=30000)


def test_clicking_an_agent_circle_explains_what_it_did(page):
    _heal_country_drift(page)
    page.locator('#mission-control [data-node="RCA_AGENT"]').click()
    panel = page.locator("#agent-detail")
    panel.wait_for(timeout=5000)
    # a renamed column is fixed by a rule, so the graph skipped Gemini RCA and says why
    assert "Skipped: a safe rule already knew the fix, so no Gemini call was needed." in panel.inner_text()

    page.locator('#mission-control [data-node="RECOVERY_AGENT"]').click()
    assert "RECONFIGURE" in panel.inner_text()  # the chosen fix for a renamed column

    # keyboard works too, and clicking the same circle again closes the panel
    page.locator('#mission-control [data-node="CLASSIFIER_AGENT"]').focus()
    page.keyboard.press("Enter")
    assert "Severity" in panel.inner_text() and "Next step" in panel.inner_text()
    page.locator('#mission-control [data-node="CLASSIFIER_AGENT"]').click()
    assert page.locator("#agent-detail").count() == 0


def test_download_report_as_pdf_and_json(page):
    pytest.importorskip("fpdf")
    _heal_country_drift(page)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.locator("#incident-detail").wait_for(timeout=10000)
    with page.expect_download() as d:
        page.get_by_role("button", name="Download PDF report").click()
    path = d.value.path()
    assert d.value.suggested_filename.endswith("_report.pdf")
    assert open(path, "rb").read(4) == b"%PDF"
    with page.expect_download() as d:
        page.get_by_role("button", name="Download JSON report").click()
    assert json.load(open(d.value.path()))["outcome"] == "Healed by agents"


def test_try_all_runs_every_preset_as_a_viewer(page):
    # 13 presets is more than the viewer limit (10 per minute), but Try all counts as one run.
    _login(page, VIEWER)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.get_by_role("button", name="Try all 13 presets").click()
    page.get_by_role("button", name="Stop after this one").wait_for(timeout=5000)
    page.get_by_text("Try all finished: 13 runs").wait_for(timeout=120000)
    box = page.inner_text("#try-all-results")
    assert "Too many runs" not in box, box
    for label in ["Healthy file", "Renamed column: country", "Missing column", "Missing table"]:
        assert label in box
    assert "Healed by agents" in box and "Sent to a human" in box and "quarantined" in box


def test_escalations_are_shown_in_plain_words(page, server):
    import requests
    requests.post(server + "/api/pipelines/b/execute", headers={"X-Access-Key": ADMIN},
                  data={"test_case": "missing_table.db"}, timeout=60)
    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/records")
    page.get_by_role("tab", name="Escalations").click()
    row = page.locator("tr", has_text="The source table 'device_telemetry' does not exist.").last
    row.wait_for(timeout=10000)
    assert "no safe automatic fix" in row.inner_text()
    # the raw error is hidden until "Technical error" is opened
    raw = row.get_by_text("Operational source table 'device_telemetry' does not exist on disk.")
    assert not raw.is_visible()
    row.get_by_text("Technical error").click()
    assert raw.is_visible()


def test_records_tab_shows_a_page_and_loads_more(page, server):
    import requests
    rows = [{"customer_id": str(i), "name": f"n{i}", "email": "bad", "country": "IN"} for i in range(25)]
    rows.append({"customer_id": "ok", "name": "ok", "email": "ok@x.com", "country": "IN"})
    requests.post(server + "/api/pipelines/a/execute", headers={"X-Access-Key": ADMIN},
                  files={"file": ("bad.json", json.dumps(rows), "application/json")}, timeout=60)
    total = int(requests.get(server + "/api/quarantine?limit=1", headers={"X-Access-Key": ADMIN}).headers["X-Total-Count"])
    assert total >= 25

    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/records")
    page.get_by_text(f"20 of {total}").wait_for(timeout=10000)
    assert page.locator("#records-panel tbody tr").count() == 20
    page.get_by_role("tab", name="Quarantine").get_by_text(str(total)).wait_for(timeout=5000)  # badge = real total
    page.locator("#records-panel").get_by_role("button", name="Show more").click()
    shown = min(40, total)
    if shown < total:
        page.get_by_text(f"{shown} of {total}").wait_for(timeout=10000)
    else:
        page.wait_for_function(f"document.querySelectorAll('#records-panel tbody tr').length === {total}", timeout=10000)


def test_incident_filters_in_the_browser(page, server):
    import requests
    h = {"X-Access-Key": ADMIN}
    requests.post(server + "/api/pipelines/b/execute", headers=h, data={"test_case": "missing_table.db"}, timeout=60)
    requests.post(server + "/api/pipelines/a/execute", headers=h, data={"test_case": "case_drift_country.json"}, timeout=60)
    esc = int(requests.get(server + "/api/incidents?limit=1&outcome=escalated", headers=h).headers["X-Total-Count"])

    _login(page, ADMIN)
    page.get_by_role("button", name="Log out").wait_for(timeout=10000)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_role("button", name="Escalated", exact=True).click()
    page.get_by_text(f"{min(20, esc)} of {esc} matching").wait_for(timeout=10000)
    assert page.get_by_text("Healed by agents").count() == 0

    page.get_by_label("Search incidents").fill("zzz-no-such-incident")
    page.get_by_text("No incidents match these filters.").wait_for(timeout=10000)
    page.locator("#timeline-title").get_by_text("0 of 0 matching").wait_for(timeout=5000)
    page.get_by_role("button", name="Clear filters").first.click()
    page.wait_for_function("!document.querySelector('#timeline-title').textContent.includes('matching')", timeout=10000)


def test_try_all_on_a_phone_keeps_the_results_list_in_view(server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        pg.goto(server)
        _login(pg, ADMIN)
        pg.get_by_role("button", name="Log out").wait_for(timeout=10000)
        pg.get_by_role("button", name="Try all 13 presets").click()
        in_view = "(() => { const r = document.getElementById('try-all-results')?.getBoundingClientRect();" \
                  " return !!r && r.top < innerHeight && r.bottom > 0; })()"
        for _ in range(6):  # sample while it runs
            pg.wait_for_timeout(1000)
            assert pg.evaluate(in_view), "the results list scrolled out of view"
        pg.get_by_text("Try all finished").wait_for(timeout=120000)
        assert pg.evaluate(in_view)
        browser.close()


def test_open_mode_shows_clear_history():
    # No keys set (local "Open mode"): the server treats everyone as admin, so the button must show.
    import requests
    from playwright.sync_api import sync_playwright
    port = _free_port()
    script = os.path.join(tempfile.mkdtemp(), "server.py")
    open(script, "w").write(SERVER)
    env = dict(os.environ, EHA_DB_DIR=tempfile.mkdtemp(prefix="eha_open_"), PYTHONPATH=ROOT,
               EHA_ADMIN_KEY="", EHA_VIEWER_KEY="", EHA_UI="", GOOGLE_API_KEY="x")
    env.pop("VERCEL", None)
    proc = subprocess.Popen([sys.executable, script, ROOT, str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                requests.get(base + "/", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page(viewport={"width": 1400, "height": 1000})
            pg.goto(base + "/#/incidents")
            pg.get_by_text("Open mode").wait_for(timeout=10000)
            pg.get_by_role("button", name="Clear history").wait_for(timeout=10000)
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_skipped_rca_is_explained_not_not_found_yet(page):
    # A renamed column is fixed by a rule, so RCA never runs; the card must say why.
    _heal_country_drift(page)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.locator("#incident-detail").wait_for(timeout=10000)
    page.get_by_text("Not needed: a safe rule already knew the fix, so Gemini wasn't asked.").first.wait_for(timeout=10000)
    assert page.get_by_text("Not found yet.").count() == 0


def test_blocked_filter_finds_an_older_incident_beyond_the_first_page(page, server):
    """'Show only blocked fixes' asks the server (CHANGES.md section 26). Before, it only looked at the
    20 incidents on screen, so a blocked fix on page 2 was missed and the badge count was wrong."""
    import requests
    admin = {"X-Access-Key": ADMIN}
    requests.post(server + "/api/history/clear", headers=admin, timeout=30)
    # the blocked one is the OLDEST incident; 22 newer, harmless ones push it to page 2 (page size 20)
    requests.post(server + "/api/pipelines/b/execute", data={"test_case": "sql_error.db"}, headers=admin, timeout=60)
    for _ in range(22):
        requests.post(server + "/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"}, headers=admin, timeout=60)

    _login(page, VIEWER)
    page.goto(page.url.split("#")[0] + "#/incidents")
    page.get_by_text("Incident timeline").wait_for(timeout=10000)
    page.get_by_text("20 of 23").wait_for(timeout=10000)
    assert page.get_by_text("Blocked by guard").count() == 0, "the blocked incident is not on the first page"

    button = page.get_by_role("button", name="Show only blocked fixes")
    assert button.inner_text().strip().endswith("1"), "the badge number comes from the server, so it is 1 even though the page shows none"
    button.click()
    page.get_by_text("1 of 1 matching").wait_for(timeout=10000)
    page.get_by_text("Blocked by guard").first.wait_for(timeout=10000)
    assert "Show more" not in page.inner_text("main")

    button.click()                                   # off again: back to the full list
    page.get_by_text("20 of 23").wait_for(timeout=10000)
