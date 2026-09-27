import json
import requests

from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext


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
            parts.append(logs)

    return "\n".join(
        part for part in parts if part
    )


def build_prompt(
    incident: IncidentContext,
    runbooks: list[dict],
) -> str:

    evidence_blocks = []

    for index, evidence in enumerate(
        incident.evidence,
        start=1,
    ):
        evidence_data = evidence.data.copy()

        # Hide synthetic benchmark trigger from the LLM.
        if evidence.category == "deployment_config":
            containers = []

            for container in evidence_data.get(
                "containers",
                []
            ):
                container_copy = container.copy()

                env_vars = [
                    env
                    for env in container.get(
                        "environment_variables",
                        []
                    )
                    if env.get("name") != "INCIDENT_MODE"
                ]

                container_copy[
                    "environment_variables"
                ] = env_vars

                containers.append(
                    container_copy
                )

            evidence_data["containers"] = containers

        evidence_blocks.append(
            f"E{index}\n"
            f"category={evidence.category}\n"
            f"summary={evidence.summary}\n"
            f"data={json.dumps(evidence_data)}\n"
            f"reference={evidence.reference}"
        )

    evidence_text = "\n\n".join(
        evidence_blocks
    )

    runbook_text = "\n\n".join(
        f"Source: {item['source']}\n"
        f"{item['text']}"
        for item in runbooks
    )

    return f"""
You are a production incident investigator.

Diagnose the incident using ONLY the supplied incident evidence
and runbook context.

STRICT RULES:
- Incident evidence E1, E2, E3, etc. is the source of truth.
- Runbooks are troubleshooting guidance, NOT proof that something happened.
- Never claim an event, state, error, restart, readiness failure, or observation
  unless it appears in the numbered incident evidence.
- Do not convert information from a runbook into incident evidence.
- If evidence is insufficient, lower confidence instead of guessing.

SUPPORTING EVIDENCE RULES:
- supporting_evidence must contain only facts explicitly present in the numbered evidence.
- Each item must begin with the Evidence ID that supports it.
- Copy or closely paraphrase a concrete observation from that evidence block.
- Do not use healthy/running status as proof of a failure.
- Do not infer facts that are not explicitly present.
- Fewer correct evidence items are better than speculative ones.
- Never return placeholder text.

ROOT CAUSE SELECTION:
- Select the root cause that is best supported by the incident evidence.
- Do not select a root cause merely because it appears in a runbook.
- OOMKilled with exit code 137 strongly supports container_memory_limit_exceeded.
- A Redis failure requires explicit Redis connection/dependency evidence.
- A readiness_probe_failure requires explicit readiness probe evidence.
- missing_environment_variable requires explicit evidence that a required variable is missing.


Only use the following root_cause values:

- missing_environment_variable
- container_memory_limit_exceeded
- readiness_probe_failure
- redis_dependency_unavailable
- unknown

Return valid JSON only.

Required JSON:

{{
  "root_cause": "one allowed value",
  "confidence": 0.0,
  "summary": "string",
  "supporting_evidence": [
    "E2: REDIS_CONNECTION_ERROR: failed to connect to Redis at 127.0.0.1:6399: Connection refused"
    - Never return placeholder text such as "claim supported by E1".
    - Every supporting_evidence item must include a concrete fact copied or closely paraphrased from that exact evidence block.
    - Only include evidence IDs that directly support the root cause.
  ],
  "recommended_actions": ["string"]
}}

INCIDENT EVIDENCE:

{evidence_text}

RUNBOOK CONTEXT:

{runbook_text}
""".strip()

def retrieve_runbooks(query: str, top_k: int = 3) -> list[dict]:
    # Load the embedding model only when retrieval is requested.
    from app.rag.retrieve import retrieve_runbooks as retrieve

    return retrieve(query=query, top_k=top_k)


def diagnose_with_llm(
    incident: IncidentContext,
    runbooks: list[dict] | None = None,
) -> Diagnosis:

    if runbooks is None:
        runbooks = retrieve_runbooks(
            query=build_retrieval_query(incident),
            top_k=3,
        )

    print("\n===== RETRIEVED RUNBOOKS =====")

    for item in runbooks:
        print(
        f"{item['source']} "
        f"chunk={item['chunk_index']} "
        f"score={item['score']:.4f}"
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
        timeout=300,
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