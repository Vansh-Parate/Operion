"""Reject diagnosis labels that contradict current live observations."""

import re

from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext
from app.services.temporal import annotate_event_data, assess_current_workload_health


def validate_diagnosis(incident: IncidentContext, diagnosis: Diagnosis) -> Diagnosis:
    health = assess_current_workload_health(incident)
    if diagnosis.root_cause == "no_active_incident" and not health.currently_healthy:
        return diagnosis.model_copy(update={
            "root_cause": "unknown", "confidence": 0.0,
            "summary": "Current Pod and Service routing health are not both established.",
            "supporting_evidence": [], "recommended_actions": [],
        })
    if diagnosis.root_cause != "readiness_probe_failure":
        return diagnosis
    unready_ids = {f"E{index}" for index, item in enumerate(incident.evidence, start=1)
                   if item.source == "kubernetes" and item.category == "pod_status"
                   and (item.data.get("ready") is False
                        or item.data.get("pod_ready_condition") is False)}
    recent_probe_ids = {f"E{index}" for index, item in enumerate(incident.evidence, start=1)
                        if item.source == "kubernetes" and item.category == "kubernetes_event"
                        and "readiness probe" in f"{item.summary} {item.data.get('message', '')}".lower()
                        and annotate_event_data(item.data).get("temporal_status") == "recent"}
    cited = {match.group(1) for item in diagnosis.supporting_evidence
             if (match := re.match(r"^(E[1-9][0-9]*):", item))}
    if health.pod_health == "healthy" or not (unready_ids & cited and recent_probe_ids & cited):
        return diagnosis.model_copy(update={
            "root_cause": "unknown", "confidence": min(diagnosis.confidence, 0.25),
            "summary": "A current readiness probe failure is not supported by current Pod status and a recent probe event.",
            "supporting_evidence": [], "recommended_actions": [],
        })
    return diagnosis
