from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent import nodes
from app.agent.state import IncidentState

MAX_INVESTIGATION_ITERATIONS = 5


def route_after_hypotheses(state: IncidentState) -> str:
    if state.get("sufficient_evidence", False):
        return "retrieve_knowledge"
    if (state.get("next_tool") is not None
            and state.get("investigation_iteration", 0) < MAX_INVESTIGATION_ITERATIONS):
        return "investigate_with_tool"
    return "retrieve_knowledge"


def route_after_policy(state: IncidentState) -> str:
    plan = state.get("remediation_plan")
    decision = state.get("policy_decision")
    if plan is None or plan.action == "none" or decision is None or not decision.allowed:
        return END
    return "approval"


def route_after_approval(state: IncidentState) -> str:
    return "execute" if state.get("approved", False) is True else END


def route_after_verify(state: IncidentState) -> str:
    if state.get("resolved", False) or state.get("iteration", 0) >= 3:
        return END
    return "collect_evidence"


def build_incident_graph() -> CompiledStateGraph:
    graph = StateGraph(IncidentState)
    graph.add_node("collect_evidence", nodes.collect_evidence_node)
    graph.add_node("generate_hypotheses", nodes.generate_hypotheses_node)
    graph.add_node("investigate_with_tool", nodes.investigate_with_tool_node)
    graph.add_node("retrieve_knowledge", nodes.retrieve_knowledge_node)
    graph.add_node("diagnose", nodes.diagnose_node)
    graph.add_node("plan_remediation", nodes.plan_remediation_node)
    graph.add_node("validate_policy", nodes.validate_policy_node)
    graph.add_node("approval", nodes.approval_node)
    graph.add_node("execute", nodes.execute_node)
    graph.add_node("verify", nodes.verify_node)
    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "generate_hypotheses")
    graph.add_conditional_edges("generate_hypotheses", route_after_hypotheses,
                                {"retrieve_knowledge": "retrieve_knowledge", "investigate_with_tool": "investigate_with_tool"})
    graph.add_edge("investigate_with_tool", "generate_hypotheses")
    graph.add_edge("retrieve_knowledge", "diagnose")
    graph.add_edge("diagnose", "plan_remediation")
    graph.add_edge("plan_remediation", "validate_policy")
    graph.add_conditional_edges("validate_policy", route_after_policy, {END: END, "approval": "approval"})
    graph.add_conditional_edges("approval", route_after_approval, {END: END, "execute": "execute"})
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges("verify", route_after_verify, {END: END, "collect_evidence": "collect_evidence"})
    return graph.compile()
