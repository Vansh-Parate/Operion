from types import SimpleNamespace as NS
from threading import Event

import pytest

from app.rag import readiness, retrieve


def complete_cache():
    return {"cache_root": "/cache", "model_root": "/cache/model", "snapshot": "/cache/model/snapshot",
            "missing": [], "complete": True}


class ReadyModel:
    def __init__(self, values=None, dimension=3):
        self.values = values if values is not None else [0.1, 0.2, 0.3]
        self.dimension = dimension

    def encode(self, query, normalize_embeddings):
        assert query
        assert normalize_embeddings is True
        return NS(tolist=lambda: self.values)

    def get_sentence_embedding_dimension(self):
        return self.dimension


def test_successful_readiness_validation(monkeypatch):
    monkeypatch.setattr(readiness, "inspect_model_cache", complete_cache)
    monkeypatch.setattr(readiness, "_load_model", lambda local_files_only: ReadyModel())
    result = readiness.validate_readiness()
    assert result["status"] == "ready"
    assert result["model"] == retrieve.MODEL_NAME
    assert result["dimension"] == 3


def test_missing_cache_fails_without_loading(monkeypatch):
    monkeypatch.setattr(readiness, "inspect_model_cache", lambda: {
        **complete_cache(), "complete": False, "missing": ["model.safetensors"]})
    loader = lambda **kwargs: pytest.fail("cache-only readiness must not load an incomplete cache")
    monkeypatch.setattr(readiness, "_load_model", loader)
    with pytest.raises(readiness.ReadinessError, match="model.safetensors"):
        readiness.validate_readiness()


@pytest.mark.parametrize("values", [[0.1, float("nan"), 0.3], [0.1, float("inf"), 0.3], []])
def test_invalid_embedding_fails(monkeypatch, values):
    monkeypatch.setattr(readiness, "inspect_model_cache", complete_cache)
    monkeypatch.setattr(readiness, "_load_model", lambda local_files_only: ReadyModel(values=values))
    with pytest.raises(readiness.ReadinessError, match="empty|non-finite"):
        readiness.validate_readiness()


def test_download_is_disabled_by_default_and_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(readiness, "inspect_model_cache", complete_cache)
    modes = []

    def load(local_files_only):
        modes.append(local_files_only)
        return ReadyModel()

    monkeypatch.setattr(readiness, "_load_model", load)
    readiness.validate_readiness()
    readiness.validate_readiness(allow_download=True)
    assert modes == [True, False]


def test_normal_resource_initialization_is_cache_only(monkeypatch):
    monkeypatch.setattr(retrieve, "_model", None)
    monkeypatch.setattr(retrieve, "_client", None)
    monkeypatch.setattr(retrieve, "_initialization_thread", None)
    monkeypatch.setattr(retrieve, "_initialization_error", None)
    monkeypatch.setattr(retrieve, "_initialization_timed_out", False)
    modes = []

    def load(local_files_only=True):
        modes.append(local_files_only)
        return object()

    monkeypatch.setattr(retrieve, "_load_model", load)
    monkeypatch.setattr(retrieve, "_create_client", object)
    retrieve._resources()
    assert modes == [True]


def test_model_initialization_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(readiness, "inspect_model_cache", complete_cache)
    blocked = Event()

    def load(local_files_only):
        blocked.wait(timeout=1)
        return ReadyModel()

    monkeypatch.setattr(readiness, "_load_model", load)
    with pytest.raises(readiness.ReadinessError, match="readiness timeout"):
        readiness.validate_readiness(timeout_seconds=0.01)
