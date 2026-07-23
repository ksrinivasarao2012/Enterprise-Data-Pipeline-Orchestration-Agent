"""
Crashed heal -> ESCALATED (CHANGES.md section 22).

If the agents or the fix crash halfway (e.g. a missing package such as sqlglot, or a bug),
the incident used to stay OPEN / INVESTIGATING for ever and never reached a person.
Now it is escalated, with a "Healing crashed" line in its audit log.
(A Gemini outage is not a crash: the agents already handle that themselves; see the last test.)
"""
import sys

from langchain_core.runnables import RunnableLambda

import langchain_google_genai
from src.agents import rca as rca_mod
from src.agents import recovery as recovery_mod


def test_missing_package_escalates_instead_of_staying_open(client, fake_llm, monkeypatch):
    # Like a venv without sqlglot: the agent graph can't even be imported.
    fake_llm("UNRESOLVED")
    monkeypatch.setitem(sys.modules, "src.orchestration.graph", None)
    r = client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"}).json()
    assert r.get("healed") is not True

    inc = client.get("/api/incidents").json()[0]
    assert inc["status"] == "ESCALATED", f"stuck at {inc['status']}"
    audit = [a["message"] for a in client.get(f"/api/incidents/{inc['incident_id']}/audit").json()]
    assert any(m.startswith("Healing crashed: ModuleNotFoundError") and m.endswith("Sent to a person.") for m in audit), audit

    esc = client.get("/api/escalations").json()
    assert esc[0]["incident_id"] == inc["incident_id"]
    assert esc[0]["why"] == "The healing itself crashed (ModuleNotFoundError), so a person was asked to look at it instead."


def test_crash_while_applying_the_fix_escalates(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    from src.services.remediation_service import RemediationService

    def broken(*_a, **_k):
        raise KeyError("bug in the actuator")
    monkeypatch.setattr(RemediationService, "apply_fix", staticmethod(broken))  # the graph's Apply step
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})
    inc = client.get("/api/incidents").json()[0]
    assert inc["status"] == "ESCALATED"


def test_an_incident_that_already_finished_is_left_alone(client, fake_llm):
    fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_drift_country.json"})
    inc = client.get("/api/incidents").json()[0]
    assert inc["status"] == "RESOLVED"
    from src.api.main import _escalate_after_crash
    _escalate_after_crash(inc["incident_id"], RuntimeError("late crash"))
    assert client.get("/api/incidents").json()[0]["status"] == "RESOLVED"


class DownGemini(RunnableLambda):
    """A Gemini that fails like a real outage."""

    def __init__(self):
        def boom(_):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        super().__init__(boom)

    def with_structured_output(self, _schema):
        return self


def test_gemini_outage_is_already_handled_by_the_agents(client, monkeypatch):
    factory = lambda *a, **k: DownGemini()
    for mod in (rca_mod, recovery_mod, langchain_google_genai):
        monkeypatch.setattr(mod, "ChatGoogleGenerativeAI", factory)
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    inc = client.get("/api/incidents").json()[0]
    assert inc["status"] == "ESCALATED"
    audit = " ".join(a["message"] for a in client.get(f"/api/incidents/{inc['incident_id']}/audit").json())
    assert "Healing crashed" not in audit, "not a crash: the Recovery agent escalates by itself"
