# Telemetry and Great Expectations validation assertions and logging router

def validate_telemetry_data(data):
    required_fields = {"run_id", "pipeline_id", "error_class", "error_message", "stack_trace"}

    if not isinstance(data, dict):
        return False, ["Telemetry payload must be a dictionary."]

    missing_fields = [field for field in required_fields if field not in data or data[field] in (None, "")]
    if missing_fields:
        return False, [f"Missing required field: {field}" for field in missing_fields]

    return True, []
