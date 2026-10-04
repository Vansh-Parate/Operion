import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.agent import investigation_tools as tools, investigator, nodes
from app.agent.graph import MAX_INVESTIGATION_ITERATIONS, build_incident_graph, route_after_hypotheses
from app.agent.investigation_models import Hypothesis, InvestigationDecision, ToolCallRecord
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext


@pytest.fixture
def incident():
    return IncidentContext(incident_id="INC-UNKNOWN", service="web-api", namespace="demo",
                           evidence=[Evidence(source="kubernetes", category="pod_status",
                                              summary="Pod not ready", data={"pod_name": "web-api-1"},
                                              reference="pod/web-api-1")])


def decision(tool=None, sufficient=False):
    return InvestigationDecision(hypotheses=[Hypothesis(
        cause="Pod readiness is failing", confidence=0.5,
        supporting_evidence_ids=["E1"], missing_evidence=["Recent events"],
    )], next_tool=tool, next_tool_reason="Inspect relevant evidence" if tool else None,
        sufficient_evidence=sufficient)


def test_sufficient_evidence_routes_to_diagnosis():
    assert route_after_hypotheses({"sufficient_evidence": True, "next_tool": "get_events"}) == "retrieve_knowledge"


def test_insufficient_evidence_selects_one_allowed_tool(monkeypatch, incident):
    response = Mock()
    response.json.return_value = {"response": decision("get_events").model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    result = investigator.generate_investigation_decision(incident, [])
    assert result.next_tool == "get_pod_status"
    assert result.hypotheses[0].supporting_evidence_ids == ["E1"]
    prompt = investigator.requests.post.call_args.kwargs["json"]["prompt"]
    assert "E1:" in prompt and "get_previous_logs" in prompt


def test_invalid_evidence_reference_rejected(monkeypatch, incident):
    bad = decision("get_events").model_dump()
    bad["hypotheses"][0]["supporting_evidence_ids"] = ["E2"]
    response = Mock()
    response.json.return_value = {"response": json.dumps(bad)}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    with pytest.raises(ValueError, match="outside this incident"):
        investigator.generate_investigation_decision(incident, [])


def test_repeated_tool_with_same_reason_stops(monkeypatch, incident):
    response = Mock()
    response.json.return_value = {"response": decision("get_events").model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    history = [ToolCallRecord(tool="get_events", reason="Inspect relevant evidence", success=True)
               for _ in range(2)]
    result = investigator.generate_investigation_decision(incident, history)
    assert result.next_tool == "get_pod_status"
    assert result.sufficient_evidence is False


def test_tool_merge_history_and_loop(monkeypatch, incident):
    extra = Evidence(source="kubernetes", category="kubernetes_event", summary="Probe failed",
                     data={"reason": "Unhealthy"}, reference="pod/web-api-1")
    tool = Mock(return_value=[extra])
    monkeypatch.setattr(nodes, "execute_investigation_tool", tool)
    result = nodes.investigate_with_tool_node({"incident": incident, "next_tool": "get_events",
                                                "next_tool_reason": "Inspect events"})
    assert len(result["incident"].evidence) == 2
    assert len(incident.evidence) == 1
    assert result["tool_history"][0].success
    assert result["tool_history"][0].evidence_added == 1
    assert result["investigation_iteration"] == 1
    assert build_incident_graph().get_graph().edges
    assert route_after_hypotheses({"next_tool": "get_events", "investigation_iteration": 1}) == "investigate_with_tool"


def test_duplicate_evidence_not_appended(incident):
    same = incident.evidence[0].model_copy(deep=True)
    assert len(tools.merge_evidence(incident, [same, same]).evidence) == 1
    old = Evidence(source="kubernetes", category="kubernetes_event", summary="Failed",
                   data={"reason": "Failed", "count": 1}, reference="pod/web-api-1")
    newer = old.model_copy(update={"data": {"reason": "Failed", "count": 2}})
    assert len(tools.merge_evidence(incident, [old, newer]).evidence) == 2


def test_failed_tool_recorded(monkeypatch, incident):
    monkeypatch.setattr(nodes, "execute_investigation_tool", Mock(side_effect=RuntimeError("offline")))
    result = nodes.investigate_with_tool_node({"incident": incident, "next_tool": "get_events"})
    assert result["tool_history"][0].success is False
    assert result["incident"] is incident
    assert result["investigation_iteration"] == 1


def test_unsupported_tool_rejected_before_cluster_load(monkeypatch, incident):
    load = Mock()
    monkeypatch.setattr(tools, "load_cluster", load)
    with pytest.raises(tools.InvestigationToolError, match="Unsupported"):
        tools.execute_investigation_tool("patch_deployment", incident)
    load.assert_not_called()


def test_max_iterations_use_best_evidence():
    assert MAX_INVESTIGATION_ITERATIONS == 5
    assert route_after_hypotheses({"next_tool": "get_logs",
                                   "investigation_iteration": 5}) == "retrieve_knowledge"
    assert route_after_hypotheses({"next_tool": None}) == "retrieve_knowledge"


def test_graph_stops_at_max_with_supplied_unknown_incident(monkeypatch, incident):
    collect = Mock(side_effect=AssertionError("Supplied incident should be used"))
    investigate = Mock(return_value=decision("get_events"))
    tool = Mock(return_value=[Evidence(source="kubernetes", category="kubernetes_event",
                                       summary="Warning", data={"reason": "Warning"})])
    diagnose = Mock(return_value=Diagnosis(root_cause="unknown", summary="Unknown"))
    monkeypatch.setattr(nodes, "collect_incident_context", collect)
    monkeypatch.setattr(nodes, "generate_investigation_decision", investigate)
    monkeypatch.setattr(nodes, "execute_investigation_tool", tool)
    monkeypatch.setattr(nodes, "diagnose_with_llm", diagnose)
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    result = build_incident_graph().invoke({"incident": incident, "approved": False})
    assert result["investigation_iteration"] == 5
    assert tool.call_count == 5
    assert investigate.call_count == 6
    assert len(result["incident"].evidence) == 2
    assert diagnose.call_count == 1
    collect.assert_not_called()


def test_no_tool_is_noop(monkeypatch, incident):
    call = Mock()
    monkeypatch.setattr(nodes, "execute_investigation_tool", call)
    assert nodes.investigate_with_tool_node({"incident": incident, "next_tool": None}) == {}
    call.assert_not_called()


def test_confidence_validation():
    with pytest.raises(ValidationError):
        Hypothesis(cause="Bad", confidence=1.2)
    with pytest.raises(ValidationError):
        Hypothesis(cause="Bad", confidence=-0.1)


@pytest.mark.parametrize("name", list(tools.INVESTIGATION_TOOLS))
def test_registry_only_reads(monkeypatch, incident, name):
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=[])
    api.list_namespaced_event.return_value = NS(items=[])
    api.read_namespaced_deployment.return_value = NS(
        metadata=NS(name="web-api"), spec=NS(replicas=1, selector=NS(match_labels={}),
        template=NS(metadata=NS(labels={}), spec=NS(containers=[], volumes=[]))))
    api.read_namespaced_service.return_value = NS(
        metadata=NS(name="web-api"), spec=NS(selector={}, ports=[], type="ClusterIP"))
    api.read_namespaced_endpoints.return_value = NS(subsets=[])
    monkeypatch.setattr(tools, "load_cluster", Mock())
    monkeypatch.setattr(tools.client, "CoreV1Api", Mock(return_value=api))
    monkeypatch.setattr(tools.client, "AppsV1Api", Mock(return_value=api))
    tools.execute_investigation_tool(name, incident)
    assert all(not method.startswith(("patch_", "create_", "replace_", "delete_"))
               for method in [call[0] for call in api.mock_calls])


def test_graph_continues_existing_remediation_pipeline(monkeypatch, incident):
    incident.evidence.append(Evidence(source="logs", category="application_log",
                                      summary="Unknown failure", data={"logs": "failure"}))
    collect = Mock(return_value=incident)
    diagnose = Mock(return_value=Diagnosis(root_cause="unknown", summary="Unknown"))
    investigate = Mock(side_effect=[decision("get_events"), decision(sufficient=True)])
    tool = Mock(return_value=[Evidence(source="kubernetes", category="kubernetes_event",
                                       summary="Warning", data={"reason": "Warning"})])
    monkeypatch.setattr(nodes, "collect_incident_context", collect)
    monkeypatch.setattr(nodes, "generate_investigation_decision", investigate)
    monkeypatch.setattr(nodes, "execute_investigation_tool", tool)
    monkeypatch.setattr(nodes, "diagnose_with_llm", diagnose)
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    result = build_incident_graph().invoke({"approved": False})
    assert result["investigation_iteration"] == 1
    assert len(result["incident"].evidence) == 3
    assert result["remediation_plan"].action == "none"
    assert result["policy_decision"] is not None
    assert diagnose.call_count == 1
    assert investigate.call_count == 2
