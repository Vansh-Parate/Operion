from typing import Any, Literal, Optional
from pydantic import BaseModel


EvidenceSource = Literal[
    "kubernetes",
    "logs",
    "metrics",
    "runbook",
]


class Evidence(BaseModel):
    source: EvidenceSource
    category: str

    service: Optional[str] = None
    namespace: Optional[str] = None

    summary: str

    severity: Optional[str] = None

    data: dict[str, Any] = {}

    reference: Optional[str] = None