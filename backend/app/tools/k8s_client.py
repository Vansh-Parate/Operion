from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from app.models.evidence import Evidence
import structlog

NAMESPACE = "operion-sandbox"
logger = structlog.get_logger(__name__)


def load_cluster():
    config.load_kube_config()
    logger.info("cluster_config_loaded")


def get_pods():
    load_cluster()

    v1 = client.CoreV1Api()

    logger.info(
        "pod_query_started",
        namespace=NAMESPACE,
        label_selector="app=payment-service",
    )

    try:
        pods = v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector="app=payment-service",
        )

        result = []

        for pod in pods.items:
            if pod.metadata.deletion_timestamp is not None:
                continue

            container = next(
                (
                    status
                    for status in (pod.status.container_statuses or [])
                    if status.name == "payment-service"
                ),
                None,
            )
            state = container.state if container else None
            last_state = container.last_state if container else None

            pod_data = {
                "pod_name": pod.metadata.name,
                "phase": pod.status.phase,
                "ready": container.ready if container else False,
                "restart_count": container.restart_count if container else 0,
                "image": container.image if container else pod.spec.containers[0].image,
                "current_state": None,
                "waiting_reason": None,
                "last_exit_code": None,
                "last_termination_reason": None,
            }

            if state and state.waiting:
                pod_data["current_state"] = "waiting"
                pod_data["waiting_reason"] = state.waiting.reason

            elif state and state.running:
                pod_data["current_state"] = "running"

            elif state and state.terminated:
                pod_data["current_state"] = "terminated"
                pod_data["last_exit_code"] = state.terminated.exit_code
                pod_data["last_termination_reason"] = state.terminated.reason

            if last_state and last_state.terminated:
                pod_data["last_exit_code"] = last_state.terminated.exit_code
                pod_data["last_termination_reason"] = last_state.terminated.reason

            if state and state.waiting and state.waiting.reason:
                summary = (
                    f"Pod {pod.metadata.name} is waiting because of "
                    f"{state.waiting.reason}"
                )
            elif state and state.terminated:
                summary = (
                    f"Pod {pod.metadata.name} container terminated with "
                    f"exit code {state.terminated.exit_code}"
                )
            elif state and state.running:
                summary = f"Pod {pod.metadata.name} container is running"
            else:
                summary = f"Pod {pod.metadata.name} state collected"

            evidence = Evidence(
                source="kubernetes",
                category="pod_status",
                service="payment-service",
                namespace=NAMESPACE,
                severity="warning" if not pod_data["ready"] else "info",
                summary=summary,
                data=pod_data,
                reference=f"pod/{pod.metadata.name}",
            )

            result.append(evidence)

            logger.info(
                "pod_status_collected",
                pod_name=pod.metadata.name,
                phase=pod.status.phase,
                current_state=pod_data["current_state"],
            )

        return result

    except client.ApiException:
        logger.exception(
            "kubernetes_api_error",
            operation="list_pods",
            namespace=NAMESPACE,
        )
        raise


def get_events(pod_names: set[str]):
    load_cluster()

    v1 = client.CoreV1Api()

    logger.info(
        "event_query_started",
        namespace=NAMESPACE,
        pod_names=sorted(pod_names),
    )

    try:
        events = v1.list_namespaced_event(
            namespace=NAMESPACE
        )

        result = []

        for event in events.items:
            if event.involved_object.name not in pod_names:
                continue

            event_data = {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "count": event.count,
                "object_name": event.involved_object.name,
                "object_kind": event.involved_object.kind,
            }

            object_kind = event.involved_object.kind.lower()
            object_name = event.involved_object.name

            result.append(
                Evidence(
                    source="kubernetes",
                    category="kubernetes_event",
                    service="payment-service",
                    namespace=NAMESPACE,
                    severity=(
                        "warning"
                        if event.type == "Warning"
                        else "info"
                    ),
                    summary=(
                        f"Kubernetes event "
                        f"{event.reason}: {event.message}"
                    ),
                    data=event_data,
                    reference=f"{object_kind}/{object_name}",
                )
            )

        return result

    except client.ApiException:
        logger.exception(
            "kubernetes_api_error",
            operation="list_events",
            namespace=NAMESPACE,
        )
        raise


