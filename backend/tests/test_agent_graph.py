from unittest.mock import Mock

import pytest
from langgraph.graph import END

from app.agent import nodes
from app.agent import run
from app.agent.graph import (
    build_incident_graph, route_after_approval, route_after_policy, route_after_verify,
)
from app.agent.models import PolicyDecision, RemediationPlan, RemediationProposal
from app.agent.investigation_models import InvestigationDecision
from app.agent.planner import create_remediation_plan
from app.agent.state import IncidentState
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext


@pytest.fixture
def incident() -> IncidentContext:
    return IncidentContext(
        incident_id="INC-TEST", service="payment-service", namespace="operion-sandbox",
        evidence=[Evidence(
            source="kubernetes", category="deployment_config", summary="Deployment config",
            service="payment-service", namespace="operion-sandbox",
            data={"deployment_name": "payment-service", "containers": [{
                "name": "payment-service", "environment_variables": [], "resources": {
                "limits": {"memory": "128Mi"},
            }}]},
        ), Evidence(
            source="logs", category="application_log", summary="Config error",
            service="payment-service", namespace="operion-sandbox",
            data={"logs": "CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required"},
        ), Evidence(
            source="kubernetes", category="pod_status", summary="Crashing pod",
            service="payment-service", namespace="operion-sandbox",
            data={"pod_name": "payment-1", "ready": False,
                  "current_state": "waiting", "waiting_reason": "CrashLoopBackOff"},
        )],
    )


@pytest.fixture
def diagnosis() -> Diagnosis:
    return Diagnosis(root_cause="missing_environment_variable", confidence=0.9,
                     summary="Configuration missing", supporting_evidence=["E1: Required config missing"])


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch, incident: IncidentContext,
            diagnosis: Diagnosis) -> tuple[Mock, Mock]:
    collect = Mock(return_value=incident)
    diagnose = Mock(return_value=diagnosis)
    monkeypatch.setattr(nodes, "collect_incident_context", collect)
    monkeypatch.setattr(nodes, "diagnose_with_llm", diagnose)
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    monkeypatch.setattr(nodes, "generate_remediation_proposal", Mock(return_value=RemediationProposal(
        abstain=True, abstain_reason="Offline test fallback")))
    monkeypatch.setattr(nodes, "check_investigation_completeness_node",
                        Mock(return_value={"investigation_complete": True}))
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(
        sufficient_evidence=True,
    )))
    return collect, diagnose


@pytest.mark.parametrize("root_cause, action, parameters, approval", [
    ("missing_environment_variable", "patch_environment_variable", {"name": "PAYMENT_PROVIDER_URL"}, True),
    ("container_memory_limit_exceeded", "update_memory_limit", {}, True),
    ("readiness_probe_failure", "update_readiness_probe", {"path": "/health"}, True),
    ("redis_dependency_unavailable", "none", {}, False),
    ("unknown", "none", {}, False),
    (None, "none", {}, False),
    ("unsupported", "none", {}, False),
])
def test_planner_mapping(root_cause: str | None, action: str,
                         parameters: dict, approval: bool) -> None:
    diagnosis = Diagnosis(root_cause=root_cause, summary="Observed",
                          supporting_evidence=["E2: Fact", "E3: Another fact; E2 referenced"])
    plan = create_remediation_plan(diagnosis)
    assert plan.action == action
    assert plan.parameters == parameters
    assert plan.requires_approval is approval
    assert plan.evidence_ids == ["E2", "E3"]


def test_planner_does_not_invent_evidence_ids() -> None:
    diagnosis = Diagnosis(root_cause="missing_environment_variable", summary="Missing config",
                          supporting_evidence=["Missing config without evidence ID", "E0: Invalid", "XE1"])
    assert create_remediation_plan(diagnosis).evidence_ids == []


