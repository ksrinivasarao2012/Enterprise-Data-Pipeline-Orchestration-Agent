# src/orchestration/graph.py
"""
The agent graph (CHANGES.md section 23).

    Monitor -> Classifier --rules--------------------------> Recovery
                          --llm----> RCA (Gemini) ----------> Recovery
                          --try----> RCA (Gemini) ----------> AI fix
                          --none---> RCA (Gemini) ----------> Escalate
    Recovery --ESCALATE--> AI fix (Pipeline A, once) or Escalate
    Recovery --fix-------> Apply fix -> Verify --saved rows---------------> END (healed)
                                              --quarantined--------------> END
                                              --another drift, tries left-> Recovery (loop)
                                              --failed, AI fix not tried--> AI fix (Pipeline A)
                                              --anything else------------> Escalate -> END
    AI fix --passed the dry run--> Apply fix -> Verify ...
           --rejected-----------> Escalate

The AI fix (CHANGES.md section 34): Gemini proposes one fix from a fixed set of data steps, the
steps run on a COPY of the file, and only a copy that passes every check is used for real.
It runs at most once per incident, and never after a guard blocked a fix.

Before, the graph was a straight line (Monitor -> Classifier -> RCA -> Recovery) and the
apply / verify / retry loop was hidden in plain Python after it. Now the graph makes the
decisions: Gemini is only called when no safe rule knows the fix (or to explain a failure
to the person who takes over), and the retry loop is a real edge in the graph.
"""
from dotenv import load_dotenv
load_dotenv()

import re
from typing import TypedDict, Optional, Dict, Any
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from src.services.audit_service import AuditService
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("agent_graph")


# --- 1. Shared state ---
class IncidentState(TypedDict, total=False):
    incident_id: str
    pipeline_id: str
    run_id: Optional[str]
    source_path: Optional[str]
    error_class: str
    error_message: str
    stack_trace: str
    severity: str
    category: str
    route: str                      # rules | llm | none (set by the Classifier)
    root_cause: Optional[str]       # set by RCA, read by Recovery
    recovery_action: Optional[str]
    recovery_directive: Optional[Dict[str, Any]]
    merged_params: Dict[str, Any]   # every fix collected so far (e.g. two renamed columns)
    attempt: int                    # verification run number
    pending_error: Optional[str]    # the new error Verify hands back to Recovery
    apply_result: Dict[str, Any]
    outcome: str                    # healed | quarantined | escalated
    guard_blocked: bool             # a guard stopped an AI fix: no second AI attempt
    ai_fix_tried: bool              # the AI fix runs at most once
    last_error: Optional[str]       # the error the AI fix should solve
    audit_trail: list[str]


# --- 2. Structural AI Output Schemas (kept for imports elsewhere) ---
class RCASchema(BaseModel):
    root_cause_analysis: str = Field(description="Deep technical analysis explaining why the crash occurred.")
    confidence_score: float = Field(description="Value between 0.0 and 1.0 indicating analysis certainty.")


# --- 3. Agent nodes ---
from src.agents.monitor import monitor_agent_node
from src.agents.classifier import classifier_agent_node
from src.agents.rca import rca_agent_node
from src.agents.recovery import recovery_agent_node, ai_fix_allowed, ROUTE_RULES, ROUTE_TRY, ROUTE_NONE


def _remediation():
    from src.services.remediation_service import RemediationService
    return RemediationService


def apply_node(state: IncidentState) -> Dict[str, Any]:
    """Apply fix: carries out Recovery's decision once and runs the pipeline to check it."""
    directive = state.get("recovery_directive") or {}
    result = _remediation().apply_fix(
        incident_id=state["incident_id"],
        pipeline_id=state["pipeline_id"],
        run_id=state.get("run_id"),
        action=(directive.get("action") or "ESCALATE").upper(),
        params=dict(directive.get("params") or {}),
        source_path=state.get("source_path"),
        root_cause=state.get("root_cause"),
    )
    return {"apply_result": result}


