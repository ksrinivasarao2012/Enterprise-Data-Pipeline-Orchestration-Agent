# src/api/auth.py
"""
Login (authentication) and permissions (authorization) for the control plane API
(CHANGES.md section 31).

How people log in (simple words):
    1. The dashboard shows a "Sign in with Google" button.
    2. Google gives the browser a signed ID token. The server checks it with Google's public
       keys (google-auth): right app (GOOGLE_CLIENT_ID), not expired, email verified.
    3. The server then sets its own login cookie: signed (HMAC), HttpOnly, 7 days.
       No passwords are stored anywhere.

Who is who:
    admin   -> a Google account whose email is in EHA_ADMIN_EMAILS (comma separated).
               Sees everyone's runs and can clear all history.
    viewer  -> anyone else who signs in with Google. Sees ONLY their own runs
               (see src/api/ownership.py) and can clear only their own history.

Scripts: EHA_ADMIN_KEY in the "X-Access-Key" header still gives admin rights, for scripts
such as scripts/measure.py --url. The dashboard never asks for it. Wrong keys count
towards the lockout (CHANGES.md section 19).

Nothing configured (no GOOGLE_CLIENT_ID and no EHA_ADMIN_KEY):
    - locally   -> open mode: no login, you are admin (owner "local"), so development stays easy
    - on Vercel -> everything is locked (a public deployment is never left open by accident)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from src.api import security_store as store
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("auth")

HEADER = "X-Access-Key"
SESSION_COOKIE = "eha_session"
SESSION_DAYS = 7
# Cookie logins must send this header on every request that changes something. A page on
# another site cannot add a custom header to a cross-site request without the browser asking
# this server first (CORS), so this stops cross-site request forgery (CSRF).
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "eha"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Viewers may start at most this many pipeline runs per minute (protects the Gemini quota).
VIEWER_RUNS_PER_MINUTE = int(os.environ.get("EHA_VIEWER_RUNS_PER_MINUTE", "10"))

# Lockout (CHANGES.md section 19): this many wrong admin keys from one client within the window
# locks that client out for the rest of the window, even if it then sends the right key.
MAX_FAILED_KEYS = int(os.environ.get("EHA_MAX_FAILED_KEYS", "10"))
LOCKOUT_SECONDS = int(float(os.environ.get("EHA_LOCKOUT_MINUTES", "15")) * 60)

# A client that just passed the lockout check is not checked again for this long, so a script
# that polls does not hit the database on every request. Only correct keys are cached.
_OK_CACHE_SECONDS = 30
_ok_until: dict[str, float] = {}

_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


# ---------------------------------------------------------------- who is calling

@dataclass(frozen=True)
class Principal:
    """The signed-in caller. `owner` tags their runs; `role` decides what they may do."""
    role: str             # "admin" | "viewer"
    owner: str            # "g:<google id>", "admin-key" or "local"
    via: str              # "google" | "key" | "open"
    email: str = ""
    name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def limit_id(self) -> str:
        """What the run limit counts against: the person when signed in, else the address."""
        return f"user:{self.owner}" if self.via == "google" else ""


def _admin_key() -> str:
    return os.environ.get("EHA_ADMIN_KEY", "").strip()


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def admin_emails() -> set[str]:
    raw = os.environ.get("EHA_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _on_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def auth_mode() -> str:
    """'google' when Google sign-in is set up, 'key' when only the script key is set,
    'open' locally with nothing set, 'locked' on Vercel with nothing set."""
    if os.environ.get("EHA_VIEWER_KEY", "").strip():
        _warn_once("viewer_key", "EHA_VIEWER_KEY is no longer used: viewers sign in with Google (CHANGES.md section 31).")
    if google_client_id():
        return "google"
    if _admin_key():
        return "key"
    return "locked" if _on_vercel() else "open"


def auth_enabled() -> bool:
    return auth_mode() != "open"


def get_principal(request: Request) -> Principal:
    """The caller, or 401 if they are not signed in. Checked on every API request."""
    mode = auth_mode()
    if mode == "open":
        _warn_once("open", "No GOOGLE_CLIENT_ID / EHA_ADMIN_KEY set: running in OPEN local mode (no login).")
        return Principal(role="admin", owner="local", via="open")
    if mode == "locked":
        raise HTTPException(status_code=401, detail="Sign-in is not set up on this server, so it is locked.")

    # 1. Script key (admin only).
    sent = request.headers.get(HEADER, "")
    if sent:
        return _check_admin_key(request, sent)

    # 2. Google login cookie.
    session = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if session is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing request header. Reload the page and try again.")
    return principal_for(session)


def principal_for(session: dict) -> Principal:
    email = str(session.get("email", "")).lower()
    role = "admin" if email and email in admin_emails() else "viewer"
    return Principal(role=role, owner=f"g:{session['sub']}", via="google", email=email, name=str(session.get("name", "")))


def _check_admin_key(request: Request, sent: str) -> Principal:
    admin_key = _admin_key()
    client = client_id(request)
    # compare_digest takes the same time whether the first or last letter is wrong,
    # so a key can't be guessed letter by letter from response times.
    ok = bool(admin_key) and secrets.compare_digest(sent.encode(), admin_key.encode())
    if ok and _ok_until.get(client, 0) > time.monotonic():
        return Principal(role="admin", owner="admin-key", via="key")
    _check_not_locked(client)  # a locked-out client is refused even with the right key
    if ok:
        _ok_until[client] = time.monotonic() + _OK_CACHE_SECONDS
        return Principal(role="admin", owner="admin-key", via="key")
    _record_wrong_key(client)
    raise HTTPException(status_code=401, detail="Wrong access key.")


def _check_not_locked(client: str) -> None:
    try:
        failures, oldest = store.count_failures(client, LOCKOUT_SECONDS)
    except Exception as e:  # database down: keys are still required, just no lockout
        logger.error("Lockout check failed; allowing request", error=str(e))
        return
    if failures >= MAX_FAILED_KEYS:
        wait = max(1, math.ceil((oldest or time.time()) + LOCKOUT_SECONDS - time.time()))
        minutes = max(1, math.ceil(wait / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong keys. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(wait)},
        )


def _record_wrong_key(client: str) -> None:
    _ok_until.pop(client, None)
    try:
        store.record_failure(client)
    except Exception as e:
        logger.error("Could not record wrong key", error=str(e))
    logger.warning("Wrong access key", client=client)


def require_user(request: Request) -> Principal:
    """Anyone signed in (viewer or admin)."""
    return get_principal(request)


def require_admin(request: Request) -> Principal:
    who = get_principal(request)
    if not who.is_admin:
        raise HTTPException(status_code=403, detail="Only the admin can do this.")
    return who


def client_id(request: Request) -> str:
    """The caller's address, for the key lockout. On Vercel the real visitor address is in
    x-forwarded-for, which Vercel sets itself. Locally there is no proxy, so that header is
    ignored: anyone can send it, and trusting it would let a caller dodge the limits."""
    if os.environ.get("VERCEL"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- Google sign-in

def verify_google_token(credential: str) -> dict:
    """Checks a Google ID token and returns {sub, email, name}. Raises ValueError if it is not
    valid: wrong signature, wrong app (audience), expired, or the email is not verified.
    (Tests replace this function, so they need no real Google account.)"""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    info = id_token.verify_oauth2_token(credential, google_requests.Request(), google_client_id(),
                                        clock_skew_in_seconds=10)
    if not info.get("email_verified"):
        raise ValueError("Google says this email address is not verified.")
    return {"sub": str(info["sub"]), "email": str(info.get("email", "")).lower(), "name": str(info.get("name", ""))}


def _session_secret() -> bytes:
    secret = os.environ.get("EHA_SESSION_SECRET", "").strip()
    if secret:
        return secret.encode()
    if _on_vercel():
        # Every server copy must share one secret, or a login made on one copy fails on another.
        raise HTTPException(status_code=500, detail="EHA_SESSION_SECRET is not set on the server.")
    _warn_once("session_secret", "EHA_SESSION_SECRET not set: using a random one, so logins end when the server restarts.")
    global _random_secret
    if not _random_secret:
        _random_secret = secrets.token_bytes(32)
    return _random_secret


_random_secret = b""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(user: dict, now: float | None = None) -> str:
    """payload.signature, both base64. The payload says who and until when; the signature
    proves this server made it, so it can't be edited (e.g. to change the email)."""
    payload = {"sub": user["sub"], "email": user["email"], "name": user.get("name", ""),
               "exp": int((now or time.time()) + SESSION_DAYS * 86400)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(cookie: str) -> dict | None:
    """The session payload, or None if the cookie is missing, edited or expired."""
    if not cookie or "." not in cookie:
        return None
    body, sig = cookie.rsplit(".", 1)
    try:
        expected = _b64(hmac.new(_session_secret(), body.encode(), hashlib.sha256).digest())
    except HTTPException:
        return None
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "sub" not in payload or payload.get("exp", 0) < time.time():
        return None
    return payload


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
        secure=request.url.scheme == "https" or _on_vercel(), samesite="lax", path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---------------------------------------------------------------- run limits and "Try all"

# "Try all" demo (CHANGES.md section 14): starting a demo counts as ONE run. It returns a
# token that lets each preset run once more, without counting, for the next few minutes.
DEMO_HEADER = "X-Demo-Token"
DEMO_TTL_SECONDS = 600


def _limit_key(request: Request, who: Principal) -> str:
    return who.limit_id or client_id(request)


def start_demo(request: Request, who: Principal, presets: list[tuple[str, str]]) -> str:
    """Counts as one run for the limit, then grants each (pipeline, test_case) preset one free run.
    The grants are stored in the database, so any server copy can honour them."""
    check_run_limit(request, who)
    token = secrets.token_urlsafe(16)
    store.save_demo_grant(token, presets, DEMO_TTL_SECONDS)
    return token


def _use_demo_grant(request: Request, preset: tuple[str, str] | None) -> bool:
    token = request.headers.get(DEMO_HEADER, "")
    if not token or preset is None or not preset[1]:
        return False
    try:
        return store.use_demo_grant(token, preset[0], preset[1])  # each preset runs once per demo
    except Exception as e:
        logger.error("Demo grant check failed", error=str(e))
        return False


def check_run_limit(request: Request, who: Principal, preset: tuple[str, str] | None = None) -> None:
    """Viewers: at most VIEWER_RUNS_PER_MINUTE pipeline runs per minute, counted per person
    (per Google account), not per address. Admin: no limit. A preset run that is part of a
    "Try all" demo (valid X-Demo-Token) is not counted again. The counts are in the database,
    so restarts and extra server copies don't reset them."""
    if who.is_admin:
        return
    if _use_demo_grant(request, preset):
        return
    key = _limit_key(request, who)
    try:
        used = store.count_runs(key)
    except Exception as e:  # database down: don't block runs because of the limiter itself
        logger.error("Run limit check failed; allowing run", error=str(e))
        return
    if used >= VIEWER_RUNS_PER_MINUTE:
        raise HTTPException(status_code=429,
                            detail=f"Too many runs. Viewers can start {VIEWER_RUNS_PER_MINUTE} runs per minute.")
    store.record_run(key)


def reset_rate_limit() -> None:
    """Tests only: forget all limits, lockouts and demo grants."""
    _ok_until.clear()
    store.clear_all()
