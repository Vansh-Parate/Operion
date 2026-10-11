"""Runbook vector retrieval with observable, bounded external operations."""

import time
from threading import Lock, Thread

import structlog

COLLECTION_NAME = "operion_runbooks"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
QDRANT_URL = "http://localhost:6333"
QDRANT_TIMEOUT_SECONDS = 10
EMBEDDING_INITIALIZATION_TIMEOUT_SECONDS = 10
logger = structlog.get_logger(__name__)

_resources_lock = Lock()
_model = None
_client = None
_initialization_thread = None
_initialization_error = None
_initialization_timed_out = False


def _load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def _create_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=QDRANT_URL, timeout=QDRANT_TIMEOUT_SECONDS)


def _initialize_resources(started: float) -> None:
    global _model, _client, _initialization_error
    try:
        if _model is None:
            model_started = time.perf_counter()
            model = _load_model()
            with _resources_lock:
                if _model is None:
                    _model = model
            logger.info("knowledge_embedding_model_completed", stage="model",
                        latency_ms=round((time.perf_counter() - model_started) * 1000),
                        model=MODEL_NAME)

        if _client is None:
            client_started = time.perf_counter()
            client = _create_client()
            with _resources_lock:
                if _client is None:
                    _client = client
            logger.info("knowledge_embedding_client_completed", stage="qdrant",
                        latency_ms=round((time.perf_counter() - client_started) * 1000))
    except Exception as exc:
        with _resources_lock:
            _initialization_error = exc
        logger.info("knowledge_embedding_initialization_failed",
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    error_type=type(exc).__name__,
                    stage="model" if _model is None else "qdrant")
    else:
        with _resources_lock:
            _initialization_error = None
        logger.info("knowledge_embedding_initialization_completed",
                    latency_ms=round((time.perf_counter() - started) * 1000), model=MODEL_NAME)


def _resources():
    global _initialization_thread, _initialization_error, _initialization_timed_out
    if _model is not None and _client is not None:
        return _model, _client

    with _resources_lock:
        if _model is not None and _client is not None:
            return _model, _client
        if _initialization_thread is None or not _initialization_thread.is_alive():
            started = time.perf_counter()
            _initialization_error = None
            _initialization_timed_out = False
            logger.info("knowledge_embedding_initialization_started", model=MODEL_NAME)
            _initialization_thread = Thread(target=_initialize_resources, args=(started,), daemon=True)
            _initialization_thread.start()
        initialization_thread = _initialization_thread

    if not _initialization_timed_out:
        initialization_thread.join(timeout=EMBEDDING_INITIALIZATION_TIMEOUT_SECONDS)
    if initialization_thread.is_alive():
        _initialization_timed_out = True
        logger.info("knowledge_embedding_initialization_failed",
                    latency_ms=EMBEDDING_INITIALIZATION_TIMEOUT_SECONDS * 1000,
                    error_type="TimeoutError", stage="model" if _model is None else "qdrant")
        raise TimeoutError("Embedding model initialization exceeded its bounded timeout.")
    if _initialization_error is not None:
        raise _initialization_error
    if _model is None or _client is None:
        raise RuntimeError("Embedding resources did not initialize.")
    return _model, _client


def retrieve_runbooks(query: str, top_k: int = 3) -> list[dict]:
    started = time.perf_counter()
    model, client = _resources()
    embedding_started = time.perf_counter()
    try:
        query_embedding = model.encode(query, normalize_embeddings=True)
    except Exception as exc:
        logger.info("knowledge_embedding_failed", stage="embedding",
                    latency_ms=round((time.perf_counter() - embedding_started) * 1000),
                    error_type=type(exc).__name__)
        raise
    logger.info("knowledge_embedding_completed", stage="embedding",
                latency_ms=round((time.perf_counter() - embedding_started) * 1000))

    search_started = time.perf_counter()
    try:
        results = client.query_points(
            collection_name=COLLECTION_NAME, query=query_embedding.tolist(), limit=top_k,
            timeout=QDRANT_TIMEOUT_SECONDS,
        ).points
    except Exception as exc:
        logger.info("knowledge_vector_search_failed", stage="qdrant",
                    latency_ms=round((time.perf_counter() - search_started) * 1000),
                    result_count=0, error_type=type(exc).__name__)
        raise
    logger.info("knowledge_vector_search_completed", stage="qdrant",
                latency_ms=round((time.perf_counter() - search_started) * 1000),
                result_count=len(results))

    retrieved = [{"score": result.score, "text": result.payload["text"],
                  "source": result.payload["source"],
                  "chunk_index": result.payload["chunk_index"]} for result in results]
    logger.info("knowledge_runbook_retrieval_completed",
                latency_ms=round((time.perf_counter() - started) * 1000),
                result_count=len(retrieved))
    return retrieved
