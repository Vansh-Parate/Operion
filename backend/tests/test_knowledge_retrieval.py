from types import SimpleNamespace as NS
from threading import Event, Thread

import pytest

from app.agent.investigation_models import Hypothesis
from app.agent import nodes
from app.models.incident import IncidentContext
from app.rag import knowledge, retrieve
from app.models.knowledge import KnowledgeDocument


def case():
    return IncidentContext(incident_id="knowledge", namespace="demo", service="web")


def reset_resources(monkeypatch):
    monkeypatch.setattr(retrieve, "_model", None)
    monkeypatch.setattr(retrieve, "_client", None)
    monkeypatch.setattr(retrieve, "_initialization_thread", None)
    monkeypatch.setattr(retrieve, "_initialization_error", None)
    monkeypatch.setattr(retrieve, "_initialization_timed_out", False)


def test_successful_vector_retrieval_reuses_resources_and_sets_qdrant_timeout(monkeypatch):
    class Model:
        def encode(self, query, normalize_embeddings):
            assert normalize_embeddings is True
            return NS(tolist=lambda: [0.1, 0.2])

    class Client:
        def query_points(self, **kwargs):
            assert kwargs["timeout"] == retrieve.QDRANT_TIMEOUT_SECONDS
            return NS(points=[NS(score=0.9, payload={"text": "check selectors", "source": "runbook.md", "chunk_index": 1})])

    monkeypatch.setattr(retrieve, "_model", Model())
    monkeypatch.setattr(retrieve, "_client", Client())
    result = retrieve.retrieve_runbooks("selector mismatch")
    assert result[0]["source"] == "runbook.md"


def test_embedding_failure_is_propagated_for_bounded_source_failure(monkeypatch):
    class Model:
        def encode(self, *args, **kwargs):
            raise RuntimeError("embedding unavailable")
    monkeypatch.setattr(retrieve, "_model", Model())
    monkeypatch.setattr(retrieve, "_client", object())
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        retrieve.retrieve_runbooks("query")


def test_qdrant_failure_is_propagated_without_fabricated_results(monkeypatch):
    class Model:
        def encode(self, *args, **kwargs):
            return NS(tolist=lambda: [0.1])
    class Client:
        def query_points(self, **kwargs):
            raise RuntimeError("qdrant unavailable")
    monkeypatch.setattr(retrieve, "_model", Model())
    monkeypatch.setattr(retrieve, "_client", Client())
    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        retrieve.retrieve_runbooks("query")


def test_embedding_initialization_timeout_is_bounded(monkeypatch):
    class AliveThread:
        def is_alive(self):
            return True
        def join(self, timeout):
            raise AssertionError("Timed-out initialization must not be joined again")
    reset_resources(monkeypatch)
    monkeypatch.setattr(retrieve, "_initialization_thread", AliveThread())
    monkeypatch.setattr(retrieve, "_initialization_timed_out", True)
    with pytest.raises(TimeoutError):
        retrieve._resources()


def test_successful_initialization_is_reused(monkeypatch):
    reset_resources(monkeypatch)
    calls = {"model": 0, "client": 0}

    monkeypatch.setattr(retrieve, "_load_model", lambda: calls.__setitem__("model", calls["model"] + 1) or object())
    monkeypatch.setattr(retrieve, "_create_client", lambda: calls.__setitem__("client", calls["client"] + 1) or object())

    first = retrieve._resources()
    second = retrieve._resources()
    assert first == second
    assert calls == {"model": 1, "client": 1}


def test_concurrent_initialization_is_single_flight(monkeypatch):
    reset_resources(monkeypatch)
    entered = Event()
    release = Event()
    calls = {"model": 0}

    def load_model():
        calls["model"] += 1
        entered.set()
        release.wait(timeout=2)
        return object()

    monkeypatch.setattr(retrieve, "_load_model", load_model)
    monkeypatch.setattr(retrieve, "_create_client", lambda: object())
    results = []
    errors = []

    def load():
        try:
            results.append(retrieve._resources())
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected failures
            errors.append(exc)

    first_thread = Thread(target=load)
    first_thread.start()
    assert entered.wait(timeout=2)
    second_thread = Thread(target=load)
    second_thread.start()
    release.set()
    threads = [first_thread, second_thread]
    for thread in threads:
        thread.join(timeout=3)
    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    assert calls["model"] == 1


def test_initialization_failure_is_reported_and_can_retry(monkeypatch):
    reset_resources(monkeypatch)
    calls = {"model": 0}

    def load_model():
        calls["model"] += 1
        if calls["model"] == 1:
            raise RuntimeError("model unavailable")
        return object()

    monkeypatch.setattr(retrieve, "_load_model", load_model)
    monkeypatch.setattr(retrieve, "_create_client", lambda: object())
    with pytest.raises(RuntimeError, match="model unavailable"):
        retrieve._resources()
    assert retrieve._model is None
    assert retrieve._client is None
    assert retrieve._resources()[0] is not None
    assert calls["model"] == 2


def test_knowledge_merge_returns_empty_results_without_inventing_documents(monkeypatch):
    monkeypatch.setattr(knowledge, "retrieve_runbooks", lambda query, top_k: [])
    monkeypatch.setattr(knowledge, "retrieve_kubernetes_docs", lambda query, top_k: [])
    assert knowledge.retrieve_operational_knowledge(case(), [Hypothesis(cause="unknown", confidence=0.2)]) == []


def test_knowledge_sources_remain_separate_from_evidence_ids(monkeypatch):
    doc = KnowledgeDocument(source_type="kubernetes_docs", title="Selectors", content="Compare Service selectors",
                             url="https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/")
    monkeypatch.setattr(knowledge, "retrieve_runbooks", lambda query, top_k: [])
    monkeypatch.setattr(knowledge, "retrieve_kubernetes_docs", lambda query, top_k: [doc])
    result = knowledge.retrieve_operational_knowledge(case(), [])
    assert result[0].source_type == "kubernetes_docs"
    assert all(not item.title.startswith("E") for item in result)


def test_graph_continues_safely_with_explicit_retrieval_failure(monkeypatch):
    monkeypatch.setattr(nodes, "retrieve_operational_knowledge", lambda incident, hypotheses: (_ for _ in ()).throw(TimeoutError("retrieval timeout")))
    result = nodes.retrieve_knowledge_node({"incident": case(), "hypotheses": []})
    assert result == {"knowledge_documents": [], "knowledge_retrieval_status": "failed"}
