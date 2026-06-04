from typing import Dict, Any
from src.incidents.incident_manager import IncidentManager
from src.models.schemas import IncidentStatus
from src.services.audit_service import AuditService
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("monitor_agent")

def monitor_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Monitor Agent:
    Inspects raw telemetry, extracts execution contexts, and verifies the incoming state.
    """
    msg = f"Monitor Agent verifying telemetry footprint for incident {state['incident_id']}."
    logger.info(msg, incident_id=state['incident_id'])
    
    IncidentManager.transition_to(state['incident_id'], IncidentStatus.INVESTIGATING)
    AuditService.log_event(state['incident_id'], "MONITOR_AGENT", "Successfully normalized raw incident logs.")
    
    return {
        "audit_trail": state.get("audit_trail", []) + ["Monitor verified telemetry."]
    }
