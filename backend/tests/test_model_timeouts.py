import json
from unittest.mock import Mock

import pytest
import requests

from app.agent import investigator, nodes, remediation_generator
from app.agent.investigation_models import InvestigationDecision
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services import llm_diagnosis


def incident(*evidence):
    return IncidentContext(incident_id="model-timeout", namespace="demo", service="web",
                           deployment_name="web", evidence=list(evidence))


def unknown_incident():
    return incident(Evidence(source="kubernetes", category="pod_status", summary="Observed",
                             data={"pod_name": "web-1", "phase": "Pending"}))


def selector_incident():
    return incident(
        Evidence(source="kubernetes", category="service_config", summary="Service selector",
                 data={"name": "web", "selector": {"app": "wrong"}}),
        Evidence(source="kubernetes", category="deployment_config", summary="Pod labels",
                 data={"deployment_name": "web", "pod_labels": {"app": "web"},
                       "containers": [{"name": "web"}]}),
        Evidence(source="kubernetes", category="endpoints", summary="No endpoints",
                 data={"name": "web", "ready_addresses": []}),
    )


def test_investigation_timeout_is_finite_and_logged(monkeypatch):
    response = Mock()
    response.raise_for_status.side_effect = requests.Timeout()
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(investigator.requests, "post", post)
    with pytest.raises(requests.Timeout):
        investigator.generate_investigation_decision(unknown_incident(), [])
    assert post.call_args.kwargs["timeout"] == investigator.INVESTIGATION_TIMEOUT_SECONDS


def test_investigation_timeout_terminates_before_completeness_retry(monkeypatch):
    monkeypatch.setattr(nodes, "generate_investigation_decision", Mock(side_effect=requests.Timeout()))
    result = nodes.generate_hypotheses_node({"incident": unknown_incident(), "tool_history": []})
    assert result["investigation_model_failed"] is True
    checked = nodes.check_investigation_completeness_node({
        "incident": unknown_incident(), **result, "tool_history": []})
    assert checked["next_tool"] is None
    assert "model failed" in checked["investigation_blocked_reason"]


def test_diagnosis_timeout_uses_supported_deterministic_fallback(monkeypatch):
    monkeypatch.setattr(llm_diagnosis.requests, "post", Mock(side_effect=requests.Timeout()))
    with pytest.raises(requests.Timeout):
        llm_diagnosis.diagnose_with_llm(unknown_incident(), knowledge_documents=[])
    result = {}
    monkeypatch.setattr(nodes, "diagnose_with_llm", Mock(side_effect=requests.Timeout()))
    result = nodes.diagnose_node({"incident": selector_incident(), "knowledge_documents": []})
    assert result["diagnosis"].root_cause == "service_selector_mismatch"


def test_diagnosis_invalid_output_abstains_when_deterministic_rules_are_insufficient(monkeypatch):
    monkeypatch.setattr(nodes, "diagnose_with_llm", Mock(side_effect=ValueError("invalid JSON")))
    result = nodes.diagnose_node({"incident": unknown_incident(), "knowledge_documents": []})
    assert result["diagnosis"].root_cause == "unknown"
    assert result["diagnosis"].confidence == 0.0


def test_remediation_timeout_falls_back_and_logs_stage(monkeypatch):
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(side_effect=requests.Timeout()))
    proposal, metadata = remediation_generator.generate_remediation_proposal(
        selector_incident(), [], Diagnosis(root_cause="service_selector_mismatch", confidence=0.95,
                                           summary="Mismatch"), [], return_metadata=True)
    assert metadata["status"] == "llm_timeout"
    assert metadata["fallback_used"] is True
    assert proposal.candidates[0].operation.parameters == {"selector": {"app": "web"}}


def test_stage_logging_includes_status_latency_and_no_prompt(monkeypatch):
    events = []
    monkeypatch.setattr(investigator.logger, "info", lambda event, **fields: events.append((event, fields)))
    response = Mock()
    response.json.return_value = {"response": InvestigationDecision(sufficient_evidence=True).model_dump_json()}
    monkeypatch.setattr(investigator.requests, "post", Mock(return_value=response))
    investigator.generate_investigation_decision(unknown_incident(), [])
    completed = [fields for event, fields in events if event == "model_stage_completed"]
    assert completed and completed[-1]["stage"] == "investigation"
    assert isinstance(completed[-1]["latency_ms"], int)
    assert completed[-1]["status"] == "success"
    assert "prompt" not in completed[-1]
