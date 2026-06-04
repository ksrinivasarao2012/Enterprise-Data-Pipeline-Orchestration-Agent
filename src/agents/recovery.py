# Agent to formulate the JSON fix payload
from dotenv import load_dotenv
load_dotenv()

import re
import ast
import difflib
from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from src.incidents.incident_manager import IncidentManager
from src.models.schemas import IncidentStatus, IncidentCategory
from src.services.audit_service import AuditService
from src.config import get_llm_config, get_recovery_config
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("recovery_agent")


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _best_fuzzy_match(target: str | None, candidates: list[str], cutoff: float = 0.82) -> str | None:
    normalized_target = _normalize_text(target)
    if not normalized_target or not candidates:
        return None

    normalized_candidates = {candidate: _normalize_text(candidate) for candidate in candidates}

    for candidate, normalized_candidate in normalized_candidates.items():
        if normalized_candidate == normalized_target:
            return candidate

    scored_candidates = [
        (
            difflib.SequenceMatcher(None, normalized_target, normalized_candidate).ratio(),
            candidate,
        )
        for candidate, normalized_candidate in normalized_candidates.items()
    ]
    scored_candidates.sort(reverse=True)

    if scored_candidates and scored_candidates[0][0] >= cutoff:
        return scored_candidates[0][1]

    close_matches = difflib.get_close_matches(normalized_target, list(normalized_candidates.values()), n=1, cutoff=cutoff)
    if close_matches:
        for candidate, normalized_candidate in normalized_candidates.items():
            if normalized_candidate == close_matches[0]:
                return candidate

    return None


def _resolve_param_name(missing_key: str | None, alias_map: Dict[str, list[str]], default_param_suffix: str) -> str | None:
    normalized_missing_key = _normalize_text(missing_key)
    if not normalized_missing_key:
        return None

    for param_name, aliases in alias_map.items():
        candidates = [param_name, *aliases]
        if _best_fuzzy_match(missing_key, candidates, cutoff=0.80):
            return param_name

    return f"{missing_key}{default_param_suffix}"

