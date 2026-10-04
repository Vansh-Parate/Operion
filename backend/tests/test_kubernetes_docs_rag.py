import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.agent import nodes
from app.agent.graph import build_incident_graph, route_after_hypotheses
from app.agent.investigation_models import Hypothesis
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument
from app.rag import knowledge, kubernetes_docs
from app.services import llm_diagnosis


@pytest.fixture
def incident():
    return IncidentContext(incident_id="test", namespace="demo", service="api", evidence=[
        Evidence(source="kubernetes", category="service_endpoints", summary="Service has zero endpoints",
                 data={"reason": "No matching pods"}),
        Evidence(source="kubernetes", category="service_config", summary="Selector app=api",
                 data={"message": "Pods use app=server"}),
    ])


def test_official_corpus_is_knowledge_with_urls():
    docs = kubernetes_docs.load_kubernetes_docs()
    assert len(docs) >= 15
    assert all(isinstance(item, KnowledgeDocument) and not isinstance(item, Evidence)
               and item.source_type == "kubernetes_docs" and item.url.startswith("https://kubernetes.io/docs/")
               for item in docs)
    assert kubernetes_docs.INDEX_NAME == "operion_kubernetes_docs_local"


@pytest.mark.parametrize("url", ["https://example.com/docs/pods", "https://kubernetes.io.evil.test/docs/",
                                  "http://kubernetes.io/docs/pods", "https://kubernetes.io/blog/"])
def test_nonofficial_urls_rejected(url):
    with pytest.raises(ValidationError):
        KnowledgeDocument(source_type="kubernetes_docs", title="test", content="test", url=url)


def test_retrieval_is_bounded_deduplicated_and_graceful():
    results = kubernetes_docs.retrieve_kubernetes_docs("Service selector no endpoints", top_k=3)
    assert 1 <= len(results) <= 3
    assert all(item.url and item.score is not None and len(item.content) < 500 for item in results)
    assert len({item.content for item in results}) == len(results)
    assert kubernetes_docs.retrieve_kubernetes_docs("zzzxxyyq", top_k=3) == []


def test_query_uses_hypotheses_and_evidence(incident):
    query = knowledge.build_knowledge_query(incident, [Hypothesis(cause="Service selector mismatch", confidence=0.9)])
    assert "Service selector mismatch" in query
    assert "zero endpoints" in query
    assert "No matching pods" in query


def test_combined_sources_bounded_and_runbooks_preserved(monkeypatch, incident):
    runbook = KnowledgeDocument(source_type="runbook", title="Service.md", content="Check selectors")
    docs = kubernetes_docs.retrieve_kubernetes_docs("Service selector endpoints", top_k=3)
    monkeypatch.setattr(knowledge, "retrieve_runbooks", Mock(return_value=[runbook] * 3))
    monkeypatch.setattr(knowledge, "retrieve_kubernetes_docs", Mock(return_value=docs))
    result = knowledge.retrieve_operational_knowledge(incident, [])
    assert len(result) <= 5
    assert result[0].source_type == "runbook"
    assert {item.source_type for item in result} == {"runbook", "kubernetes_docs"}


def test_knowledge_does_not_enter_evidence_and_ids_are_separate(incident):
    doc = kubernetes_docs.retrieve_kubernetes_docs("Service selector endpoints")[0]
    original = incident.model_dump()
    prompt = llm_diagnosis.build_prompt(incident, [doc])
    assert "E1\n" in prompt and "E2\n" in prompt
    assert "K1 [kubernetes_docs]" in prompt
    assert "LIVE EVIDENCE" in prompt and "OPERATIONAL KNOWLEDGE" in prompt
    assert incident.model_dump() == original
    assert len(incident.evidence) == 2


def test_llm_discards_knowledge_supporting_evidence(monkeypatch, incident):
    response = Mock()
    response.json.return_value = {"response": json.dumps({"root_cause": "unknown", "summary": "test",
        "supporting_evidence": ["K1: documentation", "E9: missing", "E1: Service has zero endpoints", "E2: K1 says mismatch"]})}
    monkeypatch.setattr(llm_diagnosis.requests, "post", Mock(return_value=response))
    diagnosis = llm_diagnosis.diagnose_with_llm(incident, knowledge_documents=[])
    assert diagnosis.supporting_evidence == ["E1: Service has zero endpoints"]


def test_graph_orders_knowledge_after_investigation_before_diagnosis():
    graph = build_incident_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("investigate_with_tool", "generate_hypotheses") in edges
    assert ("retrieve_knowledge", "diagnose") in edges
    assert route_after_hypotheses({"sufficient_evidence": True}) == "retrieve_knowledge"


def test_no_new_kubernetes_write_in_retrieval():
    assert not hasattr(kubernetes_docs, "client")
    assert not hasattr(knowledge, "patch_deployment_environment_variable")
    assert nodes.retrieve_knowledge_node.__name__ == "retrieve_knowledge_node"
