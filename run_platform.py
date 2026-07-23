"""
Starts the platform locally: the FastAPI server, which also serves the dashboard.

    python run_platform.py               # starts the server and opens the dashboard in your browser
    python run_platform.py --no-browser  # starts the server only

(The old Streamlit console was removed; the React dashboard at http://localhost:8000 replaces it.)
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

from dotenv import load_dotenv
load_dotenv()
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("run_platform")


def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(60):  # wait up to ~30 s for the server to answer
        if is_port_in_use(port):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def run():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    api_port = int(env.get("CONTROL_PLANE_PORT", "8000"))
    url = f"http://localhost:{api_port}"

    if not env.get("GOOGLE_API_KEY", "").strip():
        logger.critical("GOOGLE_API_KEY is missing or empty. Gemini-backed remediation cannot start.")
        sys.exit(1)

    if is_port_in_use(api_port):
        logger.error("Port already in use; cannot start FastAPI server", port=api_port)
        logger.info("Hint: close the previous terminal, or run 'taskkill /F /IM python.exe'.")
        return

    if not os.path.exists(os.path.join("frontend", "dist", "index.html")):
        logger.warning("React app not built, so the old HTML dashboard is served. Build it: cd frontend && npm install && npm run build")

    print(f"\n  Dashboard:  {url}\n  API docs:   {url}/docs\n  Stop:       Ctrl+C\n", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Thread(target=_open_when_ready, args=(url, api_port), daemon=True).start()

    try:
        subprocess.run([sys.executable, "-u", "-m", "uvicorn", "src.api.main:app", "--port", str(api_port)], env=env)
    except KeyboardInterrupt:
        pass
    logger.info("Platform stopped")


if __name__ == "__main__":
    run()
