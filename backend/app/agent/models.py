from typing import Any, Literal

from pydantic import BaseModel, Field


RemediationAction = Literal[
    "patch_environment_variable",
    "update_memory_limit",
    "update_readiness_probe",
    "restart_deployment",
    "none",
]


class RemediationPlan(BaseModel):
    action: RemediationAction
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str
    normalized_parameters: dict[str, Any] = Field(default_factory=dict)
