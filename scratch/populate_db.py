# scratch/populate_db.py
import sys
import os
from fastapi.testclient import TestClient

# Add project folder to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.main import app

client = TestClient(app)

def clear_db():
    print("Clearing database history for a clean slate...")
    client.post("/api/history/clear")

def run_case_a(preset_name):
    print(f"Running Pipeline A preset: {preset_name}")
    response = client.post(
        "/api/pipelines/a/execute",
        data={"test_case": preset_name}
    )
    return response.status_code, response.json()

def run_case_b(preset_name):
    print(f"Running Pipeline B preset: {preset_name}")
    response = client.post(
        "/api/pipelines/b/execute",
        data={"test_case": preset_name}
    )
    return response.status_code, response.json()

if __name__ == "__main__":
    clear_db()
    
    pipeline_a_presets = [
        "case_healthy.json",
        "case_drift_country.json",
        "case_drift_email.json",
        "case_drift_id.json",
        "case_invalid_root.json",
        "case_invalid_email.json",
        "case_duplicate_ids.json"
    ]
    
    pipeline_b_presets = [
        "healthy.db",
        "sql_error.db",
        "referential_integrity.db",
        "missing_table.db"
    ]
    
    print("\n--- POPULATING PIPELINE A DATA ---")
    for preset in pipeline_a_presets:
        try:
            code, res = run_case_a(preset)
            print(f"  Result: {code} | {res}")
        except Exception as e:
            print(f"  Error running {preset}: {e}")
            
    print("\n--- POPULATING PIPELINE B DATA ---")
    for preset in pipeline_b_presets:
        try:
            code, res = run_case_b(preset)
            print(f"  Result: {code} | {res}")
        except Exception as e:
            print(f"  Error running {preset}: {e}")
            
    print("\nDatabase fully populated with demo data and incident runs!")
