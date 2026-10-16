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
from src.config import get_llm_config, get_recovery_config, llm_text
from src.telemetry.logger import get_pipeline_logger
from src.agents.sql_guard import check_sql_repair, extract_repair_context

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

def parse_error_signature(error_msg: str):
    """Extracts (error_signature, missing_key, expected_list, found_list) from a pipeline error message."""
    sig_match = re.search(r"\[ErrorSignature:\s*([^|\]]+)\|([^|\]]+)\|([^|\]]+)\|([^|\]]+)\]", error_msg or "")
    error_signature, missing_key, expected_list, found_list = "GENERIC", None, [], []
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
    return error_signature, missing_key, expected_list, found_list


def _build_llm():
    llm_cfg = get_llm_config()
    return ChatGoogleGenerativeAI(
        model=llm_cfg["model_name"],
        temperature=llm_cfg["temperature"],
        timeout=10,
        max_retries=3
    )


def resolve_schema_drift(error_msg: str, pipeline_id: str, llm=None, root_cause: str | None = None,
                         use_llm: bool = True) -> tuple[dict, str]:
    """
    Maps ONE missing column to a column that exists in the payload.
    Deterministic alias/fuzzy matching first; the LLM is only used if that fails (and use_llm).
    Returns (param_map, justification). An empty param_map means "could not resolve".
    Used by the Recovery agent, also when the graph loops back after another drift.
    """
    _, missing_key, expected_list, found_list = parse_error_signature(error_msg)
    recovery_cfg = get_recovery_config()
    pipeline_cfg = recovery_cfg.get("pipelines", {}).get(pipeline_id, {})
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

    justification = f"Unresolved schema drift. No matching column found in payload for '{missing_key}'."
    if resolved_mapping_key:
        justification = f"Deterministic alias matched missing key '{missing_key}' to payload key '{resolved_mapping_key}'."
    elif use_llm:
        # Fallback to LLM only if deterministic matching failed
        logger.info("Deterministic matching failed; invoking LLM for schema drift alignment", missing_key=missing_key)
        recovery_prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an automated schema mapper. Map the missing expected key to one of the actual found keys in the payload. Output only the correct key name."),
            ("human", "Root cause analysis from the RCA agent: {root_cause}\nMissing Key: {missing_key}\nExpected List: {expected_list}\nFound List: {found_list}\nChoose the best fit key from the Found List. If none fit, output UNRESOLVED.")
        ])
        try:
            if llm is None:
                llm = _build_llm()
            chain = recovery_prompt | llm
            response = chain.invoke({
                "root_cause": root_cause or "(not available)",
                "missing_key": missing_key,
                "expected_list": str(expected_list),
                "found_list": str(found_list)
            })
            llm_resolved = llm_text(response).strip()
            if llm_resolved in found_list:
                resolved_mapping_key = llm_resolved
                justification = f"LLM aligned missing key '{missing_key}' to payload key '{resolved_mapping_key}'."
        except Exception as e:
            logger.exception("LLM invocation failed", error=str(e))

    if not (resolved_mapping_key and param_name):
        return {}, justification
    if allowed_params and param_name not in allowed_params:
        return {}, f"Unresolved schema drift. No matching column found in payload for '{missing_key}'."
    return {param_name: resolved_mapping_key}, justification


# How an incident is routed through the agent graph (CHANGES.md section 23).
#   "rules" - a safe rule already knows the fix: go straight to Recovery, no Gemini call
#   "llm"   - no rule fits: the RCA agent (Gemini) analyses it first, then Recovery uses that analysis
#   "try"   - a new kind of Pipeline A failure: RCA analyses it, then Gemini proposes ONE fix from a
#             fixed set of data steps, which must pass a dry run on a copy (CHANGES.md section 34)
#   "none"  - there is no automatic fix: RCA explains it for the person who takes over, then escalate
ROUTE_RULES, ROUTE_LLM, ROUTE_TRY, ROUTE_NONE = "rules", "llm", "try", "none"

# Failures no data fix can solve: the input itself is missing. These always go to a person.
_UNFIXABLE = ("missing on disk", "no such file", "filenotfound", "does not exist", "no such table", "permission denied")


