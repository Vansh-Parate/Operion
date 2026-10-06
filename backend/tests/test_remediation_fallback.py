import json
from unittest.mock import Mock

import pytest
import requests

from app.agent import remediation_generator
from app.agent.investigation_models import Hypothesis
from app.agent.models import RemediationProposal
from app.models.diagnosis import Diagnosis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext


@pytest.fixture
def selector_incident():
    return IncidentContext(incident_id="selector", namespace="incident-ns", service="selector-demo",
        deployment_name="selector-demo", evidence=[
            Evidence(source="kubernetes", category="service_config", summary="Service selector",
                     data={"name": "selector-demo", "selector": {"app": "wrong"}}),
            Evidence(source="kubernetes", category="deployment_config", summary="Pod labels",
                     data={"deployment_name": "selector-demo", "pod_labels": {"app": "selector-demo"},
                           "containers": [{"name": "selector-demo"}]}),
            Evidence(source="kubernetes", category="endpoints", summary="No ready endpoints",
                     data={"ready_addresses": []}),
        ])


def diagnosis(confidence=0.95):
    return Diagnosis(root_cause="service_selector_mismatch", confidence=confidence, summary="Selector mismatch")


def intent_response(**values):
    intent = {"operation": "patch_service_selector", "target_kind": "Service",
              "target_name": "selector-demo", "reason": "Observed selector mismatch",
              "evidence_ids": ["E1", "E2", "E3"], "knowledge_ids": [],
              "parameters": {"selector": {"app": "selector-demo"}}}
    intent.update(values)
    response = Mock()
    response.json.return_value = {"response": json.dumps(intent)}
    return response


def test_normal_llm_intent_is_deterministically_enriched(monkeypatch, selector_incident):
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=intent_response()))
    proposal, metadata = remediation_generator.generate_remediation_proposal(
        selector_incident, [Hypothesis(cause="selector", confidence=0.95)], diagnosis(), [], return_metadata=True)
    candidate = proposal.candidates[0]
    assert metadata["status"] == "llm_success"
    assert candidate.operation.target.namespace == "incident-ns"
    assert candidate.risk_level == "medium"
    assert candidate.verification_strategy == ["Service has ready endpoints.", "Selected Pods remain Ready."]
    assert proposal.candidates[0].operation.operation == "patch_service_selector"


@pytest.mark.parametrize("side_effect", [requests.Timeout(), ValueError("invalid json")])
def test_llm_failure_uses_safe_selector_fallback(monkeypatch, selector_incident, side_effect):
    post = Mock(side_effect=side_effect)
    monkeypatch.setattr(remediation_generator.requests, "post", post)
    proposal, metadata = remediation_generator.generate_remediation_proposal(
        selector_incident, [], diagnosis(), [], return_metadata=True)
    assert proposal.candidates[0].operation.operation == "patch_service_selector"
    assert proposal.fallback_used is False
    assert metadata["fallback_used"] is True
    assert metadata["status"] in {"llm_timeout", "llm_invalid"}


def test_unsupported_operation_falls_back_without_bypassing_policy(monkeypatch, selector_incident):
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=intent_response(operation="unsupported")))
    proposal = remediation_generator.generate_remediation_proposal(selector_incident, [], diagnosis(), [])
    assert proposal.candidates[0].operation.operation == "patch_service_selector"
    assert not proposal.candidates[0].operation.parameters.get("patch")


def test_unknown_and_low_confidence_diagnoses_abstain_without_fallback(monkeypatch, selector_incident):
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(remediation_generator.requests, "post", post)
    assert remediation_generator.generate_remediation_proposal(selector_incident, [], Diagnosis(root_cause="unknown", summary="Unknown"), []).abstain
    assert remediation_generator.generate_remediation_proposal(selector_incident, [], diagnosis(0.2), []).abstain
    post.assert_called_once()


def test_invalid_evidence_and_arbitrary_target_abstain(monkeypatch, selector_incident):
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=intent_response(evidence_ids=["E99"])))
    assert remediation_generator.generate_remediation_proposal(selector_incident, [], diagnosis(), []).abstain
    monkeypatch.setattr(remediation_generator.requests, "post", Mock(return_value=intent_response(target_name="invented")))
    assert remediation_generator.generate_remediation_proposal(selector_incident, [], diagnosis(), []).abstain


def test_selector_proposal_is_recommendation_only(selector_incident):
    monkeypatch = Mock(side_effect=requests.Timeout())
    original = remediation_generator.requests.post
    remediation_generator.requests.post = monkeypatch
    try:
        proposal, metadata = remediation_generator.generate_remediation_proposal(selector_incident, [], diagnosis(), [], return_metadata=True)
    finally:
        remediation_generator.requests.post = original
    assert metadata["fallback_used"]
    assert proposal.candidates[0].operation.operation == "patch_service_selector"
    assert proposal.candidates[0].risk_level == "medium"
