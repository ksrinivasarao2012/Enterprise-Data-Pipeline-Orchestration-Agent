# src/services/pipeline_service.py
import sqlite3
import os
from datetime import datetime, timezone
from src.models.schemas import PipelineStatus, PipelineRunSchema

# Explicitly isolate the control plane tier state database path
from src.config import get_database_paths
STATE_DB_PATH = get_database_paths()["state_db"]

class PipelineService:
    @staticmethod
    def _get_connection():
        os.makedirs(os.path.dirname(STATE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(STATE_DB_PATH)
        # Enforce consistent concurrency rules across all service domains
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @classmethod
    def start_run(cls, run_id: str, pipeline_id: str) -> PipelineRunSchema:
        """
        Registers a fresh running workload in the platform control plane.
        Idempotent operation prevents crashes on transient network retries.
        """
        now = datetime.now(timezone.utc)
        run = PipelineRunSchema(
            run_id=run_id,
            pipeline_id=pipeline_id,
            status=PipelineStatus.RUNNING,
            started_at=now
        )
        
        query = """
            INSERT OR IGNORE INTO pipeline_runs (run_id, pipeline_id, status, started_at) 
            VALUES (?, ?, ?, ?)
        """
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                query, 
                (run.run_id, run.pipeline_id, run.status.value, run.started_at.isoformat())
            )
            conn.commit()
            
        return run

    @classmethod
    def update_run_status(cls, run_id: str, status: PipelineStatus) -> bool:
        """Transitions the structural state of an active workload execution."""
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            
            if status in [PipelineStatus.SUCCESS, PipelineStatus.FAILED, PipelineStatus.HEALED]:
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "UPDATE pipeline_runs SET status = ?, ended_at = ? WHERE run_id = ?",
                    (status.value, now, run_id)
                )
            else:
                cursor.execute(
                    "UPDATE pipeline_runs SET status = ? WHERE run_id = ?",
                    (status.value, run_id)
                )
                
            updated = cursor.rowcount > 0
            conn.commit()
            
        return updated