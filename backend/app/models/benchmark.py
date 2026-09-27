from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class RetrievedRunbook(BaseModel):
    source: str
    score: float
    chunk_index: int


class BenchmarkResult(BaseModel):
    incident_id: str
    scenario: str
    expected_root_cause: str
    deterministic_root_cause: Optional[str] = None
    llm_root_cause: Optional[str] = None
    llm_confidence: Optional[float] = None
    top1_runbook: Optional[str] = None
    top1_score: Optional[float] = None
    retrieval_hit: bool
    deterministic_correct: bool
    llm_correct: bool
    total_latency_ms: float
    llm_latency_ms: Optional[float] = None
    retrieval_latency_ms: Optional[float] = None
    retrieved_runbooks: list[RetrievedRunbook] = Field(default_factory=list)
    retrieval_error: Optional[str] = None
    llm_error: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
