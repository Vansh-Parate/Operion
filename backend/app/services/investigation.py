import uuid

import structlog
from kubernetes.client.exceptions import ApiException

from app.agent.investigation_tools import (
    get_deployment, get_events, get_logs, get_pod_status,
    target_deployment_name, target_label_selector, _context,
)
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services.evidence_processor import process_evidence
from app.tools.k8s_client import load_cluster

logger = structlog.get_logger(__name__)


def collect_incident_context(
    namespace: str = "operion-sandbox",
    service: str = "payment-service",
    label_selector: str | None = None,
    deployment_name: str | None = None,
) -> IncidentContext:
    """Collect initial facts for a caller-selected workload, without writing to it."""
    incident = IncidentContext(
        incident_id=f"INC-{uuid.uuid4().hex[:8].upper()}",
        namespace=namespace, service=service,
        label_selector=label_selector, deployment_name=deployment_name,
    )
    # Validate caller-supplied targeting before making any API request.
    _context(incident)
    selector = target_label_selector(incident)
    deployment = target_deployment_name(incident)
    logger.info("incident_collection_started", service=service, namespace=namespace,
                label_selector=selector, deployment=deployment)

    load_cluster()
    evidence = get_pod_status(incident)
    if not evidence:
        evidence.append(Evidence(
            source="kubernetes", category="pod_status", service=service, namespace=namespace,
            summary=f"No active pods matched selector {selector} in namespace {namespace}",
            severity="warning", data={"label_selector": selector, "matching_pods": 0},
            reference=f"pods/{selector}",
        ))
    evidence.extend(get_events(incident))
    # Logs are meaningful only for actual pods; the no-pods evidence is synthetic.
    if any(item.data.get("pod_name") for item in evidence if item.category == "pod_status"):
        evidence.extend(get_logs(incident))
    try:
        evidence.extend(get_deployment(incident))
    except ApiException as exc:
        if exc.status != 404:
            raise
        evidence.append(Evidence(
            source="kubernetes", category="deployment_config", service=service,
            namespace=namespace, summary=f"Deployment {deployment} was not found",
            severity="warning", data={"deployment_name": deployment, "found": False},
            reference=f"deployment/{deployment}",
        ))

    incident.evidence = process_evidence(evidence)
    logger.info("incident_collection_completed", incident_id=incident.incident_id,
                raw_evidence_count=len(evidence), processed_evidence_count=len(incident.evidence))
    return incident


if __name__ == "__main__":
    from app.services.diagnosis import diagnose_incident
    from app.services.llm_diagnosis import diagnose_with_llm

    current = collect_incident_context()
    print("\n===== INCIDENT =====")
    print(current.model_dump_json(indent=2))
    print("\n===== DETERMINISTIC DIAGNOSIS =====")
    print(diagnose_incident(current).model_dump_json(indent=2))
    print("\n===== LLM + RAG DIAGNOSIS =====")
    print(diagnose_with_llm(current).model_dump_json(indent=2))
