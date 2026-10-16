# src/services/remediation_service.py
import sys
import os
import re
import json
import difflib
from typing import Dict, Any

from src.models.schemas import IncidentStatus, PipelineStatus
from src.incidents.incident_manager import IncidentManager
from src.services.pipeline_service import PipelineService
from src.services.audit_service import AuditService
from src.config import save_pipeline_config_draft, verify_pipeline_config, delete_pipeline_config_version, get_recovery_config, llm_text
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("remediation_service")

class RemediationService:
    # Max healing incidents allowed per pipeline run before the actuator gives up
    # and escalates, guarding against runaway self-healing recursion.
    MAX_HEAL_ATTEMPTS = 4

    @classmethod
    def heal_attempts_exceeded(cls, run_id: str | None) -> bool:
        """True when a run has already raised more incidents than the healing budget allows."""
        if not run_id:
            return False
        from src.incidents.incident_repository import IncidentRepository
        return IncidentRepository.count_incidents_for_run(run_id) > cls.MAX_HEAL_ATTEMPTS

    @staticmethod
    def _run_pipeline(pipeline_id: str, source_path: str | None, original_filename: str | None,
                      run_id: str | None, schema_mapping_override: dict | None = None,
                      sql_override: str | None = None):
        """Runs a pipeline as a verification run: it raises on failure instead of opening a
        new incident, so healing can never start another healing loop inside itself."""
        kwargs = {"original_filename": original_filename, "run_id": run_id, "throw_on_error": True}
        if pipeline_id == "PIPELINE_A":
            from src.pipeline.json_pipeline import PipelineA
            if source_path:
                kwargs["json_file_path"] = source_path
            return PipelineA().execute(schema_mapping_override=schema_mapping_override, **kwargs)
        if pipeline_id == "PIPELINE_B":
            from src.pipeline.db_pipeline import PipelineB
            if source_path:
                kwargs["op_db_path"] = source_path
            return PipelineB().execute(sql_override=sql_override, **kwargs)
        raise ImportError("Unknown operational pipeline context target.")

    @classmethod
    def _mark_healed(cls, incident_id: str, run_id: str | None, message: str) -> None:
        if run_id:
            PipelineService.update_run_status(run_id, PipelineStatus.HEALED)
        IncidentManager.transition_to(incident_id, IncidentStatus.RESOLVED)
        AuditService.log_event(incident_id, "ACTUATOR", message)

    # Every value in a JSON text: "strings", numbers, true / false / null.
    _JSON_VALUE_TOKENS = re.compile(r'"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b')

    @classmethod
    def _repair_keeps_data(cls, original_text: str, repaired_text: str) -> tuple[bool, str]:
        """
        Security fix 5: Gemini may only fix brackets, commas and quotes-around-structure.
        Every key and value in the repaired file must match the broken original exactly:
        nothing changed, added or removed. This stops hidden instructions in an uploaded
        file (e.g. "change every email") and stops Gemini inventing missing values.
        """
        from collections import Counter
        try:
            json.loads(repaired_text)
        except Exception:
            return False, "the repaired file is still not valid JSON"

        def values(text):
            out = []
            for tok in cls._JSON_VALUE_TOKENS.findall(text):
                if tok.startswith('"'):
                    try:
                        tok = "s:" + json.loads(tok)
                    except Exception:
                        tok = "s:" + tok
                out.append(tok)
            return Counter(out)

        before, after = values(original_text), values(repaired_text)
        if before == after:
            return True, "ok"
        added = list((after - before).elements())[:3]
        removed = list((before - after).elements())[:3]
        return False, f"the repair changed the data (added {added}, removed {removed})"

    @staticmethod
    def _is_single_select(sql: str) -> bool:
        """True only for exactly one SELECT statement."""
        try:
            import sqlglot
            from sqlglot import exp
            statements = [st for st in sqlglot.parse(sql, read="sqlite") if st is not None]
        except Exception:
            return False
        return len(statements) == 1 and isinstance(statements[0], exp.Select)

    @classmethod
    def validate_fix_schema(cls, pipeline_id: str, params: dict) -> bool:
        """Validates proposed configuration overrides against structural whitelists and injection guards."""
        if not params:
            return False
        recovery_cfg = get_recovery_config()
        allowed_keys = set(recovery_cfg.get("pipelines", {}).get(pipeline_id, {}).get("allowed_params", []))

        def _normalize_text(value: str | None) -> str:
            if not value:
                return ""
            return re.sub(r"[^a-z0-9]", "", value.lower())

        def _best_fuzzy_match(target: str, candidates: set[str], cutoff: float = 0.82) -> str | None:
            if not target or not candidates:
                return None
            normalized_target = _normalize_text(target)
            normalized_candidates = {c: _normalize_text(c) for c in candidates}
            # exact match
            for c, nc in normalized_candidates.items():
                if nc == normalized_target:
                    return c
            # best ratio
            scored = [(difflib.SequenceMatcher(None, normalized_target, nc).ratio(), c) for c, nc in normalized_candidates.items()]
            scored.sort(reverse=True)
            if scored and scored[0][0] >= cutoff:
                return scored[0][1]
            return None

        for key, val in list(params.items()):
            # If allowed_keys is non-empty, ensure key is allowed (or fuzzy-match to an allowed key)
            if allowed_keys and key not in allowed_keys:
                matched = _best_fuzzy_match(key, allowed_keys, cutoff=0.82)
                if matched:
                    # replace param key with matched allowed key for further validation
                    params[matched] = params.pop(key)
                    key = matched
                else:
                    logger.warning("Validation rejected: Key not in allowed parameter whitelist", pipeline_id=pipeline_id, key=key)
                    return False
            
            # Value check (security fix 7). The old banned-word list is gone: it blocked harmless
            # names such as "last_updated" and missed risky SQL such as ATTACH.
            if key == "flatten_root_dict":
                if not isinstance(val, (bool, str)) or str(val).lower() not in ["true", "false"]:
                    logger.warning("Validation rejected: Invalid boolean for flatten_root_dict", pipeline_id=pipeline_id, value=val)
                    return False
            elif key == "sql_override":
                # Second lock for SQL: whatever the SQL guard approved must still be exactly
                # one read-only SELECT statement (blocks DELETE, DROP, ATTACH, PRAGMA, ...).
                if not isinstance(val, str) or not cls._is_single_select(val):
                    logger.warning("Validation rejected: SQL override is not a single SELECT", pipeline_id=pipeline_id)
                    return False
            else:
                # A column name may only contain letters, numbers and "_". With no spaces,
                # quotes or ";" allowed, no SQL can be written into it.
                if not isinstance(val, str) or not re.match(r"^[a-zA-Z0-9_]+$", val):
                    logger.warning("Validation rejected: Invalid alphanumeric column identifier", pipeline_id=pipeline_id, value=val)
                    return False

        return True

    # ------------------------------------------------------------------ graph steps
    # The LangGraph agent graph (src/orchestration/graph.py) calls these one step at a time:
    # Apply fix -> Verify -> (Healed | back to Recovery | Escalate). Before CHANGES.md
    # section 23 the retry loop was hidden inside this class; now the graph owns it.

    @classmethod
    def apply_fix(cls, incident_id: str, pipeline_id: str, run_id: str | None, action: str,
                  params: dict, source_path: str | None = None, root_cause: str | None = None) -> dict:
        """Applies one fix and runs the pipeline once to check it (a verification run that
        raises instead of opening a new incident). Returns what happened:
            {"rows": int}           the run saved rows
            {"error": str}          the run failed again (maybe another drift)
            {"blocked": str}        a safety check refused the fix before it ran
            {"quarantined": True}   the bad rows were isolated (nothing to re-run)
        """
        AuditService.log_event(incident_id, "ACTUATOR", f"Executing remediation strategy: [{action}]")
        from src.incidents.incident_repository import IncidentRepository
        incident = IncidentRepository.get_incident(incident_id)
        meta = incident.telemetry_metadata if incident and isinstance(incident.telemetry_metadata, dict) else {}
        original_filename = meta.get("original_filename")

        if action == "QUARANTINE":
            return {"quarantined": True}

        if action in ("RECONFIGURE", "AI_FIX") and params and not cls.validate_fix_schema(pipeline_id, params):
            logger.warning("Proposed configuration rejected during validation", incident_id=incident_id)
            return {"blocked": "the proposed configuration failed validation"}

        if action == "REPAIR_FILE":
            blocked = cls._repair_file_with_llm(incident_id, source_path, root_cause)
            if blocked:
                return {"blocked": blocked}

        # AI_FIX: the AI-fix agent already wrote the fixed copy of the file (it passed the dry
        # run, CHANGES.md section 34); this is the real run with any column fixes found so far.
        if action not in ("RECONFIGURE", "REPAIR_FILE", "RETRY", "AI_FIX"):
            return {"blocked": f"unknown action {action}"}

        try:
            rows = cls._run_pipeline(
                pipeline_id, source_path, original_filename, run_id,
                schema_mapping_override=(params or None) if (action in ("RECONFIGURE", "AI_FIX") and pipeline_id == "PIPELINE_A") else None,
                sql_override=params.get("sql_override") if (action == "RECONFIGURE" and pipeline_id == "PIPELINE_B") else None,
            )
        except Exception as e:
            logger.warning("Verification run failed", error=str(e), incident_id=incident_id)
            return {"error": str(e), "error_class": type(e).__name__}
        return {"rows": rows or 0}

    @classmethod
    def _repair_file_with_llm(cls, incident_id: str, source_path: str | None, root_cause: str | None) -> str | None:
        """Asks Gemini to fix the JSON syntax only. Returns None on success, or why it was refused."""
        if not source_path or not os.path.exists(source_path):
            logger.error("Source path not found for file repair", source_path=source_path)
            return "the source file was not found"

        from src.config import get_llm_config
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.prompts import ChatPromptTemplate

        llm_cfg = get_llm_config()
        llm = ChatGoogleGenerativeAI(model=llm_cfg["model_name"], temperature=0.0)
        with open(source_path, "r", encoding="utf-8") as f:
            raw_content = f.read()

        # Recovery uses RCA: Gemini's own diagnosis is passed in, and the rules are strict.
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a JSON syntax repair agent. Output ONLY the repaired raw JSON, with no markdown or explanations. "
                       "You may ONLY add or remove brackets, braces, commas, colons and the quotes around structure. "
                       "Never add, remove or change any key or value, even if a record looks incomplete "
                       "(for example a missing field): leave that record as it is, it will be checked separately."),
            ("human", "Root cause analysis from the RCA agent:\n{root_cause}\n\nFix only the syntax of this JSON file:\n{raw_content}")
        ])
        try:
            response = (prompt | llm).invoke({"raw_content": raw_content, "root_cause": root_cause or "(not available)"})
        except Exception as e:
            logger.exception("File repair LLM invocation failed", error=str(e))
            return f"Gemini could not be reached ({type(e).__name__})"

        fixed_content = llm_text(response).strip()
        if fixed_content.startswith("```json"):
            fixed_content = fixed_content.strip("`").replace("json\n", "", 1).strip()
        elif fixed_content.startswith("```"):
            fixed_content = fixed_content.strip("`").strip()

        ok, reason = cls._repair_keeps_data(raw_content, fixed_content)
        if not ok:
            logger.warning("LLM file repair rejected", reason=reason, incident_id=incident_id)
            AuditService.log_event(incident_id, "ACTUATOR", f"LLM file repair rejected: {reason}. The file was not changed.")
            from src.incidents.incident_repository import IncidentRepository
            IncidentRepository.mark_blocked_by_guard(incident_id)
            return reason

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(fixed_content)
        AuditService.log_event(incident_id, "ACTUATOR", "File syntax repaired successfully by LLM.")
        return None

    @classmethod
    def finish_healed(cls, incident_id: str, pipeline_id: str, run_id: str | None, action: str,
                      params: dict, error_signature: str, rows: int) -> None:
        """Real success: the verification run saved rows with every fix collected so far."""
        if action == "RECONFIGURE":
            draft_version = save_pipeline_config_draft(pipeline_id, error_signature, params)
            verify_pipeline_config(pipeline_id, error_signature, draft_version)
            cls._mark_healed(incident_id, run_id,
                             f"Configuration version {draft_version} verified ({params}). "
                             f"Pipeline auto-remediated and saved {rows} records.")
            # Restore baseline so the next (possibly healthy) file is not forced through this mapping.
            try:
                from src.config import reset_pipeline_configs
                reset_pipeline_configs(pipeline_id)
            except Exception:
                pass
        elif action == "AI_FIX":
            cls._mark_healed(incident_id, run_id,
                             f"Healed by an AI fix that passed a dry run on a copy. Re-run saved {rows} records.")
        else:
            cls._mark_healed(incident_id, run_id, f"Auto-remediation successful. Re-run saved {rows} records.")

    @classmethod
    def finish_quarantined(cls, incident_id: str, run_id: str | None) -> None:
        # The bad rows are isolated, which is the correct outcome, but no data was saved,
        # so the run is QUARANTINED, not HEALED (HEALED is reserved for runs that saved rows).
        if run_id:
            PipelineService.update_run_status(run_id, PipelineStatus.QUARANTINED)
        IncidentManager.transition_to(incident_id, IncidentStatus.RESOLVED)
        AuditService.log_event(incident_id, "ACTUATOR", "Poisoned records isolated to quarantine. No valid rows remained to load, so the run is QUARANTINED (not HEALED).")

    @classmethod
    def _handle_escalation(cls, incident_id: str, reason: str | None = None) -> None:
        """Fallback routine when automation is exhausted. Routes to human operators."""
        logger.critical("Automated remediation bounds exceeded; escalating to on-call")
        IncidentManager.transition_to(incident_id, IncidentStatus.ESCALATED)
        # Logged as its own step (not "ACTUATOR"), so the dashboard does not light up Verify
        # for incidents that were escalated without any fix being tried.
        AuditService.log_event(incident_id, "ESCALATION", reason or "System automated runbooks exhausted. Incident escalated to human engineering queue.")
        # The Escalations list is read from the incidents table (status ESCALATED), so nothing
        # else needs to be written here (CHANGES.md section 20).
