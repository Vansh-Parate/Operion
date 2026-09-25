from typing import Optional
from pydantic import BaseModel, Field


class Diagnosis(BaseModel):
    root_cause: Optional[str] = None

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )

    summary: str

    supporting_evidence: list[str] = Field(
        default_factory=list
    )

    recommended_actions: list[str] = Field(
        default_factory=list
    )