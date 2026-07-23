# src/agents/sql_guard.py
"""
SQL guard for LLM-repaired queries.

Purpose (simple words):
    When Pipeline B's query fails, Gemini writes a repaired query. The prompt tells
    Gemini never to invent values, but an LLM does not always obey. This guard checks
    the repaired query itself, BEFORE it is ever run, and rejects it if it cheats or
    is unsafe. A rejected repair is escalated to a human.

How:
    sqlglot turns both queries (the original and Gemini's) into a tree of parts, so we
    can see exactly which columns each output uses and compare them.

Rules (all must pass):
    1. The repair is exactly one SELECT statement (no DELETE, DROP, ATTACH, ...).
    2. It only reads from the original table.
    3. It only uses columns that really exist in that table.
    4. It has the same number of outputs as the original (the pipeline saves by position).
    5. An output that depended on a missing column must now use a replacement column.
       If it uses no new column, the missing column was swapped for a constant (fake data).
    6. Rename only: the repair must be the ORIGINAL query with each missing column name
       swapped for a new name, and nothing else changed. The new name must be on the
       approved list in configs/settings.yaml. This blocks formula tricks such as
       "http_status * 0" and real-but-wrong columns such as "timestamp".

Known cost: a correct rename that also changes something cosmetic (e.g. drops the
label "AS error_rate") is rejected too. That is safe: it just goes to a human.
"""
import itertools
import ast
import re

import sqlglot
from sqlglot import exp


def extract_repair_context(error_msg: str) -> dict:
    """Pulls the table, its real columns and the failed query out of Pipeline B's error message."""
    ctx = {"table": None, "available": None, "original": None}
    cols = re.search(r"Available columns in (\w+):\s*(\[[^\]]*\])", error_msg or "")
    if cols:
        ctx["table"] = cols.group(1)
        try:
            ctx["available"] = list(ast.literal_eval(cols.group(2)))
        except Exception:
            pass
    query = re.search(r"Query:\s*(.*)\Z", error_msg or "", re.DOTALL)
    if query:
        ctx["original"] = query.group(1).strip()
    return ctx


def _columns(node) -> set[str]:
    return {c.name.lower() for c in node.find_all(exp.Column)}


def _normalized(tree) -> str:
    """Same query text regardless of spacing, capitals or quoting."""
    return tree.sql(dialect="sqlite", normalize=True)


def check_sql_repair(original_sql: str, fixed_sql: str, available_cols: list[str], table: str,
                     column_aliases: dict | None = None) -> tuple[bool, str]:
    """
    Returns (ok, reason). ok=False means the repaired query must not be run.
    column_aliases: approved replacement names per column, e.g. {"status_code": ["http_status"]}.
    """
    if not original_sql or not available_cols or not table:
        return False, "cannot verify the repair (original query or table columns unknown)"

    try:
        statements = [s for s in sqlglot.parse(fixed_sql, read="sqlite") if s is not None]
    except Exception as e:
        return False, f"repair is not valid SQL ({str(e).splitlines()[0][:80]})"
    if len(statements) != 1:
        return False, f"repair must be exactly one statement, got {len(statements)}"
    fixed = statements[0]

    # Rule 1: read-only, a single SELECT
    if not isinstance(fixed, exp.Select):
        return False, f"repair is a {fixed.key.upper()} statement, only SELECT is allowed"

    # Rule 2: only the original table
    tables = {t.name.lower() for t in fixed.find_all(exp.Table)}
    if tables != {table.lower()}:
        return False, f"repair reads from table(s) {sorted(tables)}, only '{table}' is allowed"

    # Rule 3: only real columns (output aliases such as ORDER BY error_rate are fine)
    available = {c.lower() for c in available_cols}
    aliases = {e.alias.lower() for e in fixed.expressions if e.alias}
    unknown = _columns(fixed) - available - aliases
    if unknown:
        return False, f"repair uses columns that do not exist: {sorted(unknown)}"

    # Rule 4: same outputs, same order
    try:
        original = sqlglot.parse_one(original_sql, read="sqlite")
    except Exception:
        return False, "cannot parse the original query"
    if len(original.expressions) != len(fixed.expressions):
        return False, (f"repair changes the number of outputs "
                       f"({len(original.expressions)} -> {len(fixed.expressions)})")

    # Rule 5: a missing column must be replaced by a real column, not a constant
    for i, (orig_out, new_out) in enumerate(zip(original.expressions, fixed.expressions), start=1):
        orig_cols = _columns(orig_out)
        missing = orig_cols - available
        if not missing:
            continue
        if not (_columns(new_out) - orig_cols):
            name = orig_out.alias_or_name or f"output {i}"
            return False, (f"'{name}' needed missing column(s) {sorted(missing)} and the repair "
                           f"replaced them with a constant instead of a real column (fake data)")

    # Rule 6: rename only, and only to approved names
    missing_cols = sorted(_columns(original) - available)
    if not missing_cols:
        return False, "only column-rename repairs are allowed, and no column is missing from the table"
    aliases = {k.lower(): [a.lower() for a in v] for k, v in (column_aliases or {}).items()}

    new_cols = _columns(fixed) - _columns(original)
    approved_any = {a for m in missing_cols for a in aliases.get(m, [])}
    not_approved = sorted(new_cols - approved_any)
    if not_approved:
        return False, (f"replacement column(s) {not_approved} are not on the approved list for "
                       f"{missing_cols} in configs/settings.yaml")

    # Try every approved (missing -> replacement) combination that exists in the table
    choices = [[a for a in aliases.get(m, []) if a in available] for m in missing_cols]
    if any(not c for c in choices):
        return False, f"no approved replacement exists in the table for {missing_cols}"
    target = _normalized(fixed)
    for combo in itertools.product(*choices):
        mapping = dict(zip(missing_cols, combo))
        renamed = original.transform(
            lambda node: exp.column(mapping[node.name.lower()])
            if isinstance(node, exp.Column) and node.name.lower() in mapping else node
        )
        if _normalized(renamed) == target:
            return True, f"ok (renamed {mapping})"

    return False, ("repair changes more than a column rename (the formula or query shape was "
                   "altered), which could hide fake values")
