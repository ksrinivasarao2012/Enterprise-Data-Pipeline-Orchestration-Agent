"""
Plain-English text for an escalation (CHANGES.md section 16).

The escalation log stores the raw technical error (error signatures, SQL queries, Python
error names). That is useful for engineers but hard to read. This turns it into:

    summary - what went wrong, in one short sentence
    why     - why the agents handed it to a person instead of fixing it

The raw error is still returned as `reason`, so nothing is lost.
"""
import re

_SIGNATURE = re.compile(r"^\s*\[ErrorSignature:[^\]]*\]\s*")
_GUARD = re.compile(r"SQL guard rejected|LLM file repair rejected", re.I)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _guard_reason(text: str) -> str:
    """The part after 'rejected ...:' in a guard message, cleaned up."""
    m = re.search(r"rejected(?:\s+the\s+LLM\s+repair)?\s*[:\-]?\s*(.*)$", text, re.I | re.S)
    reason = (m.group(1) if m else text).strip()
    reason = re.sub(r"\.?\s*(Escalating instead of running it|The file was not changed)\.?\s*$", "", reason, flags=re.I)
    # "['status_code', 'x']" -> "status_code, x"
    reason = re.sub(r"\[([^\]]*)\]", lambda m: m.group(1).replace("'", "").replace('"', ""), reason)
    return reason.rstrip(".")


def explain(reason: str, recovery_action: str | None = None, audit_messages: list[str] | None = None) -> dict:
    raw = _SIGNATURE.sub("", reason or "").strip()
    summary, why = None, "The agents could not fix this safely, so a person needs to look at it."

    if m := re.search(r"no such column:\s*(\w+)", raw, re.I):
        table = re.search(r"columns in (\w+)", raw)
        where = f" from the table '{table.group(1)}'" if table else ""
        summary = f"The column '{m.group(1)}' is missing{where}."
        why = "The agents will not invent values for a missing column, so a person must decide what to do."
    elif m := re.search(r"table '(\w+)' does not exist|no such table:\s*(\w+)", raw, re.I):
        summary = f"The source table '{m.group(1) or m.group(2)}' does not exist."
        why = "There is no data to repair, so there is no safe automatic fix."
    elif re.search(r"Invalid JSON|JSONDecodeError|Expecting .* delimiter", raw, re.I):
        line = re.search(r"line (\d+)", raw)
        summary = f"The JSON file is broken{f' near line {line.group(1)}' if line else ''} (a bracket, comma or value is missing)."
        why = "Gemini may only fix brackets and commas, not add missing data, so a person must check the file."
    elif m := re.search(r"KeyError\|(\w+)\|", reason or ""):
        summary = f"The column '{m.group(1)}' is missing and no matching column was found."
        why = "The agents only map a column when they find a safe match, so a person must decide."
    elif re.search(r"missing on disk|FileNotFound", raw, re.I):
        summary = "The source file could not be found."
        why = "There is no data to repair, so there is no safe automatic fix."
    else:
        # Unknown error: keep it short and drop long SQL queries.
        short = raw.split(" Query:")[0].strip()
        summary = _cap(short[:160] + ("..." if len(short) > 160 else "")) or "Unknown failure."

    # A safety check that blocked an AI fix is the most precise "why".
    for text in [recovery_action or "", *(audit_messages or [])]:
        if m := re.match(r"Healing crashed:\s*(\w+)", text):
            why = f"The healing itself crashed ({m.group(1)}), so a person was asked to look at it instead."
            break
        if re.search(r"UNRESOLVABLE", text):
            why = "Gemini found no safe replacement for the missing data, so it refused to guess."
            break
        if _GUARD.search(text):
            why = f"A safety check blocked the AI fix: {_guard_reason(text)}."
            break

    return {"summary": summary, "why": why}
