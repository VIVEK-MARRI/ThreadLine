"""Threadline FastAPI application entry point.

Wires together configuration, routers, and middleware.
Keep this file thin — it delegates everything to the api layer.
"""

from fastapi import FastAPI

from app.core.config import settings
from app.api.meetings import router as meetings_router
from app.api.entities import router as entities_router
from app.api.attention import router as attention_router
from app.schemas.meeting import HealthResponse

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Threadline transforms meeting data into structured organisational memory, "
        "insights, and proactive risk intelligence."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(meetings_router, prefix=settings.api_v1_prefix)
app.include_router(entities_router, prefix=settings.api_v1_prefix)
app.include_router(attention_router, prefix=settings.api_v1_prefix)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    summary="Health check",
    description="Returns the operational status of the Threadline API.",
)
def health() -> HealthResponse:
    """Lightweight liveness probe."""
    return HealthResponse(status="healthy")
