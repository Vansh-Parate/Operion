import re

from pydantic import BaseModel, Field, field_validator


class Hypothesis(BaseModel):
    cause: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)

    @field_validator("cause")
    @classmethod
    def concise_cause(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("cause must be a technical hypothesis")
        return value

    @field_validator("supporting_evidence_ids")
    @classmethod
    def valid_evidence_ids(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"E[1-9][0-9]*", value) is None for value in values):
            raise ValueError("supporting evidence IDs must have the form E1, E2, ...")
        return values


class InvestigationDecision(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)
    next_tool: str | None = None
    next_tool_reason: str | None = None
    sufficient_evidence: bool


class ToolCallRecord(BaseModel):
    tool: str
    reason: str
    success: bool
    evidence_added: int = Field(default=0, ge=0)
