# src/pipeline/json_pipeline.py

import json
import uuid
import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator, ValidationError

# Resolve operational database path cleanly
from src.config import get_database_paths, get_active_pipeline_config, generate_error_signature
from src.telemetry.logger import get_pipeline_logger
from src.database import get_db_connection

logger = get_pipeline_logger("pipeline_json")
paths = get_database_paths()
OPERATIONAL_DB_PATH = paths["operational_db"]
DEFAULT_JSON_PATH = os.path.join(os.path.dirname(paths["state_db"]), "customers.json")


class CustomerRecord(BaseModel):
    customer_id: str = Field(..., min_length=1)
    name: str = Field(default="")
    email: str = Field(...)
    country: Optional[str] = Field(default="")

    @field_validator("customer_id", mode="before")
    @classmethod
    def coerce_id(cls, v):
        if v is None:
            raise ValueError("Customer ID cannot be empty or null")
        s = str(v).strip()
        if not s:
            raise ValueError("Customer ID cannot be empty or null")
        return s

    @field_validator("name", mode="before")
    @classmethod
    def coerce_name(cls, v):
        return str(v).strip() if v is not None else ""

    @field_validator("email", mode="before")
    @classmethod
    def coerce_email(cls, v):
        if v is None:
            raise ValueError("Invalid email formatting on value 'None'")
        s = str(v).strip()
        if "@" not in s:
            raise ValueError(f"Invalid email formatting on value '{s}'")
        return s

    @field_validator("country", mode="before")
    @classmethod
    def coerce_country(cls, v):
        return str(v).strip() if v is not None else ""


BASELINE_MAPPING = {
    "id_col": "customer_id",
    "name_col": "name",
    "email_col": "email",
    "country_col": "country",
}


def expected_columns(schema_mapping: Dict[str, str]) -> list:
    return [schema_mapping["id_col"], schema_mapping["name_col"], schema_mapping["email_col"], schema_mapping["country_col"]]


def check_schema(raw_data: list, schema_mapping: Dict[str, str], pipeline_id: str = "PIPELINE_A") -> None:
    """Schema drift = a column is missing from EVERY row (it was renamed or removed): raises KeyError.
    A field missing from only SOME rows is a problem with those rows, so they are quarantined
    instead (CHANGES.md section 23). Pure: no database, so the AI-fix dry run can use it too."""
    expected = expected_columns(schema_mapping)
    rows = [item for item in raw_data if isinstance(item, dict)]
    for expected_key in expected:
        if rows and all(expected_key not in item for item in rows):
            actual = list(rows[0].keys())
            sig = generate_error_signature(pipeline_id, "KeyError", expected_key)
            raise KeyError(
                f"[ErrorSignature: {pipeline_id}|KeyError|{expected_key}|{sig}] Expected: {expected} | Found: {actual}"
            )


