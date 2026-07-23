"""
Incident report: one incident plus its audit trail, as JSON or as a real PDF file.

Purpose: let someone (e.g. an interviewer) download what happened in a run and keep it,
without screenshots. See CHANGES.md section 13.
"""
import os
import re
from datetime import datetime, timezone
from functools import lru_cache

QUARANTINE_ONLY = {"DataQualityAnomaly", "ReferentialIntegrityAnomaly"}

# --------------------------------------------------------------------------- fonts and text
# The PDF used the built-in Helvetica/Courier fonts, which only know Latin-1, so "Москва" or
# "José ñ" printed as "?" (CHANGES.md section 27). Now the report embeds Noto fonts (SIL Open
# Font License, about 2 MB in src/assets/fonts):
#   Noto Sans          Latin (with accents), Cyrillic, Greek, Devanagari (Hindi, Marathi, ...)
#   Noto Sans Telugu   Telugu
# Indian scripts also need "text shaping" (letters join into conjuncts and vowel signs move), which
# the PDF library does with the optional uharfbuzz package. Without uharfbuzz the report still
# prints, but Telugu and Hindi look broken, so it is in requirements.txt.
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
MAIN_FONT = "NotoSans"
FONT_FILES = {  # family -> file prefix. The first family is the main font, the rest are fallbacks.
    "NotoSans": "NotoSans",
    "NotoSansTelugu": "NotoSansTelugu",
}
EMOJI_MARKER = "[emoji]"

# An emoji is one picture made of one or more code points (flags, skin tones, joined families...).
_EMOJI_CHAR = "[\U0001F000-\U0001FAFF\u2600-\u27BF\u231A\u231B\u23E9-\u23F3\u23F8-\u23FA\u2B1B\u2B1C\u2B50\u2B55]"
_EMOJI_SEQUENCE = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2}"                                   # flag
    f"|{_EMOJI_CHAR}[\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]?"            # one emoji (+ variation / skin tone)
    f"(?:\u200D{_EMOJI_CHAR}[\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]?)*"  # joined with zero-width joiners
    ")"
)
# Kept even though no font lists them: Indic scripts use these two to control how letters join.
_JOINERS = {0x200C, 0x200D}
_CHAR_FIXES = {"\u00a0": " ", "\u2022": "-", "\u2192": "->", "\u2190": "<-", "\u2014": "-", "\u2013": "-",
               "\u2026": "...", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}


@lru_cache(maxsize=1)
def _supported_chars() -> frozenset[int]:
    """Every character at least one bundled font can draw."""
    from fontTools.ttLib import TTFont  # installed with fpdf2
    chars: set[int] = set()
    for prefix in FONT_FILES.values():
        font = TTFont(os.path.join(FONT_DIR, f"{prefix}-Regular.ttf"), lazy=True)
        chars.update(font.getBestCmap().keys())
    return frozenset(chars)


def _emoji_or_keep(match: "re.Match[str]") -> str:
    """A lone symbol that a font really has (e.g. a plain arrow) is kept; real emoji become a marker."""
    text = match.group(0)
    if len(text) == 1 and ord(text) in _supported_chars():
        return text
    return EMOJI_MARKER


def _text(value) -> str:
    """Text that is safe to print: emoji become "[emoji]", anything no bundled font can draw (e.g.
    Chinese) becomes "?", and everything else (accents, Cyrillic, Greek, Telugu, Hindi...) is kept."""
    s = "" if value is None else str(value)
    s = s.replace("\r", "").replace("\t", "    ")
    s = _EMOJI_SEQUENCE.sub(_emoji_or_keep, s)
    supported = _supported_chars()
    out = []
    for ch in s:
        cp = ord(ch)
        if ch == "\n" or cp in supported or cp in _JOINERS:
            out.append(ch)
        elif ch in _CHAR_FIXES:
            out.append(_CHAR_FIXES[ch])
        elif cp in (0xFE0E, 0xFE0F):      # stray emoji variation selectors
            continue
        else:
            out.append("?")
    return "".join(out)


def outcome(incident: dict) -> str:
    """The same words the dashboard uses."""
    status = incident.get("status")
    if status == "RESOLVED":
        return "Quarantined (caught by validation)" if incident.get("error_class") in QUARANTINE_ONLY else "Healed by agents"
    if status == "ESCALATED":
        return "Escalated to a person"
    return f"Still running ({status})"


def build_report(incident: dict, audit: list[dict]) -> dict:
    return {
        "report_generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outcome": outcome(incident),
        "incident": incident,
        "audit_trail": audit,
    }


def _setup_fonts(pdf) -> None:
    """Embeds the Noto fonts, lets Telugu / Devanagari letters fall back to their own font, and
    turns on text shaping when uharfbuzz is installed."""
    for family, prefix in FONT_FILES.items():
        pdf.add_font(family, "", os.path.join(FONT_DIR, f"{prefix}-Regular.ttf"))
        pdf.add_font(family, "B", os.path.join(FONT_DIR, f"{prefix}-Bold.ttf"))
    pdf.set_fallback_fonts([f for f in FONT_FILES if f != MAIN_FONT])
    try:
        import uharfbuzz  # noqa: F401  (only checking that it is installed)
        pdf.set_text_shaping(True)
    except ImportError:
        pass  # PDF still works; Latin / Cyrillic / Greek are fine, Indian scripts will not join correctly


def build_pdf(report: dict) -> bytes:
    from fpdf import FPDF  # imported here so the rest of the API works even if fpdf2 is missing
    from fpdf.enums import XPos, YPos

    inc = report["incident"]
    pdf = FPDF(format="A4")
    _setup_fonts(pdf)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(_text(f"Incident report {inc.get('incident_id')}"))
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def heading(text, size=12):
        pdf.ln(3)
        pdf.set_font(MAIN_FONT, "B", size)
        pdf.cell(width, 8, _text(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def para(text, size=10, style=""):
        pdf.set_font(MAIN_FONT, style, size)
        pdf.multi_cell(width, 5, _text(text) or "-", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font(MAIN_FONT, "B", 18)
    pdf.cell(width, 10, "Incident report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(MAIN_FONT, "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(width, 5, _text(f"Self-healing data pipeline  |  generated {report['report_generated_at']}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)

    heading("Summary")
    rows = [
        ("Outcome", report["outcome"]),
        ("Incident", inc.get("incident_id")),
        ("Pipeline", inc.get("pipeline_id")),
        ("Run", inc.get("run_id")),
        ("Severity", inc.get("severity")),
        ("Category", inc.get("category")),
        ("Error", inc.get("error_class")),
        ("Created", inc.get("created_at")),
        ("Finished", inc.get("resolved_at") or "-"),
    ]
    for label, value in rows:
        pdf.set_font(MAIN_FONT, "B", 10)
        pdf.cell(32, 6, _text(label))
        pdf.set_font(MAIN_FONT, "", 10)
        pdf.multi_cell(width - 32, 6, _text(value) or "-", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    heading("Error message")
    para(inc.get("error_message"))
    heading("Root cause (Gemini)")
    para(inc.get("root_cause") or "Not analysed (no agent was needed, or it stopped earlier).")
    heading("Recovery action")
    para(inc.get("recovery_action"))

    heading("Audit trail")
    for a in report["audit_trail"]:
        pdf.set_font(MAIN_FONT, "", 8)
        pdf.set_text_color(110, 110, 110)
        pdf.cell(width, 4, _text(f"{a.get('timestamp')}  [{a.get('component')}]"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        para(a.get("message"), size=9)
        pdf.ln(1)
    if not report["audit_trail"]:
        para("No audit entries.")

    return bytes(pdf.output())
