# src/incidents/incident_repository.py
import json
from datetime import datetime, timezone
from typing import Optional
from src.models.schemas import IncidentSchema, IncidentStatus, SeverityLevel, IncidentCategory
from src.database import get_db_connection

class IncidentRepository:
    @staticmethod
    def _get_connection():
        return get_db_connection("state_db")


    @classmethod
    def create_incident(cls, incident: IncidentSchema) -> None:
        """Persists a newly created incident into the platform state db."""
        query = """
            INSERT INTO incidents (
                incident_id, run_id, pipeline_id, severity, category, 
                status, error_class, error_message, stack_trace, root_cause,
                recovery_action, telemetry_metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        # Fix #1: Guarantee cleanup via context managers
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (
                incident.incident_id,
                incident.run_id,
                incident.pipeline_id,
                incident.severity.value,
                incident.category.value,
                incident.status.value,
                incident.error_class,
                incident.error_message,
                incident.stack_trace,
                incident.root_cause,
                incident.recovery_action,
                json.dumps(incident.telemetry_metadata) if incident.telemetry_metadata is not None else None,
                incident.created_at.isoformat()
            ))
            conn.commit()

    @classmethod
    def update_incident_state(cls, incident_id: str, status: IncidentStatus, **kwargs) -> bool:
        """Dynamically updates an incident's lifecycle stage and optional diagnostic details."""
        update_fields = ["status = ?"]
        params = [status.value]

        for key in ['severity', 'category', 'root_cause', 'recovery_action']:
            if key in kwargs and kwargs[key] is not None:
                update_fields.append(f"{key} = ?")
                val = kwargs[key].value if hasattr(kwargs[key], 'value') else kwargs[key]
                params.append(val)

        if status == IncidentStatus.RESOLVED:
            update_fields.append("resolved_at = ?")
            params.append(datetime.now(timezone.utc).isoformat())

        params.append(incident_id)
        query = f"UPDATE incidents SET {', '.join(update_fields)} WHERE incident_id = ?"

        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            updated = cursor.rowcount > 0
            conn.commit()
        return updated

    @classmethod
    def get_incident(cls, incident_id: str) -> Optional[IncidentSchema]:
        """Retrieves a single incident and casts it back to its typed Pydantic representation."""
        query = "SELECT * FROM incidents WHERE incident_id = ?"
        
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (incident_id,))
            row = cursor.fetchone()
            if not row:
                return None
            columns = [col[0] for col in cursor.description]

        row_dict = dict(zip(columns, row))
        
        # Fix #3: Explicit parsing protection before Pydantic hydration
        if row_dict.get("created_at") and isinstance(row_dict["created_at"], str):
            row_dict["created_at"] = datetime.fromisoformat(row_dict["created_at"])
        if row_dict.get("resolved_at") and isinstance(row_dict["resolved_at"], str):
            row_dict["resolved_at"] = datetime.fromisoformat(row_dict["resolved_at"])
        if row_dict.get("telemetry_metadata") and isinstance(row_dict["telemetry_metadata"], str):
            try:
                row_dict["telemetry_metadata"] = json.loads(row_dict["telemetry_metadata"])
            except Exception:
                row_dict["telemetry_metadata"] = None

        return IncidentSchema(**row_dict)

    @classmethod
    def count_incidents_for_run(cls, run_id: str) -> int:
        """Number of incidents already raised for one pipeline run (used by the heal-attempt cap)."""
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _row_to_incident(row_dict: dict) -> IncidentSchema:
        if row_dict.get("created_at") and isinstance(row_dict["created_at"], str):
            row_dict["created_at"] = datetime.fromisoformat(row_dict["created_at"])
        if row_dict.get("resolved_at") and isinstance(row_dict["resolved_at"], str):
            row_dict["resolved_at"] = datetime.fromisoformat(row_dict["resolved_at"])
        if row_dict.get("telemetry_metadata") and isinstance(row_dict["telemetry_metadata"], str):
            try:
                row_dict["telemetry_metadata"] = json.loads(row_dict["telemetry_metadata"])
            except Exception:
                row_dict["telemetry_metadata"] = None
        return IncidentSchema(**row_dict)

    # Incidents the validation rules handled alone (quarantine only, no agents).
    QUARANTINE_ONLY_CLASSES = ("DataQualityAnomaly", "ReferentialIntegrityAnomaly")
    OUTCOMES = ("healed", "quarantined", "escalated", "in_progress")

    @classmethod
    def _filter_sql(cls, pipeline: str | None, outcome: str | None, search: str | None,
                    blocked: bool | None = None, scope: tuple[str, list] = ("", [])) -> tuple[str, list]:
        """WHERE clause for the dashboard filters (CHANGES.md section 18). Values are always
        passed as parameters, never pasted into the SQL. `scope` limits it to one person's runs
        (src/api/ownership.py, CHANGES.md section 31)."""
        where, params = [], []
        if scope[0]:
            where.append(scope[0])
            params += list(scope[1])
        if pipeline:
            where.append("pipeline_id = ?")
            params.append(pipeline)
        q_only = ", ".join("?" for _ in cls.QUARANTINE_ONLY_CLASSES)
        if outcome == "healed":
            where.append(f"status = 'RESOLVED' AND COALESCE(error_class, '') NOT IN ({q_only})")
            params += cls.QUARANTINE_ONLY_CLASSES
        elif outcome == "quarantined":
            where.append(f"status = 'RESOLVED' AND error_class IN ({q_only})")
            params += cls.QUARANTINE_ONLY_CLASSES
        elif outcome == "escalated":
            where.append("status = 'ESCALATED'")
        elif outcome == "in_progress":
            where.append("status NOT IN ('RESOLVED', 'ESCALATED')")
        if blocked is not None:
            where.append("blocked_by_guard = ?")
            params.append(1 if blocked else 0)
        if search:
            # % and _ are wildcards in LIKE; escape them so they are searched as normal letters.
            term = "%" + search.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            cols = ["incident_id", "run_id", "error_class", "error_message", "category", "root_cause"]
            where.append("(" + " OR ".join(f"LOWER(COALESCE({c}, '')) LIKE ? ESCAPE '\\'" for c in cols) + ")")
            params += [term] * len(cols)
        return (" WHERE " + " AND ".join(where)) if where else "", params

    @classmethod
    def get_incidents_page(cls, limit: int, offset: int = 0, pipeline: str | None = None,
                           outcome: str | None = None, search: str | None = None,
                           blocked: bool | None = None,
                           scope: tuple[str, list] = ("", [])) -> tuple[list[IncidentSchema], int]:
        """One page of incidents, newest first, plus the total that match the filters.
        Used by the dashboard, which refreshes every few seconds."""
        where, params = cls._filter_sql(pipeline, outcome, search, blocked, scope)
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM incidents{where}", tuple(params))
            total = cursor.fetchone()[0]
            cursor.execute(f"SELECT * FROM incidents{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                           tuple(params) + (limit, offset))
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
        return [cls._row_to_incident(dict(zip(columns, row))) for row in rows], total

    @classmethod
    def mark_blocked_by_guard(cls, incident_id: str) -> None:
        """Records that a guard stopped an AI fix for this incident. Never raises: a failed mark
        must not break healing (the audit log still has the reason)."""
        try:
            with cls._get_connection() as conn:
                conn.execute("UPDATE incidents SET blocked_by_guard = 1 WHERE incident_id = ?", (incident_id,))
                conn.commit()
        except Exception:
            from src.telemetry.logger import get_pipeline_logger
            get_pipeline_logger("incident_repository").exception("Could not mark incident as blocked by guard", incident_id=incident_id)

    @classmethod
    def count_blocked_by_guard(cls) -> int:
        """How many incidents a guard blocked, in total (the dashboard badge)."""
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE blocked_by_guard = 1")
            return cursor.fetchone()[0]

    @classmethod
    def count_unresolved_for_pipeline(cls, pipeline_id: str, exclude_incident_id: str | None = None) -> int:
        """Counts a pipeline's incidents that are not RESOLVED, in SQL (no need to load them all)."""
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM incidents WHERE pipeline_id = ? AND status != 'RESOLVED' AND incident_id != ?",
                (pipeline_id, exclude_incident_id or ""),
            )
            return cursor.fetchone()[0]

    @classmethod
    def get_all_incidents(cls) -> list[IncidentSchema]:
        """Retrieves all incidents from the repository."""
        query = "SELECT * FROM incidents ORDER BY created_at DESC"
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
        
        incidents = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            if row_dict.get("created_at") and isinstance(row_dict["created_at"], str):
                row_dict["created_at"] = datetime.fromisoformat(row_dict["created_at"])
            if row_dict.get("resolved_at") and isinstance(row_dict["resolved_at"], str):
                row_dict["resolved_at"] = datetime.fromisoformat(row_dict["resolved_at"])
            if row_dict.get("telemetry_metadata") and isinstance(row_dict["telemetry_metadata"], str):
                try:
                    row_dict["telemetry_metadata"] = json.loads(row_dict["telemetry_metadata"])
                except Exception:
                    row_dict["telemetry_metadata"] = None
            incidents.append(IncidentSchema(**row_dict))
        return incidents

    @classmethod
    def get_audit_trail(cls, ref_id: str) -> list[dict]:
        """Retrieves the audit log history for a specific incident or pipeline execution context."""
        query = "SELECT component, message, timestamp FROM audit_logs WHERE ref_id = ? ORDER BY timestamp ASC"
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (ref_id,))
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in rows]