import json
import re
import time
import requests
import structlog

from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument
from app.services.temporal import annotate_event_data, assess_current_workload_health
from app.services.diagnosis_validation import validate_diagnosis


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:1.5b"
OLLAMA_TIMEOUT_SECONDS = 30
logger = structlog.get_logger(__name__)


def _stage_log(status: str, started: float, *, fallback_used: bool = False) -> None:
    logger.info("model_stage_completed", stage="diagnosis", model=OLLAMA_MODEL,
                latency_ms=round((time.perf_counter() - started) * 1000),
                status=status, fallback_used=fallback_used)


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
    knowledge_documents: list[KnowledgeDocument] | list[dict],
) -> str:
    knowledge_documents = _as_knowledge(knowledge_documents)

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

    knowledge_text = "\n\n".join(
        f"K{index} [{item.source_type}] {item.title}"
        f"{f' — {item.section}' if item.section else ''}\n"
        f"url={item.url or 'internal'}\n{item.content}"
        for index, item in enumerate(knowledge_documents, start=1)
    )

    return f"""
You are a production incident investigator.

Diagnose the incident using the supplied live evidence and operational knowledge.

STRICT RULES:
- Incident evidence E1, E2, E3, etc. is the source of truth.
- Knowledge K1, K2, K3, etc. is external troubleshooting guidance, not a cluster observation.
- Knowledge may support interpretation and suggested actions, but never prove an incident fact.
- If documentation describes several possible causes, do not assume one without evidence.
- Never claim an event, state, error, restart, readiness failure, or observation
  unless it appears in the numbered incident evidence.
- Do not convert information from knowledge into incident evidence.
- If evidence is insufficient, lower confidence instead of guessing.
- E# evidence has temporal meaning: distinguish a past failure from one occurring now.
- Current Pod and container health overrides stale warning events unless continuing failure is observed.
- Never say a workload remains unhealthy based on a historical event alone.
- If current and historical evidence conflict, lower confidence.
- Assess Pod health and Service routing separately. Running and Ready Pods alone are
  insufficient for no_active_incident on a service-backed target.
- Require observed Service configuration and ready Endpoints/EndpointSlices before
  concluding the service is currently healthy.
- Zero ready endpoints is an active routing problem even when Pods are Ready.
- Compare Service selector with observed Pod labels before claiming a mismatch.
- If Pods and Service routing are healthy and only historical warnings remain,
  return no_active_incident.

SUPPORTING EVIDENCE RULES:
- supporting_evidence must contain only facts explicitly present in the numbered evidence.
- Each item must begin with the Evidence ID that supports it.
- Never place K# references in supporting_evidence.
- Copy or closely paraphrase a concrete observation from that evidence block.
- Do not use healthy/running status as proof of a failure.
- Do not infer facts that are not explicitly present.
- Fewer correct evidence items are better than speculative ones.
- Never return placeholder text.

ROOT CAUSE SELECTION:
- Select the root cause that is best supported by the incident evidence.
- Do not select a root cause merely because it appears in operational knowledge.
- OOMKilled with exit code 137 strongly supports container_memory_limit_exceeded.
- A Redis failure requires explicit Redis connection/dependency evidence.
- A readiness_probe_failure requires explicit readiness probe evidence.
- missing_environment_variable requires explicit evidence that a required variable is missing.
- service_selector_mismatch requires evidence comparing the Service selector with Pod labels.
- no_active_incident means current workload health is observed and no active failure is established.
- service_routing_failure means a Service has zero ready endpoints, with cause not yet proven.


Only use the following root_cause values:

- missing_environment_variable
- container_memory_limit_exceeded
- readiness_probe_failure
- redis_dependency_unavailable
- service_selector_mismatch
- service_routing_failure
- no_active_incident
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

LIVE EVIDENCE:

{evidence_text}

OPERATIONAL KNOWLEDGE:

{knowledge_text}
""".strip()

def _as_knowledge(items: list[KnowledgeDocument] | list[dict]) -> list[KnowledgeDocument]:
    return [item if isinstance(item, KnowledgeDocument) else KnowledgeDocument(
        source_type="runbook", title=item["source"], content=item["text"],
        score=item.get("score"), metadata={"chunk_index": item.get("chunk_index")})
        for item in items]


