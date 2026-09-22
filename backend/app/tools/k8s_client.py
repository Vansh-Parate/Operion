from kubernetes import client, config

NAMESPACE = "operion-sandbox"


def load_cluster():
    config.load_kube_config()


def get_pods():
    load_cluster()

    v1 = client.CoreV1Api()

    pods = v1.list_namespaced_pod(
        namespace=NAMESPACE,
        label_selector="app=payment-service"
    )

    result = []

    for pod in pods.items:
        container = pod.status.container_statuses[0]

        result.append({
            "pod_name": pod.metadata.name,
            "phase": pod.status.phase,
            "ready": container.ready,
            "restart_count": container.restart_count,
        })

    return result


if __name__ == "__main__":
    print(get_pods())
