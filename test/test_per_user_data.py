"""
Per-person data (CHANGES.md section 31): a viewer sees only their own runs, through the page
AND through the API itself; the admin sees everyone's. Each viewer can clear only their own
history.
"""
from conftest import ADMIN_EMAIL


def _run(c, case="case_drift_country.json", pipe="a"):
    r = c.post(f"/api/pipelines/{pipe}/execute", data={"test_case": case})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(c, url="/api/incidents"):
    r = c.get(url)
    assert r.status_code == 200
    return {i["incident_id"] for i in r.json()}, int(r.headers.get("X-Total-Count", -1))


def test_viewers_only_see_their_own_incidents_and_the_admin_sees_all(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann, bob, boss = sign_in("ann@example.com"), sign_in("bob@example.com"), sign_in(ADMIN_EMAIL)
    _run(ann)                                   # a drift: heals, 1 incident
    _run(bob, "sql_error.db", "b")              # escalates, 1 incident
    ann_ids, ann_total = _ids(ann)
    bob_ids, bob_total = _ids(bob)
    all_ids, all_total = _ids(boss)
    assert ann_total == 1 and bob_total == 1 and all_total == 2
    assert not ann_ids & bob_ids and ann_ids | bob_ids == all_ids


def test_someone_elses_incident_is_not_found_through_the_api(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    _run(ann)
    (ann_incident,), _ = _ids(ann)
    for url in (f"/api/incidents/{ann_incident}/audit", f"/api/incidents/{ann_incident}/report?format=json",
                f"/api/incidents/{ann_incident}/report?format=pdf"):
        assert bob.get(url).status_code == 404, url   # same answer as an id that doesn't exist
        assert ann.get(url).status_code == 200, url
    assert bob.get("/api/incidents", params={"q": ann_incident}).json() == []


def test_quarantine_escalations_and_counts_are_per_person(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann, bob, boss = sign_in("ann@example.com"), sign_in("bob@example.com"), sign_in(ADMIN_EMAIL)
    _run(ann, "case_invalid_email.json")        # 1 row held back
    _run(bob, "missing_table.db", "b")          # escalated
    assert ann.get("/api/quarantine").headers["X-Total-Count"] == "1"
    assert bob.get("/api/quarantine").headers["X-Total-Count"] == "0"
    assert ann.get("/api/escalations").headers["X-Total-Count"] == "0"
    assert bob.get("/api/escalations").headers["X-Total-Count"] == "1"
    assert boss.get("/api/quarantine").headers["X-Total-Count"] == "1"
    assert boss.get("/api/escalations").headers["X-Total-Count"] == "1"
    m_ann, m_bob, m_all = (c.get("/api/metrics").json() for c in (ann, bob, boss))
    assert (m_ann["quarantined"], m_ann["escalated"]) == (1, 0)
    assert (m_bob["quarantined"], m_bob["escalated"]) == (0, 1)
    assert (m_all["quarantined"], m_all["escalated"]) == (1, 1)


def test_clean_runs_are_counted_per_person(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann, bob = sign_in("ann@example.com"), sign_in("bob@example.com")
    _run(ann, "case_healthy.json")
    _run(ann, "case_healthy.json")
    _run(bob, "case_healthy.json")
    assert ann.get("/api/metrics").json()["runs_without_incident"] == 2
    assert bob.get("/api/metrics").json()["runs_without_incident"] == 1


def test_a_viewer_clears_only_their_own_history(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    ann, bob, boss = sign_in("ann@example.com"), sign_in("bob@example.com"), sign_in(ADMIN_EMAIL)
    _run(ann, "case_invalid_email.json")
    _run(bob)
    assert ann.post("/api/history/clear").json()["message"] == "Your history was cleared."
    assert _ids(ann)[1] == 0 and ann.get("/api/quarantine").headers["X-Total-Count"] == "0"
    assert _ids(bob)[1] == 1                    # Bob's run is untouched
    assert _ids(boss)[1] == 1
    # and the admin's clear empties everything
    assert boss.post("/api/history/clear").status_code == 200
    assert _ids(boss)[1] == 0 and _ids(bob)[1] == 0


def test_runs_are_tagged_with_the_signed_in_person(sign_in, fake_llm):
    fake_llm("UNRESOLVED")
    from src.database import get_db_connection
    _run(sign_in("ann@example.com"))
    with get_db_connection("state_db") as conn:
        owners = [r[0] for r in conn.execute("SELECT owner FROM run_owners").fetchall()]
        users = conn.execute("SELECT email, name FROM users").fetchall()
    assert owners == ["g:sub-ann@example.com"]
    assert ("ann@example.com", "Ann") in [tuple(u) for u in users]
