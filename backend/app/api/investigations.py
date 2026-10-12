"""FastAPI router and job runner for real Kubernetes incident investigations."""

from datetime import datetime, timezone
import json
import threading
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import structlog

from app.agent.graph import build_incident_graph
from app.agent.state import IncidentState
from app.models.incident import IncidentContext
from app.services.temporal import assess_current_workload_health

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/investigations", tags=["investigations"])

# In-memory thread-safe storage for investigation jobs
_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


class InvestigationRequest(BaseModel):
    namespace: str = Field(default="operion-sandbox", min_length=1, max_length=253)
    service: str = Field(default="payment-service", min_length=1, max_length=253)
    deployment: Optional[str] = Field(default=None, max_length=253)
    label_selector: Optional[str] = Field(default=None, max_length=253)
    require_service: bool = Field(default=False)
    approve: bool = Field(default=False)


class ApprovalRequest(BaseModel):
    approve: bool = Field(default=True)


def _serialize_evidence(incident: Optional[IncidentContext]) -> list[dict[str, Any]]:
    if not incident:
        return []
    result = []
    for index, item in enumerate(incident.evidence, start=1):
        content_str = ""
        if isinstance(item.data, dict) and "logs" in item.data:
            content_str = str(item.data["logs"])
        elif isinstance(item.data, dict) and item.data:
            content_str = json.dumps(item.data, indent=2, default=str)
        else:
            content_str = item.summary

        title_map = {
            "pod_status": "Pod Status Snapshot",
            "kubernetes_event": "Kubernetes Events Excerpt",
            "application_log": "Application Log Stream",
            "deployment_config": "Deployment Spec Manifest",
            "service_routing": "Service Routing & Endpoints",
        }
        title = title_map.get(item.category, item.category.replace("_", " ").title())
        source = f"{item.source.title()} · {item.reference or item.category}"
        result.append({
            "id": f"E{index}",
            "title": title,
            "source": source,
            "content": content_str,
            "highlight": item.summary,
            "category": item.category,
            "severity": item.severity,
            "reference": item.reference,
        })
    return result


