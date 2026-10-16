"""
AI fix with a dry run (CHANGES.md section 34).

When no rule knows the fix for a Pipeline A file, Gemini proposes ONE fix as a list of steps
from a fixed set; the steps run on a COPY; only a copy that passes every check is used for real.
These tests use a fake Gemini, so they prove the checks, not Gemini's skill:
    good fix -> healed      fix that breaks the data -> rejected, nothing changed
    no fix / garbage -> escalated      missing file -> never tried
"""
import json
import os

from src.agents import ai_fix
from src.database import get_db_connection
from src.pipeline.json_pipeline import BASELINE_MAPPING

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROWS = json.load(open(os.path.join(ROOT, "test_data", "pipeline_A", "case_healthy.json")))
AI = "AI_FIX_AGENT"


def steps(*ops, reason="test"):
    return json.dumps({"steps": list(ops), "reason": reason})


# ---------------------------------------------------------------- the steps themselves

def test_each_step_reshapes_without_touching_values():
    assert ai_fix.apply_ops({"customers": ROWS}, [{"op": "unwrap", "key": "customers"}]) == ROWS
    assert ai_fix.apply_ops({f"c{i}": r for i, r in enumerate(ROWS)}, [{"op": "flatten_root"}]) == ROWS
    nested = [{"customer": r} for r in ROWS]
    assert ai_fix.apply_ops(nested, [{"op": "unnest", "key": "customer"}]) == ROWS
    renamed = [{**{k: v for k, v in r.items() if k != "email"}, "mail": r["email"]} for r in ROWS]
    assert ai_fix.apply_ops(renamed, [{"op": "rename_field", "from": "mail", "to": "email"}])[0]["email"] == ROWS[0]["email"]
    upper = [{k.upper(): v for k, v in r.items()} for r in ROWS]
    assert ai_fix.apply_ops(upper, [{"op": "lowercase_keys"}]) == ROWS
    spaced = [{k: f"  {v} " for k, v in r.items()} for r in ROWS]
    assert ai_fix.apply_ops(spaced, [{"op": "trim_values"}]) == ROWS


def test_steps_that_are_not_allowed_or_do_not_fit_are_refused():
    import pytest
    for bad in ([{"op": "run_python", "code": "..."}], [{"op": "unwrap", "key": "nope"}],
                [{"op": "rename_field", "from": "a; DROP", "to": "b"}], [{"op": "trim_values"}] * 6, [], "unwrap"):
        with pytest.raises(ai_fix.OpError):
            ai_fix.apply_ops({"customers": ROWS}, bad)


def test_original_data_is_never_modified():
    original = {"customers": [dict(r) for r in ROWS]}
    before = json.dumps(original)
    ai_fix.apply_ops(original, [{"op": "unwrap", "key": "customers"}, {"op": "lowercase_keys"}])
    assert json.dumps(original) == before


# ---------------------------------------------------------------- the dry-run checks

def test_dry_run_passes_a_good_fix():
    r = ai_fix.dry_run({"customers": ROWS}, [{"op": "unwrap", "key": "customers"}], BASELINE_MAPPING)
    assert r.ok and r.valid == 10 and r.held_back == 0
    assert "same number of records (10)" in r.checks and "no value was added, changed or removed" in r.checks


def test_dry_run_rejects_losing_records():
    data = {"customers": ROWS, "archived": ROWS[:3]}           # unwrapping one list drops 3 records
    r = ai_fix.dry_run(data, [{"op": "unwrap", "key": "customers"}], BASELINE_MAPPING)
    assert not r.ok and "record count changed (13 before, 10 after)" in r.reason


def test_dry_run_rejects_dropping_any_value():
    data = {"source": "crm-export", "customers": ROWS}         # unwrapping drops the "crm-export" value
    r = ai_fix.dry_run(data, [{"op": "unwrap", "key": "customers"}], BASELINE_MAPPING)
    assert not r.ok and "values changed" in r.reason and "crm-export" in r.reason


def test_dry_run_rejects_a_fix_that_leaves_the_schema_broken():
    renamed = [{**{k: v for k, v in r.items() if k != "email"}, "mail": r["email"]} for r in ROWS]
    r = ai_fix.dry_run(renamed, [{"op": "lowercase_keys"}], BASELINE_MAPPING)
    assert not r.ok and "expected column 'email' is still missing" in r.reason


def test_dry_run_rejects_a_fix_that_makes_most_rows_invalid():
    # Renaming the name field to "email" makes every email invalid: the checks catch the wrong mapping.
    data = [{"customer_id": r["customer_id"], "full": r["name"], "country": r["country"]} for r in ROWS]
    r = ai_fix.dry_run(data, [{"op": "rename_field", "from": "full", "to": "email"},
                              {"op": "rename_field", "from": "country", "to": "name"}], BASELINE_MAPPING)
    assert not r.ok


def test_gemini_answers_are_read_strictly():
    assert ai_fix.parse_proposal('```json\n{"steps": [{"op": "flatten_root"}], "reason": "x"}\n```') == ([{"op": "flatten_root"}], "x")
    assert ai_fix.parse_proposal("UNRESOLVED")[0] == []
    assert ai_fix.parse_proposal('{"reason": "cannot"}') == ([], "cannot")
    assert ai_fix.parse_proposal('{"steps": "drop everything"}')[0] == []


def test_what_gemini_sees_is_short():
    big = {"customers": [{"customer_id": str(i), "note": "x" * 500} for i in range(500)]}
    s = ai_fix.structure_summary(big)
    assert len(s) <= 3000 and "498 more" in s and "x" * 41 not in s


# ---------------------------------------------------------------- through the agents and the API

