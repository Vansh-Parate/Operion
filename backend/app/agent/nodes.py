from typing import Any
import requests
import structlog

from app.agent.planner import create_remediation_plan
from app.agent.completeness import assess_investigation_completeness
from app.agent.models import RemediationPlan, RemediationProposal
from app.agent.proposal_policy import validate_remediation_proposal
from app.agent.remediation_generator import generate_remediation_proposal
from app.agent.investigation_models import ToolCallRecord
from app.agent.investigation_tools import execute_investigation_tool, merge_evidence
from app.agent.investigator import generate_investigation_decision
from app.agent.policy import validate_plan
from app.agent.state import IncidentState
from app.agent.tools import (
    KubernetesRemediationError, patch_deployment_environment_variable,
    verify_payment_service_recovery,
)
from app.models.incident import IncidentContext
from app.rag.knowledge import retrieve_operational_knowledge
from app.services.investigation import collect_incident_context
from app.services.diagnosis import diagnose_incident
from app.services.llm_diagnosis import diagnose_with_llm
from app.services.diagnosis_validation import validate_diagnosis

logger = structlog.get_logger(__name__)


def collect_evidence_node(state: IncidentState) -> dict[str, Any]:
    # Clear results tied to the previous evidence when retrying.
    incident = state.get("incident") if state.get("iteration", 0) == 0 else None
    previous = state.get("incident")
    target = {
        "namespace": state.get("target_namespace") or (previous.namespace if previous else "operion-sandbox"),
        "service": state.get("target_service") or (previous.service if previous else "payment-service"),
        "label_selector": state.get("target_label_selector") if "target_label_selector" in state
            else (previous.label_selector if previous else None),
        "deployment_name": state.get("target_deployment") if "target_deployment" in state
            else (previous.deployment_name if previous else None),
    }
    if incident is not None:
        # An explicitly supplied context owns its target, including on retries.
        target = {"namespace": incident.namespace, "service": incident.service,
                  "label_selector": incident.label_selector,
                  "deployment_name": incident.deployment_name}
    # Preserve the original no-argument collector call for the default target.
    default_target = target == {"namespace": "operion-sandbox", "service": "payment-service",
                                "label_selector": None, "deployment_name": None}
    return {
        "incident": incident if incident is not None else (
            collect_incident_context() if default_target else collect_incident_context(**target)),
        "iteration": state.get("iteration", 0) + 1,
        "diagnosis": None,
        "remediation_plan": None,
        "remediation_proposal": None,
        "proposal_decisions": [],
        "policy_decision": None,
        "approved": state.get("approved", False) is True and state.get("iteration", 0) == 0,
        "execution_result": None,
        "verification_result": None,
        "resolved": False,
        "hypotheses": [],
        "knowledge_documents": [],
        "next_tool": None,
        "next_tool_reason": None,
        "tool_history": [],
        "investigation_iteration": 0,
        "sufficient_evidence": False,
        "investigation_complete": False,
        "investigation_blocked_reason": None,
        "investigation_model_failed": False,
        "target_namespace": target["namespace"],
        "target_service": target["service"],
        "target_label_selector": target["label_selector"],
        "target_deployment": target["deployment_name"],
    }


def generate_hypotheses_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Investigation requires collected incident evidence.")
    try:
        decision = generate_investigation_decision(incident, state.get("tool_history", []))
    except Exception as exc:
        # A model failure must not trigger another investigation loop. The
        # completeness gate remains authoritative for all successful calls.
        logger.info("model_stage_fallback", stage="investigation", status=
                    "timeout" if isinstance(exc, requests.Timeout) else "invalid",
                    fallback_used=False)
        return {"hypotheses": state.get("hypotheses", []), "next_tool": None,
                "next_tool_reason": None, "sufficient_evidence": False,
                "investigation_model_failed": True}
    return {"hypotheses": decision.hypotheses, "next_tool": decision.next_tool,
            "next_tool_reason": decision.next_tool_reason,
            "sufficient_evidence": decision.sufficient_evidence}


def check_investigation_completeness_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Completeness check requires collected incident evidence.")
    if state.get("investigation_model_failed"):
        return {"investigation_complete": False,
                "investigation_blocked_reason": "Investigation model failed; completeness could not be established.",
                "sufficient_evidence": False, "next_tool": None, "next_tool_reason": None}
    assessment = assess_investigation_completeness(incident, state.get("tool_history", []))
    if assessment.complete:
        return {"investigation_complete": True, "investigation_blocked_reason": None}
    if assessment.next_tool is not None:
        return {"investigation_complete": False, "investigation_blocked_reason": assessment.reason,
                "sufficient_evidence": False, "next_tool": assessment.next_tool,
                "next_tool_reason": assessment.reason}
    return {"investigation_complete": False, "investigation_blocked_reason": assessment.reason,
            "sufficient_evidence": False, "next_tool": None, "next_tool_reason": None}


