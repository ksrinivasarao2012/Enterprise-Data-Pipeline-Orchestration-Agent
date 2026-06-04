import streamlit as st
import pandas as pd
import os
import tempfile
import sqlite3
from src.config import get_database_paths
from src.incidents.incident_repository import IncidentRepository
from src.pipeline.json_pipeline import PipelineA

# Dashboard Configuration
st.set_page_config(page_title="Enterprise Data Pipeline Orchestration Agent", layout="wide")

st.title("Enterprise Data Pipeline Orchestration Agent")
st.subheader("ETL automation with self-healing capabilities")

# --- Ingestion Testing Console ---
with st.expander("Pipeline Ingestion Sandbox (Upload JSON to test Pipeline A)", expanded=True):
    # Column buttons for action
    col_u1, col_u2 = st.columns([3, 1])
    with col_u1:
        uploaded_file = st.file_uploader("Upload Customer JSON (Pipeline A)", type=["json"])
    with col_u2:
        st.write("Reset Control Plane")
        st.caption("💡 If you want to have fresh data, use **Clear DB History**.")
        if st.button("Clear DB History"):
            paths = get_database_paths()
            try:
                # Clear state database tables
                with sqlite3.connect(paths["state_db"]) as conn:
                    conn.execute("DELETE FROM incidents")
                    conn.execute("DELETE FROM pipeline_runs")
                    conn.execute("DELETE FROM audit_logs")
                    conn.execute("DELETE FROM pipeline_configs")
                    conn.commit()
                # Clear operational database tables
                with sqlite3.connect(paths["operational_db"]) as conn:
                    conn.execute("DELETE FROM customers")
                    conn.commit()
                # Clear human escalation log file
                if os.path.exists("escalated_incidents.log"):
                    try:
                        os.remove("escalated_incidents.log")
                    except Exception:
                        pass
                st.success("Control Plane database history reset successfully!")
                st.rerun()
            except Exception as reset_err:
                st.error(f"Error resetting database: {reset_err}")
    
    # Display status messages from previous runs for Pipeline A
    if "ingestion_result_a" in st.session_state:
        st.success(st.session_state["ingestion_result_a"])
        del st.session_state["ingestion_result_a"]
    if "ingestion_error_a" in st.session_state:
        st.error(st.session_state["ingestion_error_a"])
        del st.session_state["ingestion_error_a"]




    if uploaded_file is not None:
        st.success("File uploaded successfully!")
        
        if st.button("Execute Ingestion Pipeline A"):
            from src.pipeline.json_pipeline import PipelineA, DEFAULT_JSON_PATH
            import json as json_mod
            try:
                # Bypassing strict UI validation to allow the telemetry gateway and LangGraph to auto-heal broken files
                raw_bytes = uploaded_file.getvalue()
                
                # Save uploaded file to the default pipeline JSON path
                os.makedirs(os.path.dirname(DEFAULT_JSON_PATH), exist_ok=True)
                with open(DEFAULT_JSON_PATH, "wb") as f:
                    f.write(raw_bytes)
                
                # Instantiate and execute pipeline
                pipeline = PipelineA()
                result = pipeline.execute(json_file_path=DEFAULT_JSON_PATH, simulate_failure_type=None, original_filename=uploaded_file.name)
                
                if result is not None:
                    st.session_state["ingestion_result_a"] = f"Ingestion successful! Ingested {result} rows."
                    # Reset overrides to restore clean default baseline upon successful ingestion
                    from src.config import reset_pipeline_configs
                    reset_pipeline_configs(pipeline.pipeline_id)
                else:
                    st.session_state["ingestion_error_a"] = "Pipeline execution failed. Telemetry reported an incident."
            except Exception as ex:
                st.session_state["ingestion_error_a"] = f"Error launching pipeline: {ex}"
            
            # Trigger page rerun to refresh incident tables
            st.rerun()

