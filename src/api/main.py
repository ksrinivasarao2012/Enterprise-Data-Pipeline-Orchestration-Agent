# src/api/main.py
import sys
import os
# Add root folder to path so Vercel can resolve absolute imports starting with 'src.'
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, status, Request, Response, UploadFile, File, Form, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import asyncio
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
from src.database import get_db_connection
from pydantic import BaseModel
from src.api import auth, ownership
from src.api.auth import Principal, require_user, require_admin, check_run_limit, start_demo

logger = get_pipeline_logger("control_plane")

# --- Security helpers -------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Fix 4: refuse uploads above this size before reading them (override with EHA_MAX_UPLOAD_MB).
MAX_UPLOAD_BYTES = int(float(os.environ.get("EHA_MAX_UPLOAD_MB", "5")) * 1024 * 1024)
# Live agent flow: pipeline runs execute in a worker thread (run_in_threadpool) so the server
# keeps answering the dashboard while agents heal a run. Runs share working files
# (e.g. database/customers.json), so they still take turns through this lock.
PIPELINE_RUN_LOCK = asyncio.Lock()

# Viewers (e.g. interviewers) get a smaller upload limit (override with EHA_VIEWER_MAX_UPLOAD_MB).
VIEWER_MAX_UPLOAD_BYTES = int(float(os.environ.get("EHA_VIEWER_MAX_UPLOAD_MB", "1")) * 1024 * 1024)


def _resolve_test_case(pipeline_folder: str, test_case: str) -> str:
    """
    Fix 2 (file path trick): only accept the name of a real file inside
    test_data/<pipeline_folder>/. Paths such as '/etc/passwd' or '../../.env' are refused
    before anything is opened.
    """
    folder = os.path.join(REPO_ROOT, "test_data", pipeline_folder)
    allowed = set(os.listdir(folder)) if os.path.isdir(folder) else set()
    if test_case not in allowed:
        raise HTTPException(status_code=404, detail=f"Unknown test case '{test_case}'. Choose one of the preset files.")
    return os.path.join(folder, test_case)


async def _read_upload(file: UploadFile, role: str = "admin") -> bytes:
    """Fix 4 (upload size limit): read at most limit + 1 bytes and refuse anything bigger.
    Viewers have a smaller limit than the admin."""
    limit = MAX_UPLOAD_BYTES if role == "admin" else min(MAX_UPLOAD_BYTES, VIEWER_MAX_UPLOAD_BYTES)
    contents = await file.read(limit + 1)
    if len(contents) > limit:
        mb = round(limit / (1024 * 1024), 2)
        raise HTTPException(status_code=413, detail=f"File too large. The limit for your role is {mb} MB.")
    return contents

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

# Initialize database on the first HTTP request (fully runtime-safe for Vercel)
_db_initialized = False

@app.middleware("http")
async def db_init_middleware(request: Request, call_next):
    global _db_initialized
    if not _db_initialized:
        try:
            from database.init_db import initialize_database
            initialize_database()
            logger.info("Database initialized successfully via middleware on first request")
            _db_initialized = True
        except Exception as e:
            logger.error("Database initialization failed in middleware", error=str(e))
    return await call_next(request)




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

    # Circuit breaker for EVERY remediation path: if this run has already raised too many
    # incidents, stop before running any agent (and before spending any LLM quota).
    if RemediationService.heal_attempts_exceeded(incident_data.run_id):
        logger.warning("Heal-attempt cap exceeded for run; escalating without running agents",
                       run_id=incident_data.run_id, incident_id=incident_id)
        RemediationService._handle_escalation(incident_id)
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
    # The graph now applies and verifies the fix itself (CHANGES.md section 23).
    initial_state.update({"run_id": incident_data.run_id, "source_path": source_path,
                          "attempt": 1, "merged_params": {}, "pending_error": None})

    logger.info("Launching LangGraph Core Engine", incident_id=incident_id)
    
    # Safety net (CHANGES.md section 22): if anything in the agents or the fix crashes
    # (e.g. a missing package such as sqlglot, or a bug), the incident must not stay
    # OPEN / INVESTIGATING for ever. It is escalated so a person is told.
    try:
        # Lazy import to prevent loading heavy orchestration/LLM modules at API startup
        from src.orchestration.graph import compiled_graph

        # 3. Run the agent graph: it routes, fixes, verifies, loops and escalates by itself.
        final_state = compiled_graph.invoke(initial_state)

        logger.info("LangGraph processing completed", incident_id=incident_id,
                    outcome=final_state.get("outcome"), recovery_directive=final_state.get("recovery_directive"))
    except Exception as e:
        _escalate_after_crash(incident_id, e)