def investigate_with_tool_node(state: IncidentState) -> dict[str, Any]:
    tool_name = state.get("next_tool")
    if tool_name is None:
        return {}
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Investigation requires collected incident evidence.")
    reason = state.get("next_tool_reason") or "Additional evidence requested."
    history = list(state.get("tool_history", []))
    try:
        updated = merge_evidence(incident, execute_investigation_tool(tool_name, incident))
        history.append(ToolCallRecord(tool=tool_name, reason=reason, success=True,
                                      evidence_added=len(updated.evidence) - len(incident.evidence)))
    except Exception:
        updated = incident
        history.append(ToolCallRecord(tool=tool_name, reason=reason, success=False))
    return {"incident": updated, "tool_history": history,
            "investigation_iteration": state.get("investigation_iteration", 0) + 1,
            "next_tool": None, "next_tool_reason": None}


def retrieve_knowledge_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Knowledge retrieval requires collected incident evidence.")
    return {"knowledge_documents": retrieve_operational_knowledge(incident, state.get("hypotheses", []))}


def diagnose_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    if incident is None:
        raise ValueError("Diagnosis requires collected incident evidence.")
    try:
        diagnosis = diagnose_with_llm(incident, knowledge_documents=state.get("knowledge_documents", []))
    except Exception as exc:
        deterministic = validate_diagnosis(incident, diagnose_incident(incident))
        supported = deterministic.root_cause not in {None, "unknown"}
        logger.info("model_stage_fallback", stage="diagnosis",
                    status="timeout" if isinstance(exc, requests.Timeout) else "invalid",
                    fallback_used=supported)
        diagnosis = deterministic if supported else deterministic.model_copy(update={
            "root_cause": "unknown", "confidence": 0.0,
            "summary": "Diagnosis model failed and deterministic evidence is insufficient.",
            "supporting_evidence": [], "recommended_actions": [],
        })
    diagnosis = validate_diagnosis(incident, diagnosis)
    return {"diagnosis": diagnosis, "resolved": diagnosis.root_cause == "no_active_incident"}


def generate_remediation_proposal_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    diagnosis = state.get("diagnosis")
    if incident is None or diagnosis is None:
        raise ValueError("Remediation proposal requires incident evidence and diagnosis.")
    try:
        generated = generate_remediation_proposal(
            incident, state.get("hypotheses", []), diagnosis,
            state.get("knowledge_documents", []), return_metadata=True)
        if isinstance(generated, tuple):
            proposal, metadata = generated
        else:
            proposal, metadata = generated, {"status": "llm_success", "fallback_used": False}
    except Exception:
        proposal = RemediationProposal(abstain=True,
                                       abstain_reason="Remediation generation failed safely.")
        metadata = {"status": "failed", "fallback_used": False}
    proposal = proposal.model_copy(update={
        "generation_status": metadata.get("status", "unknown"),
        "generation_latency_ms": metadata.get("latency_ms", 0),
        "fallback_used": metadata.get("fallback_used", False),
    })
    return {"remediation_proposal": proposal}


def validate_remediation_proposal_node(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    proposal = state.get("remediation_proposal")
    if incident is None or proposal is None:
        raise ValueError("Proposal validation requires incident evidence and a proposal.")
    try:
        checked, decisions = validate_remediation_proposal(
            proposal, incident, state.get("knowledge_documents", []))
    except Exception:
        checked = RemediationProposal(abstain=True,
                                      abstain_reason="Proposal validation failed safely.")
        decisions = []
    return {"remediation_proposal": checked, "proposal_decisions": decisions}


def plan_remediation_node(state: IncidentState) -> dict[str, Any]:
    diagnosis = state.get("diagnosis")
    if diagnosis is None:
        raise ValueError("Remediation planning requires a diagnosis.")
    legacy = create_remediation_plan(diagnosis)
    if "remediation_proposal" not in state or legacy.action == "patch_environment_variable":
        # The existing payment-service repair still passes its strict policy gate.
        return {"remediation_plan": legacy}
    proposal = state.get("remediation_proposal")
    if proposal is not None and proposal.candidates:
        reason = "Structured remediation candidates are recommendations only; no generic execution is available."
    elif proposal is not None and proposal.abstain:
        reason = proposal.abstain_reason or "No safe remediation was proposed."
    else:
        reason = legacy.reason
    return {"remediation_plan": RemediationPlan(action="none", reason=reason,
                                                requires_approval=False)}


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
    elif (state.get("incident") is not None and (
            state["incident"].namespace != "operion-sandbox"
            or state["incident"].service != "payment-service"
            or (state["incident"].deployment_name or state["incident"].service) != "payment-service")):
        reason = "The selected incident is outside the supported remediation target."
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
