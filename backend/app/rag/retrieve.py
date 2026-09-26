from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer


COLLECTION_NAME = "operion_runbooks"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


model = SentenceTransformer(MODEL_NAME)

client = QdrantClient(
    url="http://localhost:6333"
)


def retrieve_runbooks(
    query: str,
    top_k: int = 3,
) -> list[dict]:

    query_embedding = model.encode(
        query,
        normalize_embeddings=True,
    )

    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding.tolist(),
        limit=top_k,
    ).points

    retrieved = []

    for result in results:
        retrieved.append({
            "score": result.score,
            "text": result.payload["text"],
            "source": result.payload["source"],
            "chunk_index": result.payload["chunk_index"],
        })

    return retrieved


if __name__ == "__main__":
    queries = [
        "CrashLoopBackOff missing required environment variable",
        "OOMKilled exit code 137 memory limit",
        "container running but Ready False readiness probe 404",
        "Redis connection refused dependency unavailable",
    ]

    for query in queries:
        print(f"\n\nQUERY: {query}")

        results = retrieve_runbooks(
            query,
            top_k=3,
        )

        for index, result in enumerate(results, start=1):
            print(
                f"{index}. "
                f"{result['source']} "
                f"(chunk={result['chunk_index']}, "
                f"score={result['score']:.4f})"
            )