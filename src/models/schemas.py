# src/models/schemas.py
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

# --- Platform Lifecycle Enums ---
class PipelineStatus(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    HEALED = "HEALED"

class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RCA_COMPLETED = "RCA_COMPLETED"
    RECOVERY_IN_PROGRESS = "RECOVERY_IN_PROGRESS"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"

class SeverityLevel(str, Enum):
    P0 = "P0"  # Critical (Platform/Database Outage)
    P1 = "P1"  # High (Data Pipeline Blocked completely)
    P2 = "P2"  # Medium (Degraded performance / non-blocking network lag)
    P3 = "P3"  # Low (Minor data quality anomaly / missing optional fields)

class IncidentCategory(str, Enum):
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    DATA_QUALITY = "DATA_QUALITY"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    API_FAILURE = "API_FAILURE"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    UNKNOWN = "UNKNOWN"

# --- Structural Data Models ---
class PipelineRunSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    pipeline_id: str  # e.g., "PIPELINE_A", "PIPELINE_B"
    status: PipelineStatus
    started_at: datetime
    ended_at: Optional[datetime] = None

class IncidentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: str
    run_id: str
    pipeline_id: str
    severity: SeverityLevel = SeverityLevel.P2
    category: IncidentCategory = IncidentCategory.UNKNOWN
    status: IncidentStatus = IncidentStatus.OPEN
    error_class: str
    error_message: str
    stack_trace: str
    root_cause: Optional[str] = None
    recovery_action: Optional[str] = None
    telemetry_metadata: Optional[Any] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

class AgentRecoveryDirective(BaseModel):
    """The explicit JSON schema output forced onto the Recovery Agent"""
    action: str = Field(description="The structural remedy strategy: RETRY, QUARANTINE, RECONFIGURE, ESCALATE")
    parameters: Dict[str, Any] = Field(description="Dynamic key-value parameters required by the actuator to execute recovery")
    justification: str = Field(description="Brief engineering rationale backing up this remediation strategy")
