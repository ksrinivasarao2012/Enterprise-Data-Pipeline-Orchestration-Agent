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
# Same for Google sign-in (CHANGES.md section 31): off unless a test turns it on (google_on).
for _name in ("GOOGLE_CLIENT_ID", "EHA_ADMIN_EMAILS", "EHA_SESSION_SECRET"):
    os.environ[_name] = ""


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




# ---------------------------------------------------------------- Google sign-in (CHANGES.md section 31)
# Real Google tokens can't be made in a test, so verify_google_token is replaced: a credential
# "good:<email>" is accepted as that (verified) Google account, anything else is refused.
# Everything after that (the session cookie, roles, per-person data) is the real code.

GOOGLE_CLIENT = "test-client.apps.googleusercontent.com"
ADMIN_EMAIL = "boss@example.com"
SESSION_SECRET = "test-session-secret-0123456789abcdef"
CSRF = {"X-Requested-With": "eha"}


def fake_google_user(credential: str) -> dict:
    if not credential.startswith("good:"):
        raise ValueError("Token used too late / wrong audience")
    email = credential[5:]
    return {"sub": "sub-" + email, "email": email.lower(), "name": email.split("@")[0].title()}


@pytest.fixture
def google_on(monkeypatch):
    """Google sign-in switched on, with boss@example.com as the admin."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_CLIENT)
    monkeypatch.setenv("EHA_ADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setenv("EHA_SESSION_SECRET", SESSION_SECRET)
    import src.api.auth as auth
    monkeypatch.setattr(auth, "verify_google_token", fake_google_user)


def session_headers(email: str) -> dict:
    """Headers a signed-in browser sends: the login cookie plus the CSRF header.
    Used for real servers in other processes (they must share EHA_SESSION_SECRET)."""
    import src.api.auth as auth
    old = os.environ.get("EHA_SESSION_SECRET")
    os.environ["EHA_SESSION_SECRET"] = SESSION_SECRET
    try:
        token = auth.make_session(fake_google_user("good:" + email))
    finally:
        os.environ["EHA_SESSION_SECRET"] = old or ""
    return {"Cookie": f"{auth.SESSION_COOKIE}={token}", **CSRF}


@pytest.fixture
def sign_in(client, google_on):
    """sign_in("ann@example.com") -> a TestClient signed in as that Google account
    (each person gets their own client, so their cookies never mix)."""
    def _sign_in(email: str) -> TestClient:
        c = TestClient(app, headers=CSRF)
        r = c.post("/api/auth/google", json={"credential": "good:" + email})
        assert r.status_code == 200, r.text
        return c
    return _sign_in


def server_env_for_google() -> dict:
    """Environment for a real server (another process) with Google sign-in on."""
    return {"GOOGLE_CLIENT_ID": GOOGLE_CLIENT, "EHA_SESSION_SECRET": SESSION_SECRET, "EHA_ADMIN_EMAILS": ADMIN_EMAIL}


def browser_sign_in(context, base: str, email: str) -> None:
    """Signs a Playwright browser in as this Google account by giving it the login cookie the
    server would set after a real Google sign-in (Google itself can't be used in a test)."""
    import src.api.auth as auth
    cookie = session_headers(email)["Cookie"].split("=", 1)[1]
    context.add_cookies([{"name": auth.SESSION_COOKIE, "value": cookie, "url": base, "httpOnly": True, "sameSite": "Lax"}])
