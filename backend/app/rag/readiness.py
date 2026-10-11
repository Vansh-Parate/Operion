"""Explicit SentenceTransformer cache provisioning and readiness check."""

import argparse
import math
import time
from threading import Thread

from app.rag.retrieve import MODEL_NAME, _load_model, inspect_model_cache

SAMPLE_QUERY = "Kubernetes Service selector mismatch"
READINESS_TIMEOUT_SECONDS = 30


class ReadinessError(RuntimeError):
    """Raised when the configured embedding model is not ready."""


def validate_readiness(*, allow_download: bool = False, sample_query: str = SAMPLE_QUERY,
                       timeout_seconds: float = READINESS_TIMEOUT_SECONDS) -> dict:
    cache = inspect_model_cache()
    if not allow_download and not cache["complete"]:
        missing = ", ".join(cache["missing"]) or "model snapshot"
        raise ReadinessError(
            f"Local model cache is incomplete at {cache['model_root']}; missing: {missing}. "
            "Provision explicitly with --allow-download, then rerun cache-only validation."
        )

    model_started = time.perf_counter()
    loaded = {}

    def load():
        try:
            loaded["model"] = _load_model(local_files_only=not allow_download)
        except Exception as exc:  # transferred to the caller below
            loaded["error"] = exc

    loader = Thread(target=load, daemon=True)
    loader.start()
    loader.join(timeout=timeout_seconds)
    if loader.is_alive():
        raise ReadinessError(
            f"Model initialization exceeded the readiness timeout of {timeout_seconds:g} seconds. "
            "Check CPU initialization and the local model environment; no download was started in cache-only mode."
        )
    if "error" in loaded:
        exc = loaded["error"]
        mode = "cache-only" if not allow_download else "download-enabled"
        raise ReadinessError(f"{mode} model initialization failed: {type(exc).__name__}: {exc}") from exc
    model = loaded["model"]
    model_ms = round((time.perf_counter() - model_started) * 1000)

    embedding_started = time.perf_counter()
    try:
        embedding = model.encode(sample_query, normalize_embeddings=True)
        values = embedding.tolist() if hasattr(embedding, "tolist") else embedding
        dimension = model.get_sentence_embedding_dimension()
    except Exception as exc:
        raise ReadinessError(f"Test embedding failed: {type(exc).__name__}: {exc}") from exc
    embedding_ms = round((time.perf_counter() - embedding_started) * 1000)

    if not isinstance(values, list) or not values or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                                                         for value in values):
        raise ReadinessError("Test embedding was empty or contained non-finite values.")
    if not isinstance(dimension, int) or dimension <= 0 or len(values) != dimension:
        raise ReadinessError(f"Unexpected embedding dimension: got {len(values)}, expected {dimension}.")

    return {
        "status": "ready",
        "model": MODEL_NAME,
        "cache": cache,
        "model_initialization_ms": model_ms,
        "embedding_ms": embedding_ms,
        "dimension": dimension,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the local Operion SentenceTransformer model.")
    parser.add_argument("--allow-download", action="store_true",
                        help="Explicitly permit Hugging Face downloads while provisioning the model.")
    parser.add_argument("--sample-query", default=SAMPLE_QUERY)
    parser.add_argument("--timeout", type=float, default=READINESS_TIMEOUT_SECONDS,
                        help="Maximum model initialization time in seconds.")
    args = parser.parse_args(argv)
    mode = "download-enabled" if args.allow_download else "cache-only"
    print(f"Model: {MODEL_NAME}")
    print(f"Mode: {mode}")
    cache = inspect_model_cache()
    print(f"Cache: {cache['cache_root']}")
    print(f"Snapshot: {cache['snapshot'] or 'not found'}")
    print(f"Timeout: {args.timeout:g} seconds")
    try:
        result = validate_readiness(allow_download=args.allow_download, sample_query=args.sample_query,
                                    timeout_seconds=args.timeout)
    except ReadinessError as exc:
        print(f"Readiness: FAILED\nReason: {exc}")
        return 1
    print(f"Initialization: {result['model_initialization_ms']} ms")
    print(f"Embedding: {result['embedding_ms']} ms")
    print(f"Dimension: {result['dimension']}")
    print("Readiness: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
