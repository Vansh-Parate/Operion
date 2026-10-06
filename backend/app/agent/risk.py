"""Deterministic floor for remediation risk; LLM labels never lower it."""

from app.agent.models import RemediationOperation, RiskLevel


HIGH_RISK_KINDS = {
    "secret", "role", "clusterrole", "rolebinding", "clusterrolebinding",
    "persistentvolume", "persistentvolumeclaim", "node", "namespace",
    "customresourcedefinition", "database",
}


def classify_risk(operation: RemediationOperation) -> RiskLevel:
    kind = operation.target.kind.lower() if operation.target else ""
    parameter_names = " ".join(str(value).lower() for value in operation.parameters.values())
    if (kind in HIGH_RISK_KINDS or operation.operation == "unsupported"
            or any(marker in parameter_names for marker in ("secret", "password", "token", "credential"))):
        return "high"
    if operation.operation == "patch_environment_variable":
        target = operation.target
        if (target is not None and target.kind == "Deployment"
                and target.namespace == "operion-sandbox" and target.name == "payment-service"
                and operation.parameters.get("name") == "PAYMENT_PROVIDER_URL"):
            return "low"
        return "medium"
    if operation.operation in {"none", "restart_workload"}:
        return "low"
    # Probe corrections require evidence of the correct setting; until then
    # treat them as medium risk even though they can often be reversed.
    return "medium"