def _escalate_after_crash(incident_id: str, error: Exception) -> None:
    """Healing crashed: record why and hand the incident to a person (unless it already finished)."""
    logger.exception("Healing crashed; escalating", incident_id=incident_id, error=str(error))
    try:
        current = IncidentRepository.get_incident(incident_id)
        if current and current.status.value in ("RESOLVED", "ESCALATED"):
            return  # it already reached an end state before the crash
        detail = " ".join(str(error).split())[:200] or "no details"
        from src.services.audit_service import AuditService
        AuditService.log_event(incident_id, "ACTUATOR",
                               f"Healing crashed: {type(error).__name__}: {detail}. Sent to a person.")
        RemediationService._handle_escalation(incident_id)
    except Exception as inner:  # e.g. the database itself is down: nothing more we can do here
        logger.exception("Could not escalate crashed incident", incident_id=incident_id, error=str(inner))

# --- REST Gateway Endpoints ---

# Fix #1: Migrated endpoints to use central ground-truth schemas directly
@app.post("/runs/register", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def register_pipeline_run(payload: PipelineRunSchema):
    try:
        run = PipelineService.start_run(payload.run_id, payload.pipeline_id)
        AuditService.log_event(run.run_id, "CONTROL_PLANE", f"Initialized monitoring track for {payload.pipeline_id}")
        return {"status": "tracked", "run_id": run.run_id}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to register run: {str(e)}")

@app.post("/runs/{run_id}/status", status_code=status.HTTP_200_OK, dependencies=[Depends(require_admin)])
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

@app.post("/telemetry/incident", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_admin)])
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

def _whoami(who: Principal) -> dict:
    return {"role": who.role, "auth_enabled": auth.auth_enabled(), "mode": auth.auth_mode(),
            "email": who.email, "name": who.name}


@app.get("/api/whoami")
async def whoami(who: Principal = Depends(require_user)):
    """Tells the dashboard who is signed in, so it can show their name and the right buttons."""
    return _whoami(who)


# --- Google sign-in (CHANGES.md section 31) ---

@app.get("/api/auth/config")
async def auth_config():
    """Public: what the sign-in page needs before anyone is signed in. The Google client id is
    not a secret (it is in every page that shows a Google button)."""
    return {"mode": auth.auth_mode(), "google_client_id": auth.google_client_id() or None}


class GoogleLogin(BaseModel):
    credential: str


@app.post("/api/auth/google")
async def google_login(payload: GoogleLogin, request: Request, response: Response):
    """Checks Google's ID token, remembers the person and sets the login cookie."""
    if auth.auth_mode() != "google":
        raise HTTPException(status_code=400, detail="Google sign-in is not set up on this server.")
    if request.headers.get(auth.CSRF_HEADER) != auth.CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing request header. Reload the page and try again.")
    if not payload.credential or len(payload.credential) > 8192:
        raise HTTPException(status_code=400, detail="Missing Google sign-in token.")
    try:
        user = await run_in_threadpool(auth.verify_google_token, payload.credential)
    except ValueError as e:
        logger.warning("Google sign-in refused", error=str(e))
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")
    try:
        ownership.save_user(user)
    except Exception as e:  # the login still works; only the "last seen" record is missing
        logger.error("Could not save user", error=str(e))
    auth.set_session_cookie(response, request, auth.make_session(user))
    return _whoami(auth.principal_for(user))


@app.post("/api/auth/logout")
async def logout(response: Response):
    """Removes the login cookie. Works even when already signed out."""
    auth.clear_session_cookie(response)
    return {"status": "signed_out"}


