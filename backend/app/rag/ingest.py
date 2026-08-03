from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]

RUNBOOK_PATH = PROJECT_ROOT / "runbooks"


def load_runbooks():
    documents = []

    for file in RUNBOOK_PATH.rglob("*.md"):
        with open(file, "r", encoding="utf-8") as f:
            documents.append(
                {
                    "path": str(file),
                    "content": f.read(),
                }
            )

    return documents


if __name__ == "__main__":
    docs = load_runbooks()

    print(f"Loaded {len(docs)} documents\n")

    for doc in docs:
        print(doc["path"])