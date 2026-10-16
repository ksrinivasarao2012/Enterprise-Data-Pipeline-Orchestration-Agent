"""
scripts/measure.py (CHANGES.md section 28): the script that measures the 15 presets and writes the
README table. Real Gemini cannot be used in tests, so the whole machinery is run against a real
HTTP server with a fake Gemini, which proves the expectations, the counting, the table and the
README update work. The real numbers come from running the script itself.
"""
import importlib.util
import json
import os
import socket
import threading
import time

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("eha_measure", os.path.join(ROOT, "scripts", "measure.py"))
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)

BROKEN = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_malformed_syntax.json")).read()
BRACKET_ONLY = BROKEN.replace('"yuki.tanaka@example.com"\n', '"yuki.tanaka@example.com"},\n')
BROKEN_WRAPPED = open(os.path.join(ROOT, "test_data", "pipeline_A", "case_broken_and_wrapped.json")).read()


# ---------------------------------------------------------------- pure helpers

def test_expected_table_has_the_14_presets_with_real_files():
    assert len(measure.EXPECTED) == 15
    for p in measure.EXPECTED:
        folder = "pipeline_A" if p.pipe == "a" else "pipeline_B"
        assert os.path.exists(os.path.join(ROOT, "test_data", folder, p.case)), p.case


def test_expected_table_matches_test_agent_paths():
    spec = importlib.util.spec_from_file_location("eha_agent_paths", os.path.join(ROOT, "test", "test_agent_paths.py"))
    paths = importlib.util.module_from_spec(spec)   # the test that proves the design with a fake Gemini
    spec.loader.exec_module(paths)
    ours = {(p.pipe, p.case): (p.path, p.gemini) for p in measure.EXPECTED}
    theirs = {(c[0], c[1]): (c[3], c[4]) for c in paths.CASES}
    assert ours == theirs


def test_pretty_path_merges_repeats_and_drops_plumbing():
    T, M, C, R, A = measure.T, measure.M, measure.C, measure.R, measure.A
    assert measure.pretty_path([T, M, C, C, R, A, A]) == "Monitor → Classifier → Recovery → Apply → Verify"
    assert measure.pretty_path([T, M, C, C, R, A, A, R, A, A]).count("Recovery") == 2   # the retry loop stays visible
    assert measure.pretty_path(None) == measure.NO_AGENTS


def _obs(case, result="healed", held=0, components=None, calls=0, seconds=1.0):
    preset = next(p for p in measure.EXPECTED if p.case == case)
    o = {"case": case, "result": result, "held": held, "components": preset.path if components is None else components,
         "gemini_calls": calls, "seconds": seconds}
    o["why"] = measure.judge(preset, o)
    return o


def _perfect_run():
    return [_obs(p.case, result=p.result, held=p.held or 0, components=p.path, calls=p.gemini) for p in measure.EXPECTED]


def test_judge_names_each_kind_of_difference():
    p = next(p for p in measure.EXPECTED if p.case == "sql_error.db")
    good = dict(case=p.case, result="escalated", held=0, components=p.path, gemini_calls=2)
    assert measure.judge(p, good) == []
    assert "result: expected escalated, got healed" in measure.judge(p, {**good, "result": "healed"})[0]
    assert "Gemini calls: expected 2, got 1" in measure.judge(p, {**good, "gemini_calls": 1})[0]
    assert "path:" in measure.judge(p, {**good, "components": p.path[:-1]})[0]
    assert measure.judge(p, {**good, "gemini_calls": None}) == []        # deployed server: calls unknown, not a failure


def test_table_says_14_of_14_when_every_run_matches():
    text = measure.render_block([_perfect_run() for _ in range(3)],
                                {"date": "2026-10-01", "model": "gemini-2.5-flash", "where": "real local server, real Gemini"})
    assert "**15/15 presets behaved as expected in 3 of 3 runs.**" in text
    assert "2026-10-01" in text and "`gemini-2.5-flash`" in text
    assert text.startswith(measure.START_LINE) and text.endswith(measure.END_MARK)
    assert text.count("\n| `") == 15 and "3/3" in text


def test_table_names_the_case_that_changed():
    runs = [_perfect_run() for _ in range(3)]
    i = [p.case for p in measure.EXPECTED].index("sql_error.db")
    runs[1][i] = _obs("sql_error.db", result="healed", components=measure.EXPECTED[i].path, calls=2)
    text = measure.render_block(runs, {"date": "d", "model": "m", "where": "w"})
    assert "14/15 presets behaved as expected in every run" in text
    assert "`sql_error.db` (1 of 3 runs: result: expected escalated, got healed)" in text
    assert "escalated ×2, healed ×1" in text and "| 2/3 |" in text
    assert "Runs with all 15 as expected: 2 of 3" in text


