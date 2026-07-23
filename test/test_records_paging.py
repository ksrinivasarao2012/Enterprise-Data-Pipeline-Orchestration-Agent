"""
Records paging (CHANGES.md section 17): /api/quarantine and /api/escalations return one page,
newest first, with the total in X-Total-Count. Only the escalations on the page get their
plain-English text, so the list stays fast when it grows.
"""
import json

import pytest

BAD_ROWS = [{"customer_id": str(i), "name": f"n{i}", "email": "not-an-email", "country": "IN"} for i in range(25)]
BAD_ROWS.append({"customer_id": "ok", "name": "ok", "email": "ok@x.com", "country": "IN"})


def _quarantine_25(client):
    r = client.post("/api/pipelines/a/execute", files={"file": ("bad.json", json.dumps(BAD_ROWS), "application/json")})
    assert r.json()["quarantined"] is True


def test_quarantine_pages(client, fake_llm):
    fake_llm("UNRESOLVED")
    _quarantine_25(client)
    first = client.get("/api/quarantine?limit=10")
    assert first.headers["X-Total-Count"] == "25" and len(first.json()) == 10
    rest = client.get("/api/quarantine?limit=50&offset=10").json()
    assert len(rest) == 15
    ids = [q["quarantine_id"] for q in first.json() + rest]
    assert len(set(ids)) == 25, "pages must not overlap"
    assert len(client.get("/api/quarantine").json()) == 25  # default page (50) still returns a list


def _escalate(client, n):
    for _ in range(n):
        client.post("/api/pipelines/b/execute", data={"test_case": "missing_table.db"})


def test_escalations_page_newest_first(client, fake_llm):
    fake_llm("UNRESOLVABLE")
    _escalate(client, 3)
    client.post("/api/pipelines/b/execute", data={"test_case": "sql_error.db"})  # newest
    r = client.get("/api/escalations?limit=2")
    assert r.headers["X-Total-Count"] == "4"
    page = r.json()
    assert len(page) == 2
    assert page[0]["summary"].startswith("The column 'status_code' is missing"), "newest first"
    assert client.get("/api/escalations?limit=2&offset=2").json()[1]["summary"].startswith("The source table")


def test_only_the_page_is_explained(client, fake_llm, monkeypatch):
    fake_llm("UNRESOLVABLE")
    _escalate(client, 5)
    from src.incidents.incident_repository import IncidentRepository
    looked_up = []
    orig = IncidentRepository.get_audit_trail.__func__
    monkeypatch.setattr(IncidentRepository, "get_audit_trail",
                        classmethod(lambda cls, i: looked_up.append(i) or orig(cls, i)))
    client.get("/api/escalations?limit=2")
    assert len(looked_up) == 2, f"only the 2 rows on the page should be looked up, not {len(looked_up)}"


@pytest.mark.parametrize("url", ["/api/quarantine", "/api/escalations"])
@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "limit=abc"])
def test_bad_paging_values_are_refused(client, url, query):
    assert client.get(f"{url}?{query}").status_code == 422
