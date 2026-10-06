import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.agent import nodes, remediation_generator
from app.agent.graph import build_incident_graph, route_after_policy
from app.agent.investigation_models import Hypothesis, InvestigationDecision
from app.agent.models import (RemediationCandidate, RemediationOperation,
                              RemediationProposal, ResourceTarget, RemediationPlan, PolicyDecision)
from langgraph.graph import END
from app.agent.proposal_policy import classify_candidate, validate_remediation_proposal
from app.agent.risk import classify_risk
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument
from app.rag import knowledge


@pytest.fixture
def selector_incident():
    return IncidentContext(incident_id="I", namespace="staging", service="payments-api",
                           deployment_name="payments-backend", evidence=[
        Evidence(source="kubernetes", category="service_config", summary="Service selects app=wrong",
                 data={"name": "payments-api", "selector": {"app": "wrong"}}),
        Evidence(source="kubernetes", category="deployment_config", summary="Pods use app=payments",
                 data={"deployment_name": "payments-backend", "pod_labels": {"app": "payments"},
                       "containers": [{"name": "api"}]}),
        Evidence(source="kubernetes", category="endpoints", summary="Service has no endpoints",
                 data={"ready_addresses": []}),
    ])


def candidate(operation="patch_service_selector", *, namespace="staging", kind="Service",
              name="payments-api", parameters=None, evidence_ids=None, knowledge_ids=None,
              risk="low"):
    return RemediationCandidate(operation=RemediationOperation(
        operation=operation, target=ResourceTarget(kind=kind, namespace=namespace, name=name),
        parameters=parameters if parameters is not None else {"selector": {"app": "payments"}},
        reason="Selector differs from observed Pod labels",
        evidence_ids=evidence_ids if evidence_ids is not None else ["E1", "E2", "E3"],
        knowledge_ids=knowledge_ids if knowledge_ids is not None else ["K1"]),
        expected_effect="Service gains ready endpoints", risk_level=risk, confidence=0.8,
        verification_strategy=["Service EndpointSlices have ready endpoints", "Pods remain Ready"])


def test_selector_mismatch_generates_structured_proposal(monkeypatch, selector_incident):
    doc = KnowledgeDocument(source_type="kubernetes_docs", title="Debug Services",
                            url="https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/",
                            content="Compare Service selectors and Pod labels")
    proposed = RemediationProposal(candidates=[candidate()], recommended_candidate_index=0)
    response = Mock()
    response.json.return_value = {"response": proposed.model_dump_json()}
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=response))
    result = remediation_generator.generate_remediation_proposal(
        selector_incident, [Hypothesis(cause="Service selector mismatch", confidence=0.9)],
        Diagnosis(root_cause="service_selector_mismatch", summary="Mismatch"), [doc])
    assert not result.abstain and result.recommended_candidate_index == 0
    chosen = result.candidates[0]
    assert chosen.operation.operation == "patch_service_selector"
    assert chosen.operation.evidence_ids == ["E1", "E2", "E3"]
    assert chosen.operation.knowledge_ids == ["K1"]
    assert chosen.risk_level == "medium"
    assert chosen.verification_strategy
    prompt = remediation_generator.requests.post.call_args.kwargs["json"]["prompt"]
    assert "E1:" in prompt and "K1 [kubernetes_docs]" in prompt
    assert "Never generate shell commands, kubectl" in prompt


def test_no_evidence_no_active_and_unknown_abstain_without_llm(monkeypatch, selector_incident):
    post = Mock(side_effect=AssertionError("No LLM request expected"))
    monkeypatch.setattr(remediation_generator.requests, "post", post)
    empty = selector_incident.model_copy(update={"evidence": []})
    assert remediation_generator.generate_remediation_proposal(
        empty, [], Diagnosis(root_cause="unknown", summary="Unknown"), []).abstain
    assert remediation_generator.generate_remediation_proposal(
        selector_incident, [], Diagnosis(root_cause="no_active_incident", summary="Healthy"), []).abstain
    assert remediation_generator.generate_remediation_proposal(
        selector_incident, [], Diagnosis(root_cause="unknown", summary="Unknown"), []).abstain
    post.assert_not_called()


