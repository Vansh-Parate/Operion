"""Deterministic minimum observations for service-backed diagnosis."""

from pydantic import BaseModel

from app.agent.investigation_models import ToolCallRecord
from app.models.incident import IncidentContext
from app.services.temporal import assess_current_workload_health


class InvestigationCompleteness(BaseModel):
    complete: bool
    next_tool: str | None = None
    reason: str | None = None


def assess_investigation_completeness(
    incident: IncidentContext, tool_history: list[ToolCallRecord],
) -> InvestigationCompleteness:
    """A service target needs both current Service and endpoint observations."""
    attempted = {record.tool for record in tool_history}
    service_seen = any(
        item.source == "kubernetes" and item.category == "service_config"
        and item.data.get("name") == incident.service
        for item in incident.evidence
    )
    endpoints_seen = any(
        item.source == "kubernetes"
        and item.category in {"endpoints", "endpoint_slices", "service_endpoints"}
        and item.data.get("name") in (None, incident.service)
        for item in incident.evidence
    )
    if not service_seen:
        if "get_service" not in attempted:
            return InvestigationCompleteness(
                complete=False, next_tool="get_service",
                reason="Current Service configuration is required before diagnosis.")
        return InvestigationCompleteness(
            complete=False, reason="Service configuration remains unknown after get_service.")
    if not endpoints_seen:
        if "get_endpoints" not in attempted:
            return InvestigationCompleteness(
                complete=False, next_tool="get_endpoints",
                reason="Current Endpoints or EndpointSlice state is required before diagnosis.")
        return InvestigationCompleteness(
            complete=False, reason="Service endpoint state remains unknown after get_endpoints.")
    health = assess_current_workload_health(incident)
    if health.service_health == "unknown":
        if "get_endpoints" not in attempted:
            return InvestigationCompleteness(
                complete=False, next_tool="get_endpoints",
                reason="Endpoint readiness is unknown; refresh Endpoints before diagnosis.")
        return InvestigationCompleteness(
            complete=False, reason="Service routing health remains unknown after endpoint refresh.")
    return InvestigationCompleteness(complete=True)
