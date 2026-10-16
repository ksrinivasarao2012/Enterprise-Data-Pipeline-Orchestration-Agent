# src/agents/ai_fix.py
"""
AI fix with a dry run (CHANGES.md section 34).

When no rule knows the fix for a Pipeline A file (a new kind of error, or a rule's fix failed),
Gemini gets ONE chance to propose a fix before the incident goes to a person.

Simple words:
    1. Gemini proposes a fix as a short list of steps from a fixed set of data operations
       (rename a field, unwrap a list, ...). It never writes code or SQL, and never edits values.
    2. Dry run: the steps are applied to a COPY of the file, in memory. The real file and the
       database are not touched.
    3. The copy must pass every check:
         - every step is allowed and works
         - the result is a list of records
         - the same number of records as the original file
         - no value was added, changed or removed (only structure and field names may change)
         - the pipeline's own schema and row checks pass, with at most a quarter of rows held back
    4. Only then is the fixed copy used for a real run, which Verify checks as usual.
       If any check fails, nothing is changed and the incident goes to a person with
       Gemini's proposal and the reason it was rejected.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("ai_fix")

# The fixed set of operations Gemini may use. Each one only moves data around or renames
# field names; none can create, edit or delete a value.
OPS = {
    "unwrap": "the file is an object holding the records under one key: use that list",
    "flatten_root": "the file is an object of records keyed by id: use its values as the list",
    "unnest": "each record holds its fields inside one sub-object: lift those fields up",
    "rename_field": "a field has a different name in every record: rename it",
    "lowercase_keys": "field names differ only in upper/lower case: make all field names lower case",
    "trim_values": "values have extra spaces around them: strip the spaces",
}
MAX_OPS = 5
MAX_HELD_BACK_SHARE = 0.25
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ \-]{0,63}$")


class OpError(ValueError):
    pass


# ---------------------------------------------------------------- applying the steps

def _records(data: Any) -> list:
    if not isinstance(data, list):
        raise OpError("this step needs a list of records; unwrap or flatten the file first")
    return data


def _apply_one(data: Any, op: dict) -> Any:
    name = op.get("op")
    if name not in OPS:
        raise OpError(f"'{name}' is not an allowed step")
    if name == "unwrap":
        key = op.get("key")
        if not isinstance(data, dict) or key not in data:
            raise OpError(f"unwrap: the file has no top-level key '{key}'")
        if not isinstance(data[key], list):
            raise OpError(f"unwrap: '{key}' does not hold a list")
        return data[key]
    if name == "flatten_root":
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise OpError("flatten_root: the file is not an object of records")
        return list(data.values())
    if name == "unnest":
        key = op.get("key")
        out = []
        for rec in _records(data):
            if not isinstance(rec, dict) or not isinstance(rec.get(key), dict):
                raise OpError(f"unnest: not every record has an object under '{key}'")
            inner = rec[key]
            rest = {k: v for k, v in rec.items() if k != key}
            clash = set(inner) & set(rest)
            if clash:
                raise OpError(f"unnest: fields {sorted(clash)} would overwrite each other")
            out.append({**rest, **inner})
        return out
    if name == "rename_field":
        old, new = op.get("from"), op.get("to")
        if not (isinstance(old, str) and isinstance(new, str) and _NAME.match(old) and _NAME.match(new)):
            raise OpError("rename_field: field names may only use letters, numbers, spaces, '-' and '_'")
        out = []
        for rec in _records(data):
            if isinstance(rec, dict) and old in rec:
                if new in rec and new != old:
                    raise OpError(f"rename_field: a record already has a field '{new}'")
                rec = {(new if k == old else k): v for k, v in rec.items()}
            out.append(rec)
        return out
    if name == "lowercase_keys":
        out = []
        for rec in _records(data):
            if isinstance(rec, dict):
                low = {}
                for k, v in rec.items():
                    lk = k.lower() if isinstance(k, str) else k
                    if lk in low:
                        raise OpError(f"lowercase_keys: two fields become '{lk}'")
                    low[lk] = v
                rec = low
            out.append(rec)
        return out
    # trim_values
    return [{k: (v.strip() if isinstance(v, str) else v) for k, v in rec.items()} if isinstance(rec, dict) else rec
            for rec in _records(data)]


def apply_ops(data: Any, ops: list) -> Any:
    """Applies the steps to a copy of `data`. Raises OpError if a step is not allowed or doesn't fit."""
    if not isinstance(ops, list) or not ops:
        raise OpError("no steps were proposed")
    if len(ops) > MAX_OPS:
        raise OpError(f"at most {MAX_OPS} steps are allowed")
    out = copy.deepcopy(data)
    for op in ops:
        if not isinstance(op, dict):
            raise OpError("each step must be an object like {\"op\": \"unwrap\", \"key\": \"customers\"}")
        out = _apply_one(out, op)
    return out


def describe(ops: list) -> str:
    parts = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        name = op.get("op")
        if name in ("unwrap", "unnest"):
            parts.append(f"{name} '{op.get('key')}'")
        elif name == "rename_field":
            parts.append(f"rename '{op.get('from')}' to '{op.get('to')}'")
        else:
            parts.append(str(name))
    return ", then ".join(parts) or "no steps"


# ---------------------------------------------------------------- the dry-run checks

def _leaf_values(data: Any) -> Counter:
    """Every value in the data (not field names), spaces trimmed, as a multiset."""
    out: Counter = Counter()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            out[(type(x).__name__, x.strip() if isinstance(x, str) else x)] += 1
    walk(data)
    return out


