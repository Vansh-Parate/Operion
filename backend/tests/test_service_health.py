import re
from pathlib import Path
from unittest.mock import Mock

from app.agent import investigator, nodes
from app.agent.investigation_tools import merge_evidence
from app.agent.graph import build_incident_graph
from app.agent.investigation_models import Hypothesis, InvestigationDecision, ToolCallRecord
from app.agent.models import RemediationCandidate, RemediationOperation, RemediationProposal, ResourceTarget
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services import llm_diagnosis
from app.services.temporal import assess_current_workload_health


MANIFEST = Path(__file__).resolve().parents[2] / "k8s" / "incidents" / "selector-mismatch.yaml"


def pod() -> Evidence:
    return Evidence(source="kubernetes", category="pod_status", summary="Pod selector-demo is Running and Ready",
                    data={"pod_name": "selector-demo-1", "phase": "Running", "pod_ready_condition": True,
                          "ready": True, "current_state": "running", "waiting_reason": None,
                          "labels": {"app": "selector-demo"},
                          "containers": [{"name": "selector-demo", "ready": True,
                                          "current_state": "running", "waiting_reason": None}]})


def selector_demo_incident() -> IncidentContext:
    # These values mirror k8s/incidents/selector-mismatch.yaml.
    return IncidentContext(incident_id="SELECTOR-DEMO", namespace="operion-sandbox",
                           service="selector-demo", deployment_name="selector-demo", evidence=[
        pod(),
        Evidence(source="kubernetes", category="deployment_config", summary="Deployment Pod labels app=selector-demo",
                 data={"deployment_name": "selector-demo", "pod_labels": {"app": "selector-demo"},
                       "containers": [{"name": "selector-demo"}]}),
        Evidence(source="kubernetes", category="service_config", summary="Service selector app=wrong-label",
                 data={"name": "selector-demo", "selector": {"app": "wrong-label"}}),
        Evidence(source="kubernetes", category="endpoints", summary="Service selector-demo has zero endpoints",
                 data={"name": "selector-demo", "ready_addresses": [], "not_ready_addresses": []}),
    ])


def test_selector_demo_manifest_matches_regression_fixture():
    deployment, service = MANIFEST.read_text(encoding="utf-8").split("---")
    assert "kind: Deployment" in deployment and "kind: Service" in service
    assert re.search(r"name:\s*selector-demo", deployment)
    assert re.search(r"name:\s*selector-demo", service)
    assert re.search(r"app:\s*selector-demo", deployment)
    assert re.search(r"app:\s*wrong-label", service)


def test_ready_pods_alone_do_not_establish_service_health(monkeypatch):
    incident = IncidentContext(incident_id="I", namespace="operion-sandbox",
                               service="selector-demo", evidence=[pod()])
    health = assess_current_workload_health(incident)
    assert health.pod_health == "healthy"
    assert health.service_health == "unknown"
    assert not health.currently_healthy
    response = Mock()
    response.json.return_value = {"response": '{"root_cause":"no_active_incident","confidence":0.99,"summary":"Healthy"}'}
    monkeypatch.setattr(llm_diagnosis.requests, "post", Mock(return_value=response))
    diagnosis = llm_diagnosis.diagnose_with_llm(incident, knowledge_documents=[])
    assert diagnosis.root_cause == "unknown"


