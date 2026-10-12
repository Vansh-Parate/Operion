from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import pytest

from main import app
from app.api.investigations import _jobs, _jobs_lock, _serialize_state
from app.agent.state import IncidentState
from app.models.incident import IncidentContext
from app.models.evidence import Evidence
from app.models.diagnosis import Diagnosis
from app.models.knowledge import KnowledgeDocument
from app.agent.models import RemediationProposal, RemediationCandidate, RemediationOperation, ResourceTarget, ProposalDecision


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs():
    with _jobs_lock:
        _jobs.clear()
    yield
    with _jobs_lock:
        _jobs.clear()


def test_root_and_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}


def test_create_investigation(client):
    with patch("app.api.investigations._run_investigation_worker") as mock_worker:
        res = client.post("/api/investigations", json={
            "namespace": "operion-sandbox",
            "service": "payment-service",
            "deployment": "payment-service",
            "label_selector": "app=payment-service",
            "require_service": False,
        })
        assert res.status_code == 202
        data = res.json()
        assert "job_id" in data
        assert data["status"] == "queued"
        assert data["target"]["namespace"] == "operion-sandbox"
        assert data["target"]["service"] == "payment-service"


def test_get_nonexistent_job_returns_404(client):
    res = client.get("/api/investigations/job-nonexistent")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_get_investigation_status(client):
    with _jobs_lock:
        _jobs["job-123"] = {
            "job_id": "job-123",
            "status": "running",
            "progress_step": "collect_evidence",
            "target": {"namespace": "test-ns", "service": "test-svc"},
            "created_at": "2026-10-11T20:00:00Z",
            "completed_at": None,
            "error": None,
            "state": None,
        }

    res = client.get("/api/investigations/job-123")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == "job-123"
    assert data["status"] == "running"
    assert data["progress_step"] == "collect_evidence"


def test_list_investigations(client):
    with _jobs_lock:
        _jobs["job-1"] = {
            "job_id": "job-1",
            "status": "completed",
            "progress_step": "completed",
            "target": {"namespace": "ns-1", "service": "svc-1"},
            "created_at": "2026-10-11T19:00:00Z",
            "completed_at": "2026-10-11T19:01:00Z",
            "error": None,
            "state": {"incident": {"id": "INC-1"}},
        }
        _jobs["job-2"] = {
            "job_id": "job-2",
            "status": "failed",
            "progress_step": "failed",
            "target": {"namespace": "ns-2", "service": "svc-2"},
            "created_at": "2026-10-11T20:00:00Z",
            "completed_at": "2026-10-11T20:01:00Z",
            "error": "Cluster unavailable",
            "state": None,
        }

    res = client.get("/api/investigations")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    assert items[0]["job_id"] == "job-2"  # Sorted reverse by created_at
    assert items[1]["job_id"] == "job-1"


def test_approve_investigation(client):
    with _jobs_lock:
        _jobs["job-initial"] = {
            "job_id": "job-initial",
            "status": "completed",
            "progress_step": "completed",
            "target": {"namespace": "operion-sandbox", "service": "payment-service"},
            "created_at": "2026-10-11T20:00:00Z",
            "completed_at": "2026-10-11T20:01:00Z",
            "error": None,
            "state": {},
        }

    with patch("app.api.investigations._run_investigation_worker"):
        res = client.post("/api/investigations/job-initial/approve", json={"approve": True})
        assert res.status_code == 202
        data = res.json()
        assert "job_id" in data
        assert data["parent_job_id"] == "job-initial"
        assert data["target"]["namespace"] == "operion-sandbox"


def test_serialize_state():
    incident = IncidentContext(
        incident_id="INC-TEST",
        service="payment-service",
        namespace="operion-sandbox",
        evidence=[
            Evidence(source="kubernetes", category="pod_status", summary="Pod Running",
                     data={"phase": "Running", "ready": True, "restart_count": 0}),
            Evidence(source="logs", category="application_log", summary="Log output",
                     data={"logs": "Server started successfully"}),
        ]
    )
    doc = KnowledgeDocument(
        source_type="kubernetes_docs",
        title="Debug Pods",
        content="Guide to debugging pods",
        url="https://kubernetes.io/docs/tasks/debug/debug-application/",
    )
    diagnosis = Diagnosis(
        root_cause="no_active_incident",
        confidence=0.95,
        summary="Workload is healthy",
        supporting_evidence=["E1"],
        recommended_actions=[],
    )
    state: IncidentState = {
        "incident": incident,
        "diagnosis": diagnosis,
        "remediation_plan": None,
        "remediation_proposal": None,
        "policy_decision": None,
        "proposal_decisions": [],
        "approved": False,
        "execution_result": None,
        "verification_result": None,
        "iteration": 1,
        "resolved": True,
        "hypotheses": [],
        "knowledge_documents": [doc],
        "knowledge_retrieval_status": "completed",
        "next_tool": None,
        "next_tool_reason": None,
        "tool_history": [],
        "investigation_iteration": 0,
        "sufficient_evidence": True,
    }

    serialized = _serialize_state(state)
    assert serialized["incident"]["id"] == "INC-TEST"
    assert serialized["incident"]["podStatus"] == "Running"
    assert serialized["incident"]["ready"] is True
    assert len(serialized["incident"]["evidence"]) == 2
    assert serialized["incident"]["evidence"][0]["id"] == "E1"
    assert serialized["diagnosis"]["rootCause"] == "no_active_incident"
    assert serialized["knowledgeRetrievalStatus"] == "completed"
    assert len(serialized["knowledgeDocs"]) == 1
    assert serialized["knowledgeDocs"][0]["title"] == "Debug Pods"
