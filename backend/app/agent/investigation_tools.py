"""Incident-scoped, read-only Kubernetes investigation tools."""

import json
import re
from collections.abc import Callable
from typing import Any

from kubernetes import client

from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.tools.k8s_client import load_cluster


class InvestigationToolError(Exception):
    """A safe error from the read-only investigation boundary."""


def _safe_name(value: str, label: str) -> str:
    if not value or len(value) > 253 or not re.fullmatch(
        r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", value
    ) or ".." in value:
        raise InvestigationToolError(f"Invalid incident {label}.")
    return value


def _context(incident: IncidentContext) -> tuple[str, str]:
    return _safe_name(incident.namespace, "namespace"), _safe_name(incident.service, "service")


def _pods(api: Any, namespace: str, service: str) -> list[Any]:
    # Kubernetes label selectors escape neither names nor shell syntax; validate above.
    pods = api.list_namespaced_pod(namespace=namespace, label_selector=f"app={service}").items
    return [pod for pod in pods if pod.metadata.deletion_timestamp is None]


def _evidence(incident: IncidentContext, category: str, summary: str,
              data: dict[str, Any], reference: str, source: str = "kubernetes") -> Evidence:
    return Evidence(source=source, category=category, service=incident.service,
                    namespace=incident.namespace, summary=summary, data=data, reference=reference)


def get_pod_status(incident: IncidentContext) -> list[Evidence]:
    namespace, service = _context(incident)
    api = client.CoreV1Api()
    result = []
    for pod in _pods(api, namespace, service):
        statuses = []
        for status in pod.status.container_statuses or []:
            state = status.state
            previous = status.last_state
            statuses.append({
                "name": status.name, "ready": status.ready,
                "restart_count": status.restart_count,
                "current_state": "waiting" if state and state.waiting else
                    "running" if state and state.running else
                    "terminated" if state and state.terminated else None,
                "waiting_reason": state.waiting.reason if state and state.waiting else None,
                "last_termination_reason": previous.terminated.reason
                    if previous and previous.terminated else None,
                "last_exit_code": previous.terminated.exit_code
                    if previous and previous.terminated else None,
            })
        primary = next((item for item in statuses if item["name"] == service),
                       statuses[0] if statuses else {})
        data = {"pod_name": pod.metadata.name, "phase": pod.status.phase,
                "containers": statuses, **{k: v for k, v in primary.items() if k != "name"}}
        result.append(_evidence(incident, "pod_status",
                                f"Pod {pod.metadata.name} status: {pod.status.phase}", data,
                                f"pod/{pod.metadata.name}"))
    return result


def get_events(incident: IncidentContext) -> list[Evidence]:
    namespace, service = _context(incident)
    api = client.CoreV1Api()
    pod_names = {pod.metadata.name for pod in _pods(api, namespace, service)}
    result = []
    for event in api.list_namespaced_event(namespace=namespace).items:
        involved = event.involved_object
        if not (involved.kind == "Pod" and involved.name in pod_names
                or involved.kind in ("Service", "Deployment") and involved.name == service):
            continue
        data = {"type": event.type, "reason": event.reason, "message": event.message,
                "count": event.count, "object_name": involved.name, "object_kind": involved.kind}
        result.append(_evidence(incident, "kubernetes_event",
                                f"Kubernetes event {event.reason}: {event.message}", data,
                                f"{involved.kind.lower()}/{involved.name}"))
    return result


def _logs(incident: IncidentContext, previous: bool) -> list[Evidence]:
    namespace, service = _context(incident)
    api = client.CoreV1Api()
    result = []
    for pod in _pods(api, namespace, service):
        container_names = [item.name for item in pod.spec.containers]
        container = service if service in container_names else container_names[0] if len(container_names) == 1 else None
        if container is None:
            continue
        logs = api.read_namespaced_pod_log(name=pod.metadata.name, namespace=namespace,
                                           container=container, previous=previous, tail_lines=200)
        if isinstance(logs, bytes):
            logs = logs.decode("utf-8", errors="replace")
        category = "previous_application_log" if previous else "application_log"
        suffix = "previous-logs" if previous else "logs"
        result.append(_evidence(incident, category, f"Collected {suffix} from pod {pod.metadata.name}",
                                {"pod_name": pod.metadata.name, "container": container, "logs": logs},
                                f"pod/{pod.metadata.name}/{suffix}", source="logs"))
    return result


def get_logs(incident: IncidentContext) -> list[Evidence]:
    return _logs(incident, False)


def get_previous_logs(incident: IncidentContext) -> list[Evidence]:
    return _logs(incident, True)


def _deployment(incident: IncidentContext) -> Any:
    namespace, service = _context(incident)
    return client.AppsV1Api().read_namespaced_deployment(name=service, namespace=namespace)