def test_replace_block_replaces_only_between_the_markers():
    readme = f"before\n{measure.START_LINE}\nold table\n{measure.END_MARK}\nafter\n"
    out = measure.replace_block(readme, f"{measure.START_LINE}\nNEW\n{measure.END_MARK}")
    assert out == f"before\n{measure.START_LINE}\nNEW\n{measure.END_MARK}\nafter\n"
    with pytest.raises(SystemExit):
        measure.replace_block("no markers here", "x")
    with pytest.raises(SystemExit):
        measure.replace_block(readme + readme, "x")      # two sets of markers: refuse to guess


def test_the_real_readme_has_the_markers():
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert text.count(measure.START_MARK) == 1 and text.count(measure.END_MARK) == 1
    assert "2026-09-02" not in text.split(measure.START_MARK)[1].split(measure.END_MARK)[0]


def test_call_counter_counts_each_call_and_the_real_model_class_can_be_patched(tmp_path):
    class Dummy:
        def _generate(self, x):
            return x * 2
    f = tmp_path / "calls.txt"
    measure.install_call_counter(Dummy, str(f))
    assert Dummy()._generate(2) == 4 and Dummy()._generate(3) == 6
    assert len(f.read_text().splitlines()) == 2
    from langchain_google_genai import ChatGoogleGenerativeAI
    assert callable(getattr(ChatGoogleGenerativeAI, "_generate", None)), "the counter patches this method"


# ---------------------------------------------------------------- the whole thing, over real HTTP

class RoutingLLM(RunnableLambda):
    """A fake Gemini that answers like a good one: repairs brackets, says UNRESOLVABLE for a missing column."""
    def __init__(self, calls):
        def _answer(prompt):
            calls.append(1)
            text = str(prompt)
            if "JSON syntax repair agent" in text:
                # the broken-and-wrapped file: add only its missing "}"; the other broken file: add "},"
                if '"customers"' in text:
                    return AIMessage(content=BROKEN_WRAPPED.replace('"country": "Japan",\n', '"country": "Japan"},\n'))
                return AIMessage(content=BRACKET_ONLY)
            if "repair the SHAPE" in text:
                return AIMessage(content='{"steps": [{"op": "unwrap", "key": "customers"}], "reason": "wrapped"}')
            if "SQL repair agent" in text:
                return AIMessage(content="UNRESOLVABLE")
            return AIMessage(content="UNRESOLVED")
        super().__init__(_answer)
        self._calls = calls

    def with_structured_output(self, schema):
        def _rca(_prompt):
            self._calls.append(1)
            return schema(root_cause_analysis="fake rca", confidence_score=0.5)
        return RunnableLambda(_rca)


@pytest.fixture
def live_server(client, monkeypatch):
    """The real FastAPI app on a real port (uvicorn in a thread), with a counting fake Gemini."""
    import uvicorn
    import langchain_google_genai
    from src.agents import rca as rca_mod, recovery as recovery_mod
    from src.api.main import app
    calls = []
    factory = lambda *a, **k: RoutingLLM(calls)
    for target in (rca_mod, recovery_mod, langchain_google_genai):
        monkeypatch.setattr(target, "ChatGoogleGenerativeAI", factory)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    yield f"http://127.0.0.1:{port}", calls
    server.should_exit = True
    thread.join(timeout=10)


def test_full_measurement_over_http_matches_the_design(live_server, tmp_path):
    base, calls = live_server
    logged = []
    runs = measure.run_all(base, None, runs=2, gemini_count=lambda: len(calls), delay=0, log=logged.append)

    assert len(runs) == 2 and all(len(r) == 15 for r in runs)
    problems = [(o["case"], o["why"]) for run in runs for o in run if o["why"]]
    assert problems == [], problems

    # Gemini is counted per preset: 2 (malformed) + 1 (wrapped: AI fix) + 2 (sql_error) + 1 (missing_table) = 6 per run
    assert sum(o["gemini_calls"] for o in runs[0]) == 9 and len(calls) == 18
    by = {o["case"]: o for o in runs[0]}
    assert by["case_drift_multi.json"]["components"].count(measure.R) == 2 and by["sql_error.db"]["blocked_by_guard"]
    assert all(o["seconds"] > 0 for o in runs[0])

    # the table and the README update
    text = measure.render_block(runs, {"date": "2026-10-01", "model": "gemini-2.5-flash", "where": "test server"})
    assert "15/15 presets behaved as expected in 2 of 2 runs" in text
    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{measure.START_LINE}\nnot measured yet\n{measure.END_MARK}\noutro\n", encoding="utf-8")
    readme.write_text(measure.replace_block(readme.read_text(encoding="utf-8"), text), encoding="utf-8")
    final = readme.read_text(encoding="utf-8")
    assert final.startswith("intro\n") and final.endswith("outro\n") and "not measured yet" not in final
    json.dumps(runs)   # the raw numbers can be saved as JSON