def test_approved_graph_executes_and_verifies(offline: tuple[Mock, Mock], incident: IncidentContext,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    patch = Mock(return_value={"success": True, "action": "patch_environment_variable"})
    verify = Mock(return_value={"recovered": True, "pod_name": "new-pod"})
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    monkeypatch.setattr(nodes, "verify_payment_service_recovery", verify)
    state = build_incident_graph().invoke({"approved": True})
    assert state["iteration"] == 1
    assert state["resolved"] is True
    assert state["execution_result"] == {"success": True, "action": "patch_environment_variable"}
    assert state["verification_result"]["recovered"] is True
    patch.assert_called_once_with(namespace="operion-sandbox", deployment_name="payment-service",
                                  container_name="payment-service", env_name="PAYMENT_PROVIDER_URL",
                                  env_value="http://payment-provider.local")
    verify.assert_called_once_with(namespace="operion-sandbox")
    offline[0].assert_called_once_with()
    offline[1].assert_called_once_with(incident, knowledge_documents=[])


def test_graph_retry_requires_fresh_approval(offline: tuple[Mock, Mock],
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    patch = Mock(return_value={"success": True, "action": "patch_environment_variable"})
    verify = Mock(return_value={"recovered": False, "reason": "Not ready"})
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    monkeypatch.setattr(nodes, "verify_payment_service_recovery", verify)
    state = build_incident_graph().invoke({"approved": True})
    assert state["iteration"] == 2
    assert state["resolved"] is False
    assert state["approved"] is False
    assert state["policy_decision"].allowed is True
    assert state["execution_result"] is None
    assert patch.call_count == verify.call_count == 1
    assert offline[0].call_count == 2


@pytest.mark.parametrize("approved", [False, None])
def test_unapproved_graph_stops(offline: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch,
                               approved: bool | None) -> None:
    execute = Mock(side_effect=AssertionError("Execution must not run"))
    monkeypatch.setattr(nodes, "execute_node", execute)
    initial = {} if approved is None else {"approved": approved}
    state = build_incident_graph().invoke(initial)
    assert state["approved"] is False
    assert state["execution_result"] is None
    assert state["verification_result"] is None
    execute.assert_not_called()


def test_denied_policy_graph_ends(offline: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    offline[1].return_value = Diagnosis(root_cause="missing_environment_variable", summary="No IDs")
    approval = Mock(side_effect=AssertionError("Approval must not run"))
    monkeypatch.setattr(nodes, "approval_node", approval)
    state = build_incident_graph().invoke({"approved": True})
    assert state["policy_decision"].allowed is False
    assert state["execution_result"] is None
    approval.assert_not_called()


def test_none_graph_ends(offline: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch) -> None:
    offline[1].return_value = Diagnosis(root_cause="redis_dependency_unavailable", summary="Redis down")
    approval = Mock(side_effect=AssertionError("Approval must not run"))
    monkeypatch.setattr(nodes, "approval_node", approval)
    state = build_incident_graph().invoke({"approved": True})
    assert state["remediation_plan"].action == "none"
    assert state["execution_result"] is None
    assert state["resolved"] is False
    approval.assert_not_called()


def test_executor_uses_only_normalized_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = Mock(return_value={"success": True, "action": "patch_environment_variable"})
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    state: IncidentState = {  # Partial graph state supplied directly for node testing.
        "remediation_plan": RemediationPlan(
            action="patch_environment_variable", reason="Config", evidence_ids=["E1"],
            parameters={"name": "SECRET", "value": "unsafe"},
        ),
        "policy_decision": PolicyDecision(allowed=True, reason="Allowed", normalized_parameters={
            "name": "PAYMENT_PROVIDER_URL", "value": "http://payment-provider.local",
        }),
        "approved": True,
    }
    result = nodes.execute_node(state)["execution_result"]
    assert result["success"] is True
    assert patch.call_args.kwargs["env_name"] == "PAYMENT_PROVIDER_URL"
    assert patch.call_args.kwargs["env_value"] == "http://payment-provider.local"


@pytest.mark.parametrize("allowed, approved, action", [
    (False, True, "restart_deployment"), (True, False, "restart_deployment"), (True, True, "none"),
])
def test_executor_safety_guards(allowed: bool, approved: bool, action: str) -> None:
    state = {"approved": approved, "policy_decision": PolicyDecision(allowed=allowed, reason="Test"),
             "remediation_plan": RemediationPlan(action=action, reason="Test")}
    result = nodes.execute_node(state)["execution_result"]
    assert result["success"] is False
    assert result["reason"]


def test_empty_execution_state_is_safe() -> None:
    assert nodes.execute_node({})["execution_result"]["success"] is False


def test_verification_without_supported_execution() -> None:
    result = nodes.verify_node({"execution_result": None})
    assert result["resolved"] is False
    assert result["verification_result"]["recovered"] is False


@pytest.mark.parametrize("iteration, resolved, destination", [
    (1, False, "collect_evidence"), (2, False, "collect_evidence"),
    (3, False, END), (4, False, END), (1, True, END),
])
def test_verification_routing(iteration: int, resolved: bool, destination: str) -> None:
    assert route_after_verify({"iteration": iteration, "resolved": resolved}) == destination


def test_missing_route_state_fails_closed() -> None:
    assert route_after_policy({}) == END
    assert route_after_approval({}) == END


def test_failed_execution_retries_without_reapproval(offline: tuple[Mock, Mock],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    execute = Mock(return_value={"execution_result": {
        "success": False, "simulated": True, "reason": "Simulated failure",
    }})
    monkeypatch.setattr(nodes, "execute_node", execute)
    state = build_incident_graph().invoke({"approved": True})
    assert state["iteration"] == 2
    assert state["resolved"] is False
    assert state["approved"] is False
    assert state["verification_result"] is None
    assert offline[0].call_count == offline[1].call_count == 2
    assert execute.call_count == 1


def test_retry_clears_stale_execution_before_new_denial(offline: tuple[Mock, Mock],
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      diagnosis: Diagnosis) -> None:
    offline[1].side_effect = [diagnosis, Diagnosis(root_cause="unknown", summary="Insufficient evidence")]
    monkeypatch.setattr(nodes, "execute_node", lambda state: {"execution_result": {
        "success": False, "simulated": True,
    }})
    state = build_incident_graph().invoke({"approved": True})
    assert state["iteration"] == 2
    assert state["execution_result"] is None
    assert state["verification_result"] is None
    assert state["approved"] is False


def test_memory_extraction_and_policy(incident: IncidentContext) -> None:
    assert nodes.extract_current_memory_limit(incident) == "128Mi"
    plan = RemediationPlan(action="update_memory_limit", reason="OOM", evidence_ids=["E1"])
    decision = nodes.validate_policy_node({"incident": incident, "remediation_plan": plan})["policy_decision"]
    assert decision.allowed
    assert decision.normalized_parameters == {"memory_limit": "256Mi"}


def test_missing_memory_fails_closed(incident: IncidentContext) -> None:
    incident.evidence = []
    assert nodes.extract_current_memory_limit(incident) is None
    plan = RemediationPlan(action="update_memory_limit", reason="OOM", evidence_ids=["E1"])
    assert not nodes.validate_policy_node({"incident": incident, "remediation_plan": plan})["policy_decision"].allowed


def test_memory_extraction_ignores_sidecar(incident: IncidentContext) -> None:
    incident.evidence[0].data["containers"].insert(0, {
        "name": "sidecar", "resources": {"limits": {"memory": "16Mi"}},
    })
    assert nodes.extract_current_memory_limit(incident) == "128Mi"


@pytest.mark.parametrize("node, message", [
    (nodes.diagnose_node, "incident evidence"),
    (nodes.plan_remediation_node, "diagnosis"),
    (nodes.validate_policy_node, "remediation plan"),
])
def test_missing_node_inputs_raise_useful_errors(node, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        node({})


@pytest.mark.parametrize("approve", [False, True])
def test_runner_labels_real_execution_and_defaults_to_no_approval(
    offline: tuple[Mock, Mock], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], approve: bool,
) -> None:
    patch = Mock(return_value={"success": True, "action": "patch_environment_variable"})
    verify = Mock(return_value={"recovered": True, "pod_name": "new-pod"})
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    monkeypatch.setattr(nodes, "verify_payment_service_recovery", verify)
    monkeypatch.setattr("sys.argv", ["app.agent.run"] + (["--approve"] if approve else []))
    run.main()
    output = capsys.readouterr().out
    assert "REAL KUBERNETES MUTATIONS MAY OCCUR WHEN --approve IS USED." in output
    assert f"Execution happened: {approve}" in output
    assert f"Resolved: {approve}" in output
    assert ("Proposed action: patch_environment_variable" in output) is approve
    assert patch.call_count == int(approve)