def ai_fix_allowed(pipeline_id: str | None, error_msg: str | None) -> bool:
    """The AI fix (with dry run) only works on Pipeline A's JSON files, and never for a missing input."""
    msg = (error_msg or "").lower()
    return pipeline_id == "PIPELINE_A" and not any(s in msg for s in _UNFIXABLE)


def plan_route(state: Dict[str, Any]) -> tuple[str, str]:
    """Returns (route, reason in plain words). Pure: reads the incident, calls no model."""
    category = state.get("category")
    pipeline = state.get("pipeline_id")
    error_msg = state.get("error_message", "") or ""
    _, missing_key, _, _ = parse_error_signature(error_msg)

    if category == IncidentCategory.SCHEMA_DRIFT.value:
        if pipeline == "PIPELINE_A":
            fix, _ = resolve_schema_drift(error_msg, pipeline, use_llm=False)
            if fix:
                return ROUTE_RULES, "a known column alias matches, so Recovery can fix it without Gemini"
            return ROUTE_LLM, "no known alias matches the missing column, so Gemini analyses it first"
        return ROUTE_LLM, "the SQL query needs rewriting, so Gemini analyses it first"
    if category in (IncidentCategory.NETWORK_TIMEOUT.value, IncidentCategory.API_FAILURE.value):
        return ROUTE_RULES, "a temporary failure: Recovery retries without Gemini"
    if category == IncidentCategory.DATA_QUALITY.value:
        if pipeline == "PIPELINE_B" and "referential integrity" in error_msg.lower():
            return ROUTE_RULES, "every row is bad, so Recovery quarantines them without Gemini"
        if missing_key == "root_structure":
            return ROUTE_RULES, "the file is an object instead of a list; a rule flattens it without Gemini"
        if missing_key == "malformed_syntax":
            return ROUTE_LLM, "the JSON syntax is broken, so Gemini analyses it and then repairs the brackets"
    if ai_fix_allowed(pipeline, error_msg):
        return ROUTE_TRY, "no rule knows this kind of failure, so Gemini analyses it and proposes one fix that must pass a dry run on a copy"
    return ROUTE_NONE, "there is no safe automatic fix for this kind of failure; Gemini explains it for the person who takes over"