def test_investigator_gets_service_then_endpoints(monkeypatch):
    incident = IncidentContext(incident_id="I", namespace="operion-sandbox",
                               service="selector-demo", evidence=[pod()])
    decision = InvestigationDecision(hypotheses=[Hypothesis(cause="Service selector mismatch", confidence=0.7)],
                                     sufficient_evidence=True)
    response = Mock()
    response.json.return_value = {"response": decision.model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    prompt = investigator.build_investigation_prompt(incident, [])
    assert "Running and Ready Pods alone" in prompt
    first = investigator.generate_investigation_decision(incident, [])
    assert first.next_tool == "get_service" and not first.sufficient_evidence
    incident.evidence.append(Evidence(source="kubernetes", category="service_config",
                                      summary="Service selector", data={"name": "selector-demo",
                                                                        "selector": {"app": "selector-demo"}}))
    second = investigator.generate_investigation_decision(
        incident, [ToolCallRecord(tool="get_service", reason="Check Service", success=True)])
    assert second.next_tool == "get_endpoints" and not second.sufficient_evidence
    incident.evidence.append(Evidence(source="kubernetes", category="endpoints",
                                      summary="One ready endpoint", data={"name": "selector-demo",
                                                                          "ready_addresses": ["10.0.0.1"]}))
    third = investigator.generate_investigation_decision(incident, [
        ToolCallRecord(tool="get_service", reason="Check Service", success=True),
        ToolCallRecord(tool="get_endpoints", reason="Check endpoints", success=True)])
    assert third.sufficient_evidence and third.next_tool is None


def test_zero_endpoints_still_prompts_service_read(monkeypatch):
    incident = IncidentContext(incident_id="I", namespace="operion-sandbox",
                               service="selector-demo", evidence=[pod(), Evidence(
                                   source="kubernetes", category="endpoints", summary="Zero endpoints",
                                   data={"name": "selector-demo", "ready_addresses": []})])
    response = Mock()
    response.json.return_value = {"response": InvestigationDecision(sufficient_evidence=True).model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    decision = investigator.generate_investigation_decision(incident, [])
    assert decision.next_tool == "get_service" and not decision.sufficient_evidence


def test_fresh_endpoint_observation_replaces_stale_one():
    incident = selector_demo_incident()
    fresh = Evidence(source="kubernetes", category="endpoints", summary="Ready endpoint",
                     data={"name": "selector-demo", "ready_addresses": ["10.0.0.2"]},
                     reference="endpoints/selector-demo")
    incident.evidence[3].reference = "endpoints/selector-demo"
    updated = merge_evidence(incident, [fresh])
    assert len([item for item in updated.evidence if item.category == "endpoints"]) == 1
    assert not assess_current_workload_health(updated).zero_ready_endpoints_observed


def test_ready_pods_with_zero_endpoints_are_active_routing_failure():
    incident = selector_demo_incident()
    health = assess_current_workload_health(incident)
    assert health.pod_health == "healthy"
    assert health.service_health == "unhealthy"
    assert health.zero_ready_endpoints_observed
    assert health.selector_mismatch_observed
    assert health.active_failure_observed and not health.currently_healthy
    diagnosis = llm_diagnosis.diagnose_with_llm(incident, knowledge_documents=[])
    assert diagnosis.root_cause == "service_selector_mismatch"
    assert "no ready endpoints" in diagnosis.summary
    assert all(item.startswith("E") for item in diagnosis.supporting_evidence)


def test_zero_endpoints_without_label_mismatch_keeps_cause_open():
    incident = selector_demo_incident()
    incident.evidence[2].data["selector"] = {"app": "selector-demo"}
    health = assess_current_workload_health(incident)
    assert health.zero_ready_endpoints_observed and not health.selector_mismatch_observed
    diagnosis = llm_diagnosis.diagnose_with_llm(incident, knowledge_documents=[])
    assert diagnosis.root_cause == "service_routing_failure"
    assert diagnosis.root_cause != "no_active_incident"


def test_exact_selector_demo_graph_regression_is_recommendation_only(monkeypatch):
    incident = selector_demo_incident()
    proposal = RemediationProposal(candidates=[RemediationCandidate(
        operation=RemediationOperation(
            operation="patch_service_selector",
            target=ResourceTarget(kind="Service", namespace="operion-sandbox", name="selector-demo"),
            parameters={"selector": {"app": "selector-demo"}},
            reason="Service selects wrong-label while observed Pods use selector-demo",
            evidence_ids=["E1", "E2", "E3", "E4"]),
        expected_effect="Service gains ready endpoints", risk_level="low", confidence=0.9,
        verification_strategy=["Service has ready endpoints", "Pod remains Ready"]
    )], recommended_candidate_index=0)
    monkeypatch.setattr(nodes, "collect_incident_context", Mock(return_value=incident))
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(
        sufficient_evidence=True)))
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    monkeypatch.setattr(nodes, "generate_remediation_proposal", Mock(return_value=proposal))
    approve = Mock(side_effect=AssertionError("No approval for selector mutation"))
    execute = Mock(side_effect=AssertionError("No selector write"))
    monkeypatch.setattr(nodes, "approval_node", approve)
    monkeypatch.setattr(nodes, "execute_node", execute)
    state = build_incident_graph().invoke({"approved": True})
    assert state["diagnosis"].root_cause == "service_selector_mismatch"
    assert state["resolved"] is False
    assert state["proposal_decisions"][0].status == "known_but_not_executable_yet"
    assert state["proposal_decisions"][0].risk_level == "medium"
    assert state["remediation_plan"].action == "none"
    assert state["execution_result"] is None
    approve.assert_not_called()
    execute.assert_not_called()
