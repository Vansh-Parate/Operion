import json
import requests

from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext
from app.rag.retrieve import retrieve_runbooks


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:1.5b"


def build_retrieval_query(incident: IncidentContext) -> str:
    parts = []

    for evidence in incident.evidence:
        parts.append(evidence.summary)

        if evidence.category == "pod_status":
            parts.append(
                f"termination reason: "
                f"{evidence.data.get('last_termination_reason')}"
            )
            parts.append(
                f"exit code: "
                f"{evidence.data.get('last_exit_code')}"
            )

        elif evidence.category == "application_log":
            logs = evidence.data.get("logs", "")
            parts.append(logs[-1200:])

    return "\n".join(
        part for part in parts if part
    )


def build_prompt(
    incident: IncidentContext,
    runbooks: list[dict],
) -> str:

    evidence_text = "\n\n".join(
        f"[{e.category}] {e.summary}\n"
        f"data={json.dumps(e.data)}"
        for e in incident.evidence
    )

    runbook_text = "\n\n".join(
        f"Source: {item['source']}\n"
        f"{item['text']}"
        for item in runbooks
    )

    return f"""
You are a production incident investigator.

Diagnose the incident using ONLY the evidence and runbook context provided below.

Rules:
- Do not invent observations.
- Distinguish facts from hypotheses.
- Do not use synthetic incident trigger labels such as INCIDENT_MODE as root-cause evidence.
- If evidence is insufficient, return a lower confidence score.
- Return valid JSON only.
- confidence must be between 0.0 and 1.0.

Required JSON schema:

{{
  "root_cause": "string or null",
  "confidence": 0.0,
  "summary": "string",
  "supporting_evidence": ["string"],
  "recommended_actions": ["string"]
}}

INCIDENT EVIDENCE:

{evidence_text}

RUNBOOK CONTEXT:

{runbook_text}
""".strip()


def diagnose_with_llm(
    incident: IncidentContext,
) -> Diagnosis:

    query = build_retrieval_query(incident)

    runbooks = retrieve_runbooks(
        query=query,
        top_k=3,
    )

    prompt = build_prompt(
        incident=incident,
        runbooks=runbooks,
    )

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        },
        timeout=120,
    )

    response.raise_for_status()

    result = response.json()

    parsed = json.loads(
        result["response"]
    )

    diagnosis = Diagnosis(
        root_cause=parsed.get("root_cause"),
        confidence=parsed.get("confidence", 0.0),
        summary=parsed.get("summary", ""),
        supporting_evidence=parsed.get(
            "supporting_evidence",
            [],
        ),
        recommended_actions=parsed.get(
            "recommended_actions",
            [],
        ),
    )

    return diagnosis