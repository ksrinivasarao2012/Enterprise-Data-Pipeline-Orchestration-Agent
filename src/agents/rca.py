# Agent for Root Cause Analysis (Queries Knowledge Base)
from dotenv import load_dotenv
load_dotenv()

from typing import Dict, Any
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from src.incidents.incident_manager import IncidentManager
from src.models.schemas import IncidentStatus
from src.services.audit_service import AuditService
from src.config import get_llm_config
from src.telemetry.logger import get_pipeline_logger

logger = get_pipeline_logger("rca_agent")

class RCASchema(BaseModel):
    root_cause_analysis: str = Field(description="Deep technical analysis explaining why the crash occurred.")
    confidence_score: float = Field(description="Value between 0.0 and 1.0 indicating analysis certainty.")

def rca_agent_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Root Cause Analysis (RCA) Agent:
    Leverages Gemini to reason across the raw stack trace and provide clear technical context.
    """
    logger.info("Formulating diagnostic engineering assessment via LLM", incident_id=state.get('incident_id'))
    
    # Initialize the Gemini model from centralized config
    llm_cfg = get_llm_config()
    llm = ChatGoogleGenerativeAI(
        model=llm_cfg["model_name"], 
        temperature=llm_cfg["temperature"],
        timeout=10,
        max_retries=3
    )
    
    rca_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an Elite Principal Data Platform SRE. Analyze the provided pipeline exception log and output a structured root cause analysis summary."),
        ("human", "Pipeline ID: {pipeline_id}\nError Class: {error_class}\nMessage: {error_message}\nTraceback:\n{stack_trace}")
    ])
    
    # Enforce structured output parsing matching our model
    structured_llm = llm.with_structured_output(RCASchema)
    chain = rca_prompt | structured_llm
    
    try:
        response = chain.invoke({
            "pipeline_id": state["pipeline_id"],
            "error_class": state["error_class"],
            "error_message": state["error_message"],
            "stack_trace": state["stack_trace"]
        })
        analysis_result = response.root_cause_analysis
    except Exception as e:
        analysis_result = f"Fallback Analysis: Pipeline execution failed due to an un-handled {state['error_class']}."

    IncidentManager.transition_to(state['incident_id'], IncidentStatus.RCA_COMPLETED, root_cause=analysis_result)
    AuditService.log_event(state['incident_id'], "RCA_AGENT", f"Analysis completed: {analysis_result[:60]}...")
    
    return {
        "root_cause": analysis_result,
        "audit_trail": state.get("audit_trail", []) + ["RCA agent diagnosed failure root cause."]
    }
