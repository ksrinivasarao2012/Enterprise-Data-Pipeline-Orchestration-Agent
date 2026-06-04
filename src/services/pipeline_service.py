# src/services/pipeline_service.py
from datetime import datetime, timezone
from src.models.schemas import PipelineStatus, PipelineRunSchema
from src.database import get_db_connection

class PipelineService:
    @staticmethod
    def _get_connection():
        return get_db_connection("state_db")


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
            
            if status in [PipelineStatus.SUCCESS, PipelineStatus.FAILED, PipelineStatus.HEALED, PipelineStatus.QUARANTINED]:
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