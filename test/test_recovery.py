import os
import sys
import importlib

import pytest

# Ensure src is on path
ROOT = os.path.dirname(os.path.dirname(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from src.agents import recovery as recovery_mod
from src.services.remediation_service import RemediationService
from src.models.schemas import IncidentCategory


def noop(*a, **k):
    return None


def test_best_fuzzy_match_and_resolve():
    candidates = ["Country", "Email", "Full_Name"]
    # typo / case variant
    assert recovery_mod._best_fuzzy_match("Cnountry", candidates, cutoff=0.78) == "Country"

    alias_map = {"country_col": ["country", "Cntry", "cn"]}
    resolved = recovery_mod._resolve_param_name("Cntry", alias_map, "_col")
    assert resolved == "country_col"


def test_recovery_agent_node_schema_drift(monkeypatch):
    # Monkeypatch external side-effects
    monkeypatch.setattr(recovery_mod, "ChatGoogleGenerativeAI", lambda *a, **k: None)
    monkeypatch.setattr(recovery_mod, "IncidentManager", type("IM", (), {"transition_to": staticmethod(noop)}))
    monkeypatch.setattr(recovery_mod, "AuditService", type("AS", (), {"log_event": staticmethod(noop)}))

    # Create a fake state that encodes an ErrorSignature with missing_key
    state = {
        "incident_id": "INC-TEST-1",
        "category": IncidentCategory.SCHEMA_DRIFT.value,
        "pipeline_id": "PIPELINE_A",
        "error_message": "[ErrorSignature: E|X|Cntry|SIG123] Expected: ['country'] Found: ['Cntry']",
        "audit_trail": []
    }

    result = recovery_mod.recovery_agent_node(state)
    directive = result.get("recovery_directive", {})
    assert directive.get("action") == "RECONFIGURE"
    params = directive.get("params", {})
    # param key should be the canonical config param
    assert any(k.endswith("_col") for k in params.keys())
    assert list(params.values())[0] == "Cntry"


def test_remediation_service_validate_fuzzy_accept():
    # fuzzy key 'countrycol' should map to 'country_col' in config
    params = {"countrycol": "Cntry"}
    ok = RemediationService.validate_fix_schema("PIPELINE_A", params)
    assert ok is True
    # ensure the key was normalized/mapped
    assert "country_col" in params


def test_remediation_service_rejects_unknown():
    params = {"unknownparam": "value"}
    ok = RemediationService.validate_fix_schema("PIPELINE_A", params)
    assert ok is False


def test_alias_not_in_payload_should_escalate(monkeypatch):
    # alias exists in config but payload does not contain the alias -> should ESCALATE
    monkeypatch.setattr(recovery_mod, "ChatGoogleGenerativeAI", lambda *a, **k: None)
    monkeypatch.setattr(recovery_mod, "IncidentManager", type("IM", (), {"transition_to": staticmethod(noop)}))
    monkeypatch.setattr(recovery_mod, "AuditService", type("AS", (), {"log_event": staticmethod(noop)}))

    state = {
        "incident_id": "INC-TEST-2",
        "category": IncidentCategory.SCHEMA_DRIFT.value,
        "pipeline_id": "PIPELINE_A",
        # missing_key 'country' but found_list does not contain any alias like 'nation'
        "error_message": "[ErrorSignature: E|X|country|SIG999] Expected: ['country'] Found: ['id', 'name', 'email']",
        "audit_trail": []
    }

    result = recovery_mod.recovery_agent_node(state)
    directive = result.get("recovery_directive", {})
    # no mapping possible -> action should be ESCALATE
    assert directive.get("action") == "ESCALATE"


def test_alias_in_payload_matches_alias_to_param(monkeypatch):
    # alias 'nation' exists in config and is present in payload -> should map to country_col
    monkeypatch.setattr(recovery_mod, "ChatGoogleGenerativeAI", lambda *a, **k: None)
    monkeypatch.setattr(recovery_mod, "IncidentManager", type("IM", (), {"transition_to": staticmethod(noop)}))
    monkeypatch.setattr(recovery_mod, "AuditService", type("AS", (), {"log_event": staticmethod(noop)}))

    state = {
        "incident_id": "INC-TEST-3",
        "category": IncidentCategory.SCHEMA_DRIFT.value,
        "pipeline_id": "PIPELINE_A",
        # missing_key 'Cntry' and found_list contains 'nation' which is an alias for country_col
        "error_message": "[ErrorSignature: E|X|Cntry|SIG888] Expected: ['country'] Found: ['id', 'name', 'email', 'nation']",
        "audit_trail": []
    }

    result = recovery_mod.recovery_agent_node(state)
    directive = result.get("recovery_directive", {})
    assert directive.get("action") == "RECONFIGURE"
    params = directive.get("params", {})
    assert params.get("country_col") == "nation"


def test_llm_fallback_aligns_to_found_key(monkeypatch):
    # Simulate deterministic matching failing and LLM suggesting a payload key
    def fake_get_recovery_config():
        return {
            "default_param_suffix": "_col",
            "pipelines": {
                "PIPELINE_A": {
                    "allowed_params": ["country_col"],
                    "aliases": {"country_col": ["country", "nation"]}
                }
            }
        }

    # Dummy prompt/chain/response to simulate ChatPromptTemplate | llm -> invoke()
    class DummyResponse:
        def __init__(self, content):
            self.content = content

    class DummyChain:
        def __init__(self, resp):
            self._resp = resp
        def invoke(self, *_a, **_k):
            return self._resp

    class DummyPrompt:
        def __or__(self, other):
            # return an object that has invoke(...) -> DummyResponse
            return DummyChain(DummyResponse("weird_country"))

    monkeypatch.setattr(recovery_mod, "get_recovery_config", fake_get_recovery_config)
    monkeypatch.setattr(recovery_mod, "ChatPromptTemplate", type("CP", (), {"from_messages": staticmethod(lambda msgs: DummyPrompt())}))
    monkeypatch.setattr(recovery_mod, "ChatGoogleGenerativeAI", lambda *a, **k: None)
    monkeypatch.setattr(recovery_mod, "IncidentManager", type("IM", (), {"transition_to": staticmethod(noop)}))
    monkeypatch.setattr(recovery_mod, "AuditService", type("AS", (), {"log_event": staticmethod(noop)}))

    state = {
        "incident_id": "INC-LLM-1",
        "category": IncidentCategory.SCHEMA_DRIFT.value,
        "pipeline_id": "PIPELINE_A",
        "error_message": "[ErrorSignature: E|X|Cntry|SIGLLM] Expected: ['country'] Found: ['weird_country']",
        "audit_trail": []
    }

    result = recovery_mod.recovery_agent_node(state)
    directive = result.get("recovery_directive", {})
    assert directive.get("action") == "RECONFIGURE"
    params = directive.get("params", {})
    assert params.get("country_col") == "weird_country"


def test_pipeline_b_allows_sql_override():
    params = {"sql_override": "SELECT * FROM devices WHERE 1=1"}
    ok = RemediationService.validate_fix_schema("PIPELINE_B", params)
    assert ok is True


def test_pipeline_a_rejects_sql_in_value():
    params = {"country_col": "select * from users"}
    ok = RemediationService.validate_fix_schema("PIPELINE_A", params)
    assert ok is False


def test_flatten_root_dict_validation():
    params = {"flatten_root_dict": True}
    ok = RemediationService.validate_fix_schema("PIPELINE_A", params)
    assert ok is True