def verify_node(state: IncidentState) -> Dict[str, Any]:
    """Verify: decides what the result of the verification run means."""
    rs = _remediation()
    incident_id, pipeline_id = state["incident_id"], state["pipeline_id"]
    result = state.get("apply_result") or {}
    directive = state.get("recovery_directive") or {}
    attempt = state.get("attempt", 1)

    if result.get("quarantined"):
        rs.finish_quarantined(incident_id, state.get("run_id"))
        return {"outcome": "quarantined"}

    if result.get("rows"):
        rs.finish_healed(incident_id, pipeline_id, state.get("run_id"), (directive.get("action") or "").upper(),
                         dict(directive.get("params") or {}), directive.get("error_signature", "GENERIC"), result["rows"])
        return {"outcome": "healed"}

    if result.get("blocked"):
        return {"outcome": "escalate"}  # the reason was already logged by the safety check

    error = result.get("error")
    if error is None:
        AuditService.log_event(incident_id, "ACTUATOR", f"Verification run {attempt} saved 0 records, so it is not healed.")
        return {"outcome": "escalate"}

    # Another column renamed? Then loop back to Recovery (Pipeline A only), within the heal budget.
    key = re.search(r"KeyError\|([^|\]]+)", error)
    new_key = key.group(1) if key else None
    already_fixed = new_key in (state.get("merged_params") or {}).values()
    if pipeline_id == "PIPELINE_A" and new_key and not already_fixed and attempt < rs.MAX_HEAL_ATTEMPTS:
        AuditService.log_event(incident_id, "ACTUATOR",
                               f"Verification run {attempt} found another drift: column '{new_key}' is missing. Back to Recovery.")
        return {"outcome": "retry", "pending_error": error, "attempt": attempt + 1}

    short = " ".join(error.split())[:160]
    if _ai_fix_ok(state, error):
        AuditService.log_event(incident_id, "ACTUATOR", f"Verification run {attempt} failed: {short} Trying an AI fix.")
        return {"outcome": "ai_fix", "last_error": error, "attempt": attempt + 1}
    AuditService.log_event(incident_id, "ACTUATOR", f"Verification run {attempt} failed: {short}")
    return {"outcome": "escalate"}


def _ai_fix_ok(state: IncidentState, error: str | None) -> bool:
    """The AI fix runs once, on Pipeline A, when the source file is there and no guard blocked a fix."""
    import os
    return (not state.get("ai_fix_tried") and not state.get("guard_blocked")
            and ai_fix_allowed(state.get("pipeline_id"), error or state.get("error_message"))
            and bool(state.get("source_path")) and os.path.exists(state.get("source_path") or ""))


def ai_fix_node(state: IncidentState) -> Dict[str, Any]:
    """AI fix: Gemini proposes one fix from a fixed set of data steps; a dry run on a copy decides."""
    import json
    from src.agents import ai_fix
    from src.pipeline.json_pipeline import BASELINE_MAPPING, expected_columns
    from src.incidents.incident_manager import IncidentManager
    from src.incidents.incident_repository import IncidentRepository
    from src.models.schemas import IncidentStatus

    incident_id = state["incident_id"]
    source = state.get("source_path")
    error = state.get("last_error") or state.get("error_message") or ""
    # The fixed copy must fit the pipeline's own column names: any column mappings tried before
    # (which did not work) are dropped, and the real run uses no mapping overrides.
    merged: dict = {}
    mapping = dict(BASELINE_MAPPING)

    def record(summary: str) -> None:
        inc = IncidentRepository.get_incident(incident_id)
        if inc and inc.status in (IncidentStatus.INVESTIGATING, IncidentStatus.RCA_COMPLETED):
            IncidentManager.transition_to(incident_id, IncidentStatus.RECOVERY_IN_PROGRESS, recovery_action=summary)
        elif inc:
            IncidentRepository.update_incident_state(incident_id, inc.status, recovery_action=summary)

    def reject(why: str, proposal: str = "") -> Dict[str, Any]:
        if proposal:
            # A safety check stopped an AI fix: shown as "Blocked by guard", like the other guards.
            AuditService.log_event(incident_id, "AI_FIX_AGENT", f"Dry run rejected the fix: {why}. The data was not changed.")
            IncidentRepository.mark_blocked_by_guard(incident_id)
            record(f"Action: ESCALATE | Rationale: Gemini's fix ({proposal}) was rejected by the dry run: {why}.")
        else:
            AuditService.log_event(incident_id, "AI_FIX_AGENT", f"No AI fix was applied: {why}. The data was not changed.")
            record(f"Action: ESCALATE | Rationale: no AI fix was applied ({why}).")
        return {"ai_fix_tried": True, "outcome": "ai_fix_rejected", "recovery_directive": {"action": "ESCALATE", "params": {}}}

    try:
        with open(source, "r", encoding="utf-8") as f:
            original = json.load(f)
    except Exception:
        AuditService.log_event(incident_id, "AI_FIX_AGENT", "Gemini was not asked: the file is not readable JSON, so no data step can fix it.")
        return reject("the file is not readable JSON")

    try:
        steps, why = ai_fix.propose_fix(error, state.get("root_cause"), original, expected_columns(mapping))
    except Exception as e:
        AuditService.log_event(incident_id, "AI_FIX_AGENT", f"Gemini could not be reached ({type(e).__name__}).")
        return reject("Gemini could not be reached")
    proposal = ai_fix.describe(steps)
    if not steps:
        AuditService.log_event(incident_id, "AI_FIX_AGENT", f"Gemini proposed no fix: {why or 'no reason given'}.")
        return reject("Gemini proposed no fix")
    AuditService.log_event(incident_id, "AI_FIX_AGENT", f"Gemini proposed: {proposal}. Why: {why or 'no reason given'}.")

    result = ai_fix.dry_run(original, steps, mapping)
    if not result.ok:
        return reject(result.reason, proposal)

    with open(source, "w", encoding="utf-8") as f:   # the run's own working copy of the file
        json.dump(result.transformed, f, ensure_ascii=False, indent=2)
    AuditService.log_event(incident_id, "AI_FIX_AGENT", "Dry run passed on a copy: " + "; ".join(result.checks) + ".")
    record(f"Action: AI_FIX | Rationale: Gemini's fix ({proposal}) passed the dry run on a copy. Why: {why}")
    return {"ai_fix_tried": True, "outcome": "",
            "recovery_directive": {"action": "AI_FIX", "params": merged, "error_signature": "AI_FIX", "steps": proposal}}


