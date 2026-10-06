"""Small, deterministic distinction between current status and event history."""

from datetime import datetime, timezone

from typing import Literal

from pydantic import BaseModel, Field

from app.models.incident import IncidentContext


HISTORICAL_AFTER_SECONDS = 600


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def annotate_event_data(data: dict, *, now: datetime | None = None) -> dict:
    """Retain every supplied timestamp; age uses the latest observed occurrence."""
    result = data.copy()
    observed = None
    for key in ("last_timestamp", "series_last_observed_time", "event_time", "first_timestamp"):
        observed = _datetime(result.get(key))
        if observed is not None:
            break
    if observed is None:
        result["age_seconds"] = None
        result["temporal_status"] = "unknown"
        return result
    reference = now or datetime.now(timezone.utc)
    age = max(0, int((reference - observed).total_seconds()))
    result["age_seconds"] = age
    result["temporal_status"] = "historical" if age >= HISTORICAL_AFTER_SECONDS else "recent"
    return result


class WorkloadHealthAssessment(BaseModel):
    pod_health: Literal["healthy", "unhealthy", "unknown"] = "unknown"
    service_health: Literal["healthy", "unhealthy", "unknown"] = "unknown"
    selector_mismatch_observed: bool = False
    zero_ready_endpoints_observed: bool = False
    currently_healthy: bool = False
    active_failure_observed: bool = False
    reasons: list[str] = Field(default_factory=list)


def assess_current_workload_health(incident: IncidentContext) -> WorkloadHealthAssessment:
    pods = [item for item in incident.evidence if item.source == "kubernetes"
            and item.category == "pod_status"
            and item.data.get("pod_name")]
    reasons = []
    pod_health = "healthy" if pods else "unknown"
    active = False
    if not pods:
        reasons.append("No current pod status observed.")
    for pod in pods:
        data = pod.data
        containers = data.get("containers") or [data]
        pod_ready = data.get("pod_ready_condition")
        all_running_ready = bool(containers) and all(
            item.get("current_state") == "running" and item.get("ready") is True
            and not item.get("waiting_reason") for item in containers)
        if data.get("phase") == "Running" and pod_ready is True and all_running_ready:
            reasons.append(f"Pod {data['pod_name']} is currently Running and Ready.")
        else:
            if (data.get("phase") not in (None, "Running") or pod_ready is False
                    or any(item.get("ready") is False or item.get("waiting_reason")
                           or item.get("current_state") in ("waiting", "terminated")
                           for item in containers)):
                active = True
                pod_health = "unhealthy"
                reasons.append(f"Pod {data['pod_name']} has an observed current failure or unready state.")
            else:
                if pod_health != "unhealthy":
                    pod_health = "unknown"
                reasons.append(f"Current health of pod {data['pod_name']} is incomplete.")

    service = next((item for item in incident.evidence
                    if item.source == "kubernetes" and item.category == "service_config"
                    and item.data.get("name") == incident.service), None)
    endpoints = next((item for item in incident.evidence
                      if item.source == "kubernetes"
                      and item.category in {"endpoints", "endpoint_slices", "service_endpoints"}
                      and (item.data.get("name") in (None, incident.service))), None)
    service_health = "unknown"
    zero_endpoints = False
    selector_mismatch = False
    if service is None:
        reasons.append("Current Service configuration has not been observed.")
    if endpoints is None:
        reasons.append("Current Endpoints or EndpointSlice state has not been observed.")
    else:
        data = endpoints.data
        ready = data.get("ready_addresses")
        if ready is None:
            ready = data.get("ready_endpoints")
        if isinstance(ready, list) and not ready:
            zero_endpoints = True
            active = True
            service_health = "unhealthy"
            reasons.append("Service has zero ready endpoints.")
        elif not isinstance(ready, list):
            reasons.append("Endpoint readiness is unknown.")
    if service is not None:
        selector = service.data.get("selector")
        if isinstance(selector, dict) and selector:
            pod_labels = [item.data.get("labels") for item in pods
                          if isinstance(item.data.get("labels"), dict) and item.data["labels"]]
            if not pod_labels:
                pod_labels = [item.data.get("pod_labels") for item in incident.evidence
                              if item.source == "kubernetes" and item.category == "deployment_config"
                              and isinstance(item.data.get("pod_labels"), dict)
                              and item.data["pod_labels"]]
            if pod_labels and not any(all(labels.get(key) == value for key, value in selector.items())
                                      for labels in pod_labels):
                selector_mismatch = True
                active = True
                service_health = "unhealthy"
                reasons.append("Service selector does not match observed Pod labels.")
        if endpoints is not None and service_health != "unhealthy":
            ready = endpoints.data.get("ready_addresses")
            if ready is None:
                ready = endpoints.data.get("ready_endpoints")
            if isinstance(ready, list) and ready:
                service_health = "healthy"
                reasons.append("Service has ready endpoints.")
    for item in incident.evidence:
        if item.category == "kubernetes_event" and item.severity == "warning":
            status = annotate_event_data(item.data).get("temporal_status")
            if status == "recent":
                active = True
                reasons.append("A recent warning event needs investigation.")
            elif status == "historical":
                reasons.append("A historical warning is retained as context.")
            else:
                active = True
                reasons.append("A warning has no usable timestamp; current impact is uncertain.")
        elif item.category == "application_log":
            logs = item.data.get("logs")
            if isinstance(logs, str) and any(term in logs.lower() for term in (
                "error", "exception", "connection refused", "oomkilled")):
                active = True
                reasons.append("Application logs contain an error that needs separate assessment.")
    return WorkloadHealthAssessment(
        pod_health=pod_health, service_health=service_health,
        selector_mismatch_observed=selector_mismatch,
        zero_ready_endpoints_observed=zero_endpoints,
        currently_healthy=pod_health == "healthy" and service_health == "healthy" and not active,
        active_failure_observed=active, reasons=reasons)