def validate_row(item, schema_mapping: Dict[str, str], seen_ids: set):
    """Checks one row. Returns (item, validated_record or None, row_errors). Pure: no database."""
    row_errors = []
    if not isinstance(item, dict):
        item = {"value": item}
    for expected_key in expected_columns(schema_mapping):
        if expected_key not in item:
            row_errors.append(f"Missing field '{expected_key}'")
    mapped_item = {
        "customer_id": item.get(schema_mapping["id_col"]),
        "name": item.get(schema_mapping["name_col"]),
        "email": item.get(schema_mapping["email_col"]),
        "country": item.get(schema_mapping["country_col"]),
    }
    validated_record = None
    try:
        if not row_errors:  # a row with a missing field is already bad; don't add more noise
            validated_record = CustomerRecord(**mapped_item)
    except ValidationError as e:
        for err in e.errors():
            msg = err["msg"]
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            row_errors.append(msg)
    if validated_record is not None:
        if validated_record.customer_id in seen_ids:
            row_errors.append(f"Duplicate Customer ID '{validated_record.customer_id}' within batch")
        else:
            seen_ids.add(validated_record.customer_id)
    return item, validated_record, row_errors


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
        passed by the self-healing multi-agent cluster to recover from schema drift.
        """
        if not run_id:
            run_id = f"run_a_{uuid.uuid4().hex[:6]}"
        logger.info("Starting workload", pipeline_id=self.pipeline_id, run_id=run_id)
        
        # Register telemetry start
        from src.telemetry.incident_creator import TelemetryIncidentCreator
        TelemetryIncidentCreator.register_start(run_id, self.pipeline_id)
        
        # Baseline column mapping
        schema_mapping = dict(BASELINE_MAPPING)
        
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
                "Applying transient run-level schema mapping overrides",
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
                    print(f"[{self.pipeline_id}] Auto-flattening root dictionary into list based on schema override...")
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
            valid_rows = []
            bad_rows = []  # (item, errors)

            with get_db_connection("operational_db") as conn:
                cur = conn.cursor()
                seen_ids_in_batch = set()
                check_schema(raw_data, schema_mapping, self.pipeline_id)

                for item in raw_data:
                    item, validated_record, row_errors = validate_row(item, schema_mapping, seen_ids_in_batch)
                    # Database duplicate check (simulation only)
                    if validated_record and simulate_failure_type == "DUPLICATE":
                        cur.execute("SELECT 1 FROM customers WHERE customer_id = ?", (validated_record.customer_id,))
                        if cur.fetchone():
                            row_errors.append(f"Customer ID '{validated_record.customer_id}' already exists in database")
                    if row_errors:
                        bad_rows.append((item, row_errors))
                    else:
                        valid_rows.append(validated_record)

            quarantined_count = len(bad_rows)
            # A verification run (throw_on_error) whose rows ALL fail is a failed fix, not a
            # data-quality result: raise without writing quarantine rows, so a failed attempt
            # never leaves junk in the quarantine table (CHANGES.md section 34).
            if not (throw_on_error and bad_rows and not valid_rows):
                from src.services.quarantine_service import QuarantineService
                for item, row_errors in bad_rows:
                    QuarantineService.quarantine_record(
                        pipeline_id=self.pipeline_id,
                        run_id=run_id,
                        record_type="customer",
                        raw_record=item,
                        validation_errors=row_errors
                    )

            # If all rows failed, throw ValueError to register run as FAILED
            if quarantined_count > 0 and not valid_rows:
                where = "" if throw_on_error else " and were quarantined"
                raise ValueError(
                    f"Data Quality Violation: All {quarantined_count} customer rows failed validation{where}."
                )
                
            # Insert valid rows
            if valid_rows:
                with get_db_connection("operational_db") as conn:
                    cur = conn.cursor()
                    for record in valid_rows:
                        insert_q = """
                            INSERT OR REPLACE INTO customers (customer_id, name, email, country, signup_date, status, total_orders)
                            VALUES (?, ?, ?, ?, ?, 'ACTIVE', 0)
                        """
                        cur.execute(
                            insert_q,
                            (
                                record.customer_id,
                                record.name,
                                record.email,
                                record.country,
                                datetime.now(timezone.utc).date().isoformat(),
                            ),
                        )
                        records_written += 1
                    conn.commit()
            
            logger.info(
                "Process complete; ingested customer rows",
                pipeline_id=self.pipeline_id,
                records_written=records_written,
                quarantined_records=quarantined_count,
            )
            
            # Update terminal status based on whether we quarantined rows
            if quarantined_count > 0:
                TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "QUARANTINED")
                
                # Generate a P3 low severity warning incident in database
                from src.incidents.incident_manager import IncidentManager
                from src.models.schemas import IncidentStatus
                
                incident = IncidentManager.initialize_incident(
                    run_id=run_id,
                    pipeline_id=self.pipeline_id,
                    error_class="DataQualityAnomaly",
                    error_message=f"Data Quality Warning: isolated {quarantined_count} records to quarantine.",
                    stack_trace=f"Validation Errors: {quarantined_count} rows isolated.",
                    telemetry_metadata={"quarantined_count": quarantined_count, "valid_count": len(valid_rows)}
                )
                # Auto-resolve the incident immediately since valid data is ingested and pipeline is unblocked
                IncidentManager.transition_to(incident.incident_id, IncidentStatus.RESOLVED, force_override=True)
                
                from src.services.audit_service import AuditService
                AuditService.log_event(
                    incident.incident_id,
                    "CONTROL_PLANE",
                    f"Partial ingestion completed: {records_written} saved, {quarantined_count} quarantined."
                )
            else:
                TelemetryIncidentCreator.register_status(run_id, self.pipeline_id, "SUCCESS")
                
            # Restore baseline overrides only for real (non-dry-run) executions. During a
            # verification dry-run (throw_on_error=True) the actuator owns the draft-config
            # lifecycle — resetting here would delete the very draft it is about to verify.
            if not throw_on_error:
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
            # During a verification dry-run (throw_on_error=True) the actuator that
            # invoked us owns recovery. Spawning a nested healing loop here is what
            # made multi-column drifts recurse forever, ping-ponging between column
            # fixes. In that context, just raise and let the caller decide.
            if not throw_on_error:
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
