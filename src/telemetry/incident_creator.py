# src/telemetry/incident_creator.py
import traceback
import requests
from datetime import datetime, timezone
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("telemetry")
from typing import Optional

CONTROL_PLANE_URL = "http://127.0.0.1:8000"

class TelemetryIncidentCreator:
    @staticmethod
    def register_start(run_id: str, pipeline_id: str) -> bool:
        """Notifies the Control Plane that a platform workload has spun up."""
        # Fix #1: Build payload matching complete PipelineRunSchema expectations
        payload = {
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "status": "RUNNING",
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            response = requests.post(
                f"{CONTROL_PLANE_URL}/runs/register",
                json=payload,
                timeout=3
            )
            return response.status_code == 201
        except requests.exceptions.RequestException:
            logger.warning("Control Plane unreachable during run registry", run_id=run_id)
            return False

    @staticmethod
    def register_status(run_id: str, pipeline_id: str, status: str) -> None:
        """Updates the tracking status of a running workflow safely."""
        # Fix #2: Build complete schema frame to satisfy the gateway endpoint validator
        payload = {
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "status": str(status).upper(),
            "started_at": datetime.now(timezone.utc).isoformat()
        }
        try:
            requests.post(
                f"{CONTROL_PLANE_URL}/runs/{run_id}/status",
                json=payload,
                timeout=3
            )
        except requests.exceptions.RequestException:
            logger.warning("Failed to update run status", run_id=run_id)

    @classmethod
    def capture_and_report(cls, run_id: str, pipeline_id: str, exception: Exception, source_path: Optional[str] = None, original_filename: Optional[str] = None) -> Optional[dict]:
        """Intercepts a living exception, extracts execution data, and fires an incident alarm."""
        stack_trace_str = "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )
        
        # Fix #3: Extract fully qualified exception module path string where available
        module = exception.__class__.__module__
        if module == "builtins":
            error_class_str = exception.__class__.__name__
        else:
            error_class_str = f"{module}.{exception.__class__.__name__}"

        metadata = {}
        if source_path:
            metadata["source_path"] = source_path
        if original_filename:
            metadata["original_filename"] = original_filename

        payload = {
            "incident_id": "TEMP_VAL",  # Overwritten deterministically inside manager layer
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "error_class": error_class_str,
            "error_message": str(exception),
            "stack_trace": stack_trace_str,
            "status": "OPEN",
            "telemetry_metadata": metadata if metadata else None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            response = requests.post(
                f"{CONTROL_PLANE_URL}/telemetry/incident",
                json=payload,
                timeout=5
            )
            if response.status_code == 202:
                data = response.json()
                logger.info("Incident registered successfully", incident_id=data.get('incident_id'), severity=data.get('severity'))
                return data
        except requests.exceptions.RequestException as e:
            logger.error("Failed to ship crash context to Control Plane", error=str(e))
        return None