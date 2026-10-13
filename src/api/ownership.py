# src/api/ownership.py
"""
Who owns which run (CHANGES.md section 31).

Every pipeline run started from the dashboard is tagged with its owner in the run_owners table
(run_id -> owner). Incidents, audit lines and quarantined rows all belong to a run, so one tag
is enough to know whose they are.

    viewer -> sees only runs they own (filtered in SQL on the server, so the API itself
              never returns someone else's data, not just the page)
    admin  -> sees every run

The users table keeps the bare minimum to show "signed in as": Google id, email and name.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.api.auth import Principal
from src.database import get_db_connection

# SQL that limits a table with a run_id column to one owner's runs.
_OWNED = "run_id IN (SELECT run_id FROM run_owners WHERE owner = ?)"


def tag_run(run_id: str, owner: str) -> None:
    with get_db_connection("state_db") as conn:
        conn.execute("INSERT INTO run_owners (run_id, owner, created_at) VALUES (?, ?, ?)",
                     (run_id, owner, datetime.now(timezone.utc).isoformat()))
        conn.commit()


def scope(who: Principal | None) -> tuple[str, list]:
    """A WHERE condition (without "WHERE") and its parameters, or ("", []) for the admin."""
    if who is None or who.is_admin:
        return "", []
    return _OWNED, [who.owner]


def owns_run(who: Principal, run_id: str) -> bool:
    if who.is_admin:
        return True
    with get_db_connection("state_db") as conn:
        row = conn.execute("SELECT 1 FROM run_owners WHERE run_id = ? AND owner = ?", (run_id, who.owner)).fetchone()
    return row is not None


def clear_owner(owner: str) -> None:
    """Deletes one person's runs and everything that belongs to them. Shared tables (the loaded
    customers, pipeline configs) are left alone: other people's runs use them too."""
    runs = "SELECT run_id FROM run_owners WHERE owner = ?"
    with get_db_connection("state_db") as conn:
        conn.execute(f"DELETE FROM audit_logs WHERE ref_id IN ({runs}) "
                     f"OR ref_id IN (SELECT incident_id FROM incidents WHERE run_id IN ({runs}))", (owner, owner))
        conn.execute(f"DELETE FROM quarantined_records WHERE run_id IN ({runs})", (owner,))
        conn.execute(f"DELETE FROM incidents WHERE run_id IN ({runs})", (owner,))
        conn.execute(f"DELETE FROM pipeline_runs WHERE run_id IN ({runs})", (owner,))
        conn.execute("DELETE FROM run_owners WHERE owner = ?", (owner,))
        conn.commit()


def save_user(user: dict) -> None:
    """Remembers who signed in (Google id, email, name) and when."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection("state_db") as conn:
        conn.execute(
            "INSERT INTO users (user_id, email, name, created_at, last_login_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id) DO UPDATE SET email = excluded.email, name = excluded.name, "
            "last_login_at = excluded.last_login_at",
            (user["sub"], user["email"], user.get("name", ""), now, now),
        )
        conn.commit()