def recovery_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recovery Agent:
    Chooses the fix (RECONFIGURE, REPAIR_FILE, QUARANTINE, RETRY or ESCALATE). When the RCA agent ran,
    its analysis is passed into every Gemini prompt here. When the graph loops back from Verify
    (another column was renamed), it resolves the next drift and keeps the fixes found so far.
    """
    logger.info("Architecting deterministic remediation script payload", incident_id=state.get("incident_id"))

    root_cause = state.get("root_cause")
    error_msg = state.get("error_message", "")
    category_val = state["category"]
    pipeline_val = state["pipeline_id"]
    pending_error = state.get("pending_error")
    merged = dict(state.get("merged_params") or {})

    # 1. Parse standardized ErrorSignature and expected/found keys (of the ORIGINAL failure)
    error_signature, missing_key, expected_list, found_list = parse_error_signature(error_msg)

    action_type = "ESCALATE"
    param_map = {}
    justification = "System automated runbooks exhausted. Escalated."
    guard_blocked = False  # set when a guard (SQL guard / UNRESOLVABLE) stops the AI fix

    if pending_error:
        # Loop from Verify: the fix worked for one column, but the next run found another drift.
        next_fix, why = resolve_schema_drift(pending_error, pipeline_val, root_cause=root_cause)
        if next_fix and not all(merged.get(k) == v for k, v in next_fix.items()):
            action_type = "RECONFIGURE"
            param_map = {**merged, **next_fix}
            justification = why
        else:
            justification = f"Could not resolve the next schema drift. {why}"

    elif category_val == IncidentCategory.SCHEMA_DRIFT.value:
        if pipeline_val == "PIPELINE_B":
            action_type = "RECONFIGURE"
            justification = "Leveraging LLM to rewrite and heal operational SQL query drift."
            logger.info("Invoking LLM to repair SQL query syntax/schema drift", incident_id=state.get("incident_id"))

            # Never let the model invent data: if a required column is really gone, it must
            # say UNRESOLVABLE and a human decides. (Previously the prompt told it to substitute
            # constants, which silently produced fake metrics such as error_rate = 0.)
            recovery_prompt = ChatPromptTemplate.from_messages([
                ("system", "You are an automated SQL repair agent. Fix the SQL query that caused the error. "
                           "You may only fix syntax mistakes or map a missing column to an existing column that clearly holds the same data "
                           "(for example a renamed column). Never replace a column with a constant, a literal value, NULL, or an invented expression. "
                           "If a column the query needs does not exist and there is no clear equivalent in the available columns, "
                           "output exactly the single word UNRESOLVABLE. "
                           "Otherwise output ONLY the fully repaired raw SQL query, with no markdown backticks or explanations."),
                ("human", "Root cause analysis from the RCA agent:\n{root_cause}\n\n"
                          "Error Details (Contains original query and error reason):\n{error_msg}\n"
                          "Rewrite the query to fix the syntax or schema issue, or output UNRESOLVABLE.")
            ])
            try:
                chain = recovery_prompt | _build_llm()
                response = chain.invoke({"error_msg": error_msg, "root_cause": root_cause or "(not available)"})
                fixed_query = llm_text(response).strip()
                if fixed_query.startswith("```sql"):
                    fixed_query = fixed_query.strip("`").replace("sql\n", "", 1).strip()
                elif fixed_query.startswith("```"):
                    fixed_query = fixed_query.strip("`").strip()
                if fixed_query.strip().rstrip(".").upper() == "UNRESOLVABLE" or not fixed_query:
                    action_type = "ESCALATE"
                    param_map = {}
                    guard_blocked = True
                    justification = "LLM reported the SQL error as UNRESOLVABLE (required column has no equivalent). Escalating instead of inventing values."
                else:
                    # SQL guard: do not trust the prompt alone. Check the repaired query itself
                    # before it is ever run (see src/agents/sql_guard.py).
                    ctx = extract_repair_context(error_msg)
                    sql_aliases = get_recovery_config().get("pipelines", {}).get(pipeline_val, {}).get("sql_column_aliases", {})
                    ok, reason = check_sql_repair(ctx["original"], fixed_query, ctx["available"], ctx["table"],
                                                  column_aliases=sql_aliases)
                    if ok:
                        param_map = {"sql_override": fixed_query}
                    else:
                        action_type = "ESCALATE"
                        param_map = {}
                        guard_blocked = True
                        justification = f"SQL guard rejected the LLM repair: {reason}. Escalating instead of running it."
                        logger.warning("SQL guard rejected LLM repair", reason=reason, incident_id=state.get("incident_id"))
            except Exception as e:
                action_type = "ESCALATE"
                justification = f"LLM failed to rewrite SQL: {e}"
        else:
            param_map, justification = resolve_schema_drift(error_msg, pipeline_val, root_cause=root_cause)
            action_type = "RECONFIGURE" if param_map else "ESCALATE"

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
            justification = "Detected malformed JSON syntax. Gemini will add only the missing brackets and commas; the data may not change."

    remediation_summary = f"Action: {action_type} | Rationale: {justification}"

    if pending_error:
        # Already RECOVERY_IN_PROGRESS from the first pass: only record the new decision.
        from src.incidents.incident_repository import IncidentRepository
        IncidentRepository.update_incident_state(state["incident_id"], IncidentStatus.RECOVERY_IN_PROGRESS,
                                                 recovery_action=remediation_summary)
    else:
        IncidentManager.transition_to(
            state['incident_id'],
            IncidentStatus.RECOVERY_IN_PROGRESS,
            recovery_action=remediation_summary
        )
    AuditService.log_event(state['incident_id'], "RECOVERY_AGENT", f"Remediation generated: {action_type}. {justification}")
    if guard_blocked:
        from src.incidents.incident_repository import IncidentRepository
        IncidentRepository.mark_blocked_by_guard(state['incident_id'])

    return {
        "recovery_action": remediation_summary,
        "recovery_directive": {
            "action": action_type,
            "params": param_map,
            "error_signature": error_signature
        },
        "merged_params": param_map if action_type == "RECONFIGURE" else merged,
        "pending_error": None,
        "guard_blocked": guard_blocked,
        "audit_trail": state.get("audit_trail", []) + ["Recovery agent deployed remediation package."]
    }
