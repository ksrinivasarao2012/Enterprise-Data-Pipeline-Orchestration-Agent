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


def test_privacy_page_exists_and_is_linked():
    # Google needs a public privacy policy link before the sign-in app can be published (CHANGES.md section 33).
    page = open(os.path.join(ROOT, "frontend", "public", "privacy.html"), encoding="utf-8").read()
    assert "email address and name" in page and "Clear my history" in page and "Google Gemini" in page
    for f in ("App.tsx", os.path.join("components", "LoginCard.tsx")):
        assert 'href="/privacy.html"' in open(os.path.join(ROOT, "frontend", "src", f), encoding="utf-8").read(), f


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "frontend", "dist", "privacy.html")),
                    reason="React app not built (frontend/dist missing)")
def test_privacy_page_is_public_even_with_sign_in_on(client, google_on):
    r = client.get("/privacy.html")
    assert r.status_code == 200 and "Privacy" in r.text
