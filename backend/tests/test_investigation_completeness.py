from unittest.mock import Mock

from langgraph.graph import END

from app.agent import nodes
from app.agent.completeness import assess_investigation_completeness
from app.agent.graph import MAX_INVESTIGATION_ITERATIONS, build_incident_graph, route_after_hypotheses
from app.agent.investigation_models import InvestigationDecision, ToolCallRecord
from app.agent.models import RemediationProposal
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services.diagnosis_validation import validate_diagnosis
from app.services.temporal import assess_current_workload_health


def healthy_pod() -> Evidence:
    return Evidence(source="kubernetes", category="pod_status", summary="Running and Ready",
                    data={"pod_name": "selector-demo-1", "phase": "Running",
                          "pod_ready_condition": True, "ready": True, "current_state": "running",
                          "labels": {"app": "selector-demo"},
                          "containers": [{"name": "selector-demo", "ready": True,
                                          "current_state": "running"}]})


def incident() -> IncidentContext:
    return IncidentContext(incident_id="I", namespace="operion-sandbox",
                           service="selector-demo", evidence=[healthy_pod()])


def test_completeness_requires_service_then_endpoints_even_if_llm_says_sufficient():
    case = incident()
    first = assess_investigation_completeness(case, [])
    assert not first.complete and first.next_tool == "get_service"
    case.evidence.append(Evidence(source="kubernetes", category="service_config",
                                  summary="Selector", data={"name": "selector-demo",
                                                            "selector": {"app": "selector-demo"}}))
    second = assess_investigation_completeness(case, [
        ToolCallRecord(tool="get_service", reason="Read Service", success=True)])
    assert not second.complete and second.next_tool == "get_endpoints"
    case.evidence.append(Evidence(source="kubernetes", category="endpoints",
                                  summary="Ready endpoint", data={"name": "selector-demo",
                                                                  "ready_addresses": ["10.0.0.1"]}))
    assert assess_investigation_completeness(case, []).complete
    assert assess_current_workload_health(case).currently_healthy


def test_graph_forces_routing_reads_before_diagnosis(monkeypatch):
    case = incident()
    calls = []
    def read(tool, _incident):
        calls.append(tool)
        if tool == "get_service":
            return [Evidence(source="kubernetes", category="service_config", summary="Service",
                             data={"name": "selector-demo", "selector": {"app": "selector-demo"}})]
        if tool == "get_endpoints":
            return [Evidence(source="kubernetes", category="endpoints", summary="Ready endpoint",
                             data={"name": "selector-demo", "ready_addresses": ["10.0.0.1"]})]
        raise AssertionError(f"Unexpected read: {tool}")
    def diagnose(observed, *, knowledge_documents):
        assert calls == ["get_service", "get_endpoints"]
        assert assess_current_workload_health(observed).currently_healthy
        return Diagnosis(root_cause="no_active_incident", summary="Healthy")
    monkeypatch.setattr(nodes, "collect_incident_context", Mock(side_effect=AssertionError("Supplied incident expected")))
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(
        sufficient_evidence=True)))
    monkeypatch.setattr(nodes, "execute_investigation_tool", read)
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    monkeypatch.setattr(nodes, "diagnose_with_llm", diagnose)
    monkeypatch.setattr(nodes, "generate_remediation_proposal", Mock(return_value=RemediationProposal(
        abstain=True, abstain_reason="Healthy")))
    state = build_incident_graph().invoke({"incident": case, "approved": False})
    assert state["investigation_complete"] is True
    assert state["diagnosis"].root_cause == "no_active_incident"
    assert state["investigation_iteration"] == 2
    assert calls == ["get_service", "get_endpoints"]


def test_failed_service_read_ends_incomplete_without_diagnosis(monkeypatch):
    case = incident()
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(
        sufficient_evidence=True)))
    monkeypatch.setattr(nodes, "execute_investigation_tool", Mock(side_effect=RuntimeError("unavailable")))
    diagnose = Mock(side_effect=AssertionError("Diagnosis must not run"))
    monkeypatch.setattr(nodes, "diagnose_with_llm", diagnose)
    state = build_incident_graph().invoke({"incident": case, "approved": False})
    assert state["investigation_complete"] is False
    assert "Service configuration remains unknown" in state["investigation_blocked_reason"]
    assert state["diagnosis"] is None
    assert state["remediation_plan"] is None
    diagnose.assert_not_called()
    assert route_after_hypotheses({"investigation_complete": False,
                                   "next_tool": "get_service",
                                   "investigation_iteration": MAX_INVESTIGATION_ITERATIONS}) == END


def test_healthy_running_pod_cannot_support_readiness_failure_from_status_alone():
    case = incident()
    alias = Diagnosis(root_cause="ready_probe_failure", confidence=0.99,
                      summary="Readiness failing", supporting_evidence=["E1: Pod status Running"])
    assert alias.root_cause == "readiness_probe_failure"
    checked = validate_diagnosis(case, alias)
    assert checked.root_cause == "unknown"
    assert checked.confidence <= 0.25
    assert checked.supporting_evidence == []
    assert Diagnosis(root_cause="invented_probe_failure", summary="Unknown").root_cause == "unknown"


def test_no_active_requires_both_dimensions():
    case = incident()
    assert not assess_current_workload_health(case).currently_healthy
    checked = validate_diagnosis(case, Diagnosis(root_cause="no_active_incident", summary="Healthy"))
    assert checked.root_cause == "unknown"
