from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re


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


GenericOperation = Literal[
    "patch_environment_variable", "update_container_resources", "update_probe",
    "patch_service_selector", "scale_workload", "restart_workload",
    "rollback_workload", "patch_configmap", "none", "unsupported",
]
RiskLevel = Literal["low", "medium", "high"]
ProposalStatus = Literal[
    "supported_and_allowed", "known_but_not_executable_yet", "denied", "abstain",
]


class ResourceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=64)
    namespace: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=253)
    container: str | None = None


class RemediationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: GenericOperation
    target: ResourceTarget | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_refs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"E[1-9][0-9]*", value) is None for value in values):
            raise ValueError("Evidence references must be E# IDs")
        return values

    @field_validator("knowledge_ids")
    @classmethod
    def knowledge_refs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"K[1-9][0-9]*", value) is None for value in values):
            raise ValueError("Knowledge references must be K# IDs")
        return values

    @model_validator(mode="after")
    def requires_live_evidence(self):
        if self.operation not in {"none", "unsupported"} and not self.evidence_ids:
            raise ValueError("An actionable operation requires live E# evidence")
        return self


class RemediationIntent(BaseModel):
    """Small, non-executable intent returned by the remediation model."""

    model_config = ConfigDict(extra="forbid")
    operation: GenericOperation
    target_kind: str | None = None
    target_name: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)
    knowledge_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_ids")
    @classmethod
    def intent_evidence_refs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"E[1-9][0-9]*", value) is None for value in values):
            raise ValueError("Evidence references must be E# IDs")
        return values

    @field_validator("knowledge_ids")
    @classmethod
    def intent_knowledge_refs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"K[1-9][0-9]*", value) is None for value in values):
            raise ValueError("Knowledge references must be K# IDs")
        return values


class RemediationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: RemediationOperation
    expected_effect: str = Field(min_length=1, max_length=1000)
    risk_level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    verification_strategy: list[str] = Field(min_length=1, max_length=5)


class RemediationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[RemediationCandidate] = Field(default_factory=list, max_length=3)
    recommended_candidate_index: int | None = None
    abstain: bool = False
    abstain_reason: str | None = None
    generation_status: str | None = None
    generation_latency_ms: int | None = None
    fallback_used: bool = False

    @model_validator(mode="after")
    def valid_recommendation(self):
        if self.recommended_candidate_index is not None and not (
            0 <= self.recommended_candidate_index < len(self.candidates)
        ):
            raise ValueError("Recommended candidate index is outside candidates")
        if self.abstain and self.recommended_candidate_index is not None:
            raise ValueError("An abstaining proposal cannot recommend a candidate")
        if self.abstain and self.candidates:
            raise ValueError("An abstaining proposal cannot contain candidates")
        return self


class ProposalDecision(BaseModel):
    status: ProposalStatus
    risk_level: RiskLevel
    reason: str
    executable_now: bool = False
