"""
Incident search and filters (CHANGES.md section 18). Filters run on the server, so paging and
the "X of Y" total stay correct.
"""
import pytest


@pytest.fixture
def mixed(client, fake_llm):
    """Real runs: 2 healed (A), 1 quarantined (A), 2 escalated (B)."""
    fake_llm("UNRESOLVABLE")
    for case in ["case_drift_country.json", "case_drift_email.json", "case_invalid_email.json"]:
        client.post("/api/pipelines/a/execute", data={"test_case": case})
    for case in ["sql_error.db", "missing_table.db"]:
        client.post("/api/pipelines/b/execute", data={"test_case": case})
    return client


def _get(client, query):
    r = client.get(f"/api/incidents?{query}")
    assert r.status_code == 200, r.text
    return r.json(), int(r.headers["X-Total-Count"])


def test_outcome_filters(mixed):
    assert _get(mixed, "outcome=healed")[1] == 2
    assert _get(mixed, "outcome=quarantined")[1] == 1
    assert _get(mixed, "outcome=escalated")[1] == 2
    assert _get(mixed, "outcome=in_progress")[1] == 0
    items, _ = _get(mixed, "outcome=healed")
    assert all(i["status"] == "RESOLVED" and i["error_class"] != "DataQualityAnomaly" for i in items)


def test_pipeline_filter_and_combined(mixed):
    assert _get(mixed, "pipeline=PIPELINE_B")[1] == 2
    assert _get(mixed, "pipeline=PIPELINE_A&outcome=escalated")[1] == 0
    assert _get(mixed, "pipeline=PIPELINE_A&outcome=healed")[1] == 2


def test_search(mixed):
    items, total = _get(mixed, "q=STATUS_CODE")  # case does not matter
    assert total == 1 and "status_code" in items[0]["error_message"]
    one = items[0]["incident_id"]
    assert _get(mixed, f"q={one}")[0][0]["incident_id"] == one
    assert _get(mixed, "q=nothing-like-this")[1] == 0


def test_like_wildcards_are_searched_as_letters(mixed):
    # "%" and "_" would match everything in SQL LIKE if not escaped
    assert _get(mixed, "q=%25")[1] == 0      # %
    assert _get(mixed, "q=___")[1] == 0


def test_filters_and_paging_work_together(mixed):
    first, total = _get(mixed, "outcome=escalated&limit=1")
    second, _ = _get(mixed, "outcome=escalated&limit=1&offset=1")
    assert total == 2 and first[0]["incident_id"] != second[0]["incident_id"]


@pytest.mark.parametrize("query", ["outcome=fixed", "pipeline=PIPELINE_C", "pipeline=x'--", "q=" + "x" * 101])
def test_bad_filter_values_are_refused(client, query):
    assert client.get(f"/api/incidents?{query}").status_code == 422