def get_logs(pod_names: set[str] | None = None):
    load_cluster()

    v1 = client.CoreV1Api()

    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector="app=payment-service"
    )

    result = []

    for pod in pods.items:
        pod_name = pod.metadata.name
        if pod_names is not None and pod_name not in pod_names:
            continue

        try:
            logs = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=NAMESPACE,
                container="payment-service",
            )

            if isinstance(logs, bytes):
                logs = logs.decode("utf-8", errors="replace")

            result.append(
                Evidence(
                    source="logs",
                    category="application_log",
                    service="payment-service",
                    namespace=NAMESPACE,
                    summary=f"Collected application logs from pod {pod_name}",
                    data={
                        "pod_name": pod_name,
                        "logs": logs,
                    },
                    reference=f"pod/{pod_name}/logs",
                )
            )

            logger.info(
                "pod_logs_collected",
                pod_name=pod_name,
            )

        except client.ApiException:
            logger.exception(
                "kubernetes_api_error",
                operation="read_pod_logs",
                pod_name=pod_name,
                namespace=NAMESPACE,
            )
            raise

    return result

def get_deployment_config():
    load_cluster()

    apps_v1 = client.AppsV1Api()

    logger.info(
        "deployment_query_started",
        deployment="payment-service",
        namespace=NAMESPACE,
    )

    try:
        deployment = apps_v1.read_namespaced_deployment(
            name="payment-service",
            namespace=NAMESPACE,
        )

        containers = []

        for container in deployment.spec.template.spec.containers:

            env_vars = []
            resources = container.resources

            if container.env:
                for env in container.env:
                    env_data = {
                        "name": env.name,
                    }

                    # Plain environment variable
                    if env.value is not None:
                        env_data["source"] = "literal"
                        env_data["value"] = env.value

                    # ConfigMap / Secret / field reference.  Keep only the
                    # reference metadata; never resolve or include secret values.
                    elif env.value_from is not None:
                        env_data["source"] = "reference"

                        if env.value_from.config_map_key_ref is not None:
                            env_data["config_map_key_ref"] = {
                                "name": env.value_from.config_map_key_ref.name,
                                "key": env.value_from.config_map_key_ref.key,
                            }
                        elif env.value_from.secret_key_ref is not None:
                            env_data["secret_key_ref"] = {
                                "name": env.value_from.secret_key_ref.name,
                                "key": env.value_from.secret_key_ref.key,
                            }
                        elif env.value_from.field_ref is not None:
                            env_data["field_ref"] = {
                                "field_path": env.value_from.field_ref.field_path,
                            }

                    env_vars.append(env_data)

            containers.append({
                "name": container.name,
                "image": container.image,
                "environment_variables": env_vars,
                "resources": {
                    "requests": {
                        name: str(value)
                        for name, value in (resources.requests or {}).items()
                    } if resources else {},
                    "limits": {
                        name: str(value)
                        for name, value in (resources.limits or {}).items()
                    } if resources else {},
                },
            })

        result = {
            "deployment_name": deployment.metadata.name,
            "replicas": deployment.spec.replicas,
            "containers": containers,
        }

        logger.info(
            "deployment_config_collected",
            deployment=deployment.metadata.name,
        )

        return Evidence(
            source="kubernetes",
            category="deployment_config",
            service="payment-service",
            namespace=NAMESPACE,
            summary=(
                f"Collected configuration for deployment {deployment.metadata.name}"
            ),
            data=result,
            reference="deployment/payment-service",
        )

    except client.ApiException:
        logger.exception(
            "kubernetes_api_error",
            operation="read_deployment",
            deployment="payment-service",
            namespace=NAMESPACE,
        )
        raise


if __name__ == "__main__":
    pods = get_pods()
    pod_names = {pod.data["pod_name"] for pod in pods}
    events = get_events(pod_names)
    logs = get_logs(pod_names)
    deployment = get_deployment_config()

    for evidence in pods:
        print(evidence.model_dump())
    for evidence in events:
        print(evidence.model_dump())
    for evidence in logs:
        print(evidence.model_dump())
    print(deployment.model_dump())