def test_arbitrary_operation_or_command_fails_closed(monkeypatch, selector_incident):
    bad = candidate().model_dump()
    bad["operation"]["operation"] = "delete_namespace"
    response = Mock()
    response.json.return_value = {"response": json.dumps({
        "candidates": [bad], "recommended_candidate_index": 0, "abstain": False})}
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=response))
    result = remediation_generator.generate_remediation_proposal(
        selector_incident, [Hypothesis(cause="Mismatch", confidence=0.7)],
        Diagnosis(root_cause="service_selector_mismatch", summary="Mismatch"), [])
    assert result.abstain
    with pytest.raises(ValidationError):
        RemediationOperation(operation="kubectl apply", reason="Unsafe")
    scripted = candidate()
    scripted.operation.reason = "Run kubectl patch service"
    assert classify_candidate(scripted, selector_incident, [KnowledgeDocument(
        source_type="runbook", title="r", content="x")]).status == "denied"


def test_cross_namespace_and_unobserved_target_denied(selector_incident):
    assert classify_candidate(candidate(namespace="production"), selector_incident, []).status == "denied"
    assert classify_candidate(candidate(name="invented-service"), selector_incident, []).status == "denied"
    with pytest.raises(ValidationError):
        candidate(evidence_ids=[])
    assert classify_candidate(candidate(knowledge_ids=["K1"]), selector_incident, []).status == "denied"


def test_deterministic_risk_overrides_llm_and_generic_operations_are_proposal_only(selector_incident):
    proposed = RemediationProposal(candidates=[candidate(risk="low")], recommended_candidate_index=0)
    checked, decisions = validate_remediation_proposal(proposed, selector_incident, [
        KnowledgeDocument(source_type="runbook", title="Selector", content="Compare labels")])
    assert checked.candidates[0].risk_level == "medium"
    assert decisions[0].status == "known_but_not_executable_yet"
    assert not decisions[0].executable_now
    assert classify_risk(candidate().operation) == "medium"


def test_high_risk_secret_and_parameter_value_denied(selector_incident):
    secret = candidate(operation="patch_configmap", kind="Secret", name="db-password",
                       parameters={"key": "password"}, knowledge_ids=[])
    assert classify_risk(secret.operation) == "high"
    assert classify_candidate(secret, selector_incident, []).status == "denied"
    raw_patch = candidate(parameters={"selector": {"app": "payments"}, "patch": {"spec": {}}},
                          knowledge_ids=[])
    assert classify_candidate(raw_patch, selector_incident, []).status == "denied"


def test_existing_env_repair_remains_only_executable_action():
    from app.agent.policy import has_inc001_remediation_evidence
    incident = IncidentContext(incident_id="INC-001", namespace="operion-sandbox",
                               service="payment-service", deployment_name="payment-service", evidence=[
        Evidence(source="kubernetes", category="deployment_config", service="payment-service",
                 namespace="operion-sandbox", summary="Missing variable",
                 data={"deployment_name": "payment-service", "containers": [{
                     "name": "payment-service", "environment_variables": []}]}),
        Evidence(source="logs", category="application_log", service="payment-service",
                 namespace="operion-sandbox", summary="Config error",
                 data={"logs": "CONFIG_ERROR PAYMENT_PROVIDER_URL is required"}),
        Evidence(source="kubernetes", category="pod_status", service="payment-service",
                 namespace="operion-sandbox", summary="CrashLoopBackOff",
                 data={"ready": False, "waiting_reason": "CrashLoopBackOff"}),
    ])
    assert has_inc001_remediation_evidence(incident)
    env = RemediationCandidate(operation=RemediationOperation(
        operation="patch_environment_variable",
        target=ResourceTarget(kind="Deployment", namespace="operion-sandbox",
                              name="payment-service", container="payment-service"),
        parameters={"name": "PAYMENT_PROVIDER_URL"}, reason="Missing required variable",
        evidence_ids=["E1", "E2", "E3"]), expected_effect="Pod starts",
        risk_level="low", confidence=0.9, verification_strategy=["Pod Ready=True"])
    decision = classify_candidate(env, incident, [])
    assert decision.status == "supported_and_allowed" and decision.executable_now


