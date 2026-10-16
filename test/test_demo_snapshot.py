"""
Demo tab (CHANGES.md section 32): a frozen recording of the 14 presets, for signed-in users only,
read-only, and never mixed with anyone's own runs. The browser side is tested in
test_frontend_react.py::test_demo_tab_shows_the_recording_read_only.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
_spec = importlib.util.spec_from_file_location("eha_make_demo", os.path.join(ROOT, "scripts", "make_demo_snapshot.py"))
snapmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapmod)

INCIDENT = {"incident_id": "inc_demo1", "run_id": "run_a_x", "pipeline_id": "PIPELINE_A", "severity": "P2",
            "category": "SCHEMA_DRIFT", "status": "RESOLVED", "error_class": "KeyError", "error_message": "country",
            "stack_trace": "", "root_cause": None, "recovery_action": "Action: RECONFIGURE",
            "blocked_by_guard": False, "created_at": "2026-10-09T10:00:00+00:00", "resolved_at": None}
SNAP = {"meta": {"recorded_at": "2026-10-09T10:00:00+00:00", "model": "gemini-3.5-flash-lite", "presets": 14,
                 "as_expected": 14, "gemini_calls": 5},
        "metrics": {"resolved": 1, "investigating": 0, "escalated": 0, "runs_without_incident": 2, "quarantined": 0,
                    "blocked_by_guard": 0},
        "incidents": [INCIDENT],
        "audits": {"inc_demo1": [{"component": "MONITOR_AGENT", "message": "seen", "timestamp": "2026-10-09T10:00:01"}]},
        "quarantine": [], "escalations": []}


@pytest.fixture
def snapshot_file(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    monkeypatch.setenv("EHA_DEMO_SNAPSHOT", str(path))
    return path


def test_no_recording_yet_says_so(sign_in, snapshot_file):
    r = sign_in("ann@example.com").get("/api/demo/snapshot")
    assert r.status_code == 404 and "No demo has been recorded yet" in r.json()["detail"]


def test_signed_in_users_get_the_recording_signed_out_do_not(client, sign_in, snapshot_file):
    snapshot_file.write_text(json.dumps(SNAP), encoding="utf-8")
    from fastapi.testclient import TestClient
    from src.api.main import app
    assert TestClient(app).get("/api/demo/snapshot").status_code == 401     # signed out (Google sign-in is on)
    assert sign_in("ann@example.com").get("/api/demo/snapshot").json() == SNAP


def test_recorded_incident_reports_download(sign_in, snapshot_file):
    pytest.importorskip("fpdf")
    snapshot_file.write_text(json.dumps(SNAP), encoding="utf-8")
    ann = sign_in("ann@example.com")
    report = ann.get("/api/demo/incidents/inc_demo1/report?format=json").json()
    assert report["incident"]["incident_id"] == "inc_demo1" and report["audit_trail"][0]["message"] == "seen"
    assert ann.get("/api/demo/incidents/inc_demo1/report?format=pdf").content[:4] == b"%PDF"
    assert ann.get("/api/demo/incidents/inc_nope/report").status_code == 404


def test_the_recording_never_mixes_with_your_own_runs(sign_in, snapshot_file, fake_llm):
    snapshot_file.write_text(json.dumps(SNAP), encoding="utf-8")
    ann = sign_in("ann@example.com")
    assert ann.get("/api/incidents").json() == []            # the recorded incident is not "yours"
    assert ann.get("/api/incidents/inc_demo1/audit").status_code == 404


# ---------------------------------------------------------------- the recording script

def _fake_run(monkeypatch, why):
    monkeypatch.setattr(snapmod.measure, "_load_key", lambda: True)
    monkeypatch.setattr(snapmod.measure, "model_name", lambda: "gemini-3.5-flash-lite")
    monkeypatch.setattr(snapmod.measure, "start_local_server",
                        lambda tmp: (None, "http://x", "key", os.path.join(tmp, "calls.txt")))
    obs = [{"case": f"c{i}", "why": [], "gemini_calls": 0} for i in range(14)]
    obs[6] = {"case": "case_malformed_syntax.json", "why": why, "gemini_calls": 2}
    monkeypatch.setattr(snapmod.measure, "run_all", lambda *a, **k: [obs])
    monkeypatch.setattr(snapmod, "collect", lambda base, headers: {k: SNAP[k] for k in SNAP if k != "meta"})


def test_script_writes_the_recording_when_every_preset_behaves(tmp_path, monkeypatch):
    _fake_run(monkeypatch, why=[])
    out = tmp_path / "demo" / "snapshot.json"
    assert snapmod.main(["--out", str(out), "--delay", "0"]) == 0
    snap = json.loads(out.read_text(encoding="utf-8"))
    assert snap["meta"]["presets"] == 14 and snap["meta"]["as_expected"] == 14 and snap["meta"]["gemini_calls"] == 2
    assert snap["meta"]["model"] == "gemini-3.5-flash-lite" and snap["incidents"] == [INCIDENT]


def test_script_refuses_to_write_a_broken_recording(tmp_path, monkeypatch, capsys):
    _fake_run(monkeypatch, why=["Gemini call failed (check GOOGLE_API_KEY, network and the daily quota)"])
    out = tmp_path / "snapshot.json"
    assert snapmod.main(["--out", str(out), "--delay", "0"]) == 1
    assert not out.exists() and "Not written" in capsys.readouterr().err
    assert snapmod.main(["--out", str(out), "--delay", "0", "--force"]) == 0 and out.exists()
