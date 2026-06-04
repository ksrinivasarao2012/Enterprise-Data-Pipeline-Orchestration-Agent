# src/config.py
import os
import yaml
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("config")

CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../configs/settings.yaml"))

def load_settings():
    """Loads configuration settings from configs/settings.yaml."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration settings file not found at: {CONFIG_PATH}")
        
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def get_llm_config():
    """Returns LLM configuration (model_name, temperature) from settings.yaml."""
    settings = load_settings()
    llm_config = settings.get("llm", {})
    return {
        "model_name": llm_config.get("model_name", "gemini-2.5-flash"),
        "temperature": llm_config.get("temperature", 0.0)
    }

def get_recovery_config():
    """Returns recovery mapping configuration from settings.yaml."""
    settings = load_settings()
    recovery_config = settings.get("recovery", {})
    return {
        "default_param_suffix": recovery_config.get("default_param_suffix", "_col"),
        "pipelines": recovery_config.get("pipelines", {}),
    }


def get_database_paths():
    """
    Parses the database URL from settings and returns absolute file paths
    for state_db, operational_db, and analytics_db.
    """
    settings = load_settings()
    db_url = settings.get("database", {}).get("url", "sqlite:///database/platform_state.db")
    
    # Strip SQLite prefix
    if db_url.startswith("sqlite:///"):
        relative_path = db_url[len("sqlite:///"):]
    else:
        relative_path = db_url

    # Resolve absolute path relative to the workspace root
    workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
    state_db_path = os.path.abspath(os.path.join(workspace_root, relative_path))
    db_dir = os.path.dirname(state_db_path)
    
    return {
        "state_db": state_db_path,
        "operational_db": os.path.join(db_dir, "operational.db"),
        "analytics_db": os.path.join(db_dir, "analytics.db")
    }

def generate_error_signature(pipeline_id: str, exception_type: str, missing_key: str) -> str:
    """Returns a standardized sha256 hash of the error context parameters."""
    import hashlib
    raw_str = f"{pipeline_id}|{exception_type}|{missing_key}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:16]

def get_active_pipeline_config(pipeline_id: str) -> dict:
    """Queries verified active config overrides and returns a merged dictionary."""
    import sqlite3
    import json
    paths = get_database_paths()
    state_db = paths["state_db"]
    
    merged_config = {}
    try:
        with sqlite3.connect(state_db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000;")
            cursor = conn.cursor()
            query = """
                SELECT config_json FROM pipeline_configs pc
                WHERE pc.pipeline_id = ? AND pc.is_verified = 1
                AND pc.version = (
                    SELECT MAX(version) FROM pipeline_configs
                    WHERE pipeline_id = pc.pipeline_id
                      AND error_signature = pc.error_signature
                      AND is_verified = 1
                )
            """
            cursor.execute(query, (pipeline_id,))
            rows = cursor.fetchall()
            for row in rows:
                try:
                    chunk = json.loads(row[0])
                    merged_config.update(chunk)
                except Exception:
                    pass
    except Exception:
        pass
    return merged_config

def save_pipeline_config_draft(pipeline_id: str, error_signature: str, config: dict) -> int:
    """Persists a new tentative config draft (incrementing version) and returns the version."""
    import sqlite3
    import json
    from datetime import datetime, timezone
    paths = get_database_paths()
    state_db = paths["state_db"]
    
    try:
        with sqlite3.connect(state_db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000;")
            cursor = conn.cursor()
            
            # Find next version number
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) FROM pipeline_configs WHERE pipeline_id = ? AND error_signature = ?",
                (pipeline_id, error_signature)
            )
            max_v = cursor.fetchone()[0]
            next_version = max_v + 1
            
            config_str = json.dumps(config)
            created_at = datetime.now(timezone.utc).isoformat()
            
            cursor.execute(
                """
                INSERT INTO pipeline_configs (pipeline_id, error_signature, config_json, version, is_verified, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (pipeline_id, error_signature, config_str, next_version, created_at)
            )
            conn.commit()
            return next_version
    except Exception as e:
        logger.exception("Error saving config draft", error=str(e))
        return 1

def verify_pipeline_config(pipeline_id: str, error_signature: str, version: int) -> None:
    """Marks a tentative config version as verified active."""
    import sqlite3
    paths = get_database_paths()
    state_db = paths["state_db"]
    
    try:
        with sqlite3.connect(state_db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000;")
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pipeline_configs SET is_verified = 1 WHERE pipeline_id = ? AND error_signature = ? AND version = ?",
                (pipeline_id, error_signature, version)
            )
            conn.commit()
    except Exception as e:
        logger.exception("Error verifying config", error=str(e))

def delete_pipeline_config_version(pipeline_id: str, error_signature: str, version: int) -> None:
    """Deletes a specific draft config version (for rollback)."""
    import sqlite3
    paths = get_database_paths()
    state_db = paths["state_db"]
    
    try:
        with sqlite3.connect(state_db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000;")
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM pipeline_configs WHERE pipeline_id = ? AND error_signature = ? AND version = ?",
                (pipeline_id, error_signature, version)
            )
            conn.commit()
    except Exception as e:
        logger.exception("Error deleting/rolling back config", error=str(e))

def reset_pipeline_configs(pipeline_id: str) -> None:
    """Wipes all verified active config overrides for a pipeline to restore default schema rules."""
    import sqlite3
    paths = get_database_paths()
    state_db = paths["state_db"]
    try:
        with sqlite3.connect(state_db) as conn:
            conn.execute("PRAGMA busy_timeout = 5000;")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pipeline_configs WHERE pipeline_id = ?", (pipeline_id,))
            conn.commit()
        logger.info("Active overrides reset to baseline defaults", pipeline_id=pipeline_id)
    except Exception as e:
        logger.exception("Error resetting configs", error=str(e))