def _incident(client):
    return [i for i in client.get("/api/incidents").json() if i["error_class"] != "DataQualityAnomaly"][0]


def _audit(client, inc):
    return client.get(f"/api/incidents/{inc['incident_id']}/audit").json()


def _count(sql, db="state_db"):
    with get_db_connection(db) as conn:
        return conn.execute(sql).fetchone()[0]


def test_rule_fails_then_the_ai_fix_heals_it(client, fake_llm):
    calls = fake_llm(steps({"op": "unwrap", "key": "customers"}))
    r = client.post("/api/pipelines/a/execute", data={"test_case": "case_wrapped_records.json"}).json()
    inc = _incident(client)
    msgs = [a["message"] for a in _audit(client, inc)]
    assert r.get("healed") is True and inc["status"] == "RESOLVED" and len(calls) == 1
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 10
    assert _count("SELECT COUNT(*) FROM quarantined_records") == 0, "the failed first attempt left no junk rows"
    assert any(m.startswith("Gemini proposed: unwrap 'customers'") for m in msgs)
    assert any(m.startswith("Dry run passed on a copy") for m in msgs)
    assert "Healed by an AI fix that passed a dry run on a copy. Re-run saved 10 records." in msgs
    assert inc["recovery_action"].startswith("Action: AI_FIX")


def test_a_fix_that_fails_the_dry_run_changes_nothing_and_goes_to_a_person(client, fake_llm):
    fake_llm(steps({"op": "unwrap", "key": "customers"}, {"op": "rename_field", "from": "email", "to": "mail"}))
    r = client.post("/api/pipelines/a/execute", data={"test_case": "case_wrapped_records.json"}).json()
    inc = _incident(client)
    msgs = [a["message"] for a in _audit(client, inc)]
    assert r["status"] == "failure" and inc["status"] == "ESCALATED"
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 0
    assert any("Dry run rejected the fix: the expected column 'email' is still missing" in m for m in msgs)
    esc = client.get("/api/escalations").json()[0]
    assert "dry run" in esc["why"].lower() and "email" in esc["why"]


def test_no_usable_answer_from_gemini_goes_to_a_person(client, fake_llm):
    calls = fake_llm("UNRESOLVED")
    client.post("/api/pipelines/a/execute", data={"test_case": "case_wrapped_records.json"})
    inc = _incident(client)
    comps = [a["component"] for a in _audit(client, inc)]
    assert inc["status"] == "ESCALATED" and comps.count(AI) == 2 and comps[-1] == "ESCALATION" and len(calls) == 1


def test_recovery_with_no_fix_gets_one_ai_fix_before_a_person(client, fake_llm):
    # Every record is wrapped in "customer". A rule maps customer_id to "customer" (a near name),
    # the run then misses "name", Recovery asks Gemini for a column (none fits) and gives up,
    # and the AI fix lifts the fields up instead.
    nested = [{"customer": r} for r in ROWS]
    calls = fake_llm(steps({"op": "unnest", "key": "customer"}))
    r = client.post("/api/pipelines/a/execute", files={"file": ("n.json", json.dumps(nested), "application/json")}).json()
    inc = _incident(client)
    comps = [a["component"] for a in _audit(client, inc)]
    assert r.get("healed") is True and inc["status"] == "RESOLVED"
    assert comps.index("RECOVERY_AGENT") < comps.index(AI) and comps.count(AI) == 2
    assert len(calls) == 2   # Recovery's column guess, the AI fix
    assert _count("SELECT COUNT(*) FROM customers", "operational_db") == 10


def test_the_ai_fix_runs_only_once(client, fake_llm):
    # The fix passes the dry run, but the real run still fails (a drift the fix didn't solve):
    # no second AI attempt, it goes to a person.
    data = {"customers": [{**{k: v for k, v in r.items() if k != "email"}, "zz_contact": r["email"]} for r in ROWS]}
    fake_llm(steps({"op": "unwrap", "key": "customers"}))
    from src.agents import ai_fix as mod
    real = mod.dry_run
    mod.dry_run = lambda o, s, m: mod.DryRun(True, "passed", ["forced"], mod.apply_ops(o, s), 10, 0)
    try:
        client.post("/api/pipelines/a/execute", files={"file": ("w.json", json.dumps(data), "application/json")})
    finally:
        mod.dry_run = real
    inc = _incident(client)
    comps = [a["component"] for a in _audit(client, inc)]
    assert inc["status"] == "ESCALATED" and comps.count(AI) == 2


def test_routes_new_errors_to_the_ai_fix_but_never_a_missing_input():
    from src.agents.recovery import plan_route, ROUTE_TRY, ROUTE_NONE
    base = {"category": "UNKNOWN", "pipeline_id": "PIPELINE_A"}
    assert plan_route({**base, "error_message": "TypeError: unhashable type"})[0] == ROUTE_TRY
    assert plan_route({**base, "error_message": "Source JSON file missing on disk: x.json"})[0] == ROUTE_NONE
    assert plan_route({**base, "pipeline_id": "PIPELINE_B", "error_message": "odd"})[0] == ROUTE_NONE


def test_a_guard_block_is_never_followed_by_an_ai_fix(client, fake_llm):
    # Gemini invents a missing country in the broken-JSON repair: the guard blocks it, and the
    # incident goes straight to a person (no second chance for the AI).
    broken = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
    fake_llm(broken.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com", "country": "Japan"},\n'))
    client.post("/api/pipelines/a/execute", data={"test_case": "case_malformed_syntax.json"})
    comps = [a["component"] for a in _audit(client, _incident(client))]
    assert AI not in comps and comps[-1] == "ESCALATION"