def _serialize_state(state: IncidentState) -> dict[str, Any]:
    incident = state.get("incident")
    diagnosis = state.get("diagnosis")
    proposal = state.get("remediation_proposal")
    plan = state.get("remediation_plan")
    policy = state.get("policy_decision")
    decisions = state.get("proposal_decisions", [])
    knowledge_docs = state.get("knowledge_documents", [])
    hypotheses = state.get("hypotheses", [])
    tool_history = state.get("tool_history", [])

    # Workload health assessment
    health_data = None
    if incident is not None:
        try:
            health = assess_current_workload_health(incident)
            health_data = {
                "pod_health": health.pod_health,
                "service_health": health.service_health,
                "reason": health.reason,
            }
        except Exception:
            health_data = None

    # Workload pod status extract
    pod_status_str = "Unknown"
    ready_bool = False
    restarts_count = None
    if incident is not None:
        for ev in incident.evidence:
            if ev.category == "pod_status":
                data = ev.data or {}
                if data.get("ready") is not None:
                    ready_bool = bool(data.get("ready"))
                if "waiting_reason" in data and data["waiting_reason"]:
                    pod_status_str = str(data["waiting_reason"])
                elif "phase" in data and data["phase"]:
                    pod_status_str = str(data["phase"])
                elif "status" in data and data["status"]:
                    pod_status_str = str(data["status"])
                if "restart_count" in data:
                    restarts_count = data.get("restart_count")

    # Serialize knowledge documents
    serialized_docs = []
    for doc in knowledge_docs:
        serialized_docs.append({
            "sourceType": doc.source_type,
            "title": doc.title,
            "url": doc.url,
            "section": doc.section,
            "content": doc.content,
            "score": doc.score,
            "availability": "Retrieved from vector corpus" if doc.source_type == "runbook" else "Official K8s Documentation",
            "excerpt": doc.content[:280] if doc.content else None,
        })

    # Serialize diagnosis
    serialized_diagnosis = None
    if diagnosis is not None:
        serialized_diagnosis = {
            "rootCause": diagnosis.root_cause,
            "confidence": diagnosis.confidence,
            "summary": diagnosis.summary,
            "supportingEvidence": diagnosis.supporting_evidence,
            "recommendedActions": diagnosis.recommended_actions,
        }

    # Serialize remediation proposal
    serialized_proposal = None
    if proposal is not None:
        serialized_proposal = {
            "abstain": proposal.abstain,
            "abstainReason": proposal.abstain_reason,
            "abstainReasonCode": proposal.abstain_reason_code,
            "generationStatus": proposal.generation_status,
            "fallbackUsed": proposal.fallback_used,
            "recommendedCandidateIndex": proposal.recommended_candidate_index,
            "candidates": [
                {
                    "operation": c.operation.operation,
                    "target": c.operation.target.model_dump() if c.operation.target else None,
                    "reason": c.operation.reason,
                    "evidenceIds": c.operation.evidence_ids,
                    "knowledgeIds": c.operation.knowledge_ids,
                    "riskLevel": c.risk_level,
                    "confidence": c.confidence,
                    "verificationStrategy": c.verification_strategy,
                }
                for c in proposal.candidates
            ],
        }

    # Serialize remediation plan
    serialized_plan = None
    if plan is not None:
        serialized_plan = {
            "action": plan.action,
            "reason": plan.reason,
            "evidence": plan.evidence_ids,
            "requiresApproval": plan.requires_approval,
            "parameters": plan.parameters,
            "safety": "Human approval required · strict policy parameter validation" if plan.requires_approval else "Safe",
            "abstained": plan.action == "none" or (proposal.abstain if proposal else False),
        }

    # Serialize policy decision
    serialized_policy = None
    if policy is not None:
        serialized_policy = {
            "allowed": policy.allowed,
            "reason": policy.reason,
            "normalizedParameters": policy.normalized_parameters,
        }

    # Serialize proposal decisions
    serialized_decisions = [
        {
            "status": d.status,
            "riskLevel": d.risk_level,
            "reason": d.reason,
            "executableNow": d.executable_now,
        }
        for d in decisions
    ]

    # Convert tool history to trace steps
    serialized_trace = []
    for idx, t in enumerate(tool_history, start=1):
        serialized_trace.append({
            "id": f"tool-{idx}",
            "phase": "investigation",
            "label": f"Tool: {t.tool}",
            "tool": t.tool,
            "toolObservation": t.reason,
            "readOnly": True,
            "status": "complete" if t.success else "blocked",
            "detail": f"Observation captured ({t.evidence_added} evidence items added)",
        })

    # Verification state
    verification_result = state.get("verification_result")
    execution_result = state.get("execution_result")
    serialized_verification = {
        "proposedState": plan.action if plan else (proposal.candidates[0].operation.operation if proposal and proposal.candidates else "None"),
        "executedState": "executed" if execution_result and execution_result.get("success") else "not_executed",
        "verifiedState": "verified" if verification_result and verification_result.get("recovered") else "unverified",
        "verificationStrategy": proposal.candidates[0].verification_strategy if proposal and proposal.candidates else [
            "Verify container restartCount == 0",
            "Verify pod phase Running and condition Ready == True",
        ],
        "summary": "Independent cluster verification" if verification_result else "No execution or recovery verified yet.",
    }

    return {
        "incident": {
            "id": incident.incident_id if incident else "INC-LIVE",
            "title": f"Incident: {incident.service}" if incident else f"Investigation: {state.get('target_service', 'payment-service')}",
            "service": incident.service if incident else state.get("target_service", "payment-service"),
            "namespace": incident.namespace if incident else state.get("target_namespace", "operion-sandbox"),
            "labelSelector": incident.label_selector if incident else state.get("target_label_selector"),
            "deploymentName": incident.deployment_name if incident else state.get("target_deployment"),
            "severity": "Critical" if pod_status_str == "CrashLoopBackOff" else "High",
            "podStatus": pod_status_str,
            "ready": ready_bool,
            "restarts": restarts_count,
            "startedAt": incident.started_at.isoformat() if incident else datetime.now(timezone.utc).isoformat(),
            "evidence": _serialize_evidence(incident),
            "diagnosis": serialized_diagnosis or {
                "rootCause": "no_active_incident",
                "confidence": 0.95,
                "summary": "No active incident observed.",
                "supportingEvidence": [],
                "recommendedActions": [],
            },
            "runbooks": [
                {
                    "rank": idx,
                    "filename": doc.title,
                    "similarity": doc.score if doc.score is not None else 0.0,
                }
                for idx, doc in enumerate(knowledge_docs, start=1)
                if doc.source_type == "runbook"
            ],
            "remediation": serialized_plan or {
                "action": "none",
                "reason": "No active incident requires remediation.",
                "evidence": [],
                "safety": "Human approval required",
                "abstained": False,
            },
        },
        "health": health_data,
        "diagnosis": serialized_diagnosis,
        "knowledgeDocs": serialized_docs,
        "knowledgeRetrievalStatus": state.get("knowledge_retrieval_status", "not_started"),
        "remediationProposal": serialized_proposal,
        "proposalDecisions": serialized_decisions,
        "remediationPlan": serialized_plan,
        "policyDecision": serialized_policy,
        "approved": state.get("approved", False),
        "executionResult": execution_result,
        "verificationResult": verification_result,
        "resolved": state.get("resolved", False),
        "trace": serialized_trace,
        "verification": serialized_verification,
        "hypotheses": [
            {
                "cause": h.cause,
                "confidence": h.confidence,
                "supportingEvidenceIds": h.supporting_evidence_ids,
                "missingEvidence": h.missing_evidence,
            }
            for h in hypotheses
        ],
        "investigationComplete": state.get("investigation_complete", False),
        "investigationBlockedReason": state.get("investigation_blocked_reason"),
    }


