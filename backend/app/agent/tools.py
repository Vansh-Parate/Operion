import time
from typing import Any

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from app.agent.policy import ALLOWED_ENV_UPDATES
from app.tools.k8s_client import load_cluster


class KubernetesRemediationError(Exception):
    """A safe, value-free error from the supported Kubernetes remediation."""


def patch_deployment_environment_variable(
    namespace: str,
    deployment_name: str,
    container_name: str,
    env_name: str,
    env_value: str,
) -> dict[str, Any]:
    if (namespace, deployment_name, container_name) != (
        "operion-sandbox", "payment-service", "payment-service"
    ) or ALLOWED_ENV_UPDATES.get(env_name) != env_value:
        raise KubernetesRemediationError("The requested environment update is not allowed.")

    try:
        load_cluster()
        api = client.AppsV1Api()
        deployment = api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        container = next(
            (item for item in deployment.spec.template.spec.containers if item.name == container_name),
            None,
        )
        if container is None:
            raise KubernetesRemediationError("Target container was not found in the Deployment.")
        existing = next((item for item in (container.env or []) if item.name == env_name), None)
        if existing is not None and existing.value_from is not None:
            raise KubernetesRemediationError("A referenced environment variable cannot be changed.")

        # Strategic merge keys both containers and env entries by name. Sending only
        # the target entry preserves other containers, env values, and references.
        body = {"spec": {"template": {"spec": {"containers": [{
            "name": container_name,
            "env": [{"name": env_name, "value": env_value}],
        }]}}}}
        api.patch_namespaced_deployment(
            name=deployment_name, namespace=namespace, body=body,
            _content_type="application/strategic-merge-patch+json",
        )
    except KubernetesRemediationError:
        raise
    except ApiException as exc:
        raise KubernetesRemediationError(
            f"Kubernetes Deployment API request failed (status {exc.status})."
        ) from None
    except Exception:
        # Configuration and transport errors may include request data in their text.
        raise KubernetesRemediationError("Kubernetes Deployment update failed.") from None

    return {
        "success": True,
        "action": "patch_environment_variable",
        "deployment": deployment_name,
        "container": container_name,
        "name": env_name,
    }


def verify_payment_service_recovery(
    namespace: str,
    label_selector: str = "app=payment-service",
    timeout_seconds: int = 90,
    poll_interval_seconds: int = 3,
) -> dict[str, Any]:
    if namespace != "operion-sandbox" or label_selector != "app=payment-service":
        raise KubernetesRemediationError("Recovery verification target is not allowed.")
    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise ValueError("Verification timing must be nonnegative with a positive poll interval.")

    try:
        load_cluster()
        api = client.CoreV1Api()
    except Exception:
        raise KubernetesRemediationError("Kubernetes recovery verification could not start.") from None

    deadline = time.monotonic() + timeout_seconds
    last_result: dict[str, Any] = {
        "recovered": False, "pod_name": None, "ready": False,
        "current_state": None, "waiting_reason": None,
        "reason": "Recovery verification timed out.",
    }
    while True:
        try:
            pods = api.list_namespaced_pod(namespace=namespace, label_selector=label_selector).items
        except ApiException as exc:
            raise KubernetesRemediationError(
                f"Kubernetes pod API request failed (status {exc.status})."
            ) from None
        except Exception:
            raise KubernetesRemediationError("Kubernetes recovery verification failed.") from None

        active = [pod for pod in pods if pod.metadata.deletion_timestamp is None]
        active.sort(
            key=lambda pod: (
                pod.metadata.creation_timestamp.timestamp()
                if pod.metadata.creation_timestamp is not None else 0
            ), reverse=True,
        )
        for pod in active[:1]:
            status = next(
                (item for item in (pod.status.container_statuses or []) if item.name == "payment-service"),
                None,
            )
            state = status.state if status else None
            waiting_reason = state.waiting.reason if state and state.waiting else None
            current_state = (
                "running" if state and state.running else
                "waiting" if state and state.waiting else
                "terminated" if state and state.terminated else None
            )
            pod_ready = any(
                condition.type == "Ready" and condition.status == "True"
                for condition in (pod.status.conditions or [])
            )
            ready = status.ready is True and pod_ready if status else False
            result = {
                "recovered": current_state == "running" and ready and waiting_reason is None,
                "pod_name": pod.metadata.name,
                "ready": ready,
                "current_state": current_state,
                "waiting_reason": waiting_reason,
                "reason": "payment-service recovered after remediation.",
            }
            if result["recovered"]:
                return result
            last_result = result
        if time.monotonic() >= deadline:
            last_result["reason"] = "Recovery verification timed out."
            return last_result
        time.sleep(min(poll_interval_seconds, max(0, deadline - time.monotonic())))
