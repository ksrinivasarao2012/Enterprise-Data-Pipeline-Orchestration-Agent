# src/incidents/severity_rules.py
from src.models.schemas import SeverityLevel, IncidentCategory

class SeverityRulesEngine:
    # Match key structural exception signatures (handles base and partial namespaces)
    _CATEGORY_MAPPING = {
        "operationalerror": IncidentCategory.INFRASTRUCTURE_FAILURE,
        "interfaceerror": IncidentCategory.INFRASTRUCTURE_FAILURE,
        "timeouterror": IncidentCategory.NETWORK_TIMEOUT,
        "connectionerror": IncidentCategory.NETWORK_TIMEOUT,
        "httperror": IncidentCategory.API_FAILURE,             # Refined to map to specialized API sync categories
        "keyerror": IncidentCategory.SCHEMA_DRIFT,
        "valueerror": IncidentCategory.DATA_QUALITY,
        "validationerror": IncidentCategory.DATA_QUALITY
    }

    @classmethod
    def evaluate(cls, error_class: str, error_message: str, pipeline_id: str) -> tuple[SeverityLevel, IncidentCategory]:
        """
        Determines the priority tier and category of an incident based on 
        the signature of the error and the critical status of the workload.
        """
        # 1. Determine Category with namespace normalization
        clean_error_class = error_class.split(".")[-1].lower()
        category = cls._CATEGORY_MAPPING.get(clean_error_class, IncidentCategory.UNKNOWN)
        
        # Refine fallback category if keywords exist inside the error message string
        msg_lower = error_message.lower()
        if category in [IncidentCategory.UNKNOWN, IncidentCategory.INFRASTRUCTURE_FAILURE]:
            if "timeout" in msg_lower or "connection refused" in msg_lower or "connect" in msg_lower or "locked" in msg_lower:
                category = IncidentCategory.NETWORK_TIMEOUT
            elif "missing 1 required positional" in msg_lower:
                category = IncidentCategory.UNKNOWN # Python code bug, not data schema drift
            elif "no such table" in msg_lower:
                category = IncidentCategory.DATABASE_FAILURE
            elif "column" in msg_lower or "target schema" in msg_lower or "field missing" in msg_lower or "parse error" in msg_lower or "syntax error" in msg_lower:
                category = IncidentCategory.SCHEMA_DRIFT
            elif "invalid value" in msg_lower or "null value" in msg_lower or "negative" in msg_lower or "referential integrity" in msg_lower:
                category = IncidentCategory.DATA_QUALITY
            elif "sql" in msg_lower or "foreign key" in msg_lower:
                category = IncidentCategory.DATABASE_FAILURE

        # Intercept and refine base classifications if specialized pipeline error text shows up
        if category == IncidentCategory.NETWORK_TIMEOUT and "dummyjson" in msg_lower:
            category = IncidentCategory.API_FAILURE

        # 2. Determine Severity Level (P0 -> P3)
        # Platform rule: Any infrastructure or core database crash maps to a P0 incident
        if category == IncidentCategory.INFRASTRUCTURE_FAILURE:
            return SeverityLevel.P0, category

        # Workload-based priority routing matrix
        normalized_pipeline = str(pipeline_id).upper().strip()

        if normalized_pipeline == "PIPELINE_B":  # Warehouse Refresh & External Service Sync
            # Security outage requires instant manual triage
            if "401" in msg_lower or "403" in msg_lower:
                return SeverityLevel.P0, category
            # Database syntax structural failures or schema breaks completely block data products
            if category in [IncidentCategory.DATABASE_FAILURE, IncidentCategory.SCHEMA_DRIFT]:
                return SeverityLevel.P1, category
            # Hard blocking connection failure
            if category in [IncidentCategory.NETWORK_TIMEOUT, IncidentCategory.API_FAILURE]:
                return SeverityLevel.P1, category
            # Transient capacity limit -> Backoff candidate
            if "429" in msg_lower or "rate limit" in msg_lower:
                return SeverityLevel.P2, category
            return SeverityLevel.P2, category

        elif normalized_pipeline == "PIPELINE_A":  # Batch File Ingestion (Customers CSV)
            if category == IncidentCategory.SCHEMA_DRIFT:
                return SeverityLevel.P1, category  # Structure mismatch breaks parse engine
            if category == IncidentCategory.DATA_QUALITY:
                return SeverityLevel.P3, category  # Row anomalies generate quiet warnings / quarantine paths
            return SeverityLevel.P2, category

        # Fallback priority baseline
        return SeverityLevel.P2, category