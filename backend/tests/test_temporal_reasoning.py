from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

from app.agent import investigation_tools, investigator, nodes
from app.agent.graph import build_incident_graph
from app.agent.investigation_models import Hypothesis, InvestigationDecision
from app.agent.planner import create_remediation_plan
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.rag import knowledge
from app.services import llm_diagnosis
from app.services.evidence_processor import process_evidence
from app.services.temporal import annotate_event_data, assess_current_workload_health


NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def incident(*, ready=True, warning_age=2400, waiting=None):
    observed_at = datetime.now(timezone.utc)
    pod = Evidence(source="kubernetes", category="pod_status", summary="Pod payment-service-1 status: Running",
                   data={"pod_name": "payment-service-1", "phase": "Running",
                         "pod_ready_condition": ready, "current_state": "waiting" if waiting else "running",
                         "ready": ready, "waiting_reason": waiting, "restart_count": 1,
                         "containers": [{"name": "payment-service", "current_state": "waiting" if waiting else "running",
                                         "ready": ready, "waiting_reason": waiting, "restart_count": 1}]})
    event = Evidence(source="kubernetes", category="kubernetes_event", severity="warning",
                     summary="Readiness probe failed: connection refused",
                     data=annotate_event_data({"type": "Warning", "reason": "Unhealthy",
                                               "message": "Readiness probe failed: connection refused",
                                               "last_timestamp": (observed_at - timedelta(seconds=warning_age)).isoformat(),
                                               "involved_object_name": "payment-service-1"}, now=observed_at))
    return IncidentContext(incident_id="I", namespace="operion-sandbox", service="payment-service",
                           evidence=[pod, event])


def test_healthy_current_state_overrides_old_warning(monkeypatch):
    case = incident()
    assert case.evidence[1].data["temporal_status"] == "historical"
    health = assess_current_workload_health(case)
    assert health.currently_healthy and not health.active_failure_observed
    post = Mock(side_effect=AssertionError("LLM should not override current health"))
    monkeypatch.setattr(llm_diagnosis.requests, "post", post)
    diagnosis = llm_diagnosis.diagnose_with_llm(case, knowledge_documents=[])
    assert diagnosis.root_cause == "no_active_incident"
    assert "currently Running and Ready" in diagnosis.summary
    assert "historical" in diagnosis.supporting_evidence[1].lower()
    assert diagnosis.recommended_actions == []
    assert create_remediation_plan(diagnosis).action == "none"
    post.assert_not_called()
    assert len(case.evidence) == 2


def test_recent_unready_and_current_crash_not_suppressed(monkeypatch):
    unready = incident(ready=False, warning_age=30)
    assert assess_current_workload_health(unready).active_failure_observed
    response = Mock()
    response.json.return_value = {"response": '{"root_cause":"readiness_probe_failure","confidence":0.8,"summary":"Currently unready","supporting_evidence":["E1: Pod Ready=False"]}'}
    monkeypatch.setattr(llm_diagnosis.requests, "post", Mock(return_value=response))
    assert llm_diagnosis.diagnose_with_llm(unready, knowledge_documents=[]).root_cause == "readiness_probe_failure"
    crash = incident(ready=False, waiting="CrashLoopBackOff")
    assert assess_current_workload_health(crash).active_failure_observed
    assert not assess_current_workload_health(crash).currently_healthy


def test_high_confidence_readiness_claim_cannot_override_ready_pod(monkeypatch):
    case = incident(ready=True, warning_age=30)
    response = Mock()
    response.json.return_value = {"response": '{"root_cause":"readiness_probe_failure","confidence":0.99,"summary":"Remains unhealthy","supporting_evidence":["E2: Probe failed"]}'}
    monkeypatch.setattr(llm_diagnosis.requests, "post", Mock(return_value=response))
    diagnosis = llm_diagnosis.diagnose_with_llm(case, knowledge_documents=[])
    assert diagnosis.root_cause == "unknown"
    assert diagnosis.confidence <= 0.4
    assert "remains unhealthy" not in diagnosis.summary.lower()
    assert create_remediation_plan(diagnosis).action == "none"


def test_event_timestamp_variants_and_missing_fields():
    old = (NOW - timedelta(minutes=40)).isoformat()
    for key in ("last_timestamp", "series_last_observed_time", "event_time", "first_timestamp"):
        result = annotate_event_data({key: old, "reason": "Unhealthy"}, now=NOW)
        assert result[key] == old and result["age_seconds"] == 2400
        assert result["temporal_status"] == "historical"
    assert annotate_event_data({"reason": "Unhealthy"}, now=NOW)["temporal_status"] == "unknown"
    assert annotate_event_data({"last_timestamp": "invalid"}, now=NOW)["age_seconds"] is None


