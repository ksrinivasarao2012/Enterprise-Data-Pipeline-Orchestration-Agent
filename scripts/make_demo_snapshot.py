#!/usr/bin/env python
"""
Record the Demo tab (CHANGES.md section 32).

Runs all 15 presets once on a throw-away local server with REAL Gemini, then saves what the
dashboard would show (incidents, audit trails, quarantined rows, escalations, counters) to
demo/snapshot.json. Signed-in users see it, read-only, in the Demo tab.

    python scripts/make_demo_snapshot.py            # about 9 Gemini calls

The recording is frozen: it only changes when you run this script again. If any preset does
not behave as designed (for example a Gemini call failed because of the quota), nothing is
written, so a broken run can't end up in the demo. --force writes it anyway.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure  # noqa: E402  (same folder: reuses its local server and preset runner)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "snapshot.json"
LIMIT = 200


def collect(base: str, headers: dict) -> dict:
    """Reads everything the dashboard shows, through the same API the dashboard uses."""
    import requests

    s = requests.Session()
    s.headers.update(headers)
    get = lambda path, **params: s.get(base + path, params=params, timeout=60)
    incidents = get("/api/incidents", limit=LIMIT).json()
    return {
        "metrics": get("/api/metrics").json(),
        "incidents": incidents,
        "audits": {i["incident_id"]: get(f"/api/incidents/{i['incident_id']}/audit").json() for i in incidents},
        "quarantine": get("/api/quarantine", limit=LIMIT).json(),
        "escalations": get("/api/escalations", limit=LIMIT).json(),
    }


def build_snapshot(observations: list, data: dict, model: str) -> dict:
    as_expected = sum(1 for o in observations if not o["why"])
    return {
        "meta": {
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "presets": len(observations),
            "as_expected": as_expected,
            "gemini_calls": sum(o["gemini_calls"] or 0 for o in observations),
        },
        **data,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record the 15 presets for the Demo tab.")
    ap.add_argument("--out", default=str(OUT), help="where to write the snapshot")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between presets (kind to the Gemini rate limit)")
    ap.add_argument("--force", action="store_true", help="write the snapshot even if a preset did not behave as designed")
    args = ap.parse_args(argv)

    if not measure._load_key():
        print("GOOGLE_API_KEY is missing (put it in .env). Real Gemini is needed for the recording.", file=sys.stderr)
        return 2
    tmp = tempfile.TemporaryDirectory(prefix="eha_demo_")
    proc = None
    try:
        print("Starting a local server (throw-away database) ...")
        proc, base, key, call_file = measure.start_local_server(tmp.name)
        count = lambda: sum(1 for _ in open(call_file, encoding="utf-8"))
        observations = measure.run_all(base, key, 1, count, args.delay)[0]
        data = collect(base, {"X-Access-Key": key})
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        measure._cleanup_temp(tmp)

    snap = build_snapshot(observations, data, measure.model_name())
    bad = [o["case"] for o in observations if o["why"]]
    if bad and not args.force:
        print(f"Not written: {', '.join(bad)} did not behave as designed (see above). Fix that, or use --force.",
              file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    m = snap["meta"]
    print(f"Wrote {out} ({len(snap['incidents'])} incidents, {m['as_expected']}/{m['presets']} as expected, "
          f"{m['gemini_calls']} Gemini calls, model {m['model']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
