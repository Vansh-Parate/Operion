from typing import Optional
from pydantic import BaseModel, Field, field_validator


ROOT_CAUSES = {
    "missing_environment_variable", "container_memory_limit_exceeded",
    "readiness_probe_failure", "redis_dependency_unavailable",
    "service_selector_mismatch", "service_routing_failure",
    "no_active_incident", "unknown",
}
ROOT_CAUSE_ALIASES = {"ready_probe_failure": "readiness_probe_failure"}


class Diagnosis(BaseModel):
    root_cause: Optional[str] = None

    @field_validator("root_cause", mode="before")
    @classmethod
    def normalize_root_cause(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            return "unknown"
        normalized = ROOT_CAUSE_ALIASES.get(value.strip(), value.strip())
        return normalized if normalized in ROOT_CAUSES else "unknown"

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
