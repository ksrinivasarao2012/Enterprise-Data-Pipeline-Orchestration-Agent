# src/services/audit_service.py
import sqlite3
import os
import uuid
from datetime import datetime, timezone
from typing import Optional # Fix #1: Resolved missing typing dependency import

from src.config import get_database_paths
DB_PATH = get_database_paths()["state_db"]

class AuditService:
    @staticmethod
    def _get_connection():
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        # Fix #3: Tuning the engine context parameters for rapid event streaming
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

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