def test_unsupported_graph_terminates_without_approval_or_execution(monkeypatch, selector_incident):
    proposal = RemediationProposal(candidates=[candidate(knowledge_ids=[])],
                                   recommended_candidate_index=0)
    monkeypatch.setattr(nodes, "collect_incident_context", Mock(return_value=selector_incident))
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(
        sufficient_evidence=True)))
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    monkeypatch.setattr(nodes, "diagnose_with_llm", Mock(return_value=Diagnosis(
        root_cause="service_selector_mismatch", summary="Mismatch", supporting_evidence=["E1: Selector differs"])))
    monkeypatch.setattr(nodes, "generate_remediation_proposal", Mock(return_value=proposal))
    approve = Mock(side_effect=AssertionError("Approval must not run"))
    execute = Mock(side_effect=AssertionError("Execution must not run"))
    monkeypatch.setattr(nodes, "approval_node", approve)
    monkeypatch.setattr(nodes, "execute_node", execute)
    state = build_incident_graph().invoke({"approved": True})
    assert state["remediation_proposal"].candidates[0].risk_level == "medium"
    assert state["proposal_decisions"][0].status == "known_but_not_executable_yet"
    assert state["remediation_plan"].action == "none"
    assert state["execution_result"] is None
    approve.assert_not_called()
    execute.assert_not_called()
    edges = {(edge.source, edge.target) for edge in build_incident_graph().get_graph().edges}
    assert ("diagnose", "generate_remediation_proposal") in edges
    assert ("generate_remediation_proposal", "validate_remediation_proposal") in edges


def test_legacy_nonexecuted_policy_permission_cannot_reach_approval():
    assert route_after_policy({
        "remediation_plan": RemediationPlan(action="update_memory_limit", reason="OOM", evidence_ids=["E1"]),
        "policy_decision": PolicyDecision(allowed=True, reason="Legacy intent policy"),
    }) == END


def test_final_knowledge_results_have_unique_source_titles(monkeypatch, selector_incident):
    book1 = KnowledgeDocument(source_type="runbook", title="CrashLoopBackOff.md", content="Check current pod status")
    book2 = KnowledgeDocument(source_type="runbook", title="CrashLoopBackOff.md", content="Inspect old application logs")
    doc = KnowledgeDocument(source_type="kubernetes_docs", title="Pod Lifecycle",
                            url="https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/",
                            content="Crash loops retry with backoff")
    monkeypatch.setattr(knowledge, "retrieve_runbooks", Mock(return_value=[book1, book2]))
    monkeypatch.setattr(knowledge, "retrieve_kubernetes_docs", Mock(return_value=[doc]))
    result = knowledge.retrieve_operational_knowledge(selector_incident, [])
    assert [(item.source_type, item.title) for item in result] == [
        ("runbook", "CrashLoopBackOff.md"), ("kubernetes_docs", "Pod Lifecycle")]


def test_remediation_prompt_redacts_configuration_values(selector_incident):
    selector_incident.evidence.append(Evidence(
        source="kubernetes", category="configmap", summary="App config",
        data={"name": "app-config", "data": {"password": "very-secret-value"}}))
    selector_incident.evidence[1].data["containers"][0]["environment_variables"] = [
        {"name": "API_TOKEN", "value": "literal-secret-value"}]
    prompt = remediation_generator.build_remediation_prompt(
        selector_incident, [], Diagnosis(root_cause="service_selector_mismatch", summary="Mismatch"), [])
    assert "very-secret-value" not in prompt
    assert "literal-secret-value" not in prompt
    assert "app-config" in prompt and "API_TOKEN" in prompt
