# src/orchestration/graph.py
from dotenv import load_dotenv
load_dotenv()

from typing import TypedDict, Optional, Dict, Any
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from src.models.schemas import SeverityLevel, IncidentCategory, IncidentStatus, AgentRecoveryDirective
from src.incidents.incident_repository import IncidentRepository
from src.incidents.incident_manager import IncidentManager
from src.services.audit_service import AuditService

# --- 1. Define the Shared State Schema ---
class IncidentState(TypedDict):
    incident_id: str
    pipeline_id: str
    error_class: str
    error_message: str
    stack_trace: str
    severity: str
    category: str
    root_cause: Optional[str]
    recovery_action: Optional[str]
    recovery_directive: Optional[Dict[str, Any]]
    audit_trail: list[str]

# --- 2. Structural AI Output Schemas ---
class RCASchema(BaseModel):
    root_cause_analysis: str = Field(description="Deep technical analysis explaining why the crash occurred.")
    confidence_score: float = Field(description="Value between 0.0 and 1.0 indicating analysis certainty.")

# --- 3. Import Specialized Agent Nodes ---
from src.agents.monitor import monitor_agent_node
from src.agents.classifier import classifier_agent_node
from src.agents.rca import rca_agent_node
from src.agents.recovery import recovery_agent_node

# --- 4. Assemble & Compile the LangGraph Execution Map ---
workflow = StateGraph(IncidentState)

# Register workflow nodes
workflow.add_node("MonitorNode", monitor_agent_node)
workflow.add_node("ClassifierNode", classifier_agent_node)
workflow.add_node("RCANode", rca_agent_node)
workflow.add_node("RecoveryNode", recovery_agent_node)

# Establish linear routing paths across the responsibility chain
workflow.set_entry_point("MonitorNode")
workflow.add_edge("MonitorNode", "ClassifierNode")
workflow.add_edge("ClassifierNode", "RCANode")
workflow.add_edge("RCANode", "RecoveryNode")
workflow.add_edge("RecoveryNode", END)

compiled_graph = workflow.compile()