def escalate_node(state: IncidentState) -> Dict[str, Any]:
    """Escalate: hand the incident to a person."""
    if state.get("route") == ROUTE_NONE and not state.get("recovery_action"):
        # Recovery was skipped (nothing to fix): still record what was decided.
        from src.incidents.incident_repository import IncidentRepository
        from src.models.schemas import IncidentStatus
        inc = IncidentRepository.get_incident(state["incident_id"])
        if inc:
            IncidentRepository.update_incident_state(
                state["incident_id"], inc.status,
                recovery_action="Action: ESCALATE | Rationale: No safe automatic fix exists for this kind of failure.")
    # Say which of the three ways led here, in plain words.
    if state.get("route") == ROUTE_NONE and not state.get("recovery_directive"):
        reason = "No safe automatic fix exists for this failure, so it was handed to a person with Gemini's explanation."
    elif state.get("outcome") == "ai_fix_rejected":
        reason = "No AI fix could be proven safe by a dry run on a copy, so the data was not changed and the incident was handed to a person."
    elif state.get("apply_result"):
        reason = "The fix could not be applied or did not work, so the incident was handed to a person."
    else:
        reason = "Recovery found no safe fix, so the incident was handed to a person."
    _remediation()._handle_escalation(state["incident_id"], reason)
    return {"outcome": "escalated"}


# --- 4. Routing (the decisions) ---
def after_classifier(state: IncidentState) -> str:
    return "RecoveryNode" if state.get("route") == ROUTE_RULES else "RCANode"


def after_rca(state: IncidentState) -> str:
    if state.get("route") == ROUTE_TRY and _ai_fix_ok(state, state.get("error_message")):
        return "AIFixNode"
    return "EscalateNode" if state.get("route") in (ROUTE_NONE, ROUTE_TRY) else "RecoveryNode"


def after_recovery(state: IncidentState) -> str:
    action = ((state.get("recovery_directive") or {}).get("action") or "ESCALATE").upper()
    if action != "ESCALATE":
        return "ApplyNode"
    # No rule (or Gemini mapping) knew a fix: one AI fix with a dry run, before a person.
    return "AIFixNode" if _ai_fix_ok(state, state.get("error_message")) else "EscalateNode"


def after_ai_fix(state: IncidentState) -> str:
    action = ((state.get("recovery_directive") or {}).get("action") or "ESCALATE").upper()
    return "ApplyNode" if action == "AI_FIX" else "EscalateNode"


def after_verify(state: IncidentState) -> str:
    outcome = state.get("outcome")
    if outcome in ("healed", "quarantined"):
        return END
    if outcome == "retry":
        return "RecoveryNode"
    if outcome == "ai_fix":
        return "AIFixNode"
    return "EscalateNode"


# --- 5. Assemble & compile ---
workflow = StateGraph(IncidentState)
workflow.add_node("MonitorNode", monitor_agent_node)
workflow.add_node("ClassifierNode", classifier_agent_node)
workflow.add_node("RCANode", rca_agent_node)
workflow.add_node("RecoveryNode", recovery_agent_node)
workflow.add_node("ApplyNode", apply_node)
workflow.add_node("VerifyNode", verify_node)
workflow.add_node("AIFixNode", ai_fix_node)
workflow.add_node("EscalateNode", escalate_node)

workflow.set_entry_point("MonitorNode")
workflow.add_edge("MonitorNode", "ClassifierNode")
workflow.add_conditional_edges("ClassifierNode", after_classifier, ["RecoveryNode", "RCANode"])
workflow.add_conditional_edges("RCANode", after_rca, ["RecoveryNode", "AIFixNode", "EscalateNode"])
workflow.add_conditional_edges("RecoveryNode", after_recovery, ["ApplyNode", "AIFixNode", "EscalateNode"])
workflow.add_conditional_edges("AIFixNode", after_ai_fix, ["ApplyNode", "EscalateNode"])
workflow.add_edge("ApplyNode", "VerifyNode")
workflow.add_conditional_edges("VerifyNode", after_verify, ["RecoveryNode", "AIFixNode", "EscalateNode", END])
workflow.add_edge("EscalateNode", END)

compiled_graph = workflow.compile()
