# src/incidents/guard_messages.py
"""
How a "blocked by guard" incident is recognised (CHANGES.md section 26).

A guard blocks an AI fix in three ways, and each one writes a known sentence:
    SQL guard          "SQL guard rejected the LLM repair: ..."
    JSON repair check  "LLM file repair rejected: ..."
    UNRESOLVABLE       "LLM reported the SQL error as UNRESOLVABLE ..."

New incidents are marked when the block happens (IncidentRepository.mark_blocked_by_guard).
Old incidents, made before the column existed, are marked once by a small update query that
looks for the same sentences in their audit log. Both use this one list, so they cannot drift apart.
The frontend (src/lib/incident.ts) keeps the same three patterns to show the reason text.
"""

GUARD_PHRASES = ("SQL guard rejected", "LLM file repair rejected", "UNRESOLVABLE")

# Only the agents' own audit lines count; a root-cause text written by Gemini must never trigger it.
_AUDIT_COMPONENTS = ("RECOVERY_AGENT", "ACTUATOR")


def backfill_sql_and_params() -> tuple[str, tuple]:
    """One UPDATE that marks every incident with a guard sentence in its audit log or recovery action."""
    audit_like = " OR ".join("message LIKE ?" for _ in GUARD_PHRASES)
    action_like = " OR ".join("recovery_action LIKE ?" for _ in GUARD_PHRASES)
    components = ", ".join("?" for _ in _AUDIT_COMPONENTS)
    sql = (
        "UPDATE incidents SET blocked_by_guard = 1 WHERE blocked_by_guard = 0 AND ("
        f"incident_id IN (SELECT ref_id FROM audit_logs WHERE component IN ({components}) AND ({audit_like}))"
        f" OR ({action_like}))"
    )
    likes = tuple(f"%{p}%" for p in GUARD_PHRASES)
    return sql, _AUDIT_COMPONENTS + likes + likes
