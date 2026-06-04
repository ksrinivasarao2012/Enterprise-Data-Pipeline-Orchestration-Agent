# src/database.py
import os
import sqlite3
import re
from src.config import get_database_paths
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("database_adapter")

# Check if Vercel Postgres URL is present in the environment
# Vercel automatically injects POSTGRES_URL or POSTGRES_PRISMA_URL when Postgres storage is connected.
POSTGRES_URL = os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL") or os.environ.get("DATABASE_URL") or os.environ.get("STORAGE_URL")

# Resolve standard postgresql protocol if it starts with postgres:// (pg8000 expects postgresql://)
if POSTGRES_URL and POSTGRES_URL.startswith("postgres://"):
    POSTGRES_URL = POSTGRES_URL.replace("postgres://", "postgresql://", 1)

def is_postgres():
    return bool(POSTGRES_URL)

class PostgresCursorWrapper:
    """Wraps a DB-API 2.0 cursor to translate SQLite syntax and placeholders into Postgres."""
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=None):
        # 1. Translate SQLite placeholder "?" to Postgres "%s"
        # We need to be careful to only replace "?" that are not inside quotes, but in our code all "?" are raw placeholders.
        sql_translated = sql.replace("?", "%s")

        # 2. Translate "INSERT OR IGNORE" to Postgres "ON CONFLICT DO NOTHING"
        if "INSERT OR IGNORE INTO pipeline_runs" in sql_translated:
            sql_translated = sql_translated.replace(
                "INSERT OR IGNORE INTO pipeline_runs",
                "INSERT INTO pipeline_runs"
            ) + " ON CONFLICT (run_id) DO NOTHING"
        
        elif "INSERT OR IGNORE INTO incidents" in sql_translated:
            sql_translated = sql_translated.replace(
                "INSERT OR IGNORE INTO incidents",
                "INSERT INTO incidents"
            ) + " ON CONFLICT (incident_id) DO NOTHING"

        # 3. Translate "INSERT OR REPLACE INTO" to Postgres "ON CONFLICT ... DO UPDATE"
        elif "INSERT OR REPLACE INTO customers" in sql_translated:
            sql_translated = sql_translated.replace(
                "INSERT OR REPLACE INTO customers",
                "INSERT INTO customers"
            ) + " ON CONFLICT (customer_id) DO UPDATE SET name=EXCLUDED.name, email=EXCLUDED.email, country=EXCLUDED.country, signup_date=EXCLUDED.signup_date, status=EXCLUDED.status, total_orders=EXCLUDED.total_orders"

        elif "INSERT OR REPLACE INTO system_performance_metrics" in sql_translated:
            sql_translated = sql_translated.replace(
                "INSERT OR REPLACE INTO system_performance_metrics",
                "INSERT INTO system_performance_metrics"
            ) + " ON CONFLICT (device_id) DO UPDATE SET total_events=EXCLUDED.total_events, error_rate=EXCLUDED.error_rate, avg_response_time=EXCLUDED.avg_response_time"

        elif "INSERT OR REPLACE INTO device_telemetry" in sql_translated:
            sql_translated = sql_translated.replace(
                "INSERT OR REPLACE INTO device_telemetry",
                "INSERT INTO device_telemetry"
            ) + " ON CONFLICT (log_id) DO UPDATE SET device_id=EXCLUDED.device_id, timestamp=EXCLUDED.timestamp, status_code=EXCLUDED.status_code, response_time=EXCLUDED.response_time"

        # 4. Handle simple SQLite datatypes/functions
        # Convert SQLite AUTOINCREMENT to Postgres serial (usually done in CREATE TABLE, which we handle separately)
        
        # Execute query using pg8000 cursor
        if params is not None:
            # pg8000 expects params as a list or tuple
            self.cursor.execute(sql_translated, params)
        else:
            self.cursor.execute(sql_translated)
        return self

    def executemany(self, sql, seq_of_parameters):
        for params in seq_of_parameters:
            self.execute(sql, params)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def description(self):
        return self.cursor.description

    def close(self):
        self.cursor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

class PostgresConnectionWrapper:
    """Wraps pg8000 connection to provide SQLite-like API connection interface."""
    def __init__(self, conn):
        self.conn = conn

    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def execute(self, sql, params=None):
        cursor = self.cursor()
        cursor.execute(sql, params)
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()

def get_db_connection(db_key="state_db"):
    """
    Returns a connection object. If Vercel Postgres is connected,
    it returns a wrapper around a pg8000 Postgres connection.
    Otherwise, it returns a standard SQLite connection.
    """
    if is_postgres():
        try:
            import pg8000
            import ssl
            from urllib.parse import urlparse
            
            result = urlparse(POSTGRES_URL)
            username = result.username
            password = result.password
            database = result.path[1:]
            hostname = result.hostname
            port = result.port
            
            ssl_context = ssl.create_default_context()
            
            conn = pg8000.connect(
                user=username,
                password=password,
                host=hostname,
                port=port if port else 5432,
                database=database,
                ssl_context=ssl_context
            )
            return PostgresConnectionWrapper(conn)
        except Exception as e:
            logger.error("Failed to connect to Postgres, falling back to SQLite", error=str(e))
    
    # Fallback to local SQLite
    paths = get_database_paths()
    db_path = paths[db_key]
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn
