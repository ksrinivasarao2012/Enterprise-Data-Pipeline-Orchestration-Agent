import structlog
import logging
import sys
import os

def setup_logger():
    # Define processors for a clean, consistent log pipeline
    processors = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # JSON output for machine parsing, console output for human debug
        structlog.processors.JSONRenderer() if os.getenv("ENV") == "PROD" 
        else structlog.dev.ConsoleRenderer(colors=False)
    ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    return structlog.get_logger()

logger = setup_logger()

def get_pipeline_logger(name: str):
    return structlog.get_logger(name)
