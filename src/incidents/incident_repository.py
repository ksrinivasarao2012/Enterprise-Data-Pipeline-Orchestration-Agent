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