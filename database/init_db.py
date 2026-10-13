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

def _ensure_blocked_by_guard_column(cursor) -> bool:
    """Adds incidents.blocked_by_guard if it is missing. Returns True when it was just added."""
    if is_postgres():
        cursor.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'incidents' AND column_name = 'blocked_by_guard'"
        )
        if cursor.fetchone():
            return False
    else:
        cursor.execute("PRAGMA table_info(incidents)")
        if any(row[1] == "blocked_by_guard" for row in cursor.fetchall()):
            return False
    cursor.execute("ALTER TABLE incidents ADD COLUMN blocked_by_guard INTEGER NOT NULL DEFAULT 0")
    return True


def _backfill_blocked_by_guard(cursor) -> None:
    """Marks old incidents whose audit log (or recovery action) shows a guard block."""
    from src.incidents.guard_messages import backfill_sql_and_params
    sql, params = backfill_sql_and_params()
    cursor.execute(sql, params)


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
                blocked_by_guard INTEGER NOT NULL DEFAULT 0,
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
        
        # Isolated Quarantined Records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quarantined_records (
                quarantine_id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                raw_record TEXT NOT NULL,
                validation_errors TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
        # Login and run limits (CHANGES.md section 19). Kept in the database, not in memory,
        # so a restart does not reset them and every server copy on Vercel sees the same numbers.
        # Times are Unix seconds.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_failures (
                client_id TEXT NOT NULL,
                failed_at DOUBLE PRECISION NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_events (
                client_id TEXT NOT NULL,
                started_at DOUBLE PRECISION NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS demo_grants (
                token TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                test_case TEXT NOT NULL,
                expires_at DOUBLE PRECISION NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_auth_failures_client ON auth_failures(client_id, failed_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_events_client ON run_events(client_id, started_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_demo_grants_token ON demo_grants(token)")

        # Per-person data (CHANGES.md section 31): who owns each run, and who signed in.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_owners (
                run_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                name TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_owners_owner ON run_owners(owner)")

        # Databases made before CHANGES.md section 26 have no blocked_by_guard column. Add it, and
        # fill it in ONCE for the old incidents from their audit log (only when the column is new).
        if _ensure_blocked_by_guard_column(cursor):
            _backfill_blocked_by_guard(cursor)

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