"""Deterministic minimum observations for service-backed diagnosis."""

from pydantic import BaseModel

from app.agent.investigation_models import ToolCallRecord
from app.models.incident import IncidentContext
from app.services.temporal import assess_current_workload_health


class InvestigationCompleteness(BaseModel):
    complete: bool
    next_tool: str | None = None
    reason: str | None = None


def _current(item) -> bool:
    """Reject evidence explicitly marked historical or older than the temporal horizon."""
    if item.source != "kubernetes":
        return False
    if item.data.get("temporal_status") == "historical":
        return False
    return item.data.get("age_seconds") is None or item.data.get("age_seconds", 0) < 600


def _target_observation(item, incident: IncidentContext) -> bool:
    """Require a current Kubernetes observation for this incident target."""
    # Some unit and imported observations omit namespace metadata; an
    # explicitly different namespace is unrelated and must not count.
    return _current(item) and item.namespace in (None, incident.namespace)


def assess_investigation_completeness(
    incident: IncidentContext, tool_history: list[ToolCallRecord],
) -> InvestigationCompleteness:
    """A service target needs current Pod, Service, and endpoint observations."""
    attempted = {record.tool for record in tool_history}
    pod_seen = any(
        _target_observation(item, incident) and item.category == "pod_status"
        and item.data.get("pod_name")
        and isinstance(item.data.get("labels"), dict)
        for item in incident.evidence
    )
    service_seen = any(
        _target_observation(item, incident) and item.category == "service_config"
        and item.data.get("name") == incident.service
        for item in incident.evidence
    )
    endpoints_seen = any(
        _target_observation(item, incident)
        and item.category in {"endpoints", "endpoint_slice", "endpoint_slices", "service_endpoints"}
        and item.data.get("name") in (None, incident.service)
        for item in incident.evidence
    )
    if not pod_seen:
        if "get_pod_status" not in attempted:
            return InvestigationCompleteness(complete=False, next_tool="get_pod_status",
                                             reason="Current Pod health and labels are required before diagnosis.")
        return InvestigationCompleteness(complete=False,
                                         reason="Current Pod health and labels remain unknown after get_pod_status.")
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
