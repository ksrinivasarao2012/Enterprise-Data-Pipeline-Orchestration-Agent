"""
Sign-in (authentication) and permissions (authorization). See CHANGES.md sections 7 and 31.

    viewer - anyone who signs in with Google: runs presets, uploads own files (smaller limit,
             run limit), sees ONLY their own runs (test_per_user_data.py checks that part)
    admin  - a Google account listed in EHA_ADMIN_EMAILS: everything, sees everyone's runs
    script - EHA_ADMIN_KEY in the X-Access-Key header: admin, for scripts only

Nothing set: open locally (development), fully locked on Vercel.
Google itself can't be called in a test, so its token check is replaced (see conftest.py).
"""
import json
import os
import time

from conftest import ADMIN_EMAIL, CSRF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTHY = json.load(open(os.path.join(ROOT, "test_data", "pipeline_A", "case_healthy.json")))

ADMIN_KEY = "admin-test-key-123"
READ_ENDPOINTS = ["/api/metrics", "/api/incidents", "/api/quarantine", "/api/escalations"]


def run_preset(c, headers=None):
    return c.post("/api/pipelines/a/execute", data={"test_case": "case_healthy.json"}, headers=headers or {})


# ---------------------------------------------------------------- signing in

def test_signed_out_is_refused_everywhere(client, fake_llm, google_on):
    fake_llm("UNRESOLVED")
    for url in READ_ENDPOINTS + ["/api/whoami", "/api/incidents/x/audit"]:
        assert client.get(url).status_code == 401, url
    assert run_preset(client).status_code == 401
    assert client.post("/api/history/clear").status_code == 401
    assert client.post("/api/demo/start").status_code == 401


def test_sign_in_with_google_sets_a_safe_cookie(client, google_on):
    r = client.post("/api/auth/google", json={"credential": "good:ann@example.com"}, headers=CSRF)
    assert r.status_code == 200
    assert r.json() == {"role": "viewer", "auth_enabled": True, "mode": "google",
                        "email": "ann@example.com", "name": "Ann"}
    cookie = r.headers["set-cookie"].lower()
    assert "eha_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/whoami").json()["email"] == "ann@example.com"


def test_bad_google_token_is_refused(client, google_on):
    r = client.post("/api/auth/google", json={"credential": "expired-or-forged"}, headers=CSRF)
    assert r.status_code == 401 and "set-cookie" not in r.headers
    assert client.get("/api/whoami").status_code == 401


def test_sign_in_needs_the_csrf_header(client, google_on):
    # A page on another site can't add this header, so it can't sign a visitor in to its own account.
    assert client.post("/api/auth/google", json={"credential": "good:ann@example.com"}).status_code == 403


def test_admin_is_chosen_by_email(sign_in):
    assert sign_in(ADMIN_EMAIL).get("/api/whoami").json()["role"] == "admin"
    assert sign_in("BOSS@Example.com").get("/api/whoami").json()["role"] == "admin"  # case doesn't matter
    assert sign_in("ann@example.com").get("/api/whoami").json()["role"] == "viewer"


def test_edited_or_expired_cookie_is_refused(sign_in, monkeypatch):
    import src.api.auth as auth
    ann = sign_in("ann@example.com")
    token = ann.cookies.get(auth.SESSION_COOKIE)
    body, sig = token.rsplit(".", 1)
    # Change the email inside the cookie to the admin's: the signature no longer matches.
    payload = json.loads(auth._unb64(body))
    payload["email"] = ADMIN_EMAIL
    forged = auth._b64(json.dumps(payload).encode()) + "." + sig
    assert auth.read_session(forged) is None
    assert auth.read_session(token) is not None
    # A cookie made 8 days ago has expired (logins last 7 days).
    old = auth.make_session({"sub": "s", "email": "ann@example.com"}, now=time.time() - 8 * 86400)
    assert auth.read_session(old) is None


