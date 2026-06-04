# database/init_db.py
import os
from src.database import get_db_connection, is_postgres
from src.config import get_database_paths
paths = get_database_paths()
STATE_DB_PATH = paths["state_db"]
OPERATIONAL_DB_PATH = paths["operational_db"]
ANALYTICS_DB_PATH = paths["analytics_db"]
BASE_DIR = os.path.dirname(STATE_DB_PATH)

def _apply_performance_pragmas(conn):
    """Enforces optimal transactional properties across all engine scopes."""
    if not is_postgres():
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")

def initialize_database():
    if not is_postgres():
        os.makedirs(BASE_DIR, exist_ok=True)

    # ==========================================
    # TIER 1: PLATFORM STATE CONTROL PLANE DB
    # ==========================================
    with get_db_connection("state_db") as conn:
        _apply_performance_pragmas(conn)
        cursor = conn.cursor()
        
        # Pipeline Runs Execution Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)
        
        # Live Incident Telemetry Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                pipeline_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                error_class TEXT NOT NULL,
                error_message TEXT NOT NULL,
                stack_trace TEXT NOT NULL,
                root_cause TEXT,
                recovery_action TEXT,
                telemetry_metadata TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(run_id) REFERENCES pipeline_runs(run_id) ON DELETE CASCADE
            )
        """)
        
        # Centralized Core Audit Log (Fixed: Incorporated audit table structure)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                log_id TEXT PRIMARY KEY,
                ref_id TEXT,
                component TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        
        # Pipeline configurations (versioned configs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_configs (
                pipeline_id TEXT,
                error_signature TEXT,
                config_json TEXT NOT NULL,
                version INTEGER NOT NULL,
                is_verified INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (pipeline_id, error_signature, version)
            )
        """)
        
        # Performance Index Matrix
        if not is_postgres():
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_run_id ON incidents(run_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_incidents_pipeline_id_severity ON incidents(pipeline_id, severity);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_ref_id ON audit_logs(ref_id);")
        conn.commit()
    from src.telemetry.logger import get_pipeline_logger
    logger = get_pipeline_logger("init_db")
    logger.info("State engine initialized cleanly", state_db=STATE_DB_PATH)

    # ==========================================
    # TIER 2: OPERATIONAL TRANSACTIONAL DATA STORE
    # ==========================================
    with get_db_connection("operational_db") as conn:
        _apply_performance_pragmas(conn)
        cursor = conn.cursor()
        
        # Pipeline A Destination: Customer Records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                country TEXT,
                signup_date TEXT NOT NULL,
                status TEXT DEFAULT 'ACTIVE',
                total_orders INTEGER DEFAULT 0
            )
        """)
        
        # Pipeline B Destination & Standalone Source: device_telemetry
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS device_telemetry (
                log_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response_time REAL NOT NULL
            )
        """)
        
        # Check if empty, then seed mock data
        cursor.execute("SELECT COUNT(*) FROM device_telemetry")
        if cursor.fetchone()[0] == 0:
            mock_telemetry = [
                ("log_1", "device_alpha", "2026-06-04 08:00:00", 200, 45.2),
                ("log_2", "device_alpha", "2026-06-04 08:05:00", 200, 42.1),
                ("log_3", "device_beta", "2026-06-04 08:10:00", 200, 112.5),
                ("log_4", "device_beta", "2026-06-04 08:15:00", 500, 350.0), # Simulated failure
                ("log_5", "device_alpha", "2026-06-04 08:20:00", 200, 39.8),
                ("log_6", "device_gamma", "2026-06-04 08:25:00", 404, 15.0), # Simulated failure
                ("log_7", "device_beta", "2026-06-04 08:30:00", 200, 98.4),
            ]
            cursor.executemany(
                "INSERT INTO device_telemetry (log_id, device_id, timestamp, status_code, response_time) VALUES (?, ?, ?, ?, ?)",
                mock_telemetry
            )
            conn.commit()
            
    logger.info("Operational transactional database layout pinned", operational_db=OPERATIONAL_DB_PATH)

    # ==========================================
    # TIER 3: TARGET ANALYTICS STAGING WAREHOUSE
    # ==========================================
    with get_db_connection("analytics_db") as conn:
        _apply_performance_pragmas(conn)
        cursor = conn.cursor()
        
        # Aggregate Business Insights Domain: Device performance summary
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_performance_metrics (
                device_id TEXT PRIMARY KEY,
                total_events INTEGER,
                error_rate REAL,
                avg_response_time REAL
            )
        """)
        conn.commit()
    logger.info("Analytics data warehouse layout pinned", analytics_db=ANALYTICS_DB_PATH)

if __name__ == "__main__":
    initialize_database()