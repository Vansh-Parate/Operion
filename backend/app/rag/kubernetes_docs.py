"""Offline, curated excerpts sourced from official Kubernetes documentation."""

import json
import math
import re
from pathlib import Path

from app.models.knowledge import KnowledgeDocument


CORPUS_PATH = Path(__file__).with_name("kubernetes_docs_corpus.json")
INDEX_NAME = "operion_kubernetes_docs_local"
_STOP = {"a", "an", "and", "are", "at", "by", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with", "pod", "pods", "kubernetes"}


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in _STOP}


def load_kubernetes_docs() -> list[KnowledgeDocument]:
    records = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return [KnowledgeDocument.model_validate(record) for record in records]


def retrieve_kubernetes_docs(query: str, top_k: int = 3) -> list[KnowledgeDocument]:
    if top_k <= 0 or not _tokens(query):
        return []
    query_terms = _tokens(query)
    scored = []
    for document in load_kubernetes_docs():
        title_terms = _tokens(f"{document.title} {document.section or ''}")
        body_terms = _tokens(document.content)
        overlap = query_terms & (title_terms | body_terms)
        if not overlap:
            continue
        score = (2 * len(overlap & title_terms) + len(overlap & body_terms)) / math.sqrt(len(query_terms) * max(len(title_terms | body_terms), 1))
        scored.append(document.model_copy(update={"score": round(score, 4)}))
    scored.sort(key=lambda item: (-item.score, item.title, item.section or ""))
    selected = []
    seen: list[set[str]] = []
    for document in scored:
        terms = _tokens(document.content)
        if any(len(terms & prior) / max(len(terms | prior), 1) >= 0.8 for prior in seen):
            continue
        selected.append(document)
        seen.append(terms)
        if len(selected) == top_k:
            break
    return selected
