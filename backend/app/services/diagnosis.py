from app.models.diagnosis import Diagnosis
from app.models.incident import IncidentContext


def diagnose_incident(incident: IncidentContext) -> Diagnosis:
    pods = []
    events = []
    logs = []
    containers = []

    for evidence in incident.evidence:
        if evidence.category == "pod_status":
            pods.append(evidence.data)
        elif evidence.category == "kubernetes_event":
            events.append(evidence.data)
        elif evidence.category == "application_log":
            logs.append(evidence.data.get("logs", ""))
        elif evidence.category == "deployment_config":
            containers.extend(evidence.data.get("containers", []))

    # A termination reason is direct Kubernetes evidence of the OOM condition.
    oom_pod = next(
        (pod for pod in pods if pod.get("last_termination_reason") == "OOMKilled"),
        None,
    )
    if oom_pod:
        supporting_evidence = [
            f"Pod {oom_pod['pod_name']} was terminated with reason OOMKilled."
        ]
        if oom_pod.get("last_exit_code") is not None:
            supporting_evidence.append(
                f"Container exited with code {oom_pod['last_exit_code']}."
            )

        memory_limit = next(
            (
                container.get("resources", {}).get("limits", {}).get("memory")
                for container in containers
                if container.get("name") == "payment-service"
            ),
            None,
        )
        if memory_limit:
            supporting_evidence.append(
                f"Deployment memory limit is {memory_limit}."
            )

        if any(
            event.get("object_name") == oom_pod.get("pod_name")
            and event.get("reason") == "OOMKilling"
            for event in events
        ):
            supporting_evidence.append(
                "Kubernetes reported an OOMKilling event for the pod."
            )

        return Diagnosis(
            root_cause="container_memory_limit_exceeded",
            confidence=1.0,
            summary=(
                f"{incident.service} was terminated because its container "
                "exceeded its available memory limit."
            ),
            supporting_evidence=supporting_evidence,
            recommended_actions=[
                "Inspect application memory consumption.",
                "Inspect the Kubernetes memory limit.",
                "Determine whether memory usage is excessive or the limit is undersized.",
                "Restart or redeploy and verify the container remains stable.",
            ],
        )

    # Readiness failures do not require a container crash.
    for pod in pods:
        if pod.get("ready") is not False or pod.get("current_state") != "running":
            continue

        readiness_events = [
            event for event in events
            if event.get("object_name") == pod.get("pod_name")
            and "readiness probe" in (
                f"{event.get('reason', '')} {event.get('message', '')}"
            ).lower()
        ]
        if not readiness_events:
            continue

        supporting_evidence = [
            f"Pod {pod['pod_name']} is running but Ready=False."
        ]
        supporting_evidence.extend(
            f"Kubernetes event {event.get('reason')}: {event.get('message')}"
            for event in readiness_events
        )

        return Diagnosis(
            root_cause="readiness_probe_failure",
            confidence=1.0,
            summary=(
                f"{incident.service} is running but its readiness probe is "
                "failing, so the pod remains unready."
            ),
            supporting_evidence=supporting_evidence,
            recommended_actions=[
                "Inspect the readiness probe path, port, and configuration.",
                "Verify the application health endpoint directly.",
                "Update the probe configuration or application health endpoint.",
                "Confirm the pod becomes Ready.",
            ],
        )

    # Match an observed connection error, never the synthetic incident mode.
    redis_errors = [
        line.strip()
        for log in logs if isinstance(log, str)
        for line in log.splitlines()
        if "REDIS_CONNECTION_ERROR" in line
        or (
            "redis" in line.lower()
            and any(
                phrase in line.lower()
                for phrase in ("connection refused", "timed out", "timeout", "unavailable")
            )
        )
    ]
    if redis_errors:
        return Diagnosis(
            root_cause="redis_dependency_unavailable",
            confidence=1.0,
            summary=(
                f"{incident.service} could not connect to its Redis dependency."
            ),
            supporting_evidence=[
                f"Application log: {line}" for line in redis_errors[:3]
            ],
            recommended_actions=[
                "Verify the Redis service address and port.",
                "Verify the Redis process or service is available.",
                "Verify network connectivity from the payment-service pod to Redis.",
                "Verify dependency health after remediation.",
            ],
        )

    # Preserve the original missing PAYMENT_PROVIDER_URL rule.
    required_variable = "PAYMENT_PROVIDER_URL"
    crash_pod = next(
        (pod for pod in pods if pod.get("waiting_reason") == "CrashLoopBackOff"),
        None,
    )
    missing_variable_log = any(
        isinstance(log, str)
        and required_variable in log
        and "environment variable is required" in log
        for log in logs
    )
    configured_variable_names = {
        env.get("name")
        for container in containers
        for env in container.get("environment_variables", [])
    }
    if (
        crash_pod
        and missing_variable_log
        and required_variable not in configured_variable_names
    ):
        supporting_evidence = [
            "Container is in CrashLoopBackOff.",
            f"Application logs report that {required_variable} is required.",
            f"{required_variable} is absent from the Deployment configuration.",
        ]
        if crash_pod.get("last_exit_code") is not None:
            supporting_evidence.append(
                f"Container exited with code {crash_pod['last_exit_code']}."
            )

        return Diagnosis(
            root_cause="missing_environment_variable",
            confidence=1.0,
            summary=(
                f"{incident.service} is crashing because the required "
                f"{required_variable} environment variable is not configured."
            ),
            supporting_evidence=supporting_evidence,
            recommended_actions=[
                f"Configure {required_variable} in the {incident.service} Deployment.",
                "Redeploy the service.",
                "Verify that the new pod becomes Ready and stops restarting.",
            ],
        )

    supporting_evidence = []
    for pod in pods:
        if pod.get("waiting_reason"):
            supporting_evidence.append(
                f"Pod {pod.get('pod_name')} is waiting: {pod['waiting_reason']}."
            )
        if pod.get("last_exit_code") is not None:
            supporting_evidence.append(
                f"Pod {pod.get('pod_name')} exited with code {pod['last_exit_code']}."
            )

    return Diagnosis(
        root_cause=None,
        confidence=0.0,
        summary=(
            "The deterministic analyzer could not identify "
            "the root cause from the available evidence."
        ),
        supporting_evidence=supporting_evidence,
    )
