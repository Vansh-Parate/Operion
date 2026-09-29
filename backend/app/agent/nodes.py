from typing import Any

from app.agent.planner import create_remediation_plan
from app.agent.policy import validate_plan
from app.agent.state import IncidentState
from app.agent.tools import (
    KubernetesRemediationError, patch_deployment_environment_variable,
    verify_payment_service_recovery,
)
from app.models.incident import IncidentContext
from app.services.investigation import collect_incident_context
from app.services.llm_diagnosis import diagnose_with_llm


def collect_evidence_node(state: IncidentState) -> dict[str, Any]:
    # Clear results tied to the previous evidence when retrying.
    return {
        "incident": collect_incident_context(),
        "iteration": state.get("iteration", 0) + 1,
        "diagnosis": None,
        "remediation_plan": None,
        "policy_decision": None,
        "approved": state.get("approved", False) is True and state.get("iteration", 0) == 0,
        "execution_result": None,
        "verification_result": None,
        "resolved": False,
    }


def diagnose_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Diagnosis requires collected incident evidence.")
    return {"diagnosis": diagnose_with_llm(incident)}


def plan_remediation_node(state: IncidentState) -> dict[str, Any]:
    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        raise ValueError("Remediation planning requires a diagnosis.")
    return {"remediation_plan": create_remediation_plan(diagnosis)}


def extract_current_memory_limit(incident: IncidentContext) -> str | None:
    limits: set[str] = set()
    for evidence in incident.evidence:
        if evidence.category != "deployment_config":
            continue
        containers = evidence.data.get("containers", [])
        for container in containers:
            # Never use a sidecar's memory limit for the incident service.
            if container.get("name") != incident.service and len(containers) != 1:
                continue
            memory = container.get("resources", {}).get("limits", {}).get("memory")
            if isinstance(memory, str):
                limits.add(memory)
    return next(iter(limits)) if len(limits) == 1 else None


def validate_policy_node(state: IncidentState) -> dict[str, Any]:
    plan = state.get("remediation_plan")
    if plan is None:
        raise ValueError("Policy validation requires a remediation plan.")
    incident = state.get("incident")
    memory = (
        extract_current_memory_limit(incident)
        if plan.action == "update_memory_limit" and incident is not None else None
    )
    return {"policy_decision": validate_plan(plan, current_memory_limit=memory, incident=incident)}


def approval_node(state: IncidentState) -> dict[str, Any]:
    return {"approved": state.get("approved", False) is True}


def execute_node(state: IncidentState) -> dict[str, Any]:
    decision = state.get("policy_decision")
    plan = state.get("remediation_plan")
    if decision is None or not decision.allowed:
        reason = "Execution requires an allowed policy decision."
    elif state.get("approved", False) is not True:
        reason = "Execution requires approval."
    elif plan is None:
        reason = "Execution requires a remediation plan."
    elif plan.action != "patch_environment_variable":
        return {"execution_result": {
            "success": False, "supported": False, "action": plan.action,
            "reason": "Real execution is not implemented for this action yet.",
        }}
    else:
        parameters = decision.normalized_parameters
        if (parameters.get("name") != "PAYMENT_PROVIDER_URL"
                or parameters.get("value") != "http://payment-provider.local"):
            reason = "Policy parameters are invalid for the supported action."
        else:
            try:
                return {"execution_result": patch_deployment_environment_variable(
                    namespace="operion-sandbox", deployment_name="payment-service",
                    container_name="payment-service", env_name=parameters["name"],
                    env_value=parameters["value"],
                )}
            except KubernetesRemediationError as exc:
                reason = str(exc)
    return {"execution_result": {"success": False, "reason": reason}}


def verify_node(state: IncidentState) -> dict[str, Any]:
    execution = state.get("execution_result") or {}
    if execution.get("success") is True and execution.get("action") == "patch_environment_variable":
        try:
            result = verify_payment_service_recovery(namespace="operion-sandbox")
        except KubernetesRemediationError as exc:
            result = {"recovered": False, "reason": str(exc)}
    else:
        result = {"recovered": False, "reason": "No successful supported execution to verify."}
    return {
        "verification_result": result,
        "resolved": result["recovered"] is True,
        "approved": state.get("approved", False) is True if result["recovered"] is True else False,
    }