def _run_investigation_worker(job_id: str, request: InvestigationRequest) -> None:
    initial: IncidentState = {
        "incident": None,
        "diagnosis": None,
        "remediation_plan": None,
        "policy_decision": None,
        "approved": request.approve,
        "execution_result": None,
        "verification_result": None,
        "iteration": 0,
        "resolved": False,
        "hypotheses": [],
        "next_tool": None,
        "next_tool_reason": None,
        "tool_history": [],
        "investigation_iteration": 0,
        "sufficient_evidence": False,
        "target_namespace": request.namespace,
        "target_service": request.service,
        "target_label_selector": request.label_selector,
        "target_deployment": request.deployment,
        "target_service_required": request.require_service,
    }

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["progress_step"] = "collect_evidence"

        graph = build_incident_graph()
        final_state = initial

        # Stream graph steps to capture progress
        for state in graph.stream(initial, stream_mode="values"):
            final_state = state
            # Determine progress step
            current_step = "collect_evidence"
            if state.get("verification_result") is not None:
                current_step = "verify"
            elif state.get("execution_result") is not None:
                current_step = "execute"
            elif state.get("policy_decision") is not None:
                current_step = "validate_policy"
            elif state.get("remediation_plan") is not None:
                current_step = "plan_remediation"
            elif state.get("remediation_proposal") is not None:
                current_step = "generate_remediation_proposal"
            elif state.get("diagnosis") is not None:
                current_step = "diagnose"
            elif state.get("knowledge_documents"):
                current_step = "retrieve_knowledge"
            elif state.get("hypotheses"):
                current_step = "generate_hypotheses"
            elif state.get("incident") is not None:
                current_step = "collect_evidence"

            with _jobs_lock:
                _jobs[job_id]["progress_step"] = current_step
                _jobs[job_id]["state"] = _serialize_state(final_state)

        with _jobs_lock:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["progress_step"] = "completed"
            _jobs[job_id]["state"] = _serialize_state(final_state)
            _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        logger.exception("investigation_job_failed", job_id=job_id, error=str(exc))
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["progress_step"] = "failed"
            _jobs[job_id]["error"] = str(exc)
            _jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()


@router.post("", status_code=202)
def create_investigation(request: InvestigationRequest, background_tasks: BackgroundTasks):
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    job_entry = {
        "job_id": job_id,
        "status": "queued",
        "progress_step": "queued",
        "target": request.model_dump(),
        "created_at": now,
        "completed_at": None,
        "error": None,
        "state": None,
    }

    with _jobs_lock:
        _jobs[job_id] = job_entry

    background_tasks.add_task(_run_investigation_worker, job_id, request)

    return {
        "job_id": job_id,
        "status": "queued",
        "target": request.model_dump(),
        "created_at": now,
    }


@router.get("/{job_id}")
def get_investigation(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Investigation job {job_id} not found")
        # Return shallow copy
        return dict(job)


@router.post("/{job_id}/approve", status_code=202)
def approve_investigation(job_id: str, request: ApprovalRequest, background_tasks: BackgroundTasks):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Investigation job {job_id} not found")

    target_dict = job.get("target") or {}
    new_request = InvestigationRequest(
        namespace=target_dict.get("namespace", "operion-sandbox"),
        service=target_dict.get("service", "payment-service"),
        deployment=target_dict.get("deployment"),
        label_selector=target_dict.get("label_selector"),
        require_service=target_dict.get("require_service", False),
        approve=request.approve,
    )

    new_job_id = f"job-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    with _jobs_lock:
        _jobs[new_job_id] = {
            "job_id": new_job_id,
            "status": "queued",
            "progress_step": "queued",
            "target": new_request.model_dump(),
            "parent_job_id": job_id,
            "created_at": now,
            "completed_at": None,
            "error": None,
            "state": None,
        }

    background_tasks.add_task(_run_investigation_worker, new_job_id, new_request)

    return {
        "job_id": new_job_id,
        "parent_job_id": job_id,
        "status": "queued",
        "target": new_request.model_dump(),
        "created_at": now,
    }


@router.get("")
def list_investigations():
    with _jobs_lock:
        summaries = [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "progress_step": j.get("progress_step"),
                "target": j.get("target"),
                "created_at": j.get("created_at"),
                "completed_at": j.get("completed_at"),
                "error": j.get("error"),
                "has_result": j.get("state") is not None,
            }
            for j in sorted(_jobs.values(), key=lambda x: x.get("created_at", ""), reverse=True)
        ]
        return summaries