def test_collector_preserves_event_times_and_pod_ready(monkeypatch):
    old = NOW - timedelta(minutes=40)
    api = Mock()
    pod = NS(metadata=NS(name="payment-service-1", deletion_timestamp=None),
             status=NS(phase="Running", conditions=[NS(type="Ready", status="True")],
                       container_statuses=[NS(name="payment-service", ready=True, restart_count=2,
                                              state=NS(running=NS(), waiting=None, terminated=None),
                                              last_state=NS(terminated=NS(reason="Error", exit_code=1)))]))
    api.list_namespaced_pod.return_value = NS(items=[pod])
    api.list_namespaced_event.return_value = NS(items=[NS(
        involved_object=NS(kind="Pod", name="payment-service-1"), type="Warning",
        reason="Unhealthy", message="Readiness probe failed", count=2,
        first_timestamp=old - timedelta(minutes=1), last_timestamp=old,
        event_time=None, series=None)])
    monkeypatch.setattr(investigation_tools.client, "CoreV1Api", Mock(return_value=api))
    case = IncidentContext(incident_id="I", namespace="operion-sandbox", service="payment-service")
    status = investigation_tools.get_pod_status(case)[0].data
    event = investigation_tools.get_events(case)[0]
    assert status["pod_ready_condition"] is True
    assert status["phase"] == "Running" and status["ready"] is True
    assert status["restart_count"] == 2 and status["last_termination_reason"] == "Error"
    assert event.data["first_timestamp"] == (old - timedelta(minutes=1)).isoformat()
    assert event.data["last_timestamp"] == old.isoformat()
    assert event.data["count"] == 2 and event.data["involved_object_name"] == "payment-service-1"
    processed = process_evidence([event])
    assert processed[0].data["last_timestamp"] == old.isoformat()
    assert processed[0].data["temporal_status"] == "historical"


def test_collector_accepts_new_event_fields_and_missing_timestamps(monkeypatch):
    old = NOW - timedelta(minutes=40)
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=[])
    api.list_namespaced_event.return_value = NS(items=[
        NS(involved_object=None, regarding=NS(kind="Service", name="payment-service"),
           type="Warning", reason="Unhealthy", note="Probe failed", message=None,
           count=None, series=NS(count=3, last_observed_time=old),
           first_timestamp=None, last_timestamp=None, event_time=old - timedelta(minutes=1),
           deprecated_first_timestamp=None, deprecated_last_timestamp=None),
        NS(involved_object=NS(kind="Service", name="payment-service"),
           type="Warning", reason="Unhealthy", message="Unknown time", count=1),
    ])
    monkeypatch.setattr(investigation_tools.client, "CoreV1Api", Mock(return_value=api))
    case = IncidentContext(incident_id="I", namespace="operion-sandbox", service="payment-service")
    events = investigation_tools.get_events(case)
    assert events[0].data["series_last_observed_time"] == old.isoformat()
    assert events[0].data["event_time"] == (old - timedelta(minutes=1)).isoformat()
    assert events[0].data["message"] == "Probe failed" and events[0].data["count"] == 3
    assert events[1].data["temporal_status"] == "unknown"


def test_investigator_prompt_and_fresh_status_preference(monkeypatch):
    case = IncidentContext(incident_id="I", namespace="demo", service="api", evidence=[
        Evidence(source="kubernetes", category="kubernetes_event", severity="warning",
                 summary="Old Unhealthy", data={"reason": "Unhealthy", "message": "Readiness probe failed",
                                                  "last_timestamp": (NOW - timedelta(minutes=40)).isoformat()})])
    prompt = investigator.build_investigation_prompt(case, [])
    assert "Current resource state takes precedence" in prompt
    assert "get_pod_status" in prompt
    decision = InvestigationDecision(hypotheses=[Hypothesis(cause="Current readiness failure", confidence=0.7)],
                                     sufficient_evidence=True)
    response = Mock()
    response.json.return_value = {"response": decision.model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    result = investigator.generate_investigation_decision(case, [])
    assert result.next_tool == "get_pod_status" and not result.sufficient_evidence


def test_no_active_graph_ends_without_execution(monkeypatch):
    case = incident()
    patch = Mock(side_effect=AssertionError("No write expected"))
    monkeypatch.setattr(nodes, "collect_incident_context", Mock(return_value=case))
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(return_value=InvestigationDecision(sufficient_evidence=True)))
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", Mock(return_value=[]))
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    state = build_incident_graph().invoke({"approved": True})
    assert state["diagnosis"].root_cause == "no_active_incident"
    assert state["remediation_plan"].action == "none"
    assert state["resolved"] is True
    assert state["remediation_plan"].requires_approval is False
    assert state["execution_result"] is None and state["verification_result"] is None
    patch.assert_not_called()


def test_duplicate_runbooks_removed_without_losing_docs(monkeypatch):
    from app.models.knowledge import KnowledgeDocument
    book = KnowledgeDocument(source_type="runbook", title="ReadinessProbeFailure.md", content="Check readiness probe path and HTTP response")
    doc = KnowledgeDocument(source_type="kubernetes_docs", title="Probes", content="Readiness probes control Service traffic",
                            url="https://kubernetes.io/docs/concepts/workloads/pods/probes/")
    monkeypatch.setattr(knowledge, "retrieve_runbooks", Mock(return_value=[book, book, book]))
    monkeypatch.setattr(knowledge, "retrieve_kubernetes_docs", Mock(return_value=[doc]))
    result = knowledge.retrieve_operational_knowledge(incident(), [])
    assert [(item.source_type, item.title) for item in result] == [
        ("runbook", "ReadinessProbeFailure.md"), ("kubernetes_docs", "Probes")]
    assert all(not isinstance(item, Evidence) for item in result)


def test_fresh_pod_status_replaces_obsolete_status():
    case = incident(ready=False)
    old_event = case.evidence[1]
    fresh = incident(ready=True).evidence[0]
    updated = investigation_tools.merge_evidence(case, [fresh])
    assert len([item for item in updated.evidence if item.category == "pod_status"]) == 1
    assert old_event in updated.evidence
    assert assess_current_workload_health(updated).currently_healthy
