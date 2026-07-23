import os
import tempfile

# Point every database at a throwaway folder BEFORE any src module is imported,
# so the test suite can never wipe or pollute the real database/ files.
os.environ.setdefault("EHA_DB_DIR", tempfile.mkdtemp(prefix="eha_test_db_"))
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
# Login keys are OFF for the normal tests (open local mode). Set to "" on purpose so that
# keys you put in your own .env file are never picked up by the tests. test_auth.py turns
# them on per test.
os.environ["EHA_ADMIN_KEY"] = ""
os.environ["EHA_VIEWER_KEY"] = ""


# ---------------------------------------------------------------- shared fixtures
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

import langchain_google_genai
from src.agents import rca as rca_mod
from src.agents import recovery as recovery_mod
from src.api.main import app
from src.database import get_db_connection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeLLM(RunnableLambda):
    """Stands in for ChatGoogleGenerativeAI. Every call returns `reply`."""

    def __init__(self, reply: str, calls: list):
        def _answer(_prompt):
            calls.append(_prompt)
            return AIMessage(content=reply)
        super().__init__(_answer)
        self._calls = calls

    def with_structured_output(self, schema):
        def _rca(_prompt):
            self._calls.append(_prompt)
            return schema(root_cause_analysis="fake rca", confidence_score=0.5)
        return RunnableLambda(_rca)


@pytest.fixture
def fake_llm(monkeypatch):
    """Returns a setter: fake_llm("reply text") -> list that records every LLM call."""
    def _install(reply: str):
        calls = []
        factory = lambda *a, **k: FakeLLM(reply, calls)
        monkeypatch.setattr(rca_mod, "ChatGoogleGenerativeAI", factory)
        monkeypatch.setattr(recovery_mod, "ChatGoogleGenerativeAI", factory)
        # remediation_service imports it lazily from the package
        monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", factory)
        return calls
    return _install


@pytest.fixture
def client(monkeypatch):
    monkeypatch.chdir(ROOT)  # test_case paths are relative to the repo root
    # Load database/init_db.py by path (test_recovery.py puts src/ on sys.path, which
    # makes src/database.py shadow the database/ folder as a module name).
    import importlib.util
    spec = importlib.util.spec_from_file_location("eha_init_db", os.path.join(ROOT, "database", "init_db.py"))
    init_db = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_db)
    init_db.initialize_database()
    c = TestClient(app)
    c.post("/api/history/clear")
    with get_db_connection("analytics_db") as conn:
        conn.execute("DELETE FROM system_performance_metrics")
        conn.commit()
    # Limits and lockouts live in the database now; start each test with a clean slate.
    from src.api.auth import reset_rate_limit
    reset_rate_limit()
    return c