# --- Database ETL Sandbox (Pipeline B) ---
with st.expander("Database ETL Sandbox (Test Pipeline B)", expanded=True):
    # Display status messages from previous runs for Pipeline B
    if "ingestion_result_b" in st.session_state:
        st.success(st.session_state["ingestion_result_b"])
        del st.session_state["ingestion_result_b"]
    if "ingestion_error_b" in st.session_state:
        st.error(st.session_state["ingestion_error_b"])
        del st.session_state["ingestion_error_b"]

    uploaded_db = st.file_uploader("Upload Operational Database (Pipeline B .db file)", type=["db", "sqlite", "sqlite3"])

    if uploaded_db is not None:
        st.success("File uploaded successfully!")
        
        if st.button("Execute Ingestion Pipeline B", key="run_pipeline_b"):
            try:
                op_db_arg = None
                temp_db_file = None
                
                # Save uploaded .db file to a temporary file location
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
                    tmp.write(uploaded_db.getvalue())
                    temp_db_file = tmp.name
                op_db_arg = temp_db_file
                
                from src.pipeline.db_pipeline import PipelineB
                pipeline = PipelineB()
                result = pipeline.execute(simulate_failure_type=None, op_db_path=op_db_arg, original_filename=uploaded_db.name)
                
                # Cleanup temp file
                if temp_db_file and os.path.exists(temp_db_file):
                    try:
                        os.remove(temp_db_file)
                    except Exception:
                        pass
                
                if result is not None:
                    st.session_state["ingestion_result_b"] = f"Pipeline B ETL successful! Synced {result} metrics."
                    from src.config import reset_pipeline_configs
                    reset_pipeline_configs(pipeline.pipeline_id)
                else:
                    st.session_state["ingestion_error_b"] = "Pipeline B execution failed. Telemetry reported an incident."
            except Exception as ex:
                st.session_state["ingestion_error_b"] = f"Error launching Pipeline B: {ex}"
            
            st.rerun()

