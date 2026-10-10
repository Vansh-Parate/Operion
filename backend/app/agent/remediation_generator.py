"""Generate bounded remediation intent and deterministically enrich it."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests
import structlog
from pydantic import ValidationError

from app.agent.investigation_models import Hypothesis
from app.agent.models import (RemediationCandidate, RemediationIntent,
                              RemediationOperation, RemediationProposal,
                              ResourceTarget)
from app.agent.proposal_policy import validate_remediation_proposal
from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument
from app.services.llm_diagnosis import OLLAMA_MODEL, OLLAMA_URL

logger = structlog.get_logger(__name__)
REMEDIATION_TIMEOUT_SECONDS = 15
KNOWN_FALLBACK_CONFIDENCE = 0.8


def _abstain(reason: str) -> RemediationProposal:
    return RemediationProposal(abstain=True, abstain_reason=reason)


def _redact_text(value: str) -> str:
    return re.sub(r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", value)


def _safe_evidence_data(item) -> dict:
    data = item.data.copy()
    if item.category == "configmap":
        data["data"] = {key: "[redacted]" for key in data.get("data", {})}
    if item.category == "deployment_config":
        containers = []
        for container in data.get("containers", []):
            redacted = container.copy()
            redacted["environment_variables"] = [
                {key: value for key, value in env.items() if key != "value"}
                for env in container.get("environment_variables", [])
                if env.get("name") != "INCIDENT_MODE"]
            containers.append(redacted)
        data["containers"] = containers
    if item.category in {"application_log", "previous_application_log"}:
        logs = data.get("logs")
        if isinstance(logs, str):
            data["logs"] = _redact_text(logs)
    return data


def build_remediation_prompt(incident: IncidentContext, hypotheses: list[Hypothesis], diagnosis: Diagnosis,
                             knowledge_documents: list[KnowledgeDocument]) -> str:
    evidence = "\n".join(f"E{index}: {json.dumps({'category': item.category, 'summary': _redact_text(item.summary), 'data': _safe_evidence_data(item), 'reference': item.reference}, default=str)}" for index, item in enumerate(incident.evidence, start=1))
    knowledge = "\n".join(f"K{index} [{item.source_type}] {item.title}: {item.content[:500]}" for index, item in enumerate(knowledge_documents, start=1))
    return f"""Determine only the remediation intent for this investigated incident.
