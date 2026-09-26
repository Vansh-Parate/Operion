from pathlib import Path
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)
from sentence_transformers import SentenceTransformer


COLLECTION_NAME = "operion_runbooks"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

RUNBOOK_DIR = (
    Path(__file__).resolve().parents[3]
    / "runbooks"
    / "kubernetes"
)


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[str]:

    chunks = []

    start = 0

    while start < len(text):
        end = start + chunk_size

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


def load_runbooks() -> list[dict]:
    documents = []

    for path in RUNBOOK_DIR.glob("*.md"):

        text = path.read_text(
            encoding="utf-8"
        )

        chunks = chunk_text(text)

        for index, chunk in enumerate(chunks):

            documents.append({
                "text": chunk,
                "source": path.name,
                "chunk_index": index,
            })

    return documents


def ingest_runbooks():

    documents = load_runbooks()

    if not documents:
        raise RuntimeError(
            f"No runbooks found at {RUNBOOK_DIR}"
        )

    print(
        f"Found {len(documents)} chunks "
        f"from {RUNBOOK_DIR}"
    )

    # Converts text into vectors.
    model = SentenceTransformer(
        MODEL_NAME
    )

    texts = [
        document["text"]
        for document in documents
    ]

    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    vector_size = embeddings.shape[1]

    client = QdrantClient(
        url="http://localhost:6333"
    )

    # Recreate collection during development so
    # ingestion remains deterministic.
    if client.collection_exists(
        COLLECTION_NAME
    ):
        client.delete_collection(
            COLLECTION_NAME
        )

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=vector_size,
            distance=Distance.COSINE,
        ),
    )

    points = []

    for document, embedding in zip(
        documents,
        embeddings,
    ):

        point = PointStruct(
            id=str(uuid4()),
            vector=embedding.tolist(),
            payload={
                "text": document["text"],
                "source": document["source"],
                "chunk_index": document[
                    "chunk_index"
                ],
            },
        )

        points.append(point)

    client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
    )

    print(
        f"Ingested {len(points)} chunks "
        f"into '{COLLECTION_NAME}'."
    )


if __name__ == "__main__":
    ingest_runbooks()