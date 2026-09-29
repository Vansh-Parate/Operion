from typing import Any, TypedDict

from app.agent.models import PolicyDecision, RemediationPlan
from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext


class IncidentState(TypedDict):
    incident: IncidentContext | None
    diagnosis: Diagnosis | None
    remediation_plan: RemediationPlan | None
    policy_decision: PolicyDecision | None
    approved: bool
    execution_result: dict[str, Any] | None
    verification_result: dict[str, Any] | None
    iteration: int
    resolved: bool
