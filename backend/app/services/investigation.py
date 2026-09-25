import uuid
import structlog

from app.models.incident import IncidentContext
from app.tools.k8s_client import (
    get_pods,
    get_events,
    get_logs,
    get_deployment_config,
)
from app.services.evidence_processor import process_evidence
from app.services.diagnosis import diagnose_incident

logger = structlog.get_logger(__name__)


def collect_incident_context() -> IncidentContext:

    logger.info(
        "incident_collection_started",
        service="payment-service",
        namespace="operion-sandbox",
    )

    pod_evidence = get_pods()
    active_pod_names = {pod.data["pod_name"] for pod in pod_evidence}

    evidence = list(pod_evidence)
    evidence.extend(get_events(active_pod_names))
    evidence.extend(get_logs(active_pod_names))

    deployment_evidence = get_deployment_config()
    evidence.append(deployment_evidence)

    processed_evidence = process_evidence(evidence)

    incident = IncidentContext(
    incident_id=f"INC-{uuid.uuid4().hex[:8].upper()}",
    service="payment-service",
    namespace="operion-sandbox",
    evidence=processed_evidence,
    )

    logger.info(
    "incident_collection_completed",
    incident_id=incident.incident_id,
    raw_evidence_count=len(evidence),
    processed_evidence_count=len(processed_evidence),
    )

    return incident


if __name__ == "__main__":
    incident = collect_incident_context()

    diagnosis = diagnose_incident(incident)

    print("\n===== INCIDENT =====")
    print(incident.model_dump_json(indent=2))

    print("\n===== DIAGNOSIS =====")
    print(diagnosis.model_dump_json(indent=2))