def test_a_server_that_misbehaves_shows_up_as_a_difference(live_server):
    base, calls = live_server
    import langchain_google_genai
    from src.agents import rca as rca_mod, recovery as recovery_mod
    # Gemini now refuses to repair the JSON: malformed_syntax must be reported as NOT as expected
    import pytest as _p
    mp = _p.MonkeyPatch()
    try:
        factory = lambda *a, **k: _Refuses(calls)
        for target in (rca_mod, recovery_mod, langchain_google_genai):
            mp.setattr(target, "ChatGoogleGenerativeAI", factory)
        runs = measure.run_all(base, None, runs=1, gemini_count=lambda: len(calls), delay=0, log=lambda _m: None)
    finally:
        mp.undo()
    bad = {o["case"]: o["why"] for o in runs[0] if o["why"]}
    assert "case_malformed_syntax.json" in bad and any("result: expected healed" in w for w in bad["case_malformed_syntax.json"])
    text = measure.render_block(runs, {"date": "d", "model": "m", "where": "w"})
    assert "Not as expected" in text and "case_malformed_syntax.json" in text


class _Refuses(RunnableLambda):
    def __init__(self, calls):
        def _answer(prompt):
            calls.append(1)
            return AIMessage(content="{ still : broken ")
        super().__init__(_answer)
        self._calls = calls

    def with_structured_output(self, schema):
        def _rca(_prompt):
            self._calls.append(1)
            return schema(root_cause_analysis="fake rca", confidence_score=0.5)
        return RunnableLambda(_rca)


class _GeminiDown(RunnableLambda):
    """Every call fails, like a wrong key, no network or an exhausted quota."""
    def __init__(self, calls):
        def _fail(_prompt):
            calls.append(1)
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        super().__init__(_fail)
        self._calls = calls

    def with_structured_output(self, schema):
        return self


def test_a_gemini_outage_is_called_out_even_for_presets_that_escalate_either_way(live_server):
    base, calls = live_server
    import langchain_google_genai
    from src.agents import rca as rca_mod, recovery as recovery_mod
    import pytest as _p
    mp = _p.MonkeyPatch()
    try:
        for target in (rca_mod, recovery_mod, langchain_google_genai):
            mp.setattr(target, "ChatGoogleGenerativeAI", lambda *a, **k: _GeminiDown(calls))
        runs = measure.run_all(base, None, runs=1, gemini_count=lambda: len(calls), delay=0, log=lambda _m: None)
    finally:
        mp.undo()
    by = {o["case"]: o["why"] for o in runs[0]}
    # missing_table.db escalates with or without Gemini, so only this check can notice the outage
    assert any("Gemini call failed" in w for w in by["missing_table.db"]), by["missing_table.db"]
    assert any("Gemini call failed" in w for w in by["sql_error.db"])
    assert by["case_drift_country.json"] == []         # presets that never need Gemini are unaffected


# ---------------------------------------------------------------- Windows: locked temp database

class _LockedTemp:
    """Stands in for TemporaryDirectory whose cleanup fails while a file is still locked."""
    def __init__(self, fail_times):
        self.name, self.fail_times, self.calls = "C:/tmp/eha_measure_x", fail_times, 0

    def cleanup(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise PermissionError("[WinError 32] The process cannot access the file")


def test_cleanup_retries_while_the_db_file_is_locked(monkeypatch):
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    tmp = _LockedTemp(fail_times=2)
    measure._cleanup_temp(tmp)
    assert tmp.calls == 3


def test_cleanup_never_crashes_the_run_before_the_readme_is_written(monkeypatch, capsys):
    monkeypatch.setattr(measure.time, "sleep", lambda s: None)
    measure._cleanup_temp(_LockedTemp(fail_times=99))  # must not raise
    assert "safe to delete later" in capsys.readouterr().out
