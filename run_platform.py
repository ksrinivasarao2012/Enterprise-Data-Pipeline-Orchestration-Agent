import subprocess
import sys
import os
import socket

from dotenv import load_dotenv
load_dotenv()
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("run_platform")

def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def run():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    api_port = int(env.get("CONTROL_PLANE_PORT", "8000"))
    streamlit_port = int(env.get("STREAMLIT_PORT", "8501"))

    google_api_key = env.get("GOOGLE_API_KEY", "").strip()
    if not google_api_key:
        logger.critical("GOOGLE_API_KEY is missing or empty. Gemini-backed remediation cannot start.")
        sys.exit(1)

    # Pre-check: Ensure port 8000 is available
    if is_port_in_use(api_port):
        logger.error("Port already in use; cannot start FastAPI server", port=api_port)
        logger.info("Hint: Run 'taskkill /F /IM python.exe' or close the previous terminal.")
        return

    # Start FastAPI server
    logger.info("Starting FastAPI Telemetry Server", port=api_port)
    log_dir = os.path.join(os.getcwd(), "database")
    os.makedirs(log_dir, exist_ok=True)
    fastapi_log = open(os.path.join(log_dir, "fastapi.log"), "w", encoding="utf-8")
    fastapi_process = subprocess.Popen(
        [sys.executable, "-u", "-m", "uvicorn", "src.api.main:app", "--port", str(api_port)],
        env=env,
        stdout=fastapi_log,
        stderr=fastapi_log
    )

    # Start Streamlit server
    logger.info("Starting Streamlit Dashboard", port=streamlit_port)
    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "src/ui/app.py", "--server.port", str(streamlit_port)],
            env=env
        )
    except KeyboardInterrupt:
        logger.info("Stopping platform services")
    finally:
        fastapi_process.terminate()
        fastapi_process.wait()
        logger.info("Platform stopped cleanly")

if __name__ == "__main__":
    run()

