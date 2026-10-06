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
patches, commands, kubectl, YAML, or namespace values. Never generate shell commands, kubectl.
Allowed operations: patch_environment_variable, update_container_resources, update_probe,
patch_service_selector, scale_workload, restart_workload, rollback_workload, patch_configmap,
none, unsupported. Use only targets and E# evidence visible below. K# references are optional.
For no safe intent use operation none. Shape:
{{"operation":"patch_service_selector","target_kind":"Service","target_name":"{incident.service}","reason":"...","evidence_ids":["E1"],"knowledge_ids":["K1"],"parameters":{{"selector":{{"app":"observed"}}}}}}
Diagnosis: {diagnosis.model_dump_json()}
Hypotheses: {json.dumps([item.model_dump() for item in hypotheses[:3]])}
LIVE EVIDENCE:\n{evidence}
OPERATIONAL KNOWLEDGE:\n{knowledge}"""


def _evidence_ids(incident: IncidentContext, categories: set[str]) -> list[str]:
    return [f"E{index}" for index, item in enumerate(incident.evidence, start=1) if item.category in categories]


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
        service = by_category.get("service_config", [])
        deployments = [item for item in by_category.get("deployment_config", []) if item.data.get("pod_labels")]
        endpoints = [item for item in evidence if item.category in {"endpoints", "endpoint_slice"}]
        service_item = next((item for item in service if item.data.get("name") == incident.service), None)
        labels = deployments[0].data.get("pod_labels") if deployments else None
        selector = service_item.data.get("selector") if service_item else None
        if service_item is None or not endpoints or not isinstance(selector, dict) or not isinstance(labels, dict) or selector == labels:
            return None, "Selector fallback requires Service selector, Pod labels, and endpoint evidence proving a mismatch."
        return RemediationIntent(operation="patch_service_selector", target_kind="Service", target_name=service_item.data["name"],
                                 reason=diagnosis.summary, evidence_ids=_evidence_ids(incident, {"service_config", "deployment_config", "endpoints", "endpoint_slice"}),
                                 parameters={"selector": {str(key): str(value) for key, value in labels.items()}}), None
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


def _enrich(intent: RemediationIntent, incident: IncidentContext, knowledge_documents: list[KnowledgeDocument]) -> RemediationProposal:
    if intent.operation in {"none", "unsupported"}:
        return _abstain(intent.reason)
    if any(int(ref[1:]) > len(incident.evidence) for ref in intent.evidence_ids):
        return _abstain("The remediation intent cites unavailable live evidence.")
    operation = RemediationOperation(operation=intent.operation,
        target=ResourceTarget(kind=intent.target_kind or "", namespace=incident.namespace, name=intent.target_name or ""),
        parameters=intent.parameters, reason=intent.reason, evidence_ids=intent.evidence_ids,
        knowledge_ids=[ref for ref in intent.knowledge_ids if int(ref[1:]) <= len(knowledge_documents)])
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
    logger.info("remediation_generation_started", status="started")
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
        logger.warning("remediation_generation_failed", status=status)
        intent, reason = _known_pattern(incident, diagnosis)
        if intent is None:
            return _abstain(reason or "Remediation model timed out and deterministic fallback is unsafe."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        fallback_used = True
    except (requests.RequestException, KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        status = "llm_invalid"
        logger.warning("remediation_generation_failed", status=status, error_type=type(exc).__name__)
        if legacy_payload:
            return _abstain("The remediation response contained an invalid proposal."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        intent, reason = _known_pattern(incident, diagnosis)
        if intent is None:
            return _abstain(reason or f"No safe structured remediation intent: {type(exc).__name__}."), {"status": status, "fallback_used": False, "latency_ms": round((time.perf_counter() - started) * 1000)}
        fallback_used = True
    try:
        proposal = _enrich(intent, incident, knowledge_documents)
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        proposal = _abstain(f"Remediation intent failed deterministic validation: {type(exc).__name__}.")
        status = "invalid_intent"
    metadata = {"status": status, "fallback_used": fallback_used, "latency_ms": round((time.perf_counter() - started) * 1000)}
    logger.info("remediation_generation_completed", **metadata)
    return proposal, metadata


def generate_remediation_proposal(incident: IncidentContext, hypotheses: list[Hypothesis], diagnosis: Diagnosis,
                                  knowledge_documents: list[KnowledgeDocument], *, return_metadata: bool = False):
    proposal, metadata = _generate(incident, hypotheses, diagnosis, knowledge_documents)
    return (proposal, metadata) if return_metadata else proposal
