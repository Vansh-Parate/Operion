from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import structlog

NAMESPACE = "operion-sandbox"
logger = structlog.get_logger(__name__)


def load_cluster():
    config.load_kube_config()
    logger.info("cluster_config_loaded")


def get_pods():
    load_cluster()

    v1 = client.CoreV1Api()

    logger.info("pod_query_started", namespace=NAMESPACE, label_selector="app=payment-service")
    try:
        pods = v1.list_namespaced_pod(
            namespace=NAMESPACE,
            label_selector="app=payment-service"
        )
    except ApiException:
        logger.exception("kubernetes_api_error", namespace=NAMESPACE)
        raise

    result = []

    for pod in pods.items:
        container = pod.status.container_statuses[0]

        state = container.state
        last_state = container.last_state

        pod_data = {
            "pod_name": pod.metadata.name,
            "phase": pod.status.phase,
            "ready": container.ready,
            "restart_count": container.restart_count,
            "image": container.image,
            "current_state": None,
            "waiting_reason": None,
            "last_exit_code": None,
            "last_termination_reason": None,
        }

        if state.waiting:
            pod_data["current_state"] = "waiting"
            pod_data["waiting_reason"] = state.waiting.reason

        elif state.running:
            pod_data["current_state"] = "running"

        elif state.terminated:
            pod_data["current_state"] = "terminated"
            pod_data["last_exit_code"] = state.terminated.exit_code
            pod_data["last_termination_reason"] = state.terminated.reason

        if last_state.terminated:
            pod_data["last_exit_code"] = last_state.terminated.exit_code
            pod_data["last_termination_reason"] = last_state.terminated.reason

        result.append(pod_data)
        logger.info(
            "pod_status_collected",
            pod_name=pod_data["pod_name"],
            phase=pod_data["phase"],
            current_state=pod_data["current_state"],
        )

    return result

def get_events():
    load_cluster()

    v1 = client.CoreV1Api()

    logger.info(
        "event_query_started",
        namespace=NAMESPACE
    )

    try:
        events = v1.list_namespaced_event(
            namespace=NAMESPACE
        )

        result = []

        for event in events.items:

            # Only events related to payment-service pods
            if not event.involved_object.name.startswith("payment-service"):
                continue

            event_data = {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "count": event.count,
                "object_name": event.involved_object.name,
                "object_kind": event.involved_object.kind,
            }

            result.append(event_data)

            logger.info(
                "kubernetes_event_collected",
                reason=event.reason,
                type=event.type,
                object_name=event.involved_object.name,
            )

        return result

    except client.ApiException:
        logger.exception(
            "kubernetes_api_error",
            operation="list_events",
            namespace=NAMESPACE,
        )
        raise


def get_logs():
    load_cluster()

    v1 = client.CoreV1Api()

    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector="app=payment-service"
    )

    result = []

    for pod in pods.items:
        pod_name = pod.metadata.name

        try:
            logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=NAMESPACE,
            container="payment-service",
            tail_lines=100,
            )

            if isinstance(logs, bytes):
                logs = logs.decode("utf-8", errors="replace")

            result.append({
                "pod_name": pod_name,
                "logs": logs,
            })

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

            if container.env:
                for env in container.env:
                    env_data = {
                        "name": env.name,
                    }

                    # Plain environment variable
                    if env.value is not None:
                        env_data["source"] = "literal"
                        env_data["value"] = env.value

                    # ConfigMap / Secret / field reference
                    elif env.value_from is not None:
                        env_data["source"] = "reference"

                    env_vars.append(env_data)

            containers.append({
                "name": container.name,
                "image": container.image,
                "environment_variables": env_vars,
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

        return result

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
    events = get_events()
    logs = get_logs()
    deployment = get_deployment_config()

    logger.info("pods_collected", pods=pods)
    logger.info("events_collected", events=events)
    logger.info("logs_collected", logs=logs)
    logger.info("deployment_collected", deployment=deployment)