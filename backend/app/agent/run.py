import argparse
import json

from app.agent.graph import build_incident_graph
from app.agent.state import IncidentState
from app.services.temporal import assess_current_workload_health


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect incident evidence and run the approved Kubernetes remediation flow."
    )
    parser.add_argument("--approve", action="store_true", help="Approve the supported real Kubernetes mutation")
    parser.add_argument("--namespace", default="operion-sandbox", help="Kubernetes namespace to investigate")
    parser.add_argument("--service", default="payment-service", help="Service to investigate")
    parser.add_argument("--label-selector", help="Pod label selector (defaults to app=<service>)")
    parser.add_argument("--deployment", help="Deployment name (defaults to the service name)")
    args = parser.parse_args()
    print(f"Target namespace: {args.namespace}")
    print(f"Target service: {args.service}")
    print(f"Label selector: {args.label_selector or f'app={args.service}'}")
    print(f"Deployment: {args.deployment or args.service}")
    print("REAL KUBERNETES MUTATIONS MAY OCCUR WHEN --approve IS USED.")
    initial: IncidentState = {
        "incident": None, "diagnosis": None, "remediation_plan": None,
        "policy_decision": None, "approved": args.approve,
        "execution_result": None, "verification_result": None,
        "iteration": 0, "resolved": False,
        "hypotheses": [], "next_tool": None, "next_tool_reason": None,
        "tool_history": [], "investigation_iteration": 0, "sufficient_evidence": False,
        "target_namespace": args.namespace, "target_service": args.service,
        "target_label_selector": args.label_selector, "target_deployment": args.deployment,
    }
    state = initial
    announced_iteration = 0
    for state in build_incident_graph().stream(initial, stream_mode="values"):
        plan = state.get("remediation_plan")
        decision = state.get("policy_decision")
        if (args.approve and state.get("approved") is True and plan is not None
                and decision is not None and decision.allowed
                and state.get("execution_result") is None
                and state.get("iteration", 0) > announced_iteration):
            print(f"Proposed action: {plan.action}")
            print(f"Normalized parameters: {json.dumps(decision.normalized_parameters)}")
            announced_iteration = state["iteration"]
    print("\nRetrieved operational knowledge:")
    for index, item in enumerate(state.get("knowledge_documents", []), start=1):
        print(f"{index}. [{item.source_type}] {item.title}")
    proposal = state.get("remediation_proposal")
    decisions = state.get("proposal_decisions", [])
    if proposal is not None and proposal.fallback_used:
        print("\nRemediation proposal generated using deterministic fallback")
        if proposal.generation_status == "llm_timeout":
            print("because the local remediation model timed out.")
        else:
            print("because the local remediation model returned an unusable intent.")
    if proposal is not None:
        print(f"Remediation generation: status={proposal.generation_status or 'unknown'}, latency_ms={proposal.generation_latency_ms or 0}, fallback_used={proposal.fallback_used}")
    if proposal is not None and proposal.abstain:
        print(f"\nNo safe remediation proposed.\nReason: {proposal.abstain_reason or 'Insufficient support.'}")
    elif proposal is not None:
        print("\nRemediation candidates:")
        for index, candidate in enumerate(proposal.candidates, start=1):
            operation = candidate.operation
            decision = decisions[index - 1] if index - 1 < len(decisions) else None
            target = operation.target
            print(f"{index}. Operation: {operation.operation}")
            print(f"   Risk: {decision.risk_level if decision else candidate.risk_level}")
            print(f"   Target: {f'{target.kind}/{target.name}' if target else 'none'}")
            print(f"   Reason: {operation.reason}")
            print(f"   Evidence: {', '.join(operation.evidence_ids)}")
            print(f"   Knowledge: {', '.join(operation.knowledge_ids)}")
            print(f"   Executable now: {'yes' if decision and decision.executable_now else 'no'}")
        recommended = proposal.recommended_candidate_index
        print(f"Recommended candidate: {recommended + 1 if recommended is not None else 'none'}")
    for label, key in (
        ("Diagnosis", "diagnosis"), ("Remediation plan", "remediation_plan"),
        ("Policy decision", "policy_decision"),
    ):
        value = state.get(key)
        print(f"\n{label}:")
        print(value.model_dump_json(indent=2) if value is not None else "None")
    execution = state.get("execution_result")
    diagnosis = state.get("diagnosis")
    incident = state.get("incident")
    if incident is not None:
        health = assess_current_workload_health(incident)
        print(f"\nCurrent health: Pods={health.pod_health}, Service={health.service_health}")
    if state.get("investigation_complete") is False:
        print(f"Investigation incomplete: {state.get('investigation_blocked_reason') or 'Required Service routing evidence is unavailable.'}")
    print(f"\nCurrent workload: {'healthy; no action required' if diagnosis is not None and diagnosis.root_cause == 'no_active_incident' else 'see diagnosis'}")
    print(f"\nExecution happened: {execution is not None}")
    print(f"Execution result: {json.dumps(execution)}")
    print(f"Verification result: {json.dumps(state.get('verification_result'))}")
    print(f"Resolved: {state.get('resolved', False)}")


if __name__ == "__main__":
    main()
