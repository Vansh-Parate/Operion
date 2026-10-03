"""Hypothesis generation and evidence selection; no remediation decisions."""

import json

import requests

from app.agent.investigation_models import InvestigationDecision, ToolCallRecord
from app.agent.investigation_tools import INVESTIGATION_TOOLS
from app.models.incident import IncidentContext
from app.services.llm_diagnosis import OLLAMA_MODEL, OLLAMA_URL


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
    response = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": build_investigation_prompt(incident, tool_history),
        "stream": False, "format": "json",
    }, timeout=300)
    response.raise_for_status()
    decision = InvestigationDecision.model_validate_json(response.json()["response"])
    valid_ids = {f"E{i}" for i in range(1, len(incident.evidence) + 1)}
    for hypothesis in decision.hypotheses:
        if not set(hypothesis.supporting_evidence_ids) <= valid_ids:
            raise ValueError("Investigation cited an evidence ID outside this incident.")
    if decision.sufficient_evidence:
        return decision.model_copy(update={"next_tool": None, "next_tool_reason": None})
    if decision.next_tool is not None and decision.next_tool not in INVESTIGATION_TOOLS:
        raise ValueError("Investigation selected an unsupported read-only tool.")
    if decision.next_tool is not None and sum(
        record.tool == decision.next_tool and record.reason == (decision.next_tool_reason or "")
        for record in tool_history
    ) >= 2:
        # Repeating the same call for the same reason cannot improve the evidence.
        return decision.model_copy(update={"next_tool": None, "next_tool_reason": None})
    return decision