def recovery_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recovery Agent:
    Generates dynamic machine-readable directives (RETRY, QUARANTINE, RECONFIGURE) with safety validation.
    """
    logger.info("Architecting deterministic remediation script payload", incident_id=state.get("incident_id"))
    
    # Initialize the Gemini model from centralized config
    llm_cfg = get_llm_config()
    llm = ChatGoogleGenerativeAI(
        model=llm_cfg["model_name"], 
        temperature=llm_cfg["temperature"],
        timeout=10,
        max_retries=3
    )
    recovery_cfg = get_recovery_config()
    
    error_msg = state.get("error_message", "")
    category_val = state["category"]
    pipeline_val = state["pipeline_id"]
    
    # 1. Parse standardized ErrorSignature and expected/found keys
    sig_match = re.search(r"\[ErrorSignature:\s*([^|\]]+)\|([^|\]]+)\|([^|\]]+)\|([^|\]]+)\]", error_msg)
    error_signature = "GENERIC"
    missing_key = None
    expected_list = []
    found_list = []
    
    if sig_match:
        missing_key = sig_match.group(3)
        error_signature = sig_match.group(4)
        
        expected_match = re.search(r"Expected:\s*(\[[^\]]*\])", error_msg)
        if expected_match:
            try:
                expected_list = ast.literal_eval(expected_match.group(1))
            except Exception:
                pass
                
        found_match = re.search(r"Found:\s*(\[[^\]]*\])", error_msg)
        if found_match:
            try:
                found_list = ast.literal_eval(found_match.group(1))
            except Exception:
                pass

    action_type = "ESCALATE"
    param_map = {}
    justification = "System automated runbooks exhausted. Escalated."

    if category_val == IncidentCategory.SCHEMA_DRIFT.value:
        if pipeline_val == "PIPELINE_B":
            action_type = "RECONFIGURE"
            justification = "Leveraging LLM to rewrite and heal operational SQL query drift."
            logger.info("Invoking LLM to repair SQL query syntax/schema drift", incident_id=state.get("incident_id"))
            
            recovery_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are an automated SQL repair agent. Fix the SQL query that caused the error based on the missing column or syntax error. If a required column is completely missing from the available columns list, substitute it with a logical fallback (such as 0.0 or NULL) to allow the query to execute successfully. Output ONLY the fully repaired raw SQL query. Do not use markdown backticks or explanations."),
                ("human", "Error Details (Contains original query and error reason):\n{error_msg}\nRewrite the query to fix the syntax or schema issue.")
            ])
            try:
                chain = recovery_prompt | llm
                response = chain.invoke({"error_msg": error_msg})
                fixed_query = response.content.strip()
                if fixed_query.startswith("```sql"):
                    fixed_query = fixed_query.strip("`").replace("sql\n", "", 1).strip()
                elif fixed_query.startswith("```"):
                    fixed_query = fixed_query.strip("`").strip()
                param_map = {"sql_override": fixed_query}
            except Exception as e:
                action_type = "ESCALATE"
                justification = f"LLM failed to rewrite SQL: {e}"
        else:
            action_type = "RECONFIGURE"
        
            # Step 1: Deterministic Alias/Substring Matching
            resolved_mapping_key = None
            pipeline_cfg = recovery_cfg.get("pipelines", {}).get(pipeline_val, {})
            alias_map = pipeline_cfg.get("aliases", {})
            default_param_suffix = recovery_cfg.get("default_param_suffix", "_col")
            allowed_params = set(pipeline_cfg.get("allowed_params", []))
            
            param_name = _resolve_param_name(missing_key, alias_map, default_param_suffix)
    
            # Resolve the actual payload key using exact, case-insensitive, and typo-tolerant matching.
            resolved_mapping_key = _best_fuzzy_match(missing_key, found_list, cutoff=0.78)
    
            # If the exact expected key is not present, try all aliases for the mapped parameter against the payload.
            if not resolved_mapping_key and param_name in alias_map:
                for alias in alias_map[param_name]:
                    match = _best_fuzzy_match(alias, found_list, cutoff=0.78)
                    if match:
                        resolved_mapping_key = match
                        break
    
            # If nothing matched but the parameter name itself is present in the payload, use it.
            if not resolved_mapping_key and param_name:
                resolved_mapping_key = _best_fuzzy_match(param_name, found_list, cutoff=0.78)
                        
            # Step 2: Fallback to LLM only if deterministic matching failed
            if resolved_mapping_key:
                justification = f"Deterministic alias matched missing key '{missing_key}' to payload key '{resolved_mapping_key}'."
            else:
                logger.info("Deterministic matching failed; invoking LLM for schema drift alignment", incident_id=state.get("incident_id"))
                # LLM Prompt for alignment
                recovery_prompt = ChatPromptTemplate.from_messages([
                    ("system", "You are an automated schema mapper. Map the missing expected key to one of the actual found keys in the payload. Output only the correct key name."),
                    ("human", "Missing Key: {missing_key}\nExpected List: {expected_list}\nFound List: {found_list}\nChoose the best fit key from the Found List. If none fit, output UNRESOLVED.")
                ])
                try:
                    # Direct simple invocation
                    chain = recovery_prompt | llm
                    response = chain.invoke({
                        "missing_key": missing_key,
                        "expected_list": str(expected_list),
                        "found_list": str(found_list)
                    })
                    llm_resolved = response.content.strip()
                    if llm_resolved in found_list:
                        resolved_mapping_key = llm_resolved
                        justification = f"LLM aligned missing key '{missing_key}' to payload key '{resolved_mapping_key}'."
                except Exception as e:
                    logger.exception("LLM invocation failed", error=str(e))
    
            # Map to the specific config schema override parameters
            if resolved_mapping_key and param_name:
                param_map = {param_name: resolved_mapping_key}
                if allowed_params and param_name not in allowed_params:
                    action_type = "ESCALATE"
                    param_map = {}
                    justification = f"Unresolved schema drift. No matching column found in payload for '{missing_key}'."
            else:
                action_type = "ESCALATE"
                justification = f"Unresolved schema drift. No matching column found in payload for '{missing_key}'."
            
    elif category_val in [IncidentCategory.NETWORK_TIMEOUT.value, IncidentCategory.API_FAILURE.value]:
        action_type = "RETRY"
        param_map = {"attempt_delay_seconds": 5}
        justification = "Transient connection failure detected. Scheduled automatic retry loop."

    elif category_val == IncidentCategory.DATA_QUALITY.value:
        if pipeline_val == "PIPELINE_B" and "referential integrity" in error_msg.lower():
            action_type = "QUARANTINE"
            param_map = {}
            justification = "Referential Integrity violation detected. Isolating corrupted records."
        elif missing_key == "root_structure":
            action_type = "RECONFIGURE"
            param_map = {"flatten_root_dict": True}
            justification = "Detected dictionary at root level instead of list. Reconfiguring pipeline to auto-flatten dictionary values."
        elif missing_key == "malformed_syntax":
            action_type = "REPAIR_FILE"
            param_map = {}
            justification = "Detected malformed JSON syntax. Forwarding to LLM for file content repair."

    remediation_summary = f"Action: {action_type} | Rationale: {justification}"
    
    IncidentManager.transition_to(
        state['incident_id'], 
        IncidentStatus.RECOVERY_IN_PROGRESS, 
        recovery_action=remediation_summary
    )
    AuditService.log_event(state['incident_id'], "RECOVERY_AGENT", f"Remediation generated: {action_type}")
    
    return {
        "recovery_action": remediation_summary,
        "recovery_directive": {
            "action": action_type, 
            "params": param_map,
            "error_signature": error_signature
        },
        "audit_trail": state.get("audit_trail", []) + ["Recovery agent deployed remediation package."]
    }