def _count_records(data: Any) -> int:
    """How many records the original file holds: the innermost objects that hold values."""
    if isinstance(data, list):
        return sum(_count_records(x) if isinstance(x, (dict, list)) and not _is_record(x) else 1 for x in data)
    if isinstance(data, dict):
        if _is_record(data):
            return 1
        return sum(_count_records(v) for v in data.values() if isinstance(v, (dict, list)))
    return 0


def _is_record(x: Any) -> bool:
    """A record is an object with at least one plain value (a wrapper only holds lists/objects)."""
    return isinstance(x, dict) and any(not isinstance(v, (dict, list)) for v in x.values()) and \
        sum(isinstance(v, list) for v in x.values()) == 0


@dataclass
class DryRun:
    ok: bool
    reason: str
    checks: list = field(default_factory=list)
    transformed: Any = None
    valid: int = 0
    held_back: int = 0


def dry_run(original: Any, ops: list, schema_mapping: dict) -> DryRun:
    """Runs the proposed steps on a copy and checks the result. Touches no file or database."""
    from src.pipeline.json_pipeline import check_schema, validate_row
    checks = []
    try:
        result = apply_ops(original, ops)
    except OpError as e:
        return DryRun(False, str(e), checks)
    checks.append("every step is allowed and works")

    if not isinstance(result, list) or not result or not all(isinstance(r, dict) for r in result):
        return DryRun(False, "the result is not a list of records", checks)
    checks.append("the result is a list of records")

    before = _count_records(original)
    if len(result) != before:
        return DryRun(False, f"the record count changed ({before} before, {len(result)} after)", checks)
    checks.append(f"same number of records ({before})")

    old_vals, new_vals = _leaf_values(original), _leaf_values(result)
    if old_vals != new_vals:
        added = [v for _, v in (new_vals - old_vals).elements()][:3]
        removed = [v for _, v in (old_vals - new_vals).elements()][:3]
        return DryRun(False, f"values changed (added {added}, removed {removed})", checks)
    checks.append("no value was added, changed or removed")

    try:
        check_schema(result, schema_mapping)
    except KeyError as e:
        missing = re.search(r"\|KeyError\|([^|]+)\|", str(e))
        return DryRun(False, f"the expected column '{missing.group(1) if missing else '?'}' is still missing", checks)
    seen: set = set()
    valid = held = 0
    for rec in result:
        _, record, errors = validate_row(rec, schema_mapping, seen)
        if errors:
            held += 1
        else:
            valid += 1
    if valid == 0 or held > MAX_HELD_BACK_SHARE * len(result):
        return DryRun(False, f"too many rows would fail the pipeline's checks ({held} of {len(result)})", checks,
                      valid=valid, held_back=held)
    checks.append(f"the pipeline's checks pass ({valid} valid, {held} held back)")
    return DryRun(True, "passed", checks, result, valid, held)


# ---------------------------------------------------------------- asking Gemini

def structure_summary(data: Any, max_records: int = 2) -> str:
    """What Gemini sees of the file: its shape and a couple of records, long values cut short."""
    def clip(x, depth=0):
        if isinstance(x, dict):
            return {k: clip(v, depth + 1) for k, v in list(x.items())[:12]}
        if isinstance(x, list):
            head = [clip(v, depth + 1) for v in x[:max_records]]
            return head + ([f"... {len(x) - max_records} more"] if len(x) > max_records else [])
        if isinstance(x, str) and len(x) > 40:
            return x[:40] + "..."
        return x
    return json.dumps(clip(data), ensure_ascii=False)[:3000]


_PROMPT_SYSTEM = (
    "You repair the SHAPE of a customer JSON file so a data pipeline can load it. The pipeline needs a JSON "
    "list of records, each with the fields: {expected}. "
    "You may ONLY use these steps:\n{ops}\n"
    "Never invent, change or delete values. If these steps cannot fix the file, return an empty list of steps. "
    'Answer with ONLY a JSON object: {{"steps": [{{"op": "...", ...}}], "reason": "one sentence"}}. '
    'Examples of steps: {{"op": "unwrap", "key": "customers"}}, {{"op": "unnest", "key": "customer"}}, '
    '{{"op": "rename_field", "from": "mail", "to": "email"}}, {{"op": "flatten_root"}}, {{"op": "lowercase_keys"}}, {{"op": "trim_values"}}.'
)


def parse_proposal(text: str) -> tuple[list, str]:
    """Reads Gemini's answer. Anything that is not the expected JSON counts as 'no fix'."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return [], "Gemini did not return a usable fix"
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], "Gemini did not return a usable fix"
    steps = obj.get("steps") if isinstance(obj, dict) else None
    reason = str(obj.get("reason", "")).strip()[:300] if isinstance(obj, dict) else ""
    if not isinstance(steps, list):
        return [], reason or "Gemini did not return a usable fix"
    return steps, reason


def propose_fix(error: str, root_cause: str | None, data: Any, expected: list, llm=None) -> tuple[list, str]:
    """One Gemini call. Returns (steps, reason)."""
    from langchain_core.prompts import ChatPromptTemplate
    from src.config import get_llm_config, llm_text
    if llm is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        cfg = get_llm_config()
        llm = ChatGoogleGenerativeAI(model=cfg["model_name"], temperature=0.0, timeout=20, max_retries=2)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _PROMPT_SYSTEM),
        ("human", "Pipeline error:\n{error}\n\nRoot cause analysis (may be empty):\n{root_cause}\n\n"
                  "The file (shape and first records):\n{sample}"),
    ])
    response = (prompt | llm).invoke({
        "expected": ", ".join(expected),
        "ops": "\n".join(f"- {k}: {v}" for k, v in OPS.items()),
        "error": (error or "")[:1500],
        "root_cause": (root_cause or "(not available)")[:1500],
        "sample": structure_summary(data),
    })
    return parse_proposal(llm_text(response))
