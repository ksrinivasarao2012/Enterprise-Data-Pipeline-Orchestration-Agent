# src/services/audit_service.py
import uuid
from datetime import datetime, timezone
from typing import Optional # Fix #1: Resolved missing typing dependency import
from src.database import get_db_connection

class AuditService:
    @staticmethod
    def _get_connection():
        return get_db_connection("state_db")


    @classmethod
    def log_event(cls, ref_id: Optional[str], component: str, message: str) -> str:
        """Appends a secure, timestamped entry to the operational audit pool."""
        log_id = f"aud_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        
        query = """
            INSERT INTO audit_logs (log_id, ref_id, component, message, timestamp) 
            VALUES (?, ?, ?, ?, ?)
        """
        
        # Fix #2 & #3: Extracted DDL out of execution path and added context safety
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (log_id, ref_id, component, message, now))
            conn.commit()
            
        return log_id