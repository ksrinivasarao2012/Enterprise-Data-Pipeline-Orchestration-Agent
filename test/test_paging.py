"""
Incident paging (CHANGES.md section 11).

Purpose: the dashboard refreshes every few seconds. Loading every incident each time gets
slow after hundreds of runs, so /api/incidents now returns one page (newest first) plus the
total count in the X-Total-Count header.
"""
import pytest

from src.incidents.incident_manager import IncidentManager
from src.incidents.incident_repository import IncidentRepository
from src.services.pipeline_service import PipelineService


def _make(n, pipeline="PIPELINE_A"):
    ids = []
    for i in range(n):
        PipelineService.start_run(f"run_page_{i}", pipeline)
        ids.append(IncidentManager.initialize_incident(f"run_page_{i}", pipeline, "ValueError", f"boom {i}", "trace").incident_id)
    return ids


def test_first_page_is_newest_and_total_is_reported(client):
    ids = _make(30)
    r = client.get("/api/incidents?limit=10")
    assert r.status_code == 200
    page = r.json()
    assert len(page) == 10
    assert [p["incident_id"] for p in page] == list(reversed(ids))[:10]  # newest first
    assert r.headers["X-Total-Count"] == "30"


def test_pages_do_not_overlap_and_cover_everything(client):
    ids = _make(25)
    seen = []
    for offset in (0, 10, 20):
        seen += [p["incident_id"] for p in client.get(f"/api/incidents?limit=10&offset={offset}").json()]
    assert len(seen) == 25 and set(seen) == set(ids)


def test_default_page_size_is_limited(client):
    _make(60)
    r = client.get("/api/incidents")
    assert len(r.json()) == 50
    assert r.headers["X-Total-Count"] == "60"


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "limit=abc"])
def test_bad_paging_values_are_refused(client, query):
    assert client.get(f"/api/incidents?{query}").status_code == 422


def test_classifier_count_query_matches_old_behaviour(client):
    # The classifier used to load ALL incidents to count the unresolved ones; now it counts in SQL.
    ids = _make(4, "PIPELINE_B")
    IncidentManager.transition_to(ids[0], __import__("src.models.schemas", fromlist=["x"]).IncidentStatus.ESCALATED)
    assert IncidentRepository.count_unresolved_for_pipeline("PIPELINE_B", exclude_incident_id=ids[3]) == 3
    assert IncidentRepository.count_unresolved_for_pipeline("PIPELINE_A", exclude_incident_id=ids[3]) == 0
