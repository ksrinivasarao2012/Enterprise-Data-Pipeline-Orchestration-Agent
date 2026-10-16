#!/usr/bin/env python
"""
Measure all 14 presets against a REAL server with REAL Gemini, and write the results table into
README.md by itself (CHANGES.md section 28). Nothing is typed by hand, so the numbers cannot
quietly go out of date: re-run this script and the table is replaced, with the date and model name.

For every preset, in every run, it records:
    result         what the user sees (success / healed / quarantined / escalated ...)
    path           which agents ran, in order (read from the incident's audit trail)
    Gemini calls   how many times the model was called
    time           wall time of the API request (this includes the healing itself)
and compares each of them with what the design says should happen (EXPECTED below, the same
table as test/test_agent_paths.py). It repeats the whole suite (default 3 times), because Gemini
can answer differently each time, and the README says e.g. "13/14 presets as expected in 3 of
3 runs", or names the preset that changed.

Usage (from the project folder, with GOOGLE_API_KEY in .env or the environment):

    python scripts/measure.py                 # start a local server, run 3 times, update README.md
    python scripts/measure.py --runs 1        # quicker / cheaper
    python scripts/measure.py --no-write      # print the table, leave README.md alone
    python scripts/measure.py --url https://your-app.vercel.app --key YOUR_ADMIN_KEY
                                              # measure a deployed server (Gemini calls show as n/a)

Good to know:
  * The local server uses a throw-away database folder, so your real database/ is never touched.
  * Gemini calls are counted inside that local server (at the LangChain level; a retry inside one
    call is not counted). One run uses about 5 calls, so 3 runs use about 15. Check the free tier
    quota in the README (Flash-Lite allows far more than the older models).
  * The exit code is 0 only if every preset behaved as expected in every run.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
LAST_RUN_JSON = ROOT / "scripts" / "measure_last_run.json"

START_MARK = "<!-- MEASURED-RESULTS:START"
END_MARK = "<!-- MEASURED-RESULTS:END -->"
START_LINE = "<!-- MEASURED-RESULTS:START (written by scripts/measure.py, do not edit by hand) -->"

QUARANTINE_ONLY = {"DataQualityAnomaly", "ReferentialIntegrityAnomaly"}

# Audit-trail components (same letters as test/test_agent_paths.py).
T, M, C, RCA, R, A, E = ("TELEMETRY_RECEIVER", "MONITOR_AGENT", "CLASSIFIER_AGENT", "RCA_AGENT",
                         "RECOVERY_AGENT", "ACTUATOR", "ESCALATION")
AI = "AI_FIX_AGENT"
START = [T, M, C, C]  # the Classifier writes two lines: severity, then the route it chose

PATH_NAMES = {M: "Monitor", C: "Classifier", RCA: "RCA", R: "Recovery", A: "Apply → Verify", AI: "AI fix (dry run)", E: "Escalate"}
NO_AGENTS = "no agents (validation only)"


class Preset:
    def __init__(self, pipe, case, result, path, gemini, held):
        self.pipe, self.case, self.result = pipe, case, result
        self.path, self.gemini, self.held = path, gemini, held   # path None = no agent runs

    @property
    def expected_text(self) -> str:
        return result_text(self.result, self.held)


def result_text(base: str, held: Optional[int]) -> str:
    return f"{base} + {held} quarantined" if base == "healed" and held else base


# pipeline, file, result, exact audit path, Gemini calls, rows held back in quarantine (None = not checked)
# Keep in step with CASES in test/test_agent_paths.py (that test proves these with a fake Gemini).
EXPECTED = [
    Preset("a", "case_healthy.json", "success", None, 0, 0),
    Preset("a", "case_drift_country.json", "healed", START + [R, A, A], 0, 0),
    Preset("a", "case_drift_email.json", "healed", START + [R, A, A], 0, 0),
    Preset("a", "case_drift_id.json", "healed", START + [R, A, A], 0, 0),
    Preset("a", "case_drift_multi.json", "healed", START + [R, A, A, R, A, A], 0, 0),   # the retry loop
    Preset("a", "case_invalid_root.json", "healed", START + [R, A, A], 0, 0),
    Preset("a", "case_malformed_syntax.json", "healed", START + [RCA, R, A, A, A], 2, 1),
    Preset("a", "case_wrapped_records.json", "healed", START + [R, A, A, AI, AI, A, A], 1, 0),  # rule fails -> AI fix
    Preset("a", "case_invalid_email.json", "quarantined", None, 0, 1),
    Preset("a", "case_duplicate_ids.json", "quarantined", None, 0, 1),
    Preset("b", "healthy.db", "success", None, 0, 0),
    Preset("b", "sql_error.db", "escalated", START + [RCA, R, E], 2, 0),
    Preset("b", "referential_integrity.db", "quarantined (all rows)", START + [R, A, A], 0, None),
    Preset("b", "missing_table.db", "escalated", START + [RCA, E], 1, 0),
]


# ----------------------------------------------------------------------------- pure helpers

def pretty_path(components: Optional[list]) -> str:
    """['MONITOR_AGENT', 'CLASSIFIER_AGENT', 'CLASSIFIER_AGENT', 'ACTUATOR', 'ACTUATOR'] ->
    'Monitor → Classifier → Apply → Verify'. Two lines from the same agent are one step."""
    if components is None:
        return NO_AGENTS
    names = []
    for comp in components:
        name = PATH_NAMES.get(comp)
        if name and (not names or names[-1] != name):
            names.append(name)
    return " → ".join(names)


def observed_result(response: dict, agent_incident: Optional[dict]) -> str:
    """The same categories as the expectations, read from the API response + the incident."""
    if response.get("status") == "failure":
        return "escalated" if agent_incident and agent_incident.get("status") == "ESCALATED" else "failed (not escalated)"
    if response.get("healed"):
        return "healed"
    if response.get("quarantined"):
        return "quarantined (all rows)" if response.get("quarantined_count") == "all" else "quarantined"
    return "success"


def judge(preset: Preset, obs: dict) -> list:
    """Why this run differs from the design (empty list = as expected)."""
    why = []
    if obs["result"] != preset.result:
        why.append(f"result: expected {preset.result}, got {obs['result']}")
    if preset.held is not None and obs["held"] != preset.held:
        why.append(f"rows quarantined: expected {preset.held}, got {obs['held']}")
    if obs["components"] != preset.path:
        why.append(f"path: expected {pretty_path(preset.path)}, got {pretty_path(obs['components'])}")
    if obs["gemini_calls"] is not None and obs["gemini_calls"] != preset.gemini:
        why.append(f"Gemini calls: expected {preset.gemini}, got {obs['gemini_calls']}")
    return why


def _fmt_seconds(x: float) -> str:
    return f"{x:.2f} s" if x < 1 else f"{x:.1f} s"


def _variants(values: list) -> str:
    """'a' if all equal, else 'a ×2, b ×1' (most common first)."""
    counts = Counter(values)
    if len(counts) == 1:
        return next(iter(counts))
    return ", ".join(f"{v} ×{n}" for v, n in counts.most_common())


def summarize(runs: list) -> list:
    """One row per preset, combining every run."""
    rows = []
    for preset in EXPECTED:
        obs = [next(o for o in run if o["case"] == preset.case) for run in runs]
        calls = [o["gemini_calls"] for o in obs]
        times = [o["seconds"] for o in obs]
        bad = [(i + 1, o["why"]) for i, o in enumerate(obs) if o["why"]]
        rows.append({
            "pipe": preset.pipe, "case": preset.case,
            "expected": preset.expected_text,
            "results": _variants([result_text(o["result"], o["held"]) for o in obs]),
            "paths": _variants([pretty_path(o["components"]) for o in obs]),
            "gemini": "n/a" if calls[0] is None else (str(calls[0]) if len(set(calls)) == 1 else f"{min(calls)}–{max(calls)}"),
            "median_s": statistics.median(times), "min_s": min(times), "max_s": max(times),
            "ok_runs": len(obs) - len(bad), "runs": len(obs), "problems": bad,
        })
    return rows


def render_block(runs: list, meta: dict) -> str:
    """The text that goes between the README markers."""
    rows = summarize(runs)
    n_runs, n = len(runs), len(rows)
    always_ok = [r for r in rows if r["ok_runs"] == r["runs"]]
    changed = [r for r in rows if r["ok_runs"] != r["runs"]]
    clean_runs = sum(1 for run in runs if all(not o["why"] for o in run))

    if not changed:
        headline = f"**{n}/{n} presets behaved as expected in {n_runs} of {n_runs} runs.**"
    else:
        names = "; ".join(f"`{r['case']}` ({r['runs'] - r['ok_runs']} of {r['runs']} runs: {r['problems'][0][1][0]})" for r in changed)
        headline = (f"**{len(always_ok)}/{n} presets behaved as expected in every run. Not as expected: {names}.** "
                    f"Runs with all {n} as expected: {clean_runs} of {n_runs}.")

    lines = [
        START_LINE,
        f"_Measured {meta['date']} (UTC) with `{meta['model']}`, {meta['where']}, {n_runs} run{'s' if n_runs != 1 else ''}. "
        f"Generated by `scripts/measure.py`; raw numbers in `scripts/measure_last_run.json`._",
        "",
        headline,
        "",
        "| Preset | Result | Path through the graph | Gemini calls | Time, median (range) | As expected |",
        "|:---|:---|:---|:---:|:---:|:---:|",
    ]
    for r in rows:
        lines.append(
            f"| `{r['case']}` | {r['results']} | {r['paths']} | {r['gemini']} | "
            f"{_fmt_seconds(r['median_s'])} ({_fmt_seconds(r['min_s'])}–{_fmt_seconds(r['max_s'])}) | "
            f"{r['ok_runs']}/{r['runs']} |")
    lines += [
        "",
        "_Time is the wall time of the API request, including the healing. Gemini calls are counted at the "
        "LangChain level (a retry inside one call is not counted). \"As expected\" means the result, the path and "
        "the number of Gemini calls all match the design in `test/test_agent_paths.py`._",
        END_MARK,
    ]
    return "\n".join(lines)


def replace_block(readme_text: str, block: str) -> str:
    """Puts `block` between the two markers. Refuses to guess if the markers are missing."""
    if readme_text.count(START_MARK) != 1 or readme_text.count(END_MARK) != 1:
        raise SystemExit(f"README.md needs exactly one '{START_MARK} ...-->' line and one '{END_MARK}' line.")
    start = readme_text.index(START_MARK)
    end = readme_text.index(END_MARK) + len(END_MARK)
    if end < start:
        raise SystemExit("README.md markers are in the wrong order.")
    return readme_text[:start] + block + readme_text[end:]


# ----------------------------------------------------------------------------- talking to a server

def run_suite(base_url: str, key: Optional[str], gemini_count: Optional[Callable[[], int]],
              delay: float = 0.0, log: Callable[[str], None] = print) -> list:
    """Runs the 14 presets once, in order. Returns one observation per preset."""
    import requests

    s = requests.Session()
    if key:
        s.headers["X-Access-Key"] = key

    def incidents():
        r = s.get(f"{base_url}/api/incidents", params={"limit": 200}, timeout=30)
        r.raise_for_status()
        return r.json()

    def held_total():
        r = s.get(f"{base_url}/api/quarantine", params={"limit": 1}, timeout=30)
        r.raise_for_status()
        return int(r.headers.get("X-Total-Count", 0))

    observations = []
    for preset in EXPECTED:
        before_ids = {i["incident_id"] for i in incidents()}
        held_before = held_total()
        calls_before = gemini_count() if gemini_count else None

        t0 = time.perf_counter()
        resp = s.post(f"{base_url}/api/pipelines/{preset.pipe}/execute", data={"test_case": preset.case}, timeout=600)
        seconds = time.perf_counter() - t0
        try:
            body = resp.json()
        except ValueError:
            body = {"status": "failure", "detail": f"HTTP {resp.status_code}"}
        if resp.status_code >= 400:
            body = {"status": "failure", "detail": f"HTTP {resp.status_code}: {body.get('detail', '')}"}

        calls = (gemini_count() - calls_before) if gemini_count else None
        new = [i for i in incidents() if i["incident_id"] not in before_ids and i["error_class"] not in QUARANTINE_ONLY]
        agent_inc = new[0] if new else None       # newest first
        components = None
        if agent_inc:
            audit = s.get(f"{base_url}/api/incidents/{agent_inc['incident_id']}/audit", timeout=30).json()
            components = [a["component"] for a in audit]

        obs = {
            "case": preset.case, "pipe": preset.pipe, "seconds": round(seconds, 3),
            "result": observed_result(body, agent_inc), "held": held_total() - held_before,
            "components": components, "gemini_calls": calls,
            "incident_id": agent_inc["incident_id"] if agent_inc else None,
            "blocked_by_guard": bool(agent_inc and agent_inc.get("blocked_by_guard")),
            "response": body,
        }
        obs["why"] = judge(preset, obs)
        # If Gemini was down, out of quota or the key was wrong, the agents quietly fall back to a basic
        # answer, and some presets even look "as expected" (they escalate either way). Say so loudly.
        if agent_inc and (str(agent_inc.get("root_cause") or "").startswith("Fallback Analysis")
                          or "LLM failed to rewrite SQL" in str(agent_inc.get("recovery_action") or "")):
            obs["why"].append("Gemini call failed (check GOOGLE_API_KEY, network and the daily quota); this run is not a real measurement")
        if len(new) > 1:
            obs["why"].append(f"{len(new)} agent incidents were raised, expected 1")
        observations.append(obs)
        log(f"  {'ok ' if not obs['why'] else 'DIFF'} {preset.case:<28} {result_text(obs['result'], obs['held']):<26} "
            f"{_fmt_seconds(seconds):>8}  gemini={calls if calls is not None else 'n/a'}"
            + ("" if not obs["why"] else "  <- " + "; ".join(obs["why"])))
        if delay:
            time.sleep(delay)
    return observations


def run_all(base_url: str, key: Optional[str], runs: int, gemini_count: Optional[Callable[[], int]],
            delay: float = 0.0, log: Callable[[str], None] = print) -> list:
    import requests
    all_runs = []
    for n in range(1, runs + 1):
        log(f"Run {n} of {runs}")
        # every run starts from an empty history, so runs cannot influence each other
        h = {"X-Access-Key": key} if key else {}
        r = requests.post(f"{base_url}/api/history/clear", headers=h, timeout=60)
        if r.status_code >= 400:
            raise SystemExit(f"Could not clear history before run {n} (HTTP {r.status_code}). Is the key an admin key?")
        all_runs.append(run_suite(base_url, key, gemini_count, delay, log))
    return all_runs


# ----------------------------------------------------------------------------- local server

def install_call_counter(model_class, call_file: str) -> None:
    """Makes every call to the model append one line to `call_file` (the server is a separate
    process, so a file is the simplest way to report the count back)."""
    original = model_class._generate

    def counted(self, *args, **kwargs):
        with open(call_file, "a", encoding="utf-8") as f:
            f.write("call\n")
        return original(self, *args, **kwargs)

    model_class._generate = counted


def _serve(port: int, call_file: str) -> None:
    """Internal: the local server process (real app, real Gemini, plus the call counter)."""
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import langchain_google_genai
    install_call_counter(langchain_google_genai.ChatGoogleGenerativeAI, call_file)
    import uvicorn
    from src.api.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _cleanup_temp(tmp_ctx) -> None:
    """Delete the throw-away database folder. On Windows the stopped server can keep the .db
    files locked for a moment, so retry briefly; if it is still locked, leave the folder in
    the system temp directory instead of crashing before the README is written."""
    for _ in range(10):
        try:
            tmp_ctx.cleanup()
            return
        except PermissionError:
            time.sleep(0.5)
    print(f"Note: could not delete the temp folder {tmp_ctx.name} (a file is still in use); it is safe to delete later.")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_local_server(tmp: str):
    port = _free_port()
    key = secrets.token_urlsafe(24)
    call_file = os.path.join(tmp, "gemini_calls.txt")
    open(call_file, "w").close()
    env = dict(os.environ, EHA_DB_DIR=os.path.join(tmp, "db"), EHA_ADMIN_KEY=key, EHA_VIEWER_KEY="",
               PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    env.pop("VERCEL", None)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--_serve", str(port), call_file],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    import requests
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit("The local server stopped while starting:\n" + (proc.stderr.read() or "")[-2000:])
        try:
            if requests.get(base + "/api/whoami", headers={"X-Access-Key": key}, timeout=2).status_code == 200:
                return proc, base, key, call_file
        except requests.RequestException:
            time.sleep(0.5)
    proc.terminate()
    raise SystemExit("The local server did not start within 90 seconds.")


def _load_key() -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return bool(os.environ.get("GOOGLE_API_KEY", "").strip())


def model_name() -> str:
    sys.path.insert(0, str(ROOT))
    from src.config import get_llm_config
    return get_llm_config()["model_name"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure all 14 presets against a real server and update README.md.")
    ap.add_argument("--runs", type=int, default=3, help="how many times to repeat the whole suite (default 3)")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds to wait between presets (default 3, kind to the Gemini rate limit)")
    ap.add_argument("--url", help="measure an already running server instead of starting one (Gemini calls show as n/a)")
    ap.add_argument("--key", default=os.environ.get("EHA_ADMIN_KEY"), help="admin access key of that server")
    ap.add_argument("--no-write", action="store_true", help="print the table, do not touch README.md")
    ap.add_argument("--readme", default=str(README), help=argparse.SUPPRESS)
    ap.add_argument("--json", default=str(LAST_RUN_JSON), help="where to save the raw numbers")
    ap.add_argument("--_serve", nargs=2, metavar=("PORT", "CALL_FILE"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args._serve:
        _serve(int(args._serve[0]), args._serve[1])
        return 0
    if args.runs < 1:
        ap.error("--runs must be at least 1")

    proc = None
    tmp_ctx = tempfile.TemporaryDirectory(prefix="eha_measure_")
    try:
        if args.url:
            base, key, gemini_count, where = args.url.rstrip("/"), args.key, None, f"deployed server {args.url.rstrip('/')}"
        else:
            if not _load_key():
                print("GOOGLE_API_KEY is missing (put it in .env). Real Gemini is needed for this measurement.", file=sys.stderr)
                return 2
            print("Starting a local server (throw-away database) ...")
            proc, base, key, call_file = start_local_server(tmp_ctx.name)
            gemini_count = lambda: sum(1 for _ in open(call_file, encoding="utf-8"))
            where = "real local server"
        runs = run_all(base, key, args.runs, gemini_count, args.delay)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _cleanup_temp(tmp_ctx)

    meta = {"date": datetime.now(timezone.utc).date().isoformat(), "model": model_name(), "where": f"{where}, real Gemini"}
    block = render_block(runs, meta)
    print("\n" + block + "\n")

    Path(args.json).write_text(json.dumps({"meta": meta, "runs": runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.no_write:
        path = Path(args.readme)
        path.write_text(replace_block(path.read_text(encoding="utf-8"), block), encoding="utf-8", newline="\n")
        print(f"Updated {path}")
    return 0 if all(not o["why"] for run in runs for o in run) else 1


if __name__ == "__main__":
    sys.exit(main())
