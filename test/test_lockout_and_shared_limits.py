"""
Key lockout and database-backed limits (CHANGES.md section 19).

1. Lockout: too many wrong keys from one client locks it out for a while, even with the right key.
2. The viewer run limit, the lockout and "Try all" grants are stored in the database, so they
   survive a restart and are shared by several server copies (like Vercel starts).
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

ADMIN, VIEWER = "admin-lock-key", "viewer-lock-key"


def h(key, ip=None):
    headers = {"X-Access-Key": key}
    if ip:
        headers["x-forwarded-for"] = ip
    return headers


@pytest.fixture
def keys_on(monkeypatch):
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN)
    monkeypatch.setenv("EHA_VIEWER_KEY", VIEWER)


# ---------------------------------------------------------------- lockout (in-process)

def test_ten_wrong_keys_lock_the_client_out(client, keys_on, monkeypatch):
    import src.api.auth as auth
    monkeypatch.setattr(auth, "MAX_FAILED_KEYS", 3)
    for _ in range(3):
        assert client.get("/api/metrics", headers=h("guess")).status_code == 401
    r = client.get("/api/metrics", headers=h("guess"))
    assert r.status_code == 429 and "Too many wrong keys" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0
    # even the right key is refused while locked, so guessing can't continue
    assert client.get("/api/metrics", headers=h(ADMIN)).status_code == 429


def test_lockout_ends_after_the_window(client, keys_on, monkeypatch):
    import src.api.auth as auth
    monkeypatch.setattr(auth, "MAX_FAILED_KEYS", 2)
    monkeypatch.setattr(auth, "LOCKOUT_SECONDS", 1)
    for _ in range(2):
        client.get("/api/metrics", headers=h("guess"))
    assert client.get("/api/metrics", headers=h(ADMIN)).status_code == 429
    time.sleep(1.2)
    assert client.get("/api/metrics", headers=h(ADMIN)).status_code == 200


def test_no_key_is_not_counted_as_a_guess(client, keys_on, monkeypatch):
    import src.api.auth as auth
    monkeypatch.setattr(auth, "MAX_FAILED_KEYS", 2)
    for _ in range(5):  # the login screen asks "am I logged in?" without a key
        assert client.get("/api/whoami").status_code == 401
    assert client.get("/api/metrics", headers=h(VIEWER)).status_code == 200


def test_lockout_is_per_client_on_vercel(client, keys_on, monkeypatch):
    import src.api.auth as auth
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(auth, "MAX_FAILED_KEYS", 2)
    for _ in range(2):
        client.get("/api/metrics", headers=h("guess", "1.1.1.1"))
    assert client.get("/api/metrics", headers=h(VIEWER, "1.1.1.1")).status_code == 429
    assert client.get("/api/metrics", headers=h(VIEWER, "2.2.2.2")).status_code == 200


def test_made_up_forwarded_header_is_ignored_locally(client, keys_on, monkeypatch):
    # Locally there is no proxy: changing x-forwarded-for must not dodge the lockout.
    import src.api.auth as auth
    monkeypatch.setattr(auth, "MAX_FAILED_KEYS", 2)
    for i in range(2):
        client.get("/api/metrics", headers=h("guess", f"9.9.9.{i}"))
    assert client.get("/api/metrics", headers=h(ADMIN, "8.8.8.8")).status_code == 429


# ---------------------------------------------------------------- two real server copies, one database

SERVER = """
import os, sys, importlib.util
root, port = sys.argv[1], int(sys.argv[2])
os.chdir(root); sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location("i", os.path.join(root, "database", "init_db.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); m.initialize_database()
import uvicorn
from src.api.main import app
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Copies:
    """Starts server copies that share one database folder, like Vercel copies share Postgres."""

    def __init__(self):
        self.db = tempfile.mkdtemp(prefix="eha_shared_")
        self.script = os.path.join(tempfile.mkdtemp(), "server.py")
        open(self.script, "w").write(SERVER)
        self.procs = []

    def start(self):
        import requests
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        port = _free_port()
        env = dict(os.environ, EHA_DB_DIR=self.db, PYTHONPATH=root, EHA_ADMIN_KEY=ADMIN, EHA_VIEWER_KEY=VIEWER,
                   EHA_VIEWER_RUNS_PER_MINUTE="2", EHA_MAX_FAILED_KEYS="3", GOOGLE_API_KEY="x", EHA_UI="")
        env.pop("VERCEL", None)
        proc = subprocess.Popen([sys.executable, self.script, root, str(port)], cwd=root, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                requests.get(base + "/api/whoami", timeout=1)
                return base, proc
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("server did not start")

    def stop(self, proc):
        proc.terminate()
        proc.wait(timeout=10)

    def close(self):
        for p in self.procs:
            if p.poll() is None:
                self.stop(p)


@pytest.fixture
def copies():
    pytest.importorskip("requests")
    pytest.importorskip("uvicorn")
    c = Copies()
    yield c
    c.close()


def _run(base, key, extra=None):
    import requests
    return requests.post(base + "/api/pipelines/a/execute", headers={**h(key), **(extra or {})},
                         data={"test_case": "case_healthy.json"}, timeout=60).status_code


def test_run_limit_is_shared_by_server_copies_and_survives_a_restart(copies):
    a, proc_a = copies.start()
    b, _ = copies.start()
    assert _run(a, VIEWER) == 200
    assert _run(b, VIEWER) == 200          # 2 of 2 used, across two copies
    assert _run(a, VIEWER) == 429          # copy A knows about the run on copy B
    copies.stop(proc_a)                    # "restart"
    a2, _ = copies.start()
    assert _run(a2, VIEWER) == 429         # the new server still remembers


def test_lockout_is_shared_by_server_copies(copies):
    import requests
    a, _ = copies.start()
    b, _ = copies.start()
    for _ in range(3):
        assert requests.get(a + "/api/metrics", headers=h("guess"), timeout=10).status_code == 401
    assert requests.get(b + "/api/metrics", headers=h(ADMIN), timeout=10).status_code == 429


def test_try_all_token_works_on_any_copy_but_only_once(copies):
    import requests
    a, _ = copies.start()
    b, _ = copies.start()
    token = requests.post(a + "/api/demo/start", headers=h(VIEWER), timeout=10).json()["token"]  # 1 of 2
    demo = {"X-Demo-Token": token}
    assert _run(b, VIEWER, demo) == 200    # free run, used on the other copy
    assert _run(a, VIEWER, demo) == 200    # same preset again: counted (2 of 2)
    assert _run(b, VIEWER, demo) == 429