def get_deployment(incident: IncidentContext) -> list[Evidence]:
    deployment = _deployment(incident)
    containers = []
    for container in deployment.spec.template.spec.containers:
        env = []
        for item in container.env or []:
            entry = {"name": item.name}
            if item.value is not None:
                entry["source"] = "literal"
                entry["value"] = item.value
            elif item.value_from is not None:
                entry["source"] = "reference"
                for attr in ("config_map_key_ref", "secret_key_ref", "field_ref"):
                    ref = getattr(item.value_from, attr, None)
                    if ref is not None:
                        entry[attr] = {"name": getattr(ref, "name", None),
                                       "key": getattr(ref, "key", None)} if attr != "field_ref" else {
                                           "field_path": ref.field_path}
            env.append(entry)
        resources = container.resources
        containers.append({"name": container.name, "image": container.image,
                           "environment_variables": env,
                           "resources": {"requests": dict(resources.requests or {}) if resources else {},
                                         "limits": dict(resources.limits or {}) if resources else {}}})
    data = {"deployment_name": deployment.metadata.name, "replicas": deployment.spec.replicas,
            "selector": dict(deployment.spec.selector.match_labels or {}),
            "pod_labels": dict(deployment.spec.template.metadata.labels or {}),
            "containers": containers}
    return [_evidence(incident, "deployment_config", f"Deployment {deployment.metadata.name} configuration",
                      data, f"deployment/{deployment.metadata.name}")]


def get_service(incident: IncidentContext) -> list[Evidence]:
    namespace, service = _context(incident)
    obj = client.CoreV1Api().read_namespaced_service(name=service, namespace=namespace)
    data = {"name": obj.metadata.name, "selector": dict(obj.spec.selector or {}),
            "ports": [{"port": p.port, "target_port": str(p.target_port)} for p in obj.spec.ports or []],
            "type": obj.spec.type}
    return [_evidence(incident, "service_config", f"Service {service} configuration", data,
                      f"service/{service}")]


def get_endpoints(incident: IncidentContext) -> list[Evidence]:
    namespace, service = _context(incident)
    obj = client.CoreV1Api().read_namespaced_endpoints(name=service, namespace=namespace)
    ready, not_ready = [], []
    for subset in obj.subsets or []:
        ready.extend(address.ip for address in subset.addresses or [])
        not_ready.extend(address.ip for address in subset.not_ready_addresses or [])
    return [_evidence(incident, "endpoints", f"Endpoints for service {service}",
                      {"ready_addresses": ready, "not_ready_addresses": not_ready},
                      f"endpoints/{service}")]


def get_configmaps(incident: IncidentContext) -> list[Evidence]:
    namespace, _ = _context(incident)
    deployment = _deployment(incident)
    names = set()
    for container in deployment.spec.template.spec.containers:
        for source in container.env_from or []:
            ref = getattr(source, "config_map_ref", None)
            if ref is not None:
                names.add(_safe_name(ref.name, "ConfigMap name"))
        for env in container.env or []:
            ref = getattr(env.value_from, "config_map_key_ref", None) if env.value_from else None
            if ref is not None:
                names.add(_safe_name(ref.name, "ConfigMap name"))
    for volume in deployment.spec.template.spec.volumes or []:
        ref = getattr(volume, "config_map", None)
        if ref is not None:
            names.add(_safe_name(ref.name, "ConfigMap name"))
    api = client.CoreV1Api()
    result = []
    for name in sorted(names):
        obj = api.read_namespaced_config_map(name=name, namespace=namespace)
        result.append(_evidence(incident, "configmap", f"Referenced ConfigMap {name}",
                                {"name": name, "data": dict(obj.data or {})}, f"configmap/{name}"))
    return result


INVESTIGATION_TOOLS: dict[str, Callable[[IncidentContext], list[Evidence]]] = {
    "get_pod_status": get_pod_status,
    "get_events": get_events,
    "get_logs": get_logs,
    "get_deployment": get_deployment,
    "get_service": get_service,
    "get_endpoints": get_endpoints,
    "get_configmaps": get_configmaps,
    "get_previous_logs": get_previous_logs,
}


def execute_investigation_tool(tool_name: str, incident: IncidentContext) -> list[Evidence]:
    tool = INVESTIGATION_TOOLS.get(tool_name)
    if tool is None:
        raise InvestigationToolError(f"Unsupported investigation tool: {tool_name}")
    _context(incident)
    try:
        load_cluster()
        return tool(incident)
    except InvestigationToolError:
        raise
    except Exception as exc:
        raise InvestigationToolError(f"Read-only investigation tool {tool_name} failed.") from exc


def _identity(evidence: Evidence) -> str:
    data = evidence.data.copy()
    if evidence.category == "kubernetes_event":
        data.pop("count", None)
    return json.dumps([evidence.source, evidence.category, evidence.namespace,
                       evidence.service, evidence.reference, data], sort_keys=True, default=str)


def merge_evidence(incident: IncidentContext, new_evidence: list[Evidence]) -> IncidentContext:
    merged = list(incident.evidence)
    seen = {_identity(item) for item in merged}
    for item in new_evidence:
        identity = _identity(item)
        if identity not in seen:
            merged.append(item)
            seen.add(identity)
    return incident.model_copy(update={"evidence": merged})
