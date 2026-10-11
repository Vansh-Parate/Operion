from typing import Any
from typing_extensions import NotRequired, TypedDict

from app.agent.investigation_models import Hypothesis, ToolCallRecord
from app.agent.models import PolicyDecision, ProposalDecision, RemediationPlan, RemediationProposal
from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext
from app.models.knowledge import KnowledgeDocument


# Shared by graph routing and the tool node so direct node calls cannot evade
# the investigation bound.
MAX_INVESTIGATION_TOOL_CALLS = 5


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
    hypotheses: NotRequired[list[Hypothesis]]
    next_tool: NotRequired[str | None]
    next_tool_reason: NotRequired[str | None]
    tool_history: NotRequired[list[ToolCallRecord]]
    investigation_iteration: NotRequired[int]
    sufficient_evidence: NotRequired[bool]
    investigation_complete: NotRequired[bool]
    investigation_blocked_reason: NotRequired[str | None]
    investigation_model_failed: NotRequired[bool]
    knowledge_documents: NotRequired[list[KnowledgeDocument]]
    knowledge_retrieval_status: NotRequired[str]
    remediation_proposal: NotRequired[RemediationProposal | None]
    proposal_decisions: NotRequired[list[ProposalDecision]]
    # Runtime collection target; retained across verification retries.
    target_namespace: NotRequired[str]
    target_service: NotRequired[str]
    target_label_selector: NotRequired[str | None]
    target_deployment: NotRequired[str | None]
