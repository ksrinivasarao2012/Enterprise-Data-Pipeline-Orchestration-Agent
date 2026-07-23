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
    # Counted in SQL instead of loading every incident (which got slower with history).
    unresolved_count = IncidentRepository.count_unresolved_for_pipeline(pipeline_id, exclude_incident_id=incident_id)
    
    new_severity = current_severity
    rationale = f"Confirmed operational category as: {category}."
    
    if unresolved_count > 0:
        new_severity = SeverityLevel.P0.value
        rationale = f"Upgraded severity to P0 due to {unresolved_count} existing unresolved incidents on pipeline {pipeline_id}."
        logger.info(rationale, incident_id=incident_id)
        # Persist the change in the state DB
        IncidentRepository.update_incident_state(incident_id, IncidentRepository.get_incident(incident_id).status, severity=SeverityLevel.P0)
    
    AuditService.log_event(incident_id, "CLASSIFIER_AGENT", rationale)

    # Decide the path through the graph (CHANGES.md section 23): rules / Gemini RCA / human.
    from src.agents.recovery import plan_route, ROUTE_RULES, ROUTE_LLM
    route, why = plan_route(state)
    next_step = {ROUTE_RULES: "Recovery (no Gemini needed)", ROUTE_LLM: "RCA (Gemini)"}.get(route, "RCA (Gemini), then a person")
    AuditService.log_event(incident_id, "CLASSIFIER_AGENT", f"Route: {next_step}, because {why}.")

    return {
        "route": route,
        "severity": new_severity,
        "audit_trail": state.get("audit_trail", []) + [f"Classifier calibrated severity to {new_severity}."]
    }
