import re

from app.agent.models import PolicyDecision, RemediationPlan
from app.models.incident import IncidentContext


ALLOWED_ENV_UPDATES = {
    "PAYMENT_PROVIDER_URL": "http://payment-provider.local",
}
ALLOWED_READINESS_PATHS = {"/health"}
MAX_MEMORY_MIB = 1024


def has_inc001_remediation_evidence(incident: IncidentContext | None) -> bool:
    """Require direct, current evidence for the missing payment provider variable."""
    if (incident is None or incident.service != "payment-service"
            or incident.namespace != "operion-sandbox"):
        return False

    deployment_seen = False
    deployment_present = False
    config_error = False
    failing_pod = False
    for evidence in incident.evidence:
        if (evidence.service != "payment-service"
                or evidence.namespace != "operion-sandbox"):
            continue
        data = evidence.data
        if evidence.category == "deployment_config" and evidence.source == "kubernetes":
            if data.get("deployment_name") != "payment-service":
                continue
            containers = data.get("containers")
            if not isinstance(containers, list):
                continue
            target = next((container for container in containers
                           if isinstance(container, dict)
                           and container.get("name") == "payment-service"), None)
            if target is None or not isinstance(target.get("environment_variables"), list):
                continue
            deployment_seen = True
            deployment_present = deployment_present or any(
                isinstance(env, dict) and env.get("name") == "PAYMENT_PROVIDER_URL"
                for env in target["environment_variables"]
            )
        elif evidence.category == "application_log" and evidence.source == "logs":
            logs = data.get("logs")
            config_error = config_error or (
                isinstance(logs, str) and any(
                    "CONFIG_ERROR" in line and "PAYMENT_PROVIDER_URL" in line
                    for line in logs.splitlines()
                )
            )
        elif evidence.category == "pod_status" and evidence.source == "kubernetes":
            failing_pod = failing_pod or (
                data.get("ready") is False and (
                    data.get("waiting_reason") == "CrashLoopBackOff"
                    or data.get("current_state") == "terminated"
                    or data.get("phase") == "Failed"
                    or (isinstance(data.get("last_exit_code"), int)
                        and data["last_exit_code"] != 0)
                )
            )
    return deployment_seen and not deployment_present and config_error and failing_pod


def parse_mib(value: str) -> int:
    """Accept only positive integer Kubernetes memory quantities in Mi."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+Mi", value) is None:
        raise ValueError("Memory limit must be a positive integer followed by Mi.")
    mib = int(value[:-2])
    if mib <= 0:
        raise ValueError("Memory limit must be greater than zero.")
    return mib


def validate_plan(
    plan: RemediationPlan,
    current_memory_limit: str | None = None,
    incident: IncidentContext | None = None,
) -> PolicyDecision:
    # LLM chooses remediation intent; deterministic policy code chooses safe concrete parameters.
    if plan.action == "none":
        return PolicyDecision(allowed=True, reason="No mutation requested.")

    if not plan.evidence_ids or not any(item.strip() for item in plan.evidence_ids):
        return PolicyDecision(allowed=False, reason="Remediation has no supporting evidence.")

    if plan.action == "patch_environment_variable":
        name = plan.parameters.get("name")
        if not isinstance(name, str) or name not in ALLOWED_ENV_UPDATES:
            return PolicyDecision(allowed=False, reason="Environment variable is not allowlisted.")
        if not has_inc001_remediation_evidence(incident):
            return PolicyDecision(
                allowed=False,
                reason="INC-001 remediation requires missing Deployment variable, CONFIG_ERROR log, and failing pod evidence.",
            )
        return PolicyDecision(
            allowed=True,
            reason="Environment variable update is allowlisted.",
            normalized_parameters={"name": name, "value": ALLOWED_ENV_UPDATES[name]},
        )

    if plan.action == "update_memory_limit":
        if current_memory_limit is None:
            return PolicyDecision(allowed=False, reason="Current memory limit is required.")
        try:
            current_mib = parse_mib(current_memory_limit)
        except ValueError:
            return PolicyDecision(allowed=False, reason="Current memory limit is invalid.")
        new_mib = current_mib * 2
        if new_mib > MAX_MEMORY_MIB:
            return PolicyDecision(
                allowed=False, reason=f"Doubled memory limit exceeds {MAX_MEMORY_MIB}Mi."
            )
        return PolicyDecision(
            allowed=True,
            reason="Memory limit doubled within the policy maximum.",
            normalized_parameters={"memory_limit": f"{new_mib}Mi"},
        )

    if plan.action == "update_readiness_probe":
        requested_path = plan.parameters.get("path", "/health")
        if not isinstance(requested_path, str) or requested_path not in ALLOWED_READINESS_PATHS:
            return PolicyDecision(allowed=False, reason="Readiness path is not allowlisted.")
        return PolicyDecision(
            allowed=True,
            reason="Readiness path is allowlisted.",
            normalized_parameters={"path": requested_path},
        )

    if plan.action == "restart_deployment":
        return PolicyDecision(allowed=True, reason="Deployment restart has supporting evidence.")

    return PolicyDecision(allowed=False, reason="Unknown remediation action.")