# 1. Real-time Incident Feed and Workflow (Auto-Refreshes every 3 seconds)
@st.fragment(run_every=3)
def render_incident_dashboard():
    # Fetch metrics first (always visible)
    paths = get_database_paths()
    runs_without_incident = 0
    resolved_count = 0
    investigating_count = 0
    escalated_count = 0
    try:
        with sqlite3.connect(paths["state_db"]) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM pipeline_runs 
                WHERE status = 'SUCCESS' 
                  AND run_id NOT IN (SELECT DISTINCT run_id FROM incidents)
            """)
            runs_without_incident = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'RESOLVED'")
            resolved_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'INVESTIGATING'")
            investigating_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM incidents WHERE status = 'ESCALATED'")
            escalated_count = cursor.fetchone()[0]
    except Exception:
        pass

    # 2. Visualizing the Agentic Loop (Always visible)
    st.subheader("Autonomous Remediation Engine Status")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Resolved", resolved_count)
    with col2:
        st.metric("Active Investigations", investigating_count)
    with col3:
        st.metric("Escalated to Human", escalated_count)
    with col4:
        st.metric("Runs Without Incident", runs_without_incident)

    st.markdown("---")
    st.subheader("Active Incident Workflow")
    incidents = IncidentRepository.get_all_incidents()

    if incidents:
        # Hydrate dataframe cleanly using Pydantic model serialization
        df = pd.DataFrame([i.model_dump() for i in incidents])
        
        # Filter active (non-RESOLVED) and resolved incidents
        active_df = df[df['status'] != 'RESOLVED']
        resolved_df = df[df['status'] == 'RESOLVED']
        
        # Select clean columns for primary view (hiding large stack_trace)
        display_cols = [
            "incident_id", "run_id", "pipeline_id", "severity", "category",
            "status", "error_class", "error_message", "root_cause", "recovery_action", "created_at"
        ]
        
        if not active_df.empty:
            active_display = active_df[[c for c in display_cols if c in active_df.columns]]
            st.dataframe(
                active_display,
                column_config={
                    "incident_id": "Incident ID",
                    "run_id": "Run ID",
                    "pipeline_id": "Pipeline ID",
                    "severity": "Severity",
                    "category": "Category",
                    "status": "Status",
                    "error_class": "Error Class",
                    "error_message": "Error Message",
                    "root_cause": "Root Cause Analysis (Error Reason)",
                    "recovery_action": "Remediation Strategy",
                    "created_at": "Registered At"
                },
                use_container_width=True
            )
        else:
            st.info("No active incidents currently queueing in the control plane.")

        # 3. Drill-down: View Agent Logic
        incident_list = df['incident_id'].tolist()
        if "selected_incident" not in st.session_state:
            st.session_state["selected_incident"] = incident_list[0] if incident_list else None
            
        default_index = 0
        if st.session_state["selected_incident"] in incident_list:
            default_index = incident_list.index(st.session_state["selected_incident"])

        selected_incident = st.selectbox(
            "Select Incident for Audit", 
            incident_list, 
            index=default_index,
            key="selected_incident_selectbox"
        )
        st.session_state["selected_incident"] = selected_incident

        if selected_incident:
            st.subheader(f"Incident Audit Trail: {selected_incident}")
            audit_trail = IncidentRepository.get_audit_trail(selected_incident)
            
            if audit_trail:
                # Color map for components
                color_map = {
                    "TELEMETRY_RECEIVER": "#FF5733", # Orange-Red
                    "MONITOR_AGENT": "#33C1FF",      # Sky Blue
                    "CLASSIFIER_AGENT": "#B833FF",   # Violet
                    "RCA_AGENT": "#FF3333",          # Bright Red
                    "RECOVERY_AGENT": "#33FF57",     # Green
                    "ACTUATOR": "#00F5D4",           # Teal
                    "CONTROL_PLANE": "#8D99AE"       # Slate Gray
                }
                
                for step in audit_trail:
                    comp = step.get("component", "UNKNOWN")
                    msg = step.get("message", "")
                    ts = step.get("timestamp", "")
                    
                    # Clean timestamp presentation
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    except Exception:
                        formatted_ts = ts
                    
                    color = color_map.get(comp, "#FFFFFF")
                    
                    st.markdown(f"""
                    <div style="
                        padding: 12px 16px; 
                        border-left: 6px solid {color}; 
                        background-color: #111B24; 
                        margin-bottom: 10px; 
                        border-radius: 4px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                    ">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-weight: bold; color: {color}; font-size: 1.05em; letter-spacing: 0.5px;">{comp}</span>
                            <span style="font-size: 0.85em; color: #8D99AE;">{formatted_ts}</span>
                        </div>
                        <div style="
                            margin-top: 8px; 
                            color: #E2E8F0; 
                            font-family: 'Courier New', Courier, monospace; 
                            font-size: 0.95em;
                            line-height: 1.4;
                        ">
                            {msg}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("No audit logs recorded for the selected incident.")
            
            # Determine options based on current status
            selected_row = df[df['incident_id'] == selected_incident].iloc[0]
            current_status = selected_row['status']

            from src.incidents.incident_manager import IncidentManager
            from src.models.schemas import IncidentStatus
            

            # Append historical logs section for resolved files
            if not resolved_df.empty:
                resolved_display = resolved_df[[c for c in display_cols if c in resolved_df.columns]]
                st.markdown("---")
                with st.expander("History of Resolved Incidents (Closed out)", expanded=False):
                    st.dataframe(
                        resolved_display,
                        column_config={
                            "incident_id": "Incident ID",
                            "run_id": "Run ID",
                            "pipeline_id": "Pipeline ID",
                            "severity": "Severity",
                            "category": "Category",
                            "status": "Status",
                            "error_class": "Error Class",
                            "error_message": "Error Message",
                            "root_cause": "Root Cause Analysis (Error Reason)",
                            "recovery_action": "Remediation Strategy",
                            "created_at": "Registered At"
                        },
                        use_container_width=True
                    )
    else:
        st.info("No active incidents found in the platform control plane database.")

    # Render Human Escalation Queue from escalated_incidents.log
    st.markdown("---")
    with st.expander("Human Escalation Queue (Requires Manual Intervention)", expanded=True):
        if os.path.exists("escalated_incidents.log"):
            with open("escalated_incidents.log", "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            if lines:
                import re
                escalations = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    match = re.match(r"\[(.*?)\] Incident:\s*(.*?)\s*\|\s*(?:Reason|Source File):\s*(.*)", line)
                    if match:
                        raw_ts = match.group(1)
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                            formatted_ts = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                        except Exception:
                            formatted_ts = raw_ts
                            
                        escalations.append({
                            "Escalated At": formatted_ts,
                            "Incident ID": match.group(2),
                            "Problem Description": match.group(3)
                        })
                if escalations:
                    st.dataframe(
                        pd.DataFrame(escalations),
                        column_config={
                            "Escalated At": "Escalated At",
                            "Incident ID": "Incident ID",
                            "Problem Description": "Problem Description"
                        },
                        use_container_width=True
                    )
                else:
                    st.info("No formatted escalations found in the queue log.")
            else:
                st.info("Escalation queue is empty.")
        else:
            st.info("No escalations log found on disk.")

render_incident_dashboard()

