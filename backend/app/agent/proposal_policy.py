"""Validate proposal identity and classify execution without adding writes."""

import re

from app.agent.models import ProposalDecision, RemediationCandidate, RemediationProposal
from app.agent.policy import has_inc001_remediation_evidence
from app.agent.risk import classify_risk
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument


PARAMETER_KEYS = {
    "patch_environment_variable": {"name"},
    "update_container_resources": {"resource", "direction"},
    "update_probe": {"probe_type", "path"},
    "patch_service_selector": {"selector"},
    "scale_workload": {"replicas"},
    "restart_workload": set(),
    "rollback_workload": {"revision"},
    "patch_configmap": {"key"},
    "none": set(),
    "unsupported": set(),
}
TARGET_KINDS = {
    "patch_environment_variable": {"Deployment"},
    "update_container_resources": {"Deployment", "StatefulSet", "DaemonSet"},
    "update_probe": {"Deployment", "StatefulSet", "DaemonSet"},
    "patch_service_selector": {"Service"},
    "scale_workload": {"Deployment", "StatefulSet"},
    "restart_workload": {"Deployment", "StatefulSet", "DaemonSet"},
    "rollback_workload": {"Deployment"},
    "patch_configmap": {"ConfigMap"},
}
FORBIDDEN_TEXT = re.compile(
    r"\b(?:kubectl|helm|bash|powershell|cmd\.exe|sudo|curl|wget)\b|```|\$\(|"
    r"\b(?:rm\s+-|sh\s+-c|python\s+-c|apiVersion\s*:)" 
    r"|;\s*(?:rm|kubectl)", re.I)
HIGH_RISK_TEXT = re.compile(r"\b(?:secret|rbac|persistentvolume(?:claim)?|privileged|database)\b|\b(?:password|token|credential)\s*[:=]", re.I)


def _observed_names(incident: IncidentContext) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {"Deployment": set(), "Service": set(),
                                  "ConfigMap": set(), "StatefulSet": set(), "DaemonSet": set()}
    if incident.deployment_name:
        names["Deployment"].add(incident.deployment_name)
    for evidence in incident.evidence:
        data = evidence.data
        if evidence.category == "deployment_config" and isinstance(data.get("deployment_name"), str):
            names["Deployment"].add(data["deployment_name"])
        elif evidence.category == "service_config" and isinstance(data.get("name"), str):
            names["Service"].add(data["name"])
        elif evidence.category == "configmap" and isinstance(data.get("name"), str):
            names["ConfigMap"].add(data["name"])
    return names


