"""Small, deterministic distinction between current status and event history."""

from datetime import datetime, timezone

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
    currently_healthy: bool = False
    active_failure_observed: bool = False
    reasons: list[str] = Field(default_factory=list)


def assess_current_workload_health(incident: IncidentContext) -> WorkloadHealthAssessment:
    pods = [item for item in incident.evidence if item.category == "pod_status"
            and item.data.get("pod_name")]
    if not pods:
        return WorkloadHealthAssessment(reasons=["No current pod status observed."])
    reasons = []
    healthy = True
    active = False
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
            healthy = False
            if (data.get("phase") not in (None, "Running") or pod_ready is False
                    or any(item.get("ready") is False or item.get("waiting_reason")
                           or item.get("current_state") in ("waiting", "terminated")
                           for item in containers)):
                active = True
                reasons.append(f"Pod {data['pod_name']} has an observed current failure or unready state.")
            else:
                reasons.append(f"Current health of pod {data['pod_name']} is incomplete.")
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
        elif item.category == "endpoints" and not item.data.get("ready_addresses"):
            active = True
            reasons.append("No ready Service endpoints were observed.")
        elif item.category == "application_log":
            logs = item.data.get("logs")
            if isinstance(logs, str) and any(term in logs.lower() for term in (
                "error", "exception", "connection refused", "oomkilled")):
                active = True
                reasons.append("Application logs contain an error that needs separate assessment.")
    return WorkloadHealthAssessment(currently_healthy=healthy,
                                    active_failure_observed=active, reasons=reasons)
