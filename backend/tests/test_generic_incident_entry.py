from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.agent import investigation_tools as tools, nodes, run
from app.agent.models import PolicyDecision, RemediationPlan
from app.models.evidence import Evidence
from app.models.incident import IncidentContext
from app.services import investigation


def evidence(category, summary="Observed", data=None):
    return Evidence(source="kubernetes", category=category, summary=summary, data=data or {})


def test_collector_custom_target_and_preprocessing(monkeypatch):
    calls = {}
    pod = evidence("pod_status", data={"pod_name": "checkout-1"})
    event = Evidence(source="kubernetes", category="kubernetes_event", summary="Probe failed",
                     severity="warning", data={"reason": "Unhealthy", "message": "Probe failed"})
    log = Evidence(source="logs", category="application_log", summary="Log",
                   data={"logs": "ERROR failed\nERROR failed"})
    deployment = evidence("deployment_config", data={"deployment_name": "checkout-backend"})
    monkeypatch.setattr(investigation, "load_cluster", Mock())
    for name, result in (("get_pod_status", [pod]), ("get_events", [event]),
                         ("get_logs", [log]), ("get_deployment", [deployment])):
        mock = Mock(return_value=result)
        monkeypatch.setattr(investigation, name, mock)
        calls[name] = mock
    incident = investigation.collect_incident_context(
        namespace="staging", service="checkout-service",
        label_selector="app=checkout", deployment_name="checkout-backend")
    assert (incident.namespace, incident.service, incident.label_selector, incident.deployment_name) == (
        "staging", "checkout-service", "app=checkout", "checkout-backend")
    assert [item.category for item in incident.evidence] == [
        "pod_status", "kubernetes_event", "application_log", "deployment_config"]
    assert incident.evidence[2].data["logs"] == "ERROR failed"
    assert all(mock.call_args.args == (incident,) for mock in calls.values())


def test_collector_defaults_and_no_pods(monkeypatch):
    monkeypatch.setattr(investigation, "load_cluster", Mock())
    monkeypatch.setattr(investigation, "get_pod_status", Mock(return_value=[]))
    monkeypatch.setattr(investigation, "get_events", Mock(return_value=[]))
    logs = Mock()
    monkeypatch.setattr(investigation, "get_logs", logs)
    monkeypatch.setattr(investigation, "get_deployment", Mock(return_value=[
        evidence("deployment_config", data={"deployment_name": "checkout-service"})]))
    incident = investigation.collect_incident_context(namespace="staging", service="checkout-service")
    assert incident.label_selector is None and incident.deployment_name is None
    assert incident.evidence[0].data == {"label_selector": "app=checkout-service", "matching_pods": 0}
    assert len(incident.evidence) == 2
    logs.assert_not_called()


def test_missing_deployment_with_no_pods_returns_context(monkeypatch):
    from kubernetes.client.exceptions import ApiException

    monkeypatch.setattr(investigation, "load_cluster", Mock())
    monkeypatch.setattr(investigation, "get_pod_status", Mock(return_value=[]))
    monkeypatch.setattr(investigation, "get_events", Mock(return_value=[]))
    monkeypatch.setattr(investigation, "get_deployment", Mock(side_effect=ApiException(status=404)))
    incident = investigation.collect_incident_context(namespace="staging", service="checkout-service")
    assert [item.category for item in incident.evidence] == ["pod_status", "deployment_config"]
    assert incident.evidence[1].data["found"] is False


def test_read_tools_use_custom_incident_target(monkeypatch):
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=[])
    api.list_namespaced_event.return_value = NS(items=[NS(
        involved_object=NS(kind="Deployment", name="payments-backend"),
        type="Warning", reason="Failed", message="Rollout failed", count=1)])
    api.read_namespaced_deployment.return_value = NS(
        metadata=NS(name="payments-backend"), spec=NS(replicas=1,
        selector=NS(match_labels={"app": "payments"}),
        template=NS(metadata=NS(labels={"app": "payments"}),
                    spec=NS(containers=[], volumes=[]))))
    api.read_namespaced_service.return_value = NS(
        metadata=NS(name="payments-api"), spec=NS(selector={"app": "payments"},
                                                   ports=[], type="ClusterIP"))
    api.read_namespaced_endpoints.return_value = NS(subsets=[])
    monkeypatch.setattr(tools, "load_cluster", Mock())
    monkeypatch.setattr(tools.client, "CoreV1Api", Mock(return_value=api))
    monkeypatch.setattr(tools.client, "AppsV1Api", Mock(return_value=api))
    incident = IncidentContext(incident_id="I", namespace="staging", service="payments-api",
                               label_selector="app=payments", deployment_name="payments-backend")
    for name in tools.INVESTIGATION_TOOLS:
        tools.execute_investigation_tool(name, incident)
    assert api.list_namespaced_pod.call_args.kwargs == {
        "namespace": "staging", "label_selector": "app=payments"}
    api.read_namespaced_deployment.assert_any_call(name="payments-backend", namespace="staging")
    api.read_namespaced_service.assert_called_once_with(name="payments-api", namespace="staging")
    api.read_namespaced_endpoints.assert_called_once_with(name="payments-api", namespace="staging")
    events = tools.get_events(incident)
    assert events[0].reference == "deployment/payments-backend"
    assert all("payment-service" not in item.summary for item in events)