def classify_candidate(candidate: RemediationCandidate, incident: IncidentContext,
                       knowledge_documents: list[KnowledgeDocument]) -> ProposalDecision:
    operation = candidate.operation
    risk_order = {"low": 0, "medium": 1, "high": 2}
    deterministic_risk = classify_risk(operation)
    risk = deterministic_risk
    def decision(status, reason):
        return ProposalDecision(status=status, risk_level=risk, reason=reason,
                                executable_now=status == "supported_and_allowed")
    if operation.operation in {"none", "unsupported"}:
        return decision("abstain", "No actionable structured operation was proposed.")
    if not operation.evidence_ids or any(
        re.fullmatch(r"E[1-9][0-9]*", ref) is None
        or int(ref[1:]) > len(incident.evidence) for ref in operation.evidence_ids):
        return decision("denied", "Candidate lacks valid live E# evidence references.")
    if any(re.fullmatch(r"K[1-9][0-9]*", ref) is None
           or int(ref[1:]) > len(knowledge_documents) for ref in operation.knowledge_ids):
        return decision("denied", "Candidate cites an unavailable K# knowledge item.")
    target = operation.target
    if target is None or target.namespace != incident.namespace:
        return decision("denied", "Target must be in the active incident namespace.")
    if target.kind not in TARGET_KINDS.get(operation.operation, set()):
        return decision("denied", "Operation is not valid for the proposed resource kind.")
    if target.name not in _observed_names(incident).get(target.kind, set()):
        return decision("denied", "Target name was not observed in incident context or evidence.")
    if target.container is not None:
        if target.kind in {"Service", "ConfigMap"}:
            return decision("denied", "This resource kind cannot target a container.")
        observed_containers = {container.get("name") for evidence in incident.evidence
                               if evidence.category == "deployment_config"
                               for container in evidence.data.get("containers") or []
                               if isinstance(container, dict)}
        if target.container not in observed_containers:
            return decision("denied", "Target container was not observed in Deployment evidence.")
    parameters = operation.parameters
    if set(parameters) - PARAMETER_KEYS[operation.operation]:
        return decision("denied", "Parameters include unsupported or patch-like fields.")
    if operation.operation == "patch_environment_variable" and not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", str(parameters.get("name", ""))):
        return decision("denied", "Environment variable name is invalid.")
    if operation.operation == "patch_service_selector":
        selector = parameters.get("selector")
        labels = {key: value for evidence in incident.evidence
                  if evidence.category == "deployment_config"
                  for key, value in (evidence.data.get("pod_labels") or {}).items()}
        if not isinstance(selector, dict) or not selector or not all(
            isinstance(key, str) and isinstance(value, str) and labels.get(key) == value
            for key, value in selector.items()):
            return decision("denied", "Proposed selector must match observed Pod template labels.")
    if operation.operation == "update_container_resources" and (
        parameters.get("resource") not in {"cpu", "memory"}
        or parameters.get("direction") not in {"increase", "decrease"}):
        return decision("denied", "Resource intent must specify CPU or memory and a direction.")
    if operation.operation == "update_probe" and parameters.get("probe_type") not in {
        "readiness", "liveness", "startup"}:
        return decision("denied", "Probe type is required.")
    if operation.operation == "update_probe" and "path" in parameters:
        path = parameters["path"]
        observed = " ".join(str(evidence.data) for evidence in incident.evidence)
        if not isinstance(path, str) or not path.startswith("/") or path not in observed:
            return decision("denied", "Probe path must be observed in live evidence.")
    if operation.operation == "scale_workload" and (
        type(parameters.get("replicas")) is not int or not 0 <= parameters["replicas"] <= 100):
        return decision("denied", "Replica count is invalid.")
    if operation.operation == "rollback_workload" and (
        type(parameters.get("revision")) is not int or parameters["revision"] < 1):
        return decision("denied", "Rollback revision is invalid.")
    if operation.operation == "patch_configmap":
        key = parameters.get("key")
        observed_keys = {name for evidence in incident.evidence
                         if evidence.category == "configmap" and evidence.data.get("name") == target.name
                         for name in evidence.data.get("data") or {}}
        if not isinstance(key, str) or key not in observed_keys:
            return decision("denied", "ConfigMap key must be observed; values are not accepted.")
    text = " ".join([operation.reason, candidate.expected_effect,
                     *candidate.verification_strategy, str(parameters)])
    if FORBIDDEN_TEXT.search(text):
        return decision("denied", "Commands or scripts are forbidden in remediation proposals.")
    if HIGH_RISK_TEXT.search(text):
        return decision("denied", "Sensitive or data-risk changes are outside the planning boundary.")
    if risk == "high":
        return decision("denied", "High-risk resource changes are outside the planning boundary.")
    if (operation.operation == "patch_environment_variable" and target.kind == "Deployment"
            and target.name == "payment-service" and target.container in (None, "payment-service")
            and parameters.get("name") == "PAYMENT_PROVIDER_URL"
            and has_inc001_remediation_evidence(incident)):
        return decision("supported_and_allowed", "Existing payment-service env repair allowlist applies.")
    return decision("known_but_not_executable_yet", "Structured recommendation only; no executor exists for this operation.")


def validate_remediation_proposal(proposal: RemediationProposal, incident: IncidentContext,
                                  knowledge_documents: list[KnowledgeDocument]) -> tuple[RemediationProposal, list[ProposalDecision]]:
    decisions = [classify_candidate(item, incident, knowledge_documents)
                 for item in proposal.candidates]
    candidates = [item.model_copy(update={"risk_level": decision.risk_level})
                  for item, decision in zip(proposal.candidates, decisions)]
    return proposal.model_copy(update={"candidates": candidates}), decisions
