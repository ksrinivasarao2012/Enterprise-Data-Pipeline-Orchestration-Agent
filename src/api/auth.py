# src/api/auth.py
"""
Login (authentication) and permissions (authorization) for the control plane API.

Purpose (simple words):
    Without this, anyone who opens the dashboard can do everything, including
    "Clear History", which deletes all data. Now every API request must carry a key.

Two keys (you create them yourself, see README "Security"):
    EHA_VIEWER_KEY  -> "viewer": see everything, run preset tests, upload own files
                       (smaller size limit and a runs-per-minute limit)
    EHA_ADMIN_KEY   -> "admin":  everything, including Clear History and the telemetry API

The browser sends the key in the "X-Access-Key" header.

No keys configured:
    - locally  -> open mode (everyone is admin), so development stays easy
    - on Vercel -> everything is locked (a public deployment is never left open by accident)
"""
import math
import os
import secrets
import time

from fastapi import HTTPException, Request

from src.api import security_store as store
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("auth")

HEADER = "X-Access-Key"

# Viewers may start at most this many pipeline runs per minute (protects the Gemini quota).
VIEWER_RUNS_PER_MINUTE = int(os.environ.get("EHA_VIEWER_RUNS_PER_MINUTE", "10"))

# Lockout (CHANGES.md section 19): this many wrong keys from one client within the window
# locks that client out for the rest of the window, even if it then sends the right key.
MAX_FAILED_KEYS = int(os.environ.get("EHA_MAX_FAILED_KEYS", "10"))
LOCKOUT_SECONDS = int(float(os.environ.get("EHA_LOCKOUT_MINUTES", "15")) * 60)

# A client that just passed the lockout check is not checked again for this long, so the
# dashboard (which polls every few seconds) does not hit the database on every request.
# Only clients that sent a CORRECT key are cached; wrong keys are always checked.
_OK_CACHE_SECONDS = 30
_ok_until: dict[str, float] = {}

_warned_open_mode = False


def _keys() -> tuple[str, str]:
    # Read on every request, so a changed key works without code changes.
    return os.environ.get("EHA_ADMIN_KEY", "").strip(), os.environ.get("EHA_VIEWER_KEY", "").strip()


def auth_enabled() -> bool:
    admin_key, viewer_key = _keys()
    return bool(admin_key or viewer_key)


def _on_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def get_role(request: Request) -> str:
    """Returns 'admin' or 'viewer', or raises 401 if the key is missing or wrong."""
    global _warned_open_mode
    admin_key, viewer_key = _keys()

    if not admin_key and not viewer_key:
        if _on_vercel():
            raise HTTPException(status_code=401, detail="Server has no access keys configured, so it is locked.")
        if not _warned_open_mode:
            logger.warning("No EHA_ADMIN_KEY / EHA_VIEWER_KEY set: running in OPEN local mode (no login).")
            _warned_open_mode = True
        return "admin"

    sent = request.headers.get(HEADER, "")
    # compare_digest takes the same time whether the first or last letter is wrong,
    # so a key can't be guessed letter by letter from response times.
    role = None
    if sent and admin_key and secrets.compare_digest(sent.encode(), admin_key.encode()):
        role = "admin"
    elif sent and viewer_key and secrets.compare_digest(sent.encode(), viewer_key.encode()):
        role = "viewer"

    if not sent:
        # No key at all (e.g. the login screen asking "am I logged in?"): not a guess, not counted.
        raise HTTPException(status_code=401, detail="Missing or wrong access key. Please log in.")

    client = client_id(request)
    if role and _ok_until.get(client, 0) > time.monotonic():
        return role
    _check_not_locked(client)  # a locked-out client is refused even with the right key
    if role:
        _ok_until[client] = time.monotonic() + _OK_CACHE_SECONDS
        return role

    _record_wrong_key(client)
    raise HTTPException(status_code=401, detail="Missing or wrong access key. Please log in.")


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


def require_viewer(request: Request) -> str:
    """Any valid key (viewer or admin)."""
    return get_role(request)


def require_admin(request: Request) -> str:
    role = get_role(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only the admin can do this.")
    return role


def client_id(request: Request) -> str:
    """Who is calling, for the limits. On Vercel the real visitor address is in x-forwarded-for,
    which Vercel sets itself. Locally there is no proxy, so that header is ignored: anyone can
    send it, and trusting it would let a caller dodge the limits by making up addresses."""
    if os.environ.get("VERCEL"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# "Try all" demo (CHANGES.md section 14): starting a demo counts as ONE run. It returns a
# token that lets each preset run once more, without counting, for the next few minutes.
DEMO_HEADER = "X-Demo-Token"
DEMO_TTL_SECONDS = 600


def start_demo(request: Request, role: str, presets: list[tuple[str, str]]) -> str:
    """Counts as one run for the limit, then grants each (pipeline, test_case) preset one free run.
    The grants are stored in the database, so any server copy can honour them."""
    check_run_limit(request, role)
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


def check_run_limit(request: Request, role: str, preset: tuple[str, str] | None = None) -> None:
    """Viewers: at most VIEWER_RUNS_PER_MINUTE pipeline runs per minute. Admin: no limit.
    A preset run that is part of a "Try all" demo (valid X-Demo-Token) is not counted again.
    The counts are in the database, so restarts and extra server copies don't reset them."""
    if role == "admin":
        return
    if _use_demo_grant(request, preset):
        return
    client = client_id(request)
    try:
        used = store.count_runs(client)
    except Exception as e:  # database down: don't block runs because of the limiter itself
        logger.error("Run limit check failed; allowing run", error=str(e))
        return
    if used >= VIEWER_RUNS_PER_MINUTE:
        raise HTTPException(status_code=429,
                            detail=f"Too many runs. Viewers can start {VIEWER_RUNS_PER_MINUTE} runs per minute.")
    store.record_run(client)


def reset_rate_limit() -> None:
    """Tests only: forget all limits, lockouts and demo grants."""
    _ok_until.clear()
    store.clear_all()