def test_default_selector_and_deployment_derive_from_service():
    incident = IncidentContext(incident_id="I", namespace="staging", service="checkout-service")
    assert tools.target_label_selector(incident) == "app=checkout-service"
    assert tools.target_deployment_name(incident) == "checkout-service"


def test_logs_use_selected_pods_when_container_name_differs(monkeypatch):
    api = Mock()
    api.list_namespaced_pod.return_value = NS(items=[NS(
        metadata=NS(name="payments-1", deletion_timestamp=None),
        spec=NS(containers=[NS(name="api"), NS(name="proxy")]))])
    api.read_namespaced_pod_log.return_value = "ERROR example"
    monkeypatch.setattr(tools, "load_cluster", Mock())
    monkeypatch.setattr(tools.client, "CoreV1Api", Mock(return_value=api))
    incident = IncidentContext(incident_id="I", namespace="staging", service="payments-api",
                               label_selector="app=payments", deployment_name="payments-backend")
    result = tools.execute_investigation_tool("get_logs", incident)
    assert {item.data["container"] for item in result} == {"api", "proxy"}
    assert all(item.reference.startswith("pod/payments-1/") for item in result)
    assert api.read_namespaced_pod_log.call_count == 2


def test_supplied_incident_skips_collection_and_retry_preserves_target(monkeypatch):
    supplied = IncidentContext(incident_id="I", namespace="staging", service="payments-api",
                               label_selector="app=payments", deployment_name="payments-backend")
    collect = Mock(return_value=supplied)
    monkeypatch.setattr(nodes, "collect_incident_context", collect)
    first = nodes.collect_evidence_node({"incident": supplied, "iteration": 0,
                                         "target_service": "conflicting-service"})
    assert first["incident"] is supplied
    assert first["target_service"] == "payments-api"
    collect.assert_not_called()
    retry = nodes.collect_evidence_node({"incident": supplied, "iteration": 1})
    collect.assert_called_once_with(namespace="staging", service="payments-api",
                                    label_selector="app=payments", deployment_name="payments-backend")
    assert retry["iteration"] == 2
    assert retry["diagnosis"] is None


def test_runtime_target_reaches_collector(monkeypatch):
    collected = IncidentContext(incident_id="I", namespace="staging", service="payments-api")
    collect = Mock(return_value=collected)
    monkeypatch.setattr(nodes, "collect_incident_context", collect)
    result = nodes.collect_evidence_node({
        "target_namespace": "staging", "target_service": "payments-api",
        "target_label_selector": "app=payments", "target_deployment": "payments-backend",
    })
    collect.assert_called_once_with(namespace="staging", service="payments-api",
                                    label_selector="app=payments", deployment_name="payments-backend")
    assert result["target_deployment"] == "payments-backend"


def test_cli_targets_and_backward_compatible_defaults(monkeypatch, capsys):
    class Graph:
        def stream(self, initial, stream_mode):
            assert stream_mode == "values"
            yield initial

    monkeypatch.setattr(run, "build_incident_graph", lambda: Graph())
    monkeypatch.setattr("sys.argv", ["app.agent.run", "--namespace", "staging", "--service",
                                   "payments-api", "--deployment", "payments-backend",
                                   "--label-selector", "app=payments"])
    run.main()
    output = capsys.readouterr().out
    for line in ("Target namespace: staging", "Target service: payments-api",
                 "Label selector: app=payments", "Deployment: payments-backend"):
        assert line in output
    monkeypatch.setattr("sys.argv", ["app.agent.run"])
    run.main()
    output = capsys.readouterr().out
    assert "Target namespace: operion-sandbox" in output
    assert "Target service: payment-service" in output
    assert "Label selector: app=payment-service" in output
    assert "Deployment: payment-service" in output


def test_custom_target_cannot_expand_write_action(monkeypatch):
    patch = Mock()
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    incident = IncidentContext(incident_id="I", namespace="staging", service="checkout-service")
    result = nodes.execute_node({
        "incident": incident, "approved": True,
        "remediation_plan": RemediationPlan(action="patch_environment_variable", reason="test"),
        "policy_decision": PolicyDecision(allowed=True, reason="test", normalized_parameters={
            "name": "PAYMENT_PROVIDER_URL", "value": "http://payment-provider.local"}),
    })
    assert result["execution_result"]["success"] is False
    patch.assert_not_called()
