# src/pipeline/etl_engine.py
import uuid
import requests
import json
from datetime import datetime, timezone
from prefect import flow, task
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("enterprise_etl")
CONTROL_PLANE_URL = "http://127.0.0.1:8000"

@task(retries=0)
def extract_upstream_data():
    """Simulates extracting data from an external vendor.
    Notice 'postal_code' has drifted to 'zip_code' in the raw stream.
    """
    logger.info("Extracting batch payload from upstream REST endpoint...")
    return {
        "transaction_id": "tx_9921a",
        "customer_name": "Alice Smith",
        "amount": 250.75,
        "zip_code": "90210" 
    }

@task
def transform_payload(raw_data: dict):
    """Maps raw source data fields to structured enterprise schemas."""
    logger.info("Executing transformation layer schemas...")
    
    # CRASH POINT: Hardcoded constraint looking for legacy 'postal_code'
    return {
        "tx_id": raw_data["transaction_id"],
        "name": raw_data["customer_name"].upper(),
        "revenue": float(raw_data["amount"]),
        "postcode": raw_data["postal_code"].strip() 
    }

@flow(name="Enterprise Sales Ingestion Pipeline")
def run_sales_pipeline(pipeline_id: str = "sales_fallback_ingestion"):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    
    # 1. Register execution with the Control Plane API
    try:
        requests.post(
            f"{CONTROL_PLANE_URL}/runs/register", 
            json={"pipeline_id": pipeline_id, "run_id": run_id},
            timeout=5
        )
    except requests.exceptions.ConnectionError:
        logger.error("Control plane API unreachable. Halting pipeline initialization.")
        return

    # 2. Execute Data Plane operations inside an explicit safe-fail block
    try:
        raw_data = extract_upstream_data()
        clean_data = transform_payload(raw_data)
        logger.info(f"Pipeline run {run_id} completed successfully.")
        
    except Exception as e:
        import traceback
        logger.error(f"Pipeline execution derailed on run {run_id}. Packaging telemetry...")
        
        # 3. Ship structural error data directly to the Incident Pool
        incident_payload = {
            "incident_id": "TEMP_VAL",  # Overwritten deterministically inside manager layer
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "error_class": e.__class__.__name__,
            "error_message": str(e),
            "stack_trace": "".join(traceback.format_exception(type(e), e, e.__traceback__)),
            "status": "OPEN",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            response = requests.post(
                f"{CONTROL_PLANE_URL}/telemetry/incident", 
                json=incident_payload,
                timeout=5
            )
            logger.info("Incident successfully filed", incident_id=response.json().get('incident_id'))
        except Exception as api_err:
            logger.error("Failed to transmit telemetry payload to control plane", error=str(api_err))

if __name__ == "__main__":
    run_sales_pipeline()