Return JSON only. Do not generate risk, executability, verification, normalized parameters,
patches, commands, kubectl, YAML, namespace values, or arbitrary Kubernetes parameters.
Never generate shell commands, kubectl. The response fields are only operation,
target_kind, target_name, reason, evidence_ids, and optional knowledge_ids.
Allowed operations: patch_environment_variable, update_container_resources, update_probe,
patch_service_selector, scale_workload, restart_workload, rollback_workload, patch_configmap,
none, unsupported. Use only targets and E# evidence visible below. K# references are optional.
For no safe intent use operation none. Shape:
{{"operation":"patch_service_selector","target_kind":"Service","target_name":"{incident.service}","reason":"...","evidence_ids":["E1"],"knowledge_ids":["K1"]}}
Diagnosis: {diagnosis.model_dump_json()}
Hypotheses: {json.dumps([item.model_dump() for item in hypotheses[:3]])}
LIVE EVIDENCE:\n{evidence}
OPERATIONAL KNOWLEDGE:\n{knowledge}"""


def _evidence_ids(incident: IncidentContext, categories: set[str]) -> list[str]:
    return [f"E{index}" for index, item in enumerate(incident.evidence, start=1) if item.category in categories]


def _format_mapping(value: dict[str, str]) -> str:
    return "{" + ", ".join(f"{key}={value[key]}" for key in sorted(value)) + "}"


def _ground_selector_mismatch(incident: IncidentContext, diagnosis: Diagnosis) -> tuple[RemediationIntent | None, str | None]:
    """Derive selector claims, references, and target only from consistent evidence."""
    service_records = [
        (f"E{index}", item) for index, item in enumerate(incident.evidence, start=1)
        if item.category == "service_config" and item.data.get("name") == incident.service
    ]
    label_records = [
        (f"E{index}", item, item.data.get("pod_labels"))
        for index, item in enumerate(incident.evidence, start=1)
        if item.category == "deployment_config" and isinstance(item.data.get("pod_labels"), dict)
    ]
    label_records.extend(
        (f"E{index}", item, item.data.get("labels"))
        for index, item in enumerate(incident.evidence, start=1)
        if item.category == "pod_status" and isinstance(item.data.get("labels"), dict)
        and item.data.get("labels")
    )
    endpoint_records = [
        (f"E{index}", item) for index, item in enumerate(incident.evidence, start=1)
        if item.category in {"endpoints", "endpoint_slice"}
    ]
    if not service_records or not label_records or not endpoint_records:
        return None, "Selector remediation abstained: Service selector, Pod labels, and endpoint evidence are all required."

    selectors = [item.data.get("selector") for _, item in service_records]
    deployment_labels = [value for _, item, value in label_records
                         if item.category == "deployment_config"]
    pod_labels = [value for _, item, value in label_records
                  if item.category == "pod_status"]
    if (any(not isinstance(value, dict) or not value for value in selectors)
            or any(value != selectors[0] for value in selectors[1:])
            or any(not value for value in deployment_labels)
            or any(value != deployment_labels[0] for value in deployment_labels[1:])
            or (not deployment_labels and any(not value for value in pod_labels))
            or (deployment_labels and any(
                any(pod.get(key) != value for key, value in deployment_labels[0].items())
                for pod in pod_labels))):
        return None, "Selector remediation abstained: Service selector or observed Pod labels are contradictory."

    ready_counts = [len(item.data.get("ready_addresses") or []) for _, item in endpoint_records]
    if any(count != 0 for count in ready_counts):
        return None, "Selector remediation abstained: Endpoint evidence is contradictory or already shows ready endpoints."

    selector = selectors[0]
    observed_labels = deployment_labels[0] if deployment_labels else pod_labels[0]
    mismatch = any(selector.get(key) != value for key, value in observed_labels.items() if key in selector)
    mismatch = mismatch or any(key not in observed_labels for key in selector)
    if not mismatch:
        return None, "Selector remediation abstained: The observed Service selector matches the observed Pod labels."

    service_ids = [ref for ref, _ in service_records]
    label_ids = [ref for ref, _, _ in label_records]
    endpoint_ids = [ref for ref, _ in endpoint_records]
    reason = (
        f"Current Service selector {_format_mapping(selector)} (evidence {', '.join(service_ids)}) "
        f"does not match observed Pod labels {_format_mapping(observed_labels)} "
        f"(evidence {', '.join(label_ids)}). The mismatch leaves the Service with zero ready "
        f"endpoints (evidence {', '.join(endpoint_ids)})."
    )
    return RemediationIntent(
        operation="patch_service_selector", target_kind="Service", target_name=incident.service,
        reason=reason,
        evidence_ids=service_ids + label_ids + endpoint_ids,
        parameters={"selector": {str(key): str(value) for key, value in observed_labels.items()}},
    ), None


def _known_pattern(incident: IncidentContext, diagnosis: Diagnosis) -> tuple[RemediationIntent | None, str | None]:
    if diagnosis.confidence < KNOWN_FALLBACK_CONFIDENCE:
        return None, "Diagnosis confidence is too low for deterministic fallback."
    evidence = incident.evidence
    by_category = {category: [item for item in evidence if item.category == category] for category in {item.category for item in evidence}}
    root = diagnosis.root_cause
    if root == "no_active_incident":
        return RemediationIntent(operation="none", reason="No active incident requires remediation."), None
    if root == "redis_dependency_unavailable":
        return RemediationIntent(operation="none", reason="Redis dependency recovery is outside the safe remediation boundary."), None
    if root == "service_selector_mismatch":
        return _ground_selector_mismatch(incident, diagnosis)
    if root == "missing_environment_variable":
        deployments, logs, pods = by_category.get("deployment_config", []), by_category.get("application_log", []), by_category.get("pod_status", [])
        if not deployments or not logs or not pods or not any("PAYMENT_PROVIDER_URL" in str(item.data.get("logs", "")) for item in logs):
            return None, "Missing-variable fallback requires Deployment, log, and failing Pod evidence."
        return RemediationIntent(operation="patch_environment_variable", target_kind="Deployment", target_name=incident.deployment_name or incident.service,
                                 reason=diagnosis.summary, evidence_ids=_evidence_ids(incident, {"deployment_config", "application_log", "pod_status"}),
                                 parameters={"name": "PAYMENT_PROVIDER_URL"}), None
    if root == "container_memory_limit_exceeded":
        if not any(item.data.get("last_termination_reason") == "OOMKilled" for item in by_category.get("pod_status", [])) or not by_category.get("deployment_config"):
            return None, "OOM fallback requires OOMKilled Pod and Deployment evidence."
        return RemediationIntent(operation="update_container_resources", target_kind="Deployment", target_name=incident.deployment_name or incident.service,
                                 reason=diagnosis.summary, evidence_ids=_evidence_ids(incident, {"pod_status", "deployment_config"}),
                                 parameters={"resource": "memory", "direction": "increase"}), None
    if root == "readiness_probe_failure":
        pods = [item for item in by_category.get("pod_status", []) if item.data.get("ready") is False and item.data.get("current_state") == "running"]
        events = [item for item in by_category.get("kubernetes_event", []) if "readiness probe" in str(item.data).lower()]
        if not pods or not events or not by_category.get("deployment_config"):
            return None, "Readiness fallback requires running unready Pod, probe event, and Deployment evidence."
        return RemediationIntent(operation="update_probe", target_kind="Deployment", target_name=incident.deployment_name or incident.service,
                                 reason=diagnosis.summary, evidence_ids=_evidence_ids(incident, {"pod_status", "kubernetes_event", "deployment_config"}),
                                 parameters={"probe_type": "readiness"}), None
    return None, "Diagnosis is not a supported deterministic remediation pattern."


def _deterministic_parameters(intent: RemediationIntent, incident: IncidentContext,
                             diagnosis: Diagnosis) -> dict[str, Any]:
    """Build operation parameters from the diagnosis and observed evidence only."""
    if intent.operation == "patch_service_selector":
        labels = next((item.data.get("pod_labels") for item in incident.evidence
                       if item.category == "deployment_config"
                       and isinstance(item.data.get("pod_labels"), dict)), None)
        return {"selector": {str(key): str(value) for key, value in labels.items()}} if labels else {}
    if intent.operation == "patch_environment_variable":
        return {"name": "PAYMENT_PROVIDER_URL"} if diagnosis.root_cause == "missing_environment_variable" else {}
    if intent.operation == "update_container_resources":
        return {"resource": "memory", "direction": "increase"} if diagnosis.root_cause == "container_memory_limit_exceeded" else {}
    if intent.operation == "update_probe":
        return {"probe_type": "readiness"} if diagnosis.root_cause == "readiness_probe_failure" else {}
    return {}


def _enrich(intent: RemediationIntent, incident: IncidentContext, diagnosis: Diagnosis,
            knowledge_documents: list[KnowledgeDocument]) -> RemediationProposal:
    if intent.operation in {"none", "unsupported"}:
        return _abstain(intent.reason)
    if any(int(ref[1:]) > len(incident.evidence) for ref in intent.evidence_ids):
        return _abstain("The remediation intent cites unavailable live evidence.")
    if any(int(ref[1:]) > len(knowledge_documents) for ref in intent.knowledge_ids):
        return _abstain("The remediation intent cites unavailable operational knowledge.")
    if intent.operation == "patch_service_selector":
        grounded, reason = _ground_selector_mismatch(incident, diagnosis)
        if grounded is None:
            return _abstain(reason or "Selector evidence could not be grounded safely.")
        # The model may choose an intent, but never supplies selector claims or prose.
        intent = grounded.model_copy(update={"knowledge_ids": intent.knowledge_ids})
    operation = RemediationOperation(operation=intent.operation,
        target=ResourceTarget(kind=intent.target_kind or "", namespace=incident.namespace, name=intent.target_name or ""),
        parameters=_deterministic_parameters(intent, incident, diagnosis), reason=intent.reason, evidence_ids=intent.evidence_ids,
        knowledge_ids=intent.knowledge_ids)
    verification = {
        "patch_service_selector": ["Service has ready endpoints.", "Selected Pods remain Ready."],
        "patch_environment_variable": ["Replacement Pod is Running and Ready."],
        "update_container_resources": ["Replacement Pod is Running and Ready."],
        "update_probe": ["Pod has Ready=True.", "No recent readiness failures are observed."],
    }.get(intent.operation, ["Recollect current workload health."])
    candidate = RemediationCandidate(operation=operation, expected_effect="Observed health improves after the recommended change.",
        risk_level="medium", confidence=1.0, verification_strategy=verification)
    checked, decisions = validate_remediation_proposal(RemediationProposal(candidates=[candidate], recommended_candidate_index=0), incident, knowledge_documents)
    if not decisions or decisions[0].status in {"denied", "abstain"}:
        return _abstain(decisions[0].reason if decisions else "No candidate passed deterministic validation.")
    return checked


def _generate(incident: IncidentContext, hypotheses: list[Hypothesis], diagnosis: Diagnosis, knowledge_documents: list[KnowledgeDocument]) -> tuple[RemediationProposal, dict[str, Any]]:
    if diagnosis.root_cause == "no_active_incident":
        return _abstain("No active incident currently requires remediation."), {"status": "not_needed", "fallback_used": False, "latency_ms": 0}
    if not incident.evidence:
        return _abstain("No live evidence supports a remediation."), {"status": "abstain", "fallback_used": False, "latency_ms": 0}
    if diagnosis.root_cause in {None, "unknown", "service_routing_failure"}:
        return _abstain("The diagnosis does not establish a supported remediation pattern."), {"status": "abstain", "fallback_used": False, "latency_ms": 0}
    started = time.perf_counter()
    status, fallback_used = "llm_success", False
    logger.info("model_stage_started", stage="remediation", model=OLLAMA_MODEL)
    legacy_payload = False
    try:
        response = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": build_remediation_prompt(incident, hypotheses, diagnosis, knowledge_documents), "stream": False, "format": "json"}, timeout=REMEDIATION_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()["response"]
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and "candidates" in decoded:
            legacy_payload = True
            legacy = RemediationProposal.model_validate(decoded)
            if legacy.abstain or not legacy.candidates:
                return _abstain(legacy.abstain_reason or "No safe remediation intent was proposed."), {"status": "llm_success", "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
            selected = legacy.candidates[legacy.recommended_candidate_index or 0].operation
            if selected.target is not None and selected.target.namespace != incident.namespace:
                return _abstain("The remediation target namespace must come from IncidentContext."), {"status": "invalid_target", "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
            intent = RemediationIntent(operation=selected.operation, target_kind=selected.target.kind if selected.target else None,
                target_name=selected.target.name if selected.target else None, reason=selected.reason,
                evidence_ids=selected.evidence_ids, knowledge_ids=selected.knowledge_ids, parameters=selected.parameters)
        else:
            intent = RemediationIntent.model_validate(decoded)
        if intent.operation not in {"none", "unsupported"}:
            if any(int(ref[1:]) > len(incident.evidence) for ref in intent.evidence_ids):
                return _abstain("The remediation intent cites unavailable live evidence."), {"status": "invalid_evidence", "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
            if (intent.operation == "patch_service_selector"
                    and (intent.target_kind != "Service" or intent.target_name != incident.service)):
                return _abstain("The remediation target must be the observed incident Service."), {"status": "invalid_target", "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
            if not intent.evidence_ids or not intent.target_kind or not intent.target_name:
                status = "llm_invalid"
                intent, reason = _known_pattern(incident, diagnosis)
                if intent is None:
                    return _abstain(reason or "Required remediation intent fields are missing."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
                fallback_used = True
        elif intent.operation == "unsupported":
            status = "llm_invalid"
            intent, reason = _known_pattern(incident, diagnosis)
            if intent is None:
                return _abstain(reason or "The model returned an unsupported operation."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
            fallback_used = True
    except requests.Timeout:
        status = "llm_timeout"
        intent, reason = _known_pattern(incident, diagnosis)
        if intent is None:
            logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        status=status, fallback_used=False)
            return _abstain(reason or "Remediation model timed out and deterministic fallback is unsafe."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        fallback_used = True
        logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status=status, fallback_used=True)
    except (requests.RequestException, KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        status = "llm_invalid"
        if legacy_payload:
            logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        status=status, fallback_used=False)
            return _abstain("The remediation response contained an invalid proposal."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        intent, reason = _known_pattern(incident, diagnosis)
        if intent is None:
            logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        status=status, fallback_used=False)
            return _abstain(reason or f"No safe structured remediation intent: {type(exc).__name__}."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        fallback_used = True
        logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status=status, fallback_used=True)
    try:
        proposal = _enrich(intent, incident, diagnosis, knowledge_documents)
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        proposal = _abstain(f"Remediation intent failed deterministic validation: {type(exc).__name__}.")
        status = "invalid_intent"
    metadata = {"status": status, "fallback_used": fallback_used, "latency_ms": round((time.perf_counter() - started) * 1000)}
    logger.info("model_stage_completed", stage="remediation", model=OLLAMA_MODEL,
                latency_ms=metadata["latency_ms"], status=status, fallback_used=fallback_used)
    logger.info("remediation_generation_completed", **metadata)
    return proposal, metadata


def generate_remediation_proposal(incident: IncidentContext, hypotheses: list[Hypothesis], diagnosis: Diagnosis,
                                  knowledge_documents: list[KnowledgeDocument], *, return_metadata: bool = False):
    proposal, metadata = _generate(incident, hypotheses, diagnosis, knowledge_documents)
    return (proposal, metadata) if return_metadata else proposal
