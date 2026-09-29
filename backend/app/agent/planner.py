import re

from app.agent.models import RemediationAction, RemediationPlan
from app.models.diagnosis import Diagnosis


def create_remediation_plan(diagnosis: Diagnosis) -> RemediationPlan:
    evidence_ids = list(dict.fromkeys(
        evidence_id
        for evidence in diagnosis.supporting_evidence
        for evidence_id in re.findall(r"\bE[1-9][0-9]*\b", evidence)
    ))
    action: RemediationAction
    parameters: dict[str, str] = {}
    if diagnosis.root_cause == "missing_environment_variable":
        action = "patch_environment_variable"
        reason = "Required payment provider configuration is missing."
        parameters = {"name": "PAYMENT_PROVIDER_URL"}
    elif diagnosis.root_cause == "container_memory_limit_exceeded":
        action = "update_memory_limit"
        reason = "The container exceeded its memory limit."
    elif diagnosis.root_cause == "readiness_probe_failure":
        action = "update_readiness_probe"
        reason = "Readiness probe configuration is failing."
        parameters = {"path": "/health"}
    elif diagnosis.root_cause == "redis_dependency_unavailable":
        action = "none"
        reason = (
            "Redis unavailability has multiple possible causes; there is insufficient "
            "evidence for a safe automatic mutation."
        )
    else:
        action = "none"
        reason = "No safe remediation is available for this diagnosis."

    # The planner selects intent; policy supplies safe concrete values.
    return RemediationPlan(
        action=action, reason=reason, evidence_ids=evidence_ids,
        parameters=parameters, requires_approval=action != "none",
    )
