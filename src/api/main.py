# src/api/main.py
import sys
import os
# Add root folder to path so Vercel can resolve absolute imports starting with 'src.'
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, status, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
import shutil
import tempfile
import sqlite3
from typing import Optional

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
from src.config import get_database_paths, reset_pipeline_configs

logger = get_pipeline_logger("control_plane")

# Initialize database on module import (critical for Vercel where ASGI lifespan is bypassed)
try:
    from database.init_db import initialize_database
    initialize_database()
    logger.info("Database initialized successfully on module import")
except Exception as e:
    logger.error("Database initialization failed on module import", error=str(e))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables on startup (critical for Vercel cold starts)."""
    try:
        from database.init_db import initialize_database
        initialize_database()
        logger.info("Database initialized successfully on startup")
    except Exception as e:
        logger.error("Database initialization failed on startup", error=str(e))
    yield

app = FastAPI(
    title="Enterprise Data Reliability Control Plane Gateway",
    version="1.0.0",
    lifespan=lifespan
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

# --- UI Dashboard Support Routes ---

@app.get("/api/metrics")
async def get_metrics():
    paths = get_database_paths()
    runs_without_incident = 0
    resolved_count = 0
    investigating_count = 0
    escalated_count = 0
    try:
        with sqlite3.connect(paths["state_db"]) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM pipeline_runs 
                WHERE status = 'SUCCESS' 
                  AND run_id NOT IN (SELECT DISTINCT run_id FROM incidents)
            """)
            runs_without_incident = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'RESOLVED'")
            resolved_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'INVESTIGATING'")
            investigating_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'ESCALATED'")
            escalated_count = cursor.fetchone()[0]
    except Exception as e:
        logger.exception("Failed to query metrics from state DB", error=str(e))
        
    return {
        "resolved": resolved_count,
        "investigating": investigating_count,
        "escalated": escalated_count,
        "runs_without_incident": runs_without_incident
    }

