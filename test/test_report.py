"""
Download report (CHANGES.md section 13): one incident + its audit trail as JSON or a real PDF.
"""
import pytest

ADMIN, VIEWER = "admin-rep-key", "viewer-rep-key"


def _escalated_incident(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})
    return client.get("/api/incidents").json()[0]


def test_json_report_has_incident_and_audit(client, fake_llm):
    inc = _escalated_incident(client, fake_llm)
    r = client.get(f"/api/incidents/{inc['incident_id']}/report?format=json")
    assert r.status_code == 200
    assert f'{inc["incident_id"]}_report.json' in r.headers["content-disposition"]
    body = r.json()
    assert body["incident"]["incident_id"] == inc["incident_id"]
    assert body["outcome"] == "Escalated to a person"
    components = [a["component"] for a in body["audit_trail"]]
    assert "RCA_AGENT" in components and "ESCALATION" in components  # escalated by Recovery, no fix applied


def test_pdf_report_is_a_real_pdf(client, fake_llm):
    pytest.importorskip("fpdf")
    inc = _escalated_incident(client, fake_llm)
    r = client.get(f"/api/incidents/{inc['incident_id']}/report?format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 1000


def test_pdf_survives_unusual_characters(fake_llm):
    pytest.importorskip("fpdf")
    from src.services.report_service import build_pdf, build_report
    inc = {"incident_id": "inc_x", "status": "RESOLVED", "error_class": "KeyError",
           "root_cause": "Column “country” → “nation” — renamed 🚀 列", "error_message": None}
    audit = [{"component": "RCA_AGENT", "message": "Analysis… done ✓", "timestamp": "t"}]
    assert build_pdf(build_report(inc, audit)).startswith(b"%PDF")


def test_quarantine_only_outcome_wording():
    from src.services.report_service import outcome
    assert outcome({"status": "RESOLVED", "error_class": "DataQualityAnomaly"}).startswith("Quarantined")
    assert outcome({"status": "RESOLVED", "error_class": "KeyError"}) == "Healed by agents"


def test_unknown_incident_and_bad_format(client, fake_llm):
    inc = _escalated_incident(client, fake_llm)
    assert client.get("/api/incidents/inc_nope/report").status_code == 404
    assert client.get(f"/api/incidents/{inc['incident_id']}/report?format=exe").status_code == 422


def test_report_needs_a_key_but_viewers_may_download(client, fake_llm, monkeypatch):
    inc = _escalated_incident(client, fake_llm)
    monkeypatch.setenv("EHA_ADMIN_KEY", ADMIN)
    monkeypatch.setenv("EHA_VIEWER_KEY", VIEWER)
    url = f"/api/incidents/{inc['incident_id']}/report"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Access-Key": VIEWER}).status_code == 200


# ---------------------------------------------------------------- Unicode in the PDF (CHANGES.md section 27)

def _pdf_text_and_fonts(pdf_bytes):
    pypdf = pytest.importorskip("pypdf")
    import io
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() for page in reader.pages)
    fonts = set()
    for page in reader.pages:
        for f in page["/Resources"]["/Font"].values():
            fonts.add(str(f.get_object()["/BaseFont"]))
    return text, " ".join(fonts)


def _report(**fields):
    from src.services.report_service import build_report
    inc = {"incident_id": "inc_u", "status": "RESOLVED", "error_class": "KeyError", "error_message": None, **fields}
    return build_report(inc, [{"component": "RCA_AGENT", "message": fields.get("audit", "ok"), "timestamp": "t"}])


def test_pdf_prints_accents_cyrillic_telugu_and_marks_emoji():
    pytest.importorskip("fpdf")
    from src.services.report_service import build_pdf
    report = _report(root_cause="José  Москва  తెలుగు  🚀")
    text, fonts = _pdf_text_and_fonts(build_pdf(report))

    assert "José" in text                      # accents, not "Jos?"
    assert "Москва" in text                    # Cyrillic, not "??????"
    assert "తెలుగు" in text                     # Telugu (needs the Telugu font)
    assert "🚀" not in text and "[emoji]" in text
    assert "NotoSansTelugu" in fonts           # the Telugu font was really embedded
    assert "Jos?" not in text and "??????" not in text


def test_pdf_prints_greek_hindi_and_keeps_bold_headings():
    pytest.importorskip("fpdf")
    from src.services.report_service import build_pdf
    text, _ = _pdf_text_and_fonts(build_pdf(_report(root_cause="Αθήνα हिन्दी")))
    assert "Αθήνα" in text and "हिन्दी" in text
    assert "Root cause" in text  # headings (bold) still print


def test_each_emoji_becomes_one_marker_and_plain_symbols_stay():
    from src.services.report_service import _text
    # flag, family (joined with zero-width joiners) and a skin-tone emoji: one marker each
    assert _text("a 🇮🇳 b 👨‍👩‍👧 c 👍🏽 d 🚀🚀") == "a [emoji] b [emoji] c [emoji] d [emoji][emoji]"
    assert _text("a — c “q” José ñ") == "a — c “q” José ñ"       # letters and quotes stay
    assert _text("a → b") == "a -> b"         # no font has this arrow, so the old plain-text swap is used
    assert _text("列") == "?"                  # Chinese is not bundled: same "?" as before, no crash
    assert _text(None) == "" and _text("x\ty\r\nz") == "x    y\nz"


def test_indic_joiners_are_kept():
    from src.services.report_service import _text
    word = "క్ష‌"                          # a zero-width non-joiner is part of the word
    assert _text(word) == word


def test_pdf_still_builds_without_uharfbuzz(monkeypatch):
    pytest.importorskip("fpdf")
    import sys
    monkeypatch.setitem(sys.modules, "uharfbuzz", None)   # "import uharfbuzz" now raises ImportError
    from src.services.report_service import build_pdf
    assert build_pdf(_report(root_cause="José తెలుగు")).startswith(b"%PDF")


def test_unicode_incident_downloads_through_the_api(client, fake_llm):
    pytest.importorskip("fpdf")
    from src.database import get_db_connection
    inc = _escalated_incident(client, fake_llm)
    with get_db_connection("state_db") as conn:
        conn.execute("UPDATE incidents SET root_cause = ? WHERE incident_id = ?",
                     ("José, Москва, తెలుగు 🚀", inc["incident_id"]))
        conn.commit()
    r = client.get(f"/api/incidents/{inc['incident_id']}/report?format=pdf")
    assert r.status_code == 200
    text, _ = _pdf_text_and_fonts(r.content)
    assert "José" in text and "Москва" in text and "తెలుగు" in text and "[emoji]" in text
    # the JSON report keeps the original text untouched (only the PDF swaps the emoji)
    assert "🚀" in client.get(f"/api/incidents/{inc['incident_id']}/report?format=json").json()["incident"]["root_cause"]