def test_cookie_requests_that_change_things_need_the_csrf_header(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann = sign_in("ann@example.com")
    ann.headers.pop("X-Requested-With")
    assert ann.get("/api/metrics").status_code == 200          # reading is fine
    assert run_preset(ann).status_code == 403                  # changing is not
    assert ann.post("/api/history/clear").status_code == 403
    assert run_preset(ann, CSRF).json()["status"] == "success"


def test_sign_out_removes_the_cookie(sign_in):
    ann = sign_in("ann@example.com")
    assert ann.post("/api/auth/logout").status_code == 200
    assert ann.get("/api/whoami").status_code == 401


def test_auth_config_is_public_and_tells_the_page_what_to_show(client, google_on, monkeypatch):
    assert client.get("/api/auth/config").json() == {"mode": "google",
                                                     "google_client_id": "test-client.apps.googleusercontent.com"}
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    assert client.get("/api/auth/config").json()["mode"] == "open"
    monkeypatch.setenv("VERCEL", "1")
    assert client.get("/api/auth/config").json()["mode"] == "locked"


def test_no_session_secret_on_vercel_refuses_sign_in(client, google_on, monkeypatch):
    # Every server copy must share one secret, so a missing one is an error, not a random secret.
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("EHA_SESSION_SECRET", "")
    r = client.post("/api/auth/google", json={"credential": "good:ann@example.com"}, headers=CSRF)
    assert r.status_code == 500 and "EHA_SESSION_SECRET" in r.json()["detail"]


# ---------------------------------------------------------------- viewer limits and rights

def test_viewer_cannot_use_telemetry_endpoints(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann = sign_in("ann@example.com")
    assert ann.post("/telemetry/incident", json={}).status_code == 403
    assert ann.post("/runs/register", json={}).status_code == 403


def test_viewer_upload_limit_is_smaller(sign_in, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.main as api
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 5000)
    monkeypatch.setattr(api, "VIEWER_MAX_UPLOAD_BYTES", 1000)
    body = json.dumps(HEALTHY)  # about 1 KB+, between the two limits
    assert 1000 < len(body) < 5000
    up = lambda c: c.post("/api/pipelines/a/execute", files={"file": ("f.json", body, "application/json")})
    assert up(sign_in("ann@example.com")).status_code == 413
    assert up(sign_in(ADMIN_EMAIL)).json()["status"] == "success"


def test_viewer_run_limit_is_per_person(sign_in, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    assert run_preset(ann).status_code == 200
    assert run_preset(ann).status_code == 200
    assert run_preset(ann).status_code == 429
    # Bob has his own allowance (same address, different person), reading is never limited,
    # and the admin is never limited.
    assert run_preset(bob).status_code == 200
    assert ann.get("/api/metrics").status_code == 200
    assert run_preset(sign_in(ADMIN_EMAIL)).status_code == 200
    auth.reset_rate_limit()


# ---------------------------------------------------------------- the script key

def test_admin_key_still_works_for_scripts(client, fake_llm, google_on, monkeypatch):
    fake_llm("UNRESOLVED")
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN_KEY)
    h = {"X-Access-Key": ADMIN_KEY}
    for url in READ_ENDPOINTS:
        assert client.get(url, headers=h).status_code == 200, url
    assert run_preset(client, h).json()["status"] == "success"
    assert client.get("/api/whoami", headers=h).json()["role"] == "admin"
    assert client.get("/api/metrics", headers={"X-Access-Key": "guess"}).status_code == 401


def test_old_viewer_key_no_longer_signs_anyone_in(client, google_on, monkeypatch):
    monkeypatch.setenv("EHA_VIEWER_KEY", "old-viewer-key")
    assert client.get("/api/metrics", headers={"X-Access-Key": "old-viewer-key"}).status_code == 401


def test_only_the_key_set_means_key_mode(client, monkeypatch):
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN_KEY)
    assert client.get("/api/auth/config").json()["mode"] == "key"
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers={"X-Access-Key": ADMIN_KEY}).status_code == 200


# ---------------------------------------------------------------- nothing configured

def test_nothing_set_locally_means_open_mode(client, fake_llm):
    fake_llm("UNRESOLVED")
    who = client.get("/api/whoami").json()
    assert who["role"] == "admin" and who["auth_enabled"] is False and who["mode"] == "open"
    assert run_preset(client).json()["status"] == "success"


def test_nothing_set_on_vercel_means_locked(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    monkeypatch.setenv("VERCEL", "1")
    assert client.get("/api/metrics").status_code == 401
    assert run_preset(client).status_code == 401
    assert client.post("/api/history/clear").status_code == 401


def test_dashboard_page_itself_is_public(client, google_on):
    # the page itself contains no data, so the sign-in page can be shown
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


# ---------------------------------------------------------------- "Try all" (CHANGES.md section 14)

def test_try_all_counts_as_one_run(sign_in, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    ann = sign_in("ann@example.com")
    demo = ann.post("/api/demo/start").json()
    assert len(demo["presets"]) == 14
    dh = {"X-Demo-Token": demo["token"]}
    for p in demo["presets"]:  # 14 runs, far above the limit of 2
        r = ann.post(f"/api/pipelines/{p['pipeline']}/execute", headers=dh, data={"test_case": p["test_case"]})
        assert r.status_code == 200, (p, r.text)
    # the demo itself used 1 of the 2 runs, so exactly one normal run is left
    assert run_preset(ann).status_code == 200
    assert run_preset(ann).status_code == 429
    auth.reset_rate_limit()


def test_demo_token_cannot_be_reused_or_faked(sign_in, fake_llm, monkeypatch):
    fake_llm("UNRESOLVED")
    import src.api.auth as auth
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 2)
    auth.reset_rate_limit()
    ann = sign_in("ann@example.com")
    dh = {"X-Demo-Token": ann.post("/api/demo/start").json()["token"]}   # 1 run used
    preset = lambda headers: ann.post("/api/pipelines/a/execute", headers=headers, data={"test_case": "case_healthy.json"})
    assert preset(dh).status_code == 200      # free (part of the demo)
    assert preset(dh).status_code == 200      # same preset again: counted -> 2 of 2 used
    assert preset(dh).status_code == 429      # so now blocked
    assert preset({"X-Demo-Token": "made-up"}).status_code == 429
    # uploads are never free, even with a valid token
    auth.reset_rate_limit()
    monkeypatch.setattr(auth, "VIEWER_RUNS_PER_MINUTE", 1)
    dh = {"X-Demo-Token": ann.post("/api/demo/start").json()["token"]}   # uses the only run
    up = ann.post("/api/pipelines/a/execute", headers=dh,
                  files={"file": ("x.json", json.dumps(HEALTHY), "application/json")})
    assert up.status_code == 429
    auth.reset_rate_limit()
