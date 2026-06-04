# src/pipeline/json_pipeline.py

import json
import uuid
import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional, Dict

# Resolve operational database path cleanly
from src.config import get_database_paths, get_active_pipeline_config, generate_error_signature
from src.telemetry.logger import get_pipeline_logger
from src.database import get_db_connection

logger = get_pipeline_logger("pipeline_csv")
paths = get_database_paths()
OPERATIONAL_DB_PATH = paths["operational_db"]
DEFAULT_JSON_PATH = os.path.join(os.path.dirname(paths["state_db"]), "customers.json")


class PipelineA:
    """Workload: Batch File Ingestion (Customers JSON -> Operational DB)"""
    def __init__(self):
        self.pipeline_id = "PIPELINE_A"

    def execute(
        self,
        json_file_path: str = DEFAULT_JSON_PATH,
        simulate_failure_type: Optional[str] = None,
        schema_mapping_override: Optional[Dict[str, str]] = None,
        throw_on_error: bool = False,
        original_filename: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Optional[int]:
        """
        Runs the batch customer JSON parsing engine. Supports schema adjustment parameters 
        passed by the self‑healing multi‑agent cluster to recover from schema drift.
        """
        if not run_id:
            run_id = f"run_a_{uuid.uuid4().hex[:6]}"
        logger.info("Starting workload", pipeline_id=self.pipeline_id, run_id=run_id)
        
        # Register telemetry start
        from src.telemetry.incident_creator import TelemetryIncidentCreator
        TelemetryIncidentCreator.register_start(run_id, self.pipeline_id)
        
        # Baseline column mapping
        schema_mapping = {
            "id_col": "customer_id",
            "name_col": "name",
            "email_col": "email",
            "country_col": "country",
        }
        
        # Persistent overrides from DB
        active_db_overrides = get_active_pipeline_config(self.pipeline_id)
        if active_db_overrides:
            logger.info(
                "Applying persistent active schema mapping overrides from database",
                pipeline_id=self.pipeline_id,
                overrides=active_db_overrides,
            )
            schema_mapping.update(active_db_overrides)
        
        # Transient overrides passed at execution time
        if schema_mapping_override:
            logger.info(
                "Applying transient run‑level schema mapping overrides",
                pipeline_id=self.pipeline_id,
                overrides=schema_mapping_override,
            )
            schema_mapping.update(schema_mapping_override)
        
        try:
            if not os.path.exists(json_file_path):
                raise FileNotFoundError(f"Source JSON file missing on disk: {json_file_path}")
            
            with open(json_file_path, "r", encoding="utf-8") as file:
                try:
                    raw_data = json.load(file)
                except json.JSONDecodeError as e:
                    sig = generate_error_signature(self.pipeline_id, "JSONDecodeError", "malformed_syntax")
                    raise ValueError(
                        f"[ErrorSignature: {self.pipeline_id}|JSONDecodeError|malformed_syntax|{sig}] Invalid JSON Syntax: {e}"
                    )
            
            if not isinstance(raw_data, list):
                if schema_mapping.get("flatten_root_dict") in [True, "True", "true"] and isinstance(raw_data, dict):
                    print(f"[{self.pipeline_id}] Auto‑flattening root dictionary into list based on schema override…")
                    raw_data = list(raw_data.values())
                else:
                    sig = generate_error_signature(self.pipeline_id, "ValueError", "root_structure")
                    raise ValueError(
                        f"[ErrorSignature: {self.pipeline_id}|ValueError|root_structure|{sig}] Invalid JSON: File must contain a JSON array (list) of customer records."
                    )
            
            # Simulated failure scenarios (unchanged from original implementation)
            if simulate_failure_type == "DRIFT" or schema_mapping.get("email_col") == "user_email":
                logger.info(
                    "Simulating schema drift: Renaming target 'email' column key",
                    pipeline_id=self.pipeline_id,
                )
                for item in raw_data:
                    if "email" in item:
                        item["user_email"] = item.pop("email")
            elif simulate_failure_type == "QUALITY":
                logger.info(
                    "Simulating data quality anomaly: Injecting invalid email",
                    pipeline_id=self.pipeline_id,
                )
                if raw_data:
                    raw_data[0]["email"] = "invalid_email_format"
            elif simulate_failure_type == "DUPLICATE":
                logger.info(
                    "Simulating duplicate customer ID entry",
                    pipeline_id=self.pipeline_id,
                )
                if raw_data:
                    first_item = raw_data[0]
                    p_id = first_item[schema_mapping["id_col"]]
                    p_name = first_item[schema_mapping["name_col"]]
                    p_email = first_item[schema_mapping["email_col"]]
                    p_country = first_item[schema_mapping["country_col"]]
                    with sqlite3.connect(OPERATIONAL_DB_PATH) as conn:
                        cur = conn.cursor()
                        cur.execute(
                            """
                            INSERT OR REPLACE INTO customers (customer_id, name, email, country, signup_date)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                p_id,
                                p_name,
                                p_email,
                                p_country,
                                datetime.now(timezone.utc).date().isoformat(),
                            ),
                        )
                        conn.commit()
            
            logger.info("Validating structural constraints & parsing values", pipeline_id=self.pipeline_id)
            records_written = 0
            problematic_rows = []
            
            with get_db_connection("operational_db") as conn:
                cur = conn.cursor()
                seen_ids_in_batch = set()
                
                for idx, item in enumerate(raw_data):
                    row_errors = []
                    try:
                        p_id = item[schema_mapping["id_col"]]
                        p_email = item[schema_mapping["email_col"]]
                        p_name = item[schema_mapping["name_col"]]
                        p_country = item[schema_mapping["country_col"]]
                    except KeyError as ke:
                        missing = ke.args[0]
                        expected = [
                            schema_mapping["id_col"],
                            schema_mapping["name_col"],
                            schema_mapping["email_col"],
                            schema_mapping["country_col"],
                        ]
                        actual = list(item.keys())
                        sig = generate_error_signature(self.pipeline_id, "KeyError", missing)
                        raise KeyError(
                            f"[ErrorSignature: {self.pipeline_id}|KeyError|{missing}|{sig}] Expected: {expected} | Found: {actual}"
                        )
                    
                    if simulate_failure_type == "DUPLICATE":
                        cur.execute("SELECT 1 FROM customers WHERE customer_id = ?", (p_id,))
                        if cur.fetchone():
                            row_errors.append(f"Customer ID '{p_id}' already exists in database")
                    
                    if p_id in seen_ids_in_batch:
                        row_errors.append(f"Duplicate Customer ID '{p_id}' within batch")
                    else:
                        seen_ids_in_batch.add(p_id)
                    
                    if not p_email or "@" not in p_email:
                        row_errors.append(f"Invalid email formatting on value '{p_email}'")
                    
                    if row_errors:
                        problematic_rows.append({"row_index": idx, "errors": row_errors})
            
            if problematic_rows:
                num = len(problematic_rows)
                details = "; ".join(
                    [f"Row {r['row_index']} error(s): {', '.join(r['errors'])}" for r in problematic_rows[:3]]
                )
                if num > 3:
                    details += f" ... and {num - 3} more"
                is_duplicate = any(
                    "duplicate" in err.lower() or "already exists" in err.lower()
                    for r in problematic_rows
                    for err in r["errors"]
                )
                if is_duplicate:
                    raise ValueError(
                        f"Duplicate customer ID detected. Total problematic rows: {num}. Details: {details}"
                    )
                else:
                    raise ValueError(
                        f"Data Quality Violation: Field formatting errors. Total problematic rows: {num}. Details: {details}"
                    )
            
            with get_db_connection("operational_db") as conn:
                cur = conn.cursor()
                for item in raw_data:
                    p_id = item[schema_mapping["id_col"]]
                    p_email = item[schema_mapping["email_col"]]
                    p_name = item[schema_mapping["name_col"]]
                    p_country = item[schema_mapping["country_col"]]
                    insert_q = """
                        INSERT OR REPLACE INTO customers (customer_id, name, email, country, signup_date, status, total_orders)
                        VALUES (?, ?, ?, ?, ?, 'ACTIVE', 0)
                    """
                    cur.execute(
                        insert_q,
                        (
                            p_id,
                            p_name,
                            p_email,
                            p_country,
                            datetime.now(timezone.utc).date().isoformat(),
                        ),
                    )
                    records_written += 1
                conn.commit()
            
            logger.info(
                "Process complete; ingested customer rows",
                pipeline_id=self.pipeline_id,
                records_written=records_written,
            )
            TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "SUCCESS")
            try:
                from src.config import reset_pipeline_configs
                reset_pipeline_configs(self.pipeline_id)
            except Exception as e:
                logger.warning(
                    "Failed to reset active overrides",
                    pipeline_id=self.pipeline_id,
                    error=str(e),
                )
            return records_written
        except Exception as e:
            logger.exception(
                "Critical failure detected during pipeline execution",
                pipeline_id=self.pipeline_id,
                error=str(e),
            )
            TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "FAILED")
            TelemetryIncidentCreator.capture_and_report(
                run_id,
                self.pipeline_id,
                e,
                source_path=json_file_path,
                original_filename=original_filename,
            )
            if throw_on_error:
                raise e
            return None

# Export for external imports
from src.telemetry.incident_creator import TelemetryIncidentCreator