@app.get("/api/metrics")
async def get_metrics(who: Principal = Depends(require_user)):
    """The counters at the top of the Incident log. Viewers count only their own runs."""
    own, own_params = ownership.scope(who)
    and_own = f" AND {own}" if own else ""
    where_own = f" WHERE {own}" if own else ""
    p = tuple(own_params)
    runs_without_incident = 0
    resolved_count = 0
    investigating_count = 0
    escalated_count = 0
    quarantined_count = 0
    blocked_count = 0
    try:
        with get_db_connection("state_db") as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT COUNT(*) FROM pipeline_runs 
                WHERE status = 'SUCCESS' 
                  AND run_id NOT IN (SELECT DISTINCT run_id FROM incidents){and_own}
            """, p)
            runs_without_incident = cursor.fetchone()[0]
            
            # "Healed" counts only incidents the agents fixed. Quarantine-only warnings (bad rows set
            # aside, the rest loaded, no agent run) are counted under "quarantined" instead.
            cursor.execute(
                "SELECT COUNT(*) FROM incidents WHERE status = 'RESOLVED' "
                "AND error_class NOT IN ('DataQualityAnomaly', 'ReferentialIntegrityAnomaly')" + and_own, p
            )
            resolved_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'INVESTIGATING'" + and_own, p)
            investigating_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'ESCALATED'" + and_own, p)
            escalated_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM quarantined_records" + where_own, p)
            quarantined_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM incidents WHERE blocked_by_guard = 1" + and_own, p)
            blocked_count = cursor.fetchone()[0]
    except Exception as e:
        logger.exception("Failed to query metrics from state DB", error=str(e))
        
    return {
        "resolved": resolved_count,
        "investigating": investigating_count,
        "escalated": escalated_count,
        "runs_without_incident": runs_without_incident,
        "quarantined": quarantined_count,
        "blocked_by_guard": blocked_count
    }


@app.get("/api/incidents")
async def get_incidents(
    response: Response,
    who: Principal = Depends(require_user),
    limit: int = Query(50, ge=1, le=200, description="How many incidents to return (newest first)"),
    offset: int = Query(0, ge=0, description="How many of the newest incidents to skip"),
    pipeline: Optional[str] = Query(None, pattern="^PIPELINE_[AB]$", description="Only this pipeline"),
    outcome: Optional[str] = Query(None, pattern="^(healed|quarantined|escalated|in_progress)$",
                                   description="Only incidents with this outcome"),
    q: Optional[str] = Query(None, max_length=100, description="Search in id, run, error text and root cause"),
    blocked: Optional[bool] = Query(None, description="true: only incidents where a guard blocked the fix; false: only the others"),
):
    """One page of incidents, newest first. The total (after filters) is in the X-Total-Count
    header, so the dashboard can show "20 of 134" and load more on request."""
    try:
        incidents, total = IncidentRepository.get_incidents_page(limit, offset, pipeline, outcome, (q or "").strip() or None, blocked,
                                                                ownership.scope(who))
        response.headers["X-Total-Count"] = str(total)
        return [i.model_dump() for i in incidents]
    except Exception as e:
        logger.exception("Failed to query incidents", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quarantine")
async def get_quarantine_records(
    response: Response,
    who: Principal = Depends(require_user),
    limit: int = Query(50, ge=1, le=200, description="Page size (newest first)"),
    offset: int = Query(0, ge=0, description="How many newest records to skip"),
):
    """One page of quarantined rows; the total is in the X-Total-Count header (CHANGES.md section 17)."""
    try:
        from src.services.quarantine_service import QuarantineService
        records = QuarantineService.get_quarantined_records(limit, offset, ownership.scope(who))
        response.headers["X-Total-Count"] = str(QuarantineService.count_quarantined_records(ownership.scope(who)))
        return records
    except Exception as e:
        logger.exception("Failed to query quarantined records", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

def _visible_incident(incident_id: str, who: Principal):
    """The incident if this person may see it. Someone else's incident answers 404, exactly like
    one that doesn't exist, so the API doesn't even reveal that it exists."""
    incident = IncidentRepository.get_incident(incident_id)
    if incident is None or not ownership.owns_run(who, incident.run_id):
        raise HTTPException(status_code=404, detail="Incident not found.")
    return incident


@app.get("/api/incidents/{incident_id}/audit")
async def get_incident_audit(incident_id: str, who: Principal = Depends(require_user)):
    _visible_incident(incident_id, who)
    try:
        audit_trail = IncidentRepository.get_audit_trail(incident_id)
        return audit_trail
    except Exception as e:
        logger.exception("Failed to query audit trail", incident_id=incident_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/incidents/{incident_id}/report")
async def get_incident_report(incident_id: str, format: str = Query("json", pattern="^(json|pdf)$"),
                              who: Principal = Depends(require_user)):
    """Download one incident and its audit trail as JSON or PDF (CHANGES.md section 13)."""
    from src.services.report_service import build_pdf, build_report
    incident = _visible_incident(incident_id, who)
    report = build_report(incident.model_dump(mode="json"), IncidentRepository.get_audit_trail(incident_id))
    name = f"{incident.incident_id}_report"
    if format == "json":
        return JSONResponse(report, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    try:
        pdf = build_pdf(report)
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF export needs fpdf2: pip install -r requirements.txt")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})

@app.get("/api/escalations")
async def get_escalations(
    response: Response,
    who: Principal = Depends(require_user),
    limit: int = Query(50, ge=1, le=200, description="Page size (newest first)"),
    offset: int = Query(0, ge=0, description="How many newest escalations to skip"),
):
    """Incidents handed to a person, newest first; the total is in the X-Total-Count header.
    Read from the incidents table (status ESCALATED), so the list is the same on every server
    copy and can't disagree with the Incident log (CHANGES.md section 20). Only the rows on
    this page get their plain-English text (sections 16 and 17)."""
    from src.incidents.escalation_text import explain
    try:
        incidents, total = IncidentRepository.get_incidents_page(limit, offset, outcome="escalated", scope=ownership.scope(who))
    except Exception as e:
        logger.exception("Failed to query escalations", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
    response.headers["X-Total-Count"] = str(total)
    page = []
    for inc in incidents:
        reason = (inc.error_message or "Unknown failure reason").replace("\n", " ").strip()
        item = {"timestamp": inc.created_at.isoformat() if inc.created_at else None,
                "incident_id": inc.incident_id, "reason": reason}
        try:
            audit = [a["message"] for a in IncidentRepository.get_audit_trail(inc.incident_id)]
            item.update(explain(reason, inc.recovery_action, audit))
        except Exception as e:
            logger.exception("Failed to explain escalation", incident_id=inc.incident_id, error=str(e))
        page.append(item)
    return page

# --- Demo tab (CHANGES.md section 32) ---
# A frozen recording of all 13 presets (made with real Gemini by scripts/make_demo_snapshot.py),
# so anyone signed in can see what the app looks like after a full run without running anything.

def _demo_snapshot_path() -> str:
    return os.environ.get("EHA_DEMO_SNAPSHOT") or os.path.join(REPO_ROOT, "demo", "snapshot.json")


def _load_demo_snapshot() -> dict:
    import json
    try:
        with open(_demo_snapshot_path(), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No demo has been recorded yet.")


@app.get("/api/demo/snapshot", dependencies=[Depends(require_user)])
async def demo_snapshot():
    """The whole recording: incidents, their audit trails, quarantined rows, escalations, counters."""
    return _load_demo_snapshot()


@app.get("/api/demo/incidents/{incident_id}/report", dependencies=[Depends(require_user)])
async def demo_incident_report(incident_id: str, format: str = Query("json", pattern="^(json|pdf)$")):
    """Report download for an incident in the recording (same files as a live incident)."""
    from src.services.report_service import build_pdf, build_report
    snap = _load_demo_snapshot()
    incident = next((i for i in snap.get("incidents", []) if i.get("incident_id") == incident_id), None)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    report = build_report(incident, snap.get("audits", {}).get(incident_id, []))
    name = f"{incident_id}_report"
    if format == "json":
        return JSONResponse(report, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    try:
        pdf = build_pdf(report)
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF export needs fpdf2: pip install -r requirements.txt")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@app.post("/api/demo/start")
async def start_try_all_demo(request: Request, who: Principal = Depends(require_user)):
    """'Try all' button: counts as one run, then each preset may run once without counting
    against the viewer limit (CHANGES.md section 14)."""
    presets = []
    for pipe, folder in (("a", "pipeline_A"), ("b", "pipeline_B")):
        path = os.path.join(REPO_ROOT, "test_data", folder)
        if os.path.isdir(path):
            presets += [(pipe, name) for name in sorted(os.listdir(path))]
    token = start_demo(request, who, presets)
    return {"token": token, "presets": [{"pipeline": p, "test_case": c} for p, c in presets]}


@app.post("/api/pipelines/a/execute")
async def execute_pipeline_a(
    request: Request,
    who: Principal = Depends(require_user),
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
    
    # Validate the request BEFORE doing any work (security fixes 2 and 4).
    check_run_limit(request, who, None if file else ("a", test_case))
    if file:
        contents = await _read_upload(file, who.role)
    elif test_case:
        test_file_path = _resolve_test_case("pipeline_A", test_case)
    else:
        raise HTTPException(status_code=400, detail="Must provide either a test_case name or a file upload.")

    async with PIPELINE_RUN_LOCK:
        try:
            if file:
                # Write uploaded file directly to execution path
                with open(temp_file_path, "wb") as f:
                    f.write(contents)
                original_filename = file.filename
            else:
                shutil.copy2(test_file_path, temp_file_path)
                original_filename = test_case
        
            import uuid
            run_id = f"run_a_{uuid.uuid4().hex[:6]}"
            ownership.tag_run(run_id, who.owner)  # whose run this is (CHANGES.md section 31)

            result = await run_in_threadpool(
                pipeline.execute,
                json_file_path=temp_file_path,
                simulate_failure_type=None,
                original_filename=original_filename,
                run_id=run_id
            )
        
            if result is not None:
                # Clear overrides to restore clean default baseline upon successful ingestion
                try:
                    reset_pipeline_configs(pipeline.pipeline_id)
                except Exception:
                    pass
            
                is_quarantined = False
                try:
                    with get_db_connection("state_db") as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
                        row = cursor.fetchone()
                        if row and row[0] == "QUARANTINED":
                            is_quarantined = True
                except Exception:
                    pass
                
                if is_quarantined:
                    q_count = 0
                    try:
                        with get_db_connection("state_db") as conn:
                            cursor = conn.cursor()
                            cursor.execute("SELECT COUNT(*) FROM quarantined_records WHERE run_id = ?", (run_id,))
                            q_count = cursor.fetchone()[0]
                    except Exception:
                        pass
                    return {"status": "success", "rows_ingested": result, "quarantined": True, "quarantined_count": q_count}
                
                return {"status": "success", "rows_ingested": result}
            else:
                final_status = None
                try:
                    with get_db_connection("state_db") as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
                        row = cursor.fetchone()
                        final_status = row[0] if row else None
                except Exception:
                    pass
                if final_status == "HEALED":
                    return {"status": "success", "rows_ingested": "Auto-healed", "healed": True}
                if final_status == "QUARANTINED":
                    # Healing isolated every bad row; nothing valid was left to load.
                    return {"status": "success", "rows_ingested": 0, "quarantined": True, "quarantined_count": "all"}
                return {"status": "failure", "detail": "Pipeline execution failed. Telemetry reported an incident."}
            
        except Exception as ex:
            logger.exception("Error executing pipeline A", error=str(ex))
            raise HTTPException(status_code=500, detail=f"Error executing pipeline A: {str(ex)}")

@app.post("/api/pipelines/b/execute")
async def execute_pipeline_b(
    request: Request,
    who: Principal = Depends(require_user),
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
    
    # Validate the request BEFORE doing any work (security fixes 2 and 4).
    check_run_limit(request, who, None if file else ("b", test_case))
    if file:
        contents = await _read_upload(file, who.role)
    elif test_case:
        test_file_path = _resolve_test_case("pipeline_B", test_case)
    else:
        raise HTTPException(status_code=400, detail="Must provide either a test_case name or a file upload.")

    async with PIPELINE_RUN_LOCK:
        try:
            if file:
                # Write uploaded file to a writeable temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                    tmp.write(contents)
                    temp_db_file = tmp.name
                original_filename = file.filename
            elif test_case:
                # Copy test DB to a writeable temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                    shutil.copy2(test_file_path, tmp.name)
                    temp_db_file = tmp.name
                original_filename = test_case
            else:
                raise HTTPException(status_code=400, detail="Must provide either a test_case name or a file upload.")
        
            import uuid
            run_id = f"run_b_{uuid.uuid4().hex[:6]}"
            ownership.tag_run(run_id, who.owner)  # whose run this is (CHANGES.md section 31)

            result = await run_in_threadpool(
                pipeline.execute,
                simulate_failure_type=None,
                op_db_path=temp_db_file,
                original_filename=original_filename,
                run_id=run_id
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
                
                is_quarantined = False
                try:
                    with get_db_connection("state_db") as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
                        row = cursor.fetchone()
                        if row and row[0] == "QUARANTINED":
                            is_quarantined = True
                except Exception:
                    pass
                
                if is_quarantined:
                    q_count = 0
                    try:
                        with get_db_connection("state_db") as conn:
                            cursor = conn.cursor()
                            cursor.execute("SELECT COUNT(*) FROM quarantined_records WHERE run_id = ?", (run_id,))
                            q_count = cursor.fetchone()[0]
                    except Exception:
                        pass
                    return {"status": "success", "rows_ingested": result, "quarantined": True, "quarantined_count": q_count}
                
                return {"status": "success", "rows_ingested": result}
            else:
                final_status = None
                try:
                    with get_db_connection("state_db") as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,))
                        row = cursor.fetchone()
                        final_status = row[0] if row else None
                except Exception:
                    pass
                if final_status == "HEALED":
                    return {"status": "success", "rows_ingested": "Auto-healed", "healed": True}
                if final_status == "QUARANTINED":
                    # Healing isolated every bad row; nothing valid was left to load.
                    return {"status": "success", "rows_ingested": 0, "quarantined": True, "quarantined_count": "all"}
                return {"status": "failure", "detail": "Pipeline execution failed. Telemetry reported an incident."}
            
        except Exception as ex:
            logger.exception("Error executing pipeline B", error=str(ex))
            raise HTTPException(status_code=500, detail=f"Error executing pipeline B: {str(ex)}")

@app.post("/api/history/clear")
async def clear_history(who: Principal = Depends(require_user)):
    """Viewers clear only their own runs; the admin clears everything (CHANGES.md section 31)."""
    # Wait for a running pipeline to finish so a run is never cut in half.
    async with PIPELINE_RUN_LOCK:
        if not who.is_admin:
            try:
                ownership.clear_owner(who.owner)
            except Exception as err:
                logger.exception("Error clearing a user's history", error=str(err))
                raise HTTPException(status_code=500, detail="Could not clear your history.")
            return {"status": "success", "message": "Your history was cleared."}
        try:
            # Clear state database tables
            with get_db_connection("state_db") as conn:
                conn.execute("DELETE FROM incidents")
                conn.execute("DELETE FROM pipeline_runs")
                conn.execute("DELETE FROM audit_logs")
                conn.execute("DELETE FROM pipeline_configs")
                conn.execute("DELETE FROM quarantined_records")
                conn.execute("DELETE FROM run_owners")
                conn.commit()
            # Clear operational database tables
            with get_db_connection("operational_db") as conn:
                conn.execute("DELETE FROM customers")
                conn.commit()
            return {"status": "success", "message": "Control Plane database history reset successfully!"}
        except Exception as err:
            logger.exception("Error resetting history", error=str(err))
            raise HTTPException(status_code=500, detail=f"Error resetting database: {str(err)}")



# The dashboard (CHANGES.md section 30): the React app (frontend/, built with `npm run build`),
# served from the same origin as the API, locally and on Vercel. On Vercel the build runs during
# deploy (vercel.json "buildCommand") and Vercel copies these files to its CDN.
_REACT_DIST = os.path.join(REPO_ROOT, "frontend", "dist")
if os.path.exists(os.path.join(_REACT_DIST, "index.html")):
    app.mount("/", StaticFiles(directory=_REACT_DIST, html=True), name="dashboard")
else:
    @app.get("/", include_in_schema=False)
    async def dashboard_not_built():
        return JSONResponse({"detail": "The dashboard is not built yet: cd frontend && npm install && npm run build"},
                            status_code=503)
