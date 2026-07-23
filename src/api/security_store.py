"""
Database storage for login and run limits (CHANGES.md section 19).

Purpose (simple words):
    Before, these counters lived in the server's memory. A restart wiped them, and on Vercel
    every server copy had its own counters, so the limits were weaker than they looked.
    Now they live in the state database (SQLite locally, Postgres on Vercel), so every copy
    reads the same numbers and a restart changes nothing.

Tables (created in database/init_db.py):
    auth_failures (client_id, failed_at)                     - wrong keys, for the lockout
    run_events    (client_id, started_at)                    - viewer runs, for the runs-per-minute limit
    demo_grants   (token, pipeline, test_case, expires_at)   - "Try all": one row per free preset run

If the database cannot be reached, the checks are skipped (logged) rather than locking
everyone out; the access keys themselves are still required.
"""
import time

from src.database import get_db_connection
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("security_store")

_KEEP_SECONDS = 24 * 3600  # old rows are deleted now and then so the tables stay small


def _now() -> float:
    return time.time()  # wall clock: the same on every server copy (unlike time.monotonic)


# ---------------------------------------------------------------- wrong keys (lockout)

def count_failures(client_id: str, window_seconds: float) -> tuple[int, float | None]:
    """How many wrong keys this client sent in the window, and when the oldest of them was."""
    with get_db_connection("state_db") as conn:
        row = conn.execute(
            "SELECT COUNT(*), MIN(failed_at) FROM auth_failures WHERE client_id = ? AND failed_at > ?",
            (client_id, _now() - window_seconds),
        ).fetchone()
    return int(row[0] or 0), row[1]


def record_failure(client_id: str) -> None:
    with get_db_connection("state_db") as conn:
        conn.execute("INSERT INTO auth_failures (client_id, failed_at) VALUES (?, ?)", (client_id, _now()))
        conn.execute("DELETE FROM auth_failures WHERE failed_at < ?", (_now() - _KEEP_SECONDS,))


def clear_failures(client_id: str) -> None:
    with get_db_connection("state_db") as conn:
        conn.execute("DELETE FROM auth_failures WHERE client_id = ?", (client_id,))


# ---------------------------------------------------------------- viewer runs per minute

def count_runs(client_id: str, window_seconds: float = 60) -> int:
    with get_db_connection("state_db") as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM run_events WHERE client_id = ? AND started_at > ?",
            (client_id, _now() - window_seconds),
        ).fetchone()
    return int(row[0] or 0)


def record_run(client_id: str) -> None:
    with get_db_connection("state_db") as conn:
        conn.execute("INSERT INTO run_events (client_id, started_at) VALUES (?, ?)", (client_id, _now()))
        conn.execute("DELETE FROM run_events WHERE started_at < ?", (_now() - _KEEP_SECONDS,))


# ---------------------------------------------------------------- "Try all" demo grants

def save_demo_grant(token: str, presets: list[tuple[str, str]], ttl_seconds: float) -> None:
    expires = _now() + ttl_seconds
    with get_db_connection("state_db") as conn:
        conn.execute("DELETE FROM demo_grants WHERE expires_at < ?", (_now(),))
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO demo_grants (token, pipeline, test_case, expires_at) VALUES (?, ?, ?, ?)",
            [(token, pipe, case, expires) for pipe, case in presets],
        )


def use_demo_grant(token: str, pipeline: str, test_case: str) -> bool:
    """Deletes the grant row and returns True if it existed and had not expired.
    One DELETE, so two server copies can never both use the same grant."""
    with get_db_connection("state_db") as conn:
        cursor = conn.execute(
            "DELETE FROM demo_grants WHERE token = ? AND pipeline = ? AND test_case = ? AND expires_at > ?",
            (token, pipeline, test_case, _now()),
        )
        return (cursor.rowcount or 0) > 0


# ---------------------------------------------------------------- tests

def clear_all() -> None:
    with get_db_connection("state_db") as conn:
        for table in ("auth_failures", "run_events", "demo_grants"):
            conn.execute(f"DELETE FROM {table}")
