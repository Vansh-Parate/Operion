"""Keep external guidance separate from live incident evidence."""

import re

from app.agent.investigation_models import Hypothesis
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument
from app.rag.kubernetes_docs import retrieve_kubernetes_docs


def build_knowledge_query(incident: IncidentContext, hypotheses: list[Hypothesis]) -> str:
    parts = [item.cause for item in sorted(hypotheses, key=lambda item: -item.confidence)[:3]]
    for evidence in incident.evidence:
        parts.append(evidence.category.replace("_", " "))
        parts.append(evidence.summary)
        for key in ("reason", "waiting_reason", "last_termination_reason", "message"):
            value = evidence.data.get(key)
            if isinstance(value, str):
                parts.append(value[:240])
        logs = evidence.data.get("logs")
        if isinstance(logs, str):
            parts.extend(re.findall(r"\b(?:CrashLoopBackOff|ImagePullBackOff|OOMKilled|FailedScheduling|Unhealthy|[A-Z][A-Z_]+ERROR|readiness|liveness|startup|endpoint|selector|environment variable|insufficient memory)\b", logs, re.I)[:8])
    return " ".join(parts)[:1600]


def retrieve_runbooks(query: str, top_k: int = 3) -> list[KnowledgeDocument]:
    # The existing Qdrant runbook collection remains unchanged.
    from app.rag.retrieve import retrieve_runbooks as retrieve

    return [KnowledgeDocument(source_type="runbook", title=item["source"],
                              content=item["text"], score=item.get("score"),
                              metadata={"chunk_index": item.get("chunk_index")})
            for item in retrieve(query, top_k=top_k)]


def retrieve_operational_knowledge(incident: IncidentContext,
                                   hypotheses: list[Hypothesis]) -> list[KnowledgeDocument]:
    query = build_knowledge_query(incident, hypotheses)
    try:
        runbooks = retrieve_runbooks(query, top_k=3)
    except Exception:
        runbooks = []
    try:
        docs = retrieve_kubernetes_docs(query, top_k=3)
    except Exception:
        docs = []
    # Interleave while giving the internal source first position.
    combined = []
    for index in range(3):
        if index < len(runbooks):
            combined.append(runbooks[index])
        if index < len(docs):
            combined.append(docs[index])
    distinct = []
    seen_sources = set()
    for item in combined:
        source_key = (item.source_type, item.title)
        if source_key in seen_sources:
            continue
        words = set(re.findall(r"[a-z0-9]+", item.content.lower()))
        duplicate = False
        for prior in distinct:
            prior_words = set(re.findall(r"[a-z0-9]+", prior.content.lower()))
            similarity = len(words & prior_words) / max(len(words | prior_words), 1)
            if ((item.source_type == prior.source_type and item.title == prior.title
                 and similarity >= 0.4) or similarity >= 0.85):
                duplicate = True
                break
        if not duplicate:
            distinct.append(item)
            seen_sources.add(source_key)
        if len(distinct) == 5:
            break
    return distinct
