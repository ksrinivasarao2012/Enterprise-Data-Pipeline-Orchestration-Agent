# src/incidents/incident_manager.py
import uuid
from datetime import datetime, timezone
from src.models.schemas import IncidentSchema, IncidentStatus, SeverityLevel, IncidentCategory
from src.incidents.incident_repository import IncidentRepository
from src.incidents.severity_rules import SeverityRulesEngine
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("incident_manager")

class IncidentManager:
    # Define a strict, immutable state matrix mapping valid transitions
    _VALID_TRANSITIONS = {
        IncidentStatus.OPEN: [IncidentStatus.INVESTIGATING, IncidentStatus.ESCALATED],
        IncidentStatus.INVESTIGATING: [IncidentStatus.RCA_COMPLETED, IncidentStatus.ESCALATED, IncidentStatus.RESOLVED],
        IncidentStatus.RCA_COMPLETED: [IncidentStatus.RECOVERY_IN_PROGRESS, IncidentStatus.ESCALATED, IncidentStatus.RESOLVED],
        IncidentStatus.RECOVERY_IN_PROGRESS: [IncidentStatus.RESOLVED, IncidentStatus.ESCALATED, IncidentStatus.INVESTIGATING],
        IncidentStatus.RESOLVED: [],  # Terminal state
        IncidentStatus.ESCALATED: [IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING]
    }

    @staticmethod
    def initialize_incident(run_id: str, pipeline_id: str, error_class: str, error_message: str, stack_trace: str, telemetry_metadata: dict | None = None) -> IncidentSchema:
        """
        Hooks directly into the telemetry ingestion point. Analyzes signatures, 
        evaluates severity rules, and persists a fresh OPEN incident.
        """
        incident_id = f"inc_{uuid.uuid4().hex[:8]}"
        
        severity, category = SeverityRulesEngine.evaluate(error_class, error_message, pipeline_id)
        
        incident = IncidentSchema(
            incident_id=incident_id,
            run_id=run_id,
            pipeline_id=pipeline_id,
            severity=severity,
            category=category,
            status=IncidentStatus.OPEN,
            error_class=error_class,
            error_message=error_message,
            stack_trace=stack_trace,
            telemetry_metadata=telemetry_metadata,
            created_at=datetime.now(timezone.utc)
        )
        
        IncidentRepository.create_incident(incident)
        return incident

    @classmethod
    def transition_to(cls, incident_id: str, target_status: IncidentStatus, **kwargs) -> None:
        """
        Advances an incident safely to its next lifecycle target stage.
        Enforces strict state-machine routing rules and transactional guarantees.
        """
        current_incident = IncidentRepository.get_incident(incident_id)
        if not current_incident:
            raise ValueError(f"Operational error: Target incident {incident_id} does not exist.")
            
        current_status = current_incident.status

        # Enforce State Machine Guardrails
        if target_status not in cls._VALID_TRANSITIONS.get(current_status, []):
            # Allow resetting an incident to INVESTIGATING or ESCALATED if an override is requested explicitly
            if kwargs.get("force_override") is not True:
                raise RuntimeError(
                    f"Illegal State Transition: Incident {incident_id} cannot transition "
                    f"from {current_status.value} to {target_status.value}."
                )

        success = IncidentRepository.update_incident_state(incident_id, target_status, **kwargs)
        
        if not success:
            raise RuntimeError(
                f"[Critical] Write Conflict: State update failed to execute for {incident_id}. "
                f"Database may be locked or record was modified concurrently."
            )
            
        logger.info("Incident state shifted", incident_id=incident_id, from_status=current_status.value, to_status=target_status.value)