@app.get("/api/incidents")
async def get_incidents():
    try:
        incidents = IncidentRepository.get_all_incidents()
        return [i.model_dump() for i in incidents]
    except Exception as e:
        logger.exception("Failed to query incidents", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/incidents/{incident_id}/audit")
async def get_incident_audit(incident_id: str):
    try:
        audit_trail = IncidentRepository.get_audit_trail(incident_id)
        return audit_trail
    except Exception as e:
        logger.exception("Failed to query audit trail", incident_id=incident_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/escalations")
async def get_escalations():
    import re
    escalations = []
    log_path = "escalated_incidents.log"
    
    # If on Vercel, check in /tmp as well
    if os.environ.get("VERCEL") == "1" or os.environ.get("VERCEL"):
        log_path = "/tmp/escalated_incidents.log"
        
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                match = re.match(r"\[(.*?)\] Incident:\s*(.*?)\s*\|\s*(?:Reason|Source File):\s*(.*)", line)
                if match:
                    escalations.append({
                        "timestamp": match.group(1),
                        "incident_id": match.group(2),
                        "reason": match.group(3)
                    })
        except Exception as e:
            logger.exception("Failed to read escalations log", error=str(e))
    return escalations

@app.post("/api/pipelines/a/execute")
async def execute_pipeline_a(
    request: Request,
    test_case: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    # Set CONTROL_PLANE_URL environment variable dynamically
    base_url = str(request.base_url).rstrip("/")
    os.environ["CONTROL_PLANE_URL"] = base_url
    
    from src.pipeline.json_pipeline import PipelineA, DEFAULT_JSON_PATH
    pipeline = PipelineA()
    
    # Temporary workspace for custom file or loaded test case
    temp_file_path = DEFAULT_JSON_PATH
    original_filename = "customers.json"
    
    # Ensure folder exists
    os.makedirs(os.path.dirname(temp_file_path), exist_ok=True)
    
    try:
        if file:
            # Write uploaded file directly to execution path
            contents = await file.read()
            with open(temp_file_path, "wb") as f:
                f.write(contents)
            original_filename = file.filename
        elif test_case:
            # Read preloaded test case from test_data/pipeline_A/
            test_file_path = os.path.join("test_data", "pipeline_A", test_case)
            if not os.path.exists(test_file_path):
                raise HTTPException(status_code=404, detail=f"Test case file {test_case} not found.")
            
            shutil.copy2(test_file_path, temp_file_path)
            original_filename = test_case
        else:
            raise HTTPException(status_code=400, detail="Must provide either a test_case name or a file upload.")
        
        result = pipeline.execute(
            json_file_path=temp_file_path,
            simulate_failure_type=None,
            original_filename=original_filename
        )
        
        if result is not None:
            # Clear overrides to restore clean default baseline upon successful ingestion
            try:
                reset_pipeline_configs(pipeline.pipeline_id)
            except Exception:
                pass
            return {"status": "success", "rows_ingested": result}
        else:
            return {"status": "failure", "detail": "Pipeline execution failed. Telemetry reported an incident."}
            
    except Exception as ex:
        logger.exception("Error executing pipeline A", error=str(ex))
        raise HTTPException(status_code=500, detail=f"Error executing pipeline A: {str(ex)}")

@app.post("/api/pipelines/b/execute")
async def execute_pipeline_b(
    request: Request,
    test_case: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    # Set CONTROL_PLANE_URL environment variable dynamically
    base_url = str(request.base_url).rstrip("/")
    os.environ["CONTROL_PLANE_URL"] = base_url
    
    from src.pipeline.db_pipeline import PipelineB
    pipeline = PipelineB()
    
    temp_db_file = None
    original_filename = "telemetry.db"
    
    try:
        if file:
            # Write uploaded file to a writeable temp file
            suffix = ".db"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                contents = await file.read()
                tmp.write(contents)
                temp_db_file = tmp.name
            original_filename = file.filename
        elif test_case:
            # Read preloaded test case from test_data/pipeline_B/
            test_file_path = os.path.join("test_data", "pipeline_B", test_case)
            if not os.path.exists(test_file_path):
                raise HTTPException(status_code=404, detail=f"Test case file {test_case} not found.")
            
            # Copy test DB to a writeable temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                shutil.copy2(test_file_path, tmp.name)
                temp_db_file = tmp.name
            original_filename = test_case
        else:
            raise HTTPException(status_code=400, detail="Must provide either a test_case name or a file upload.")
        
        result = pipeline.execute(
            simulate_failure_type=None,
            op_db_path=temp_db_file,
            original_filename=original_filename
        )
        
        # Cleanup temp file
        if temp_db_file and os.path.exists(temp_db_file):
            try:
                os.remove(temp_db_file)
            except Exception:
                pass
                
        if result is not None:
            try:
                reset_pipeline_configs(pipeline.pipeline_id)
            except Exception:
                pass
            return {"status": "success", "rows_ingested": result}
        else:
            return {"status": "failure", "detail": "Pipeline execution failed. Telemetry reported an incident."}
            
    except Exception as ex:
        logger.exception("Error executing pipeline B", error=str(ex))
        raise HTTPException(status_code=500, detail=f"Error executing pipeline B: {str(ex)}")

@app.post("/api/history/clear")
async def clear_history():
    paths = get_database_paths()
    try:
        # Clear state database tables
        with sqlite3.connect(paths["state_db"]) as conn:
            conn.execute("DELETE FROM incidents")
            conn.execute("DELETE FROM pipeline_runs")
            conn.execute("DELETE FROM audit_logs")
            conn.execute("DELETE FROM pipeline_configs")
            conn.commit()
        # Clear operational database tables
        with sqlite3.connect(paths["operational_db"]) as conn:
            conn.execute("DELETE FROM customers")
            conn.commit()
        # Clear human escalation log file
        for log_path in ["escalated_incidents.log", "/tmp/escalated_incidents.log"]:
            if os.path.exists(log_path):
                try:
                    os.remove(log_path)
                except Exception:
                    pass
        return {"status": "success", "message": "Control Plane database history reset successfully!"}
    except Exception as err:
        logger.exception("Error resetting history", error=str(err))
        raise HTTPException(status_code=500, detail=f"Error resetting database: {str(err)}")
@app.get("/api/debug")
async def debug_info():
    import traceback
    info = {
        "cwd": os.getcwd(),
        "exists_settings": os.path.exists(os.path.abspath(os.path.join(os.path.dirname(__file__), "../configs/settings.yaml"))),
        "paths": None,
        "init_error": None,
        "db_exists": {}
    }
    try:
        paths = get_database_paths()
        info["paths"] = paths
        for k, p in paths.items():
            info["db_exists"][k] = {
                "path": p,
                "exists": os.path.exists(p),
                "size": os.path.getsize(p) if os.path.exists(p) else 0
            }
    except Exception as e:
        info["paths_error"] = traceback.format_exc()
        
    try:
        from database.init_db import initialize_database
        initialize_database()
        info["initialized"] = True
    except Exception as e:
        info["init_error"] = traceback.format_exc()
        
    return info


# Mount static files at the root route - must be registered last to avoid intercepting specific API paths
# Commented out static mount to let Vercel serve public files
# base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# public_dir = os.path.join(base_dir, "public")
# if os.path.exists(public_dir):
#     app.mount("/", StaticFiles(directory=public_dir, html=True), name="public")