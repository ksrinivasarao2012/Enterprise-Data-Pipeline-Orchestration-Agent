"""
Vercel serves the React app (CHANGES.md section 30). These checks catch the mistakes that
would quietly bring back the old page or break the deploy. (The full build was also checked
once with `vercel build`; see CHANGES.md.)
"""
import json
import os
import tomllib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _vercel():
    return json.load(open(os.path.join(ROOT, "vercel.json"), encoding="utf-8"))


def test_vercel_uses_the_fastapi_preset_not_the_old_builds_list():
    cfg = _vercel()
    assert cfg["framework"] == "fastapi"
    assert "builds" not in cfg and "routes" not in cfg, "the legacy 'builds' list turns off the FastAPI preset"


def test_the_react_app_is_built_during_deploy():
    cfg = _vercel()
    assert "cd frontend" in cfg["buildCommand"] and "npm run build" in cfg["buildCommand"]


def test_function_settings_point_at_the_real_entrypoint():
    py = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))
    module = py["tool"]["vercel"]["entrypoint"].split(":")[0]           # src.api.main
    assert list(_vercel()["functions"]) == [module.replace(".", "/") + ".py"]
    excluded = _vercel()["functions"]["src/api/main.py"]["excludeFiles"]
    assert "frontend/node_modules/**" in excluded and "test_data" not in excluded  # presets are needed at runtime


def test_no_public_folder_that_vercel_would_serve_instead_of_the_app():
    # Vercel serves a root public/ folder before the app, so the old dashboard would win at "/".
    assert not os.path.exists(os.path.join(ROOT, "public"))


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "frontend", "dist", "index.html")),
                    reason="React app not built (frontend/dist missing)")
def test_the_api_mounts_the_react_build_at_the_root():
    from src.api.main import app
    mounts = [r for r in app.routes if getattr(r, "name", "") == "dashboard"]
    assert mounts and mounts[0].path == "" and mounts[0].app.directory.endswith(os.path.join("frontend", "dist"))
