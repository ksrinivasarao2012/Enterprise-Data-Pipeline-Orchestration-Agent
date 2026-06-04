# src/telemetry/incident_creator.py
import traceback
import requests
from datetime import datetime, timezone
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("telemetry")
from typing import Optional

import os

def get_control_plane_url():
    return os.environ.get("CONTROL_PLANE_URL", "http://127.0.0.1:8000")

class TelemetryIncidentCreator:
    @staticmethod
    def register_start(run_id: str, pipeline_id: str) -> bool:
        """Notifies the Control Plane that a platform workload has spun up."""
        try:
            from src.services.pipeline_service import PipelineService
            from src.services.audit_service import AuditService
            PipelineService.start_run(run_id, pipeline_id)
            AuditService.log_event(run_id, "CONTROL_PLANE", f"Initialized monitoring track for {pipeline_id}")
            return True
        except Exception as e:
            logger.warning("Local run registry failed", error=str(e))
            return False

    @staticmethod
    def register_status(run_id: str, pipeline_id: str, status: str) -> None:
        """Updates the tracking status of a running workflow safely."""
        try:
            from src.services.pipeline_service import PipelineService
            from src.models.schemas import PipelineStatus
            from src.services.audit_service import AuditService
            PipelineService.update_run_status(run_id, PipelineStatus(status.upper()))
            AuditService.log_event(run_id, "CONTROL_PLANE", f"Workload updated status to: {status}")
        except Exception as e:
            logger.warning("Local status update failed", error=str(e))

    @classmethod
    def capture_and_report(cls, run_id: str, pipeline_id: str, exception: Exception, source_path: Optional[str] = None, original_filename: Optional[str] = None) -> Optional[dict]:
        """Intercepts a living exception, extracts execution data, and fires an incident alarm."""
        stack_trace_str = "".join(
            traceback.format_exception(type(exception), exception, exception.__traceback__)
        )
        
        # Extract fully qualified exception module path string where available
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

        try:
            from src.services.pipeline_service import PipelineService
            from src.models.schemas import PipelineStatus
            PipelineService.update_run_status(run_id, PipelineStatus.FAILED)
            
            from src.incidents.incident_manager import IncidentManager
            incident = IncidentManager.initialize_incident(
                run_id=run_id,
                pipeline_id=pipeline_id,
                error_class=error_class_str,
                error_message=str(exception),
                stack_trace=stack_trace_str,
                telemetry_metadata=metadata
            )
            
            from src.services.audit_service import AuditService
            AuditService.log_event(
                incident.incident_id, 
                "TELEMETRY_RECEIVER", 
                f"Intercepted critical failure [{error_class_str}]. Severity ranked: {incident.severity.value}"
            )
            
            # Execute LangGraph workflow synchronously in the same process
            # Lazy import to avoid circular imports during startup
            from src.api.main import run_agentic_healing_workflow
            run_agentic_healing_workflow(incident.incident_id)
            
            logger.info("Incident registered and healed in-process successfully", incident_id=incident.incident_id, severity=incident.severity.value)
            return {
                "status": "incident_registered",
                "incident_id": incident.incident_id,
                "severity": incident.severity.value,
                "category": incident.category.value
            }
        except Exception as e:
            logger.error("Failed in-process incident capture and healing", error=str(e))
            return None