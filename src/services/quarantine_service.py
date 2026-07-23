# src/services/quarantine_service.py
import json
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any
from src.database import get_db_connection
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("quarantine_service")

class QuarantineService:
    @staticmethod
    def _get_connection():
        return get_db_connection("state_db")

    @classmethod
    def quarantine_record(
        cls, 
        pipeline_id: str, 
        run_id: str, 
        record_type: str, 
        raw_record: Dict[str, Any], 
        validation_errors: List[str]
    ) -> str:
        """Saves a poisoned or invalid record to the quarantined_records table."""
        quarantine_id = f"qnt_{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(timezone.utc).isoformat()
        
        query = """
            INSERT INTO quarantined_records 
            (quarantine_id, pipeline_id, run_id, record_type, raw_record, validation_errors, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        
        try:
            with cls._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    query,
                    (
                        quarantine_id,
                        pipeline_id,
                        run_id,
                        record_type,
                        json.dumps(raw_record),
                        json.dumps(validation_errors),
                        created_at
                    )
                )
                conn.commit()
            logger.info("Successfully quarantined anomalous record", quarantine_id=quarantine_id, pipeline_id=pipeline_id, run_id=run_id)
            return quarantine_id
        except Exception as e:
            logger.error("Failed to quarantine anomalous record", error=str(e), run_id=run_id)
            raise e

    @classmethod
    def get_quarantined_records(cls, limit: int | None = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Quarantined records, newest first. With `limit`, only that page (CHANGES.md section 17)."""
        query = """
            SELECT quarantine_id, pipeline_id, run_id, record_type, raw_record, validation_errors, created_at
            FROM quarantined_records
            ORDER BY created_at DESC
        """
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params = (limit, offset)
        records = []
        try:
            with cls._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                for row in rows:
                    records.append({
                        "quarantine_id": row[0],
                        "pipeline_id": row[1],
                        "run_id": row[2],
                        "record_type": row[3],
                        "raw_record": json.loads(row[4]) if row[4] else {},
                        "validation_errors": json.loads(row[5]) if row[5] else [],
                        "created_at": row[6]
                    })
        except Exception as e:
            logger.error("Failed to fetch quarantined records", error=str(e))
        return records

    @classmethod
    def count_quarantined_records(cls) -> int:
        with cls._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM quarantined_records")
            return cursor.fetchone()[0]

    @classmethod
    def clear_quarantine(cls) -> None:
        """Completely clears the quarantined records table."""
        try:
            with cls._get_connection() as conn:
                conn.execute("DELETE FROM quarantined_records")
                conn.commit()
            logger.info("Cleared all quarantined records successfully")
        except Exception as e:
            logger.error("Failed to clear quarantined records", error=str(e))
            raise e
