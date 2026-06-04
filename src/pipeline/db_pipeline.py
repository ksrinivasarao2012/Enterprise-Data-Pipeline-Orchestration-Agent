# src/pipeline/db_pipeline.py
import uuid
import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Resolve operational & analytics database paths cleanly
from src.config import get_database_paths
from src.telemetry.logger import get_pipeline_logger
from src.database import get_db_connection, is_postgres

logger = get_pipeline_logger("pipeline_db")
paths = get_database_paths()
BASE_DIR = os.path.dirname(paths["state_db"])
OPERATIONAL_DB_PATH = paths["operational_db"]
ANALYTICS_DB_PATH = paths["analytics_db"]

class PipelineB:
    """Workload: Database ETL Ingestion & Analytics (Operational DB -> Analytics DB)"""
    def __init__(self):
        self.pipeline_id = "PIPELINE_B"

    def execute(self, simulate_failure_type: Optional[str] = None, sql_override: Optional[str] = None, op_db_path: Optional[str] = None, original_filename: Optional[str] = None, run_id: Optional[str] = None) -> Optional[int]:
        """
        Runs the database ETL warehouse synchronization engine. 
        Extracts operational tables, aggregates data, and writes results to analytics database.
        """
        run_db_path = op_db_path if op_db_path else OPERATIONAL_DB_PATH
        if not run_id:
            run_id = f"run_b_{uuid.uuid4().hex[:6]}"
        logger.info("Starting workload", pipeline_id=self.pipeline_id, run_id=run_id)
        
        TelemetryIncidentCreator.register_start(run_id, self.pipeline_id)
        
        try:
            # 1. Handle connection level failures
            if simulate_failure_type == "CONNECTION_ERROR":
                raise sqlite3.OperationalError("Database connection timed out or database is locked (simulated).")
            
            # Ensure physical files exist or initialize dirs
            os.makedirs(BASE_DIR, exist_ok=True)
            
            # 2. Check if operational database is initialized and contains source tables
            use_pg = is_postgres() and not op_db_path
            
            if use_pg:
                conn_op = get_db_connection("operational_db")
            else:
                conn_op = sqlite3.connect(run_db_path)
                
            with conn_op as conn:
                cursor = conn.cursor()
                
                check_table = "device_telemetry"
                if simulate_failure_type == "MISSING_TABLE":
                    check_table = "non_existent_telemetry_table"
                
                if use_pg:
                    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=%s", (check_table,))
                else:
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (check_table,))
                    
                if not cursor.fetchone():
                    if use_pg:
                        raise ValueError(f"Operational source table '{check_table}' does not exist on disk.")
                    else:
                        raise sqlite3.OperationalError(f"Operational source table '{check_table}' does not exist on disk.")

            # 3. Handle programmatically simulated referential integrity failures
            if simulate_failure_type == "REFERENTIAL_INTEGRITY":
                raise ValueError("Referential Integrity Violation: Device log maps to an unknown/unregistered device_id group (simulated).")

            # 4. Extract & Aggregation layers
            # Telemetry Aggregation Query
            telemetry_query = """
                SELECT 
                    device_id, 
                    COUNT(log_id) as total_events,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) * 1.0 / COUNT(log_id) as error_rate,
                    AVG(response_time) as avg_response_time
                FROM device_telemetry
                GROUP BY device_id
            """
            
            # If user provided dynamic SQL override (for testing or query recovery), apply it
            if sql_override:
                logger.info("Dynamic SQL correction override applied by active handler agent", pipeline_id=self.pipeline_id)
                telemetry_query = sql_override

            # Handle dynamic syntax failure injection (SQL_ERROR)
            if simulate_failure_type == "SQL_ERROR":
                logger.info("Injecting invalid SQL syntax statement (simulation)", pipeline_id=self.pipeline_id)
                telemetry_query = "SELECT FROM WHERE device_id GROUP BY (syntax error simulation)"

            # Execute Extractor query
            if use_pg:
                conn_op_exec = get_db_connection("operational_db")
            else:
                conn_op_exec = sqlite3.connect(run_db_path)
                
            with conn_op_exec as conn:
                cursor = conn.cursor()
                
                try:
                    cursor.execute(telemetry_query)
                    metrics_rows = cursor.fetchall()
                except Exception as sq_err:
                    try:
                        if use_pg:
                            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='device_telemetry'")
                            columns = [row[0] for row in cursor.fetchall()]
                        else:
                            cursor.execute("PRAGMA table_info(device_telemetry)")
                            columns = [row[1] for row in cursor.fetchall()]
                        schema_info = f" Available columns in device_telemetry: {columns}."
                    except Exception:
                        schema_info = ""
                    if use_pg:
                        raise ValueError(f"Database Query Parse Error: {str(sq_err)}.{schema_info} Query: {telemetry_query}")
                    else:
                        raise sqlite3.OperationalError(f"Database Query Parse Error: {str(sq_err)}.{schema_info} Query: {telemetry_query}")

                # Check for actual database referential integrity violations
                for row in metrics_rows:
                    if row[0] is None:
                        raise ValueError("Referential Integrity Violation: Device log maps to an unknown/unregistered device_id group (NULL device_id detected).")

            # 5. Load aggregate records into analytics.db staging target tables
            records_written = 0
            with get_db_connection("analytics_db") as conn:
                cursor = conn.cursor()
                
                for r in metrics_rows:
                    cursor.execute("""
                        INSERT OR REPLACE INTO system_performance_metrics 
                        (device_id, total_events, error_rate, avg_response_time)
                        VALUES (?, ?, ?, ?)
                    """, r)
                    records_written += 1
                
                conn.commit()

            logger.info("Process complete; loaded aggregated records into warehouse", pipeline_id=self.pipeline_id, records_written=records_written)
            TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "SUCCESS")
            return records_written

        except Exception as e:
            logger.exception("Critical failure detected during DB pipeline execution", pipeline_id=self.pipeline_id, error=str(e))
            TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "FAILED")
            TelemetryIncidentCreator.capture_and_report(run_id, self.pipeline_id, e, source_path=run_db_path, original_filename=original_filename)
            return None

from src.telemetry.incident_creator import TelemetryIncidentCreator
