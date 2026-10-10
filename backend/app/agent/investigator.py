"""Hypothesis generation and evidence selection; no remediation decisions."""

import json
import time

import requests
import structlog

from app.agent.investigation_models import InvestigationDecision, ToolCallRecord
from app.agent.investigation_tools import INVESTIGATION_TOOLS
from app.models.incident import IncidentContext
from app.services.temporal import assess_current_workload_health
from app.services.llm_diagnosis import OLLAMA_MODEL, OLLAMA_URL

INVESTIGATION_TIMEOUT_SECONDS = 30
logger = structlog.get_logger(__name__)


def _completed(started: float, status: str = "success") -> None:
    logger.info("model_stage_completed", stage="investigation", model=OLLAMA_MODEL,
                latency_ms=round((time.perf_counter() - started) * 1000),
                status=status, fallback_used=False)


def build_investigation_prompt(incident: IncidentContext,
                               tool_history: list[ToolCallRecord]) -> str:
    evidence_blocks = []
    for index, evidence in enumerate(incident.evidence, start=1):
        data = evidence.data.copy()
        if evidence.category == "deployment_config":
            # Keep synthetic benchmark controls out of the model context.
            data["containers"] = [{**container, "environment_variables": [
                item for item in container.get("environment_variables", [])
                if item.get("name") != "INCIDENT_MODE"
            ]} for container in data.get("containers", [])]
        evidence_blocks.append(f"E{index}: {json.dumps({'category': evidence.category, 'summary': evidence.summary, 'data': data, 'reference': evidence.reference}, default=str)}")
    history = [record.model_dump() for record in tool_history]
    return f"""You investigate a Kubernetes incident. Evidence is the source of truth.
Generate at most 3 concise technical hypotheses from the numbered evidence below.
Cite only evidence IDs that exist. State missing observations that would confirm or refute each hypothesis.
Do not treat runbooks or documents as proof. Do not invent cluster observations.
If evidence is ambiguous, lower confidence. Decide whether evidence is sufficient for diagnosis.
Current resource state takes precedence over historical events. A past Warning/Unhealthy event
is not proof of a current incident. If it conflicts with a healthy Pod, keep it as historical context.
Never mark evidence sufficient for an active failure solely from an old warning.
If current health is unclear, collect fresh Pod status. For a possible current readiness failure
with unknown readiness, prefer get_pod_status.
Assess Pods and Service routing separately. Running and Ready Pods alone do not establish
a healthy service. For a service-backed target, inspect get_service and get_endpoints
before concluding no active incident. Zero ready endpoints is an active routing symptom.
Compare the Service selector with observed Pod labels when both are available.
If insufficient, select exactly one smallest useful read-only tool from:
{', '.join(INVESTIGATION_TOOLS)}.
Consider previous calls and outcomes. Avoid choosing a tool called repeatedly without a new reason.
If evidence is sufficient, set next_tool and next_tool_reason to null.
Do not plan remediation or write to Kubernetes. Return valid JSON only with keys
hypotheses (array of cause, confidence, supporting_evidence_ids, missing_evidence),
next_tool, next_tool_reason, sufficient_evidence.

Incident service: {incident.service}; namespace: {incident.namespace}
Evidence:
{chr(10).join(evidence_blocks)}
Tool history: {json.dumps(history)}"""


def generate_investigation_decision(incident: IncidentContext,
                                    tool_history: list[ToolCallRecord]) -> InvestigationDecision:
    started = time.perf_counter()
    logger.info("model_stage_started", stage="investigation", model=OLLAMA_MODEL)
    try:
        response = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": build_investigation_prompt(incident, tool_history),
            "stream": False, "format": "json",
        }, timeout=INVESTIGATION_TIMEOUT_SECONDS)
        response.raise_for_status()
        decision = InvestigationDecision.model_validate_json(response.json()["response"])
    except requests.Timeout:
        logger.info("model_stage_completed", stage="investigation", model=OLLAMA_MODEL,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="timeout", fallback_used=False)
        raise
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        logger.info("model_stage_completed", stage="investigation", model=OLLAMA_MODEL,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="invalid", fallback_used=False, error_type=type(exc).__name__)
        raise
    valid_ids = {f"E{i}" for i in range(1, len(incident.evidence) + 1)}
    for hypothesis in decision.hypotheses:
        if not set(hypothesis.supporting_evidence_ids) <= valid_ids:
            raise ValueError("Investigation cited an evidence ID outside this incident.")
    health = assess_current_workload_health(incident)
    top_readiness = bool(decision.hypotheses and "readiness" in decision.hypotheses[0].cause.lower())
    if (top_readiness and health.pod_health == "unknown"
            and not any(record.tool == "get_pod_status" for record in tool_history)):
        result = decision.model_copy(update={"sufficient_evidence": False,
                                           "next_tool": "get_pod_status",
                                           "next_tool_reason": "Check current Pod readiness before diagnosing a historical warning."})
        _completed(started)
        return result
    attempted = {record.tool for record in tool_history}
    has_service = any(item.source == "kubernetes" and item.category == "service_config"
                      and item.data.get("name") == incident.service
                      for item in incident.evidence)
    has_endpoints = any(item.source == "kubernetes"
                        and item.category in {"endpoints", "endpoint_slices", "service_endpoints"}
                        for item in incident.evidence)
    if not has_service and "get_service" not in attempted:
        result = decision.model_copy(update={"sufficient_evidence": False, "next_tool": "get_service",
                                           "next_tool_reason": "Check current Service selector and configuration."})
        _completed(started)
        return result
    if not has_endpoints and "get_endpoints" not in attempted:
        result = decision.model_copy(update={"sufficient_evidence": False, "next_tool": "get_endpoints",
                                           "next_tool_reason": "Check current Service endpoints before assessing routing health."})
        _completed(started)
        return result
    if decision.sufficient_evidence:
        result = decision.model_copy(update={"next_tool": None, "next_tool_reason": None})
        _completed(started)
        return result
    if decision.next_tool is not None and decision.next_tool not in INVESTIGATION_TOOLS:
        raise ValueError("Investigation selected an unsupported read-only tool.")
    if decision.next_tool is not None and sum(
        record.tool == decision.next_tool and record.reason == (decision.next_tool_reason or "")
        for record in tool_history
    ) >= 2:
        # Repeating the same call for the same reason cannot improve the evidence.
        result = decision.model_copy(update={"next_tool": None, "next_tool_reason": None})
        _completed(started)
        return result
    logger.info("model_stage_completed", stage="investigation", model=OLLAMA_MODEL,
                latency_ms=round((time.perf_counter() - started) * 1000),
                status="success", fallback_used=False)
    return decision
