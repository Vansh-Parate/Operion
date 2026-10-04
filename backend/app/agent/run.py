import argparse
import json

from app.agent.graph import build_incident_graph
from app.agent.state import IncidentState


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
    for label, key in (
        ("Diagnosis", "diagnosis"), ("Remediation plan", "remediation_plan"),
        ("Policy decision", "policy_decision"),
    ):
        value = state.get(key)
        print(f"\n{label}:")
        print(value.model_dump_json(indent=2) if value is not None else "None")
    execution = state.get("execution_result")
    diagnosis = state.get("diagnosis")
    print(f"\nCurrent workload: {'healthy; no action required' if diagnosis is not None and diagnosis.root_cause == 'no_active_incident' else 'see diagnosis'}")
    print(f"\nExecution happened: {execution is not None}")
    print(f"Execution result: {json.dumps(execution)}")
    print(f"Verification result: {json.dumps(state.get('verification_result'))}")
    print(f"Resolved: {state.get('resolved', False)}")


if __name__ == "__main__":
    main()
