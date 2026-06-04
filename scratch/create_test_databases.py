# scratch/create_test_databases.py
import sqlite3
import os
from pathlib import Path
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("scratch.create_test_databases")

OUTPUT_DIR = Path(r"d:\DRDO\enterprise_healing_agent\test_data\pipeline_B")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEALTHY_DB = OUTPUT_DIR / "healthy.db"
MISSING_TABLE_DB = OUTPUT_DIR / "missing_table.db"
REFERENTIAL_INTEGRITY_DB = OUTPUT_DIR / "referential_integrity.db"
SQL_ERROR_DB = OUTPUT_DIR / "sql_error.db" # Database where the device_telemetry schema has drifted, e.g. status_code column missing

def create_healthy_db():
    if HEALTHY_DB.exists():
        HEALTHY_DB.unlink()
    conn = sqlite3.connect(HEALTHY_DB)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE device_telemetry (
            log_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_time REAL NOT NULL
        )
    """)
    mock_data = [
        ("log_h1", "device_alpha", "2026-06-04 09:00:00", 200, 30.5),
        ("log_h2", "device_beta", "2026-06-04 09:02:00", 200, 85.0),
    ]
    cursor.executemany("INSERT INTO device_telemetry VALUES (?, ?, ?, ?, ?)", mock_data)
    conn.commit()
    conn.close()
    logger.info("Created healthy database", path=str(HEALTHY_DB))

def create_missing_table_db():
    # Table device_telemetry is absent, will trigger MISSING_TABLE simulation behavior or table-not-found error
    if MISSING_TABLE_DB.exists():
        MISSING_TABLE_DB.unlink()
    conn = sqlite3.connect(MISSING_TABLE_DB)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE wrong_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    logger.info("Created missing table database", path=str(MISSING_TABLE_DB))

def create_referential_integrity_db():
    if REFERENTIAL_INTEGRITY_DB.exists():
        REFERENTIAL_INTEGRITY_DB.unlink()
    conn = sqlite3.connect(REFERENTIAL_INTEGRITY_DB)
    cursor = conn.cursor()
    # Device log maps to an unknown device ID structure or missing registration
    cursor.execute("""
        CREATE TABLE device_telemetry (
            log_id TEXT PRIMARY KEY,
            device_id TEXT,
            timestamp TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_time REAL NOT NULL
        )
    """)
    # Insert null or invalid reference device IDs
    mock_data = [
        ("log_r1", None, "2026-06-04 09:00:00", 200, 30.5),
    ]
    cursor.executemany("INSERT INTO device_telemetry VALUES (?, ?, ?, ?, ?)", mock_data)
    conn.commit()
    conn.close()
    logger.info("Created referential integrity database", path=str(REFERENTIAL_INTEGRITY_DB))

def create_sql_error_db():
    if SQL_ERROR_DB.exists():
        SQL_ERROR_DB.unlink()
    conn = sqlite3.connect(SQL_ERROR_DB)
    cursor = conn.cursor()
    # Missing status_code column, causing select aggregation syntax query failure
    cursor.execute("""
        CREATE TABLE device_telemetry (
            log_id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            response_time REAL NOT NULL
        )
    """)
    mock_data = [
        ("log_s1", "device_alpha", "2026-06-04 09:00:00", 30.5),
    ]
    cursor.executemany("INSERT INTO device_telemetry VALUES (?, ?, ?, ?)", mock_data)
    conn.commit()
    conn.close()
    logger.info("Created SQL error database", path=str(SQL_ERROR_DB))

if __name__ == "__main__":
    create_healthy_db()
    create_missing_table_db()
    create_referential_integrity_db()
    create_sql_error_db()
