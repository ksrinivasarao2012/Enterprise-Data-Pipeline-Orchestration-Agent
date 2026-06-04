from typing import Dict, Any
from src.models.schemas import IncidentCategory, SeverityLevel
from src.services.audit_service import AuditService
from src.incidents.incident_repository import IncidentRepository
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("classifier_agent")

def classifier_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classification Agent:
    Reviews the deterministic rules engine output and adjusts severity mapping if required.
    """
    logger.info("Analyzing blast radius prioritization tier", incident_id=state.get('incident_id'))
    
    incident_id = state['incident_id']
    pipeline_id = state['pipeline_id']
    category = state['category']
    current_severity = state['severity']
    
    # Heuristic adjustment: If there are multiple active/unresolved incidents for the same pipeline,
    # raise severity to P0 (Critical outage) to prioritize human or automated remediation speed.
    all_incidents = IncidentRepository.get_all_incidents()
    unresolved_for_pipeline = [
        inc for inc in all_incidents 
        if inc.pipeline_id == pipeline_id 
        and inc.status.value != "RESOLVED" 
        and inc.incident_id != incident_id
    ]
    
    new_severity = current_severity
    rationale = f"Confirmed operational category as: {category}."
    
    if len(unresolved_for_pipeline) > 0:
        new_severity = SeverityLevel.P0.value
        rationale = f"Upgraded severity to P0 due to {len(unresolved_for_pipeline)} existing unresolved incidents on pipeline {pipeline_id}."
        logger.info(rationale, incident_id=incident_id)
        # Persist the change in the state DB
        IncidentRepository.update_incident_state(incident_id, IncidentRepository.get_incident(incident_id).status, severity=SeverityLevel.P0)
    
    AuditService.log_event(incident_id, "CLASSIFIER_AGENT", rationale)
    
    return {
        "severity": new_severity,
        "audit_trail": state.get("audit_trail", []) + [f"Classifier calibrated severity to {new_severity}."]
    }