def retrieve_runbooks(query: str, top_k: int = 3) -> list[dict]:
    from app.rag.retrieve import retrieve_runbooks as retrieve
    return retrieve(query=query, top_k=top_k)


def diagnose_with_llm(
    incident: IncidentContext,
    knowledge_documents: list[KnowledgeDocument] | None = None,
    *,
    runbooks: list[dict] | None = None,
) -> Diagnosis:
    started = time.perf_counter()
    logger.info("model_stage_started", stage="diagnosis", model=OLLAMA_MODEL)
    health = assess_current_workload_health(incident)
    if health.selector_mismatch_observed or health.zero_ready_endpoints_observed:
        service_refs = [f"E{index}: {item.summary}"
                        for index, item in enumerate(incident.evidence, start=1)
                        if item.category in {"service_config", "endpoints", "endpoint_slices", "service_endpoints",
                                             "pod_status", "deployment_config"}]
        mismatch = health.selector_mismatch_observed
        result = Diagnosis(
            root_cause="service_selector_mismatch" if mismatch else "service_routing_failure",
            confidence=0.9 if mismatch and health.zero_ready_endpoints_observed else 0.75,
            summary=("The Service selector does not match observed Pod labels and the Service has no ready endpoints."
                     if mismatch and health.zero_ready_endpoints_observed else
                     "The Service has zero ready endpoints; the routing cause is not yet established."
                     if health.zero_ready_endpoints_observed else
                     "The Service selector does not match observed Pod labels."),
            supporting_evidence=service_refs, recommended_actions=[])
        _stage_log("deterministic_bypass", started)
        return result
    if health.currently_healthy and not health.active_failure_observed:
        pod_ids = [f"E{index}: {item.summary}; Pod Ready=True."
                   for index, item in enumerate(incident.evidence, start=1)
                   if item.category == "pod_status" and item.data.get("pod_name")]
        historical_ids = [f"E{index}: Historical {item.data.get('reason')} warning: {item.data.get('message')}."
                          for index, item in enumerate(incident.evidence, start=1)
                          if item.category == "kubernetes_event"
                          and annotate_event_data(item.data).get("temporal_status") == "historical"]
        summary = ("Historical warnings were observed, but the workload is currently Running and Ready."
                   if historical_ids else "The workload is currently Running and Ready; no active incident is observed.")
        result = Diagnosis(root_cause="no_active_incident", confidence=0.95,
                         summary=summary,
                         supporting_evidence=pod_ids + historical_ids[:2], recommended_actions=[])
        _stage_log("deterministic_bypass", started)
        return result
    if knowledge_documents is None:
        knowledge_documents = _as_knowledge(
            runbooks if runbooks is not None else retrieve_runbooks(build_retrieval_query(incident), top_k=3))

    prompt = build_prompt(
        incident=incident,
        knowledge_documents=knowledge_documents or [],
    )

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()
        parsed = json.loads(result["response"])
    except requests.Timeout:
        _stage_log("timeout", started)
        raise
    except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.info("model_stage_completed", stage="diagnosis", model=OLLAMA_MODEL,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    status="invalid", fallback_used=False, error_type=type(exc).__name__)
        raise

    valid_evidence_ids = {f"E{index}" for index in range(1, len(incident.evidence) + 1)}
    supporting_evidence = [item for item in parsed.get("supporting_evidence", [])
                           if isinstance(item, str)
                           and re.match(r"^(E[1-9][0-9]*):\s+\S", item)
                           and re.match(r"^(E[1-9][0-9]*):", item).group(1) in valid_evidence_ids
                           and not re.search(r"\bK[1-9][0-9]*\b", item)]
    diagnosis = Diagnosis(
        root_cause=parsed.get("root_cause"),
        confidence=parsed.get("confidence", 0.0),
        summary=parsed.get("summary", ""),
        supporting_evidence=supporting_evidence,
        recommended_actions=parsed.get(
            "recommended_actions",
            [],
        ),
    )

    result = validate_diagnosis(incident, diagnosis)
    _stage_log("success", started)
    return result
