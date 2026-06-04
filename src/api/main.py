# src/api/main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, status
import os

from src.models.schemas import (
    PipelineStatus, 
    IncidentStatus, 
    PipelineRunSchema, 
    IncidentSchema
)
from src.services.pipeline_service import PipelineService
from src.services.audit_service import AuditService
from src.incidents.incident_manager import IncidentManager
from src.incidents.incident_repository import IncidentRepository
from src.services.remediation_service import RemediationService
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("control_plane")

app = FastAPI(
    title="Enterprise Data Reliability Control Plane Gateway",
    version="1.0.0"
)

# --- Out-of-Band Intelligence Trigger Loop ---
def run_agentic_healing_workflow(incident_id: str):
    """
    Asynchronous worker loop. Intercepts newly registered incident profiles
    and passes them through the multi-agent LangGraph lifecycle.
    """
    # 1. Fetch the persisted base incident data model
    incident_data = IncidentRepository.get_incident(incident_id)
    if not incident_data:
        return

    # 2. Map the data structure directly into our initial LangGraph state
    initial_state = {
        "incident_id": incident_data.incident_id,
        "pipeline_id": incident_data.pipeline_id,
        "error_class": incident_data.error_class,
        "error_message": incident_data.error_message,
        "stack_trace": incident_data.stack_trace,
        "severity": incident_data.severity.value,
        "category": incident_data.category.value,
        "root_cause": None,
        "recovery_action": None,
        "recovery_directive": None,
        "audit_trail": []
    }
    telemetry_metadata = getattr(incident_data, "telemetry_metadata", None)
    if not isinstance(telemetry_metadata, dict):
        telemetry_metadata = {}
    source_path = telemetry_metadata.get("source_path")

    logger.info("Launching LangGraph Core Engine", incident_id=incident_id)
    
    # Lazy import to prevent loading heavy orchestration/LLM modules at API startup
    from src.orchestration.graph import compiled_graph
    
    # 3. Stream the execution path context across nodes
    final_state = compiled_graph.invoke(initial_state)
    
    logger.info("LangGraph processing completed", incident_id=incident_id)
    logger.info("Final remediation vector", incident_id=incident_id, recovery_directive=final_state.get('recovery_directive'))

    # 4. Invoke the physical remediation actuator to execute the directive
    RemediationService.execute_directive(
        incident_id=incident_id,
        pipeline_id=incident_data.pipeline_id,
        run_id=incident_data.run_id,
        directive=final_state.get("recovery_directive"),
        source_path=source_path
    )

# --- REST Gateway Endpoints ---

# Fix #1: Migrated endpoints to use central ground-truth schemas directly
@app.post("/runs/register", status_code=status.HTTP_201_CREATED)
async def register_pipeline_run(payload: PipelineRunSchema):
    try:
        run = PipelineService.start_run(payload.run_id, payload.pipeline_id)
        AuditService.log_event(run.run_id, "CONTROL_PLANE", f"Initialized monitoring track for {payload.pipeline_id}")
        return {"status": "tracked", "run_id": run.run_id}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to register run: {str(e)}")

@app.post("/runs/{run_id}/status", status_code=status.HTTP_200_OK)
async def update_pipeline_run(run_id: str, payload: PipelineRunSchema):
    # Fix #3: Wrapped state tracking update calls in try-catch guards to protect against database blocks
    try:
        success = PipelineService.update_run_status(run_id, payload.status)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target tracking run payload not found.")
        
        AuditService.log_event(run_id, "CONTROL_PLANE", f"Workload updated status to: {payload.status.value}")
        return {"status": "updated", "run_id": run_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database update failure: {str(e)}")

@app.post("/telemetry/incident", status_code=status.HTTP_202_ACCEPTED)
async def report_pipeline_incident(payload: IncidentSchema, background_tasks: BackgroundTasks):
    try:
        # Fix #2: Safe status checks. If the model payload specifies an explicit status (like HEALED or QUARANTINED), 
        # respect it. Otherwise, use FAILED as the fallback baseline.
        target_status = payload.status if payload.status in [PipelineStatus.FAILED, PipelineStatus.HEALED] else PipelineStatus.FAILED
        PipelineService.update_run_status(payload.run_id, target_status)
        
        # Process severity matrix, categorize, and initialize the incident record
        incident = IncidentManager.initialize_incident(
            run_id=payload.run_id,
            pipeline_id=payload.pipeline_id,
            error_class=payload.error_class,
            error_message=payload.error_message,
            stack_trace=payload.stack_trace,
            telemetry_metadata=payload.telemetry_metadata
        )
        
        # Log the system interception event
        AuditService.log_event(
            incident.incident_id, 
            "TELEMETRY_RECEIVER", 
            f"Intercepted critical failure [{payload.error_class}]. Severity ranked: {incident.severity.value}"
        )
        
        # Offload the LangGraph execution loop to a background task
        background_tasks.add_task(run_agentic_healing_workflow, incident.incident_id)
        
        return {
            "status": "incident_registered",
            "incident_id": incident.incident_id,
            "severity": incident.severity.value,
            "category": incident.category.value
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Telemetry parser registration rejected: {str(e)}")