from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.agent import nodes, tools
from app.agent.models import PolicyDecision, RemediationPlan


def env(name: str, value: str | None = None, value_from=None):
    return NS(name=name, value=value, value_from=value_from)


def deployment(environment, container_name="payment-service"):
    return NS(spec=NS(template=NS(spec=NS(containers=[NS(name=container_name, env=environment)]))))


@pytest.fixture
def apps_api(monkeypatch):
    api = Mock()
    monkeypatch.setattr(tools, "load_cluster", Mock())
    monkeypatch.setattr(tools.client, "AppsV1Api", Mock(return_value=api))
    return api


@pytest.mark.parametrize("existing", [False, True])
def test_patch_appends_or_updates_and_preserves_other_env(apps_api, existing):
    original = [env("UNRELATED", "keep")]
    if existing:
        original.append(env("PAYMENT_PROVIDER_URL", "old"))
    apps_api.read_namespaced_deployment.return_value = deployment(original)
    result = tools.patch_deployment_environment_variable(
        "operion-sandbox", "payment-service", "payment-service",
        "PAYMENT_PROVIDER_URL", "http://payment-provider.local",
    )
    assert result["success"] is True
    assert "value" not in result
    body = apps_api.patch_namespaced_deployment.call_args.kwargs["body"]
    assert body == {"spec": {"template": {"spec": {"containers": [{
        "name": "payment-service",
        "env": [{"name": "PAYMENT_PROVIDER_URL", "value": "http://payment-provider.local"}],
    }]}}}}
    assert apps_api.patch_namespaced_deployment.call_args.kwargs["_content_type"] == (
        "application/strategic-merge-patch+json"
    )
    assert original[0].value == "keep"
    assert len(original) == (2 if existing else 1)


def test_missing_container_fails_safely(apps_api):
    apps_api.read_namespaced_deployment.return_value = deployment([], "other")
    with pytest.raises(tools.KubernetesRemediationError, match="Target container"):
        tools.patch_deployment_environment_variable(
            "operion-sandbox", "payment-service", "payment-service",
            "PAYMENT_PROVIDER_URL", "http://payment-provider.local",
        )
    apps_api.patch_namespaced_deployment.assert_not_called()


def test_existing_reference_fails_safely(apps_api):
    apps_api.read_namespaced_deployment.return_value = deployment([
        env("PAYMENT_PROVIDER_URL", value_from=NS(secret_key_ref=NS(name="secret")))
    ])
    with pytest.raises(tools.KubernetesRemediationError, match="referenced"):
        tools.patch_deployment_environment_variable(
            "operion-sandbox", "payment-service", "payment-service",
            "PAYMENT_PROVIDER_URL", "http://payment-provider.local",
        )
    apps_api.patch_namespaced_deployment.assert_not_called()


@pytest.mark.parametrize("approved,allowed,action", [
    (False, True, "patch_environment_variable"),
    (True, False, "patch_environment_variable"),
    (True, True, "update_memory_limit"),
    (True, True, "update_readiness_probe"),
    (True, True, "restart_deployment"),
    (True, True, "none"),
])
def test_executor_guards_never_mutate(monkeypatch, approved, allowed, action):
    patch = Mock()
    monkeypatch.setattr(nodes, "patch_deployment_environment_variable", patch)
    state = {
        "approved": approved,
        "policy_decision": PolicyDecision(allowed=allowed, reason="test", normalized_parameters={
            "name": "PAYMENT_PROVIDER_URL", "value": "http://payment-provider.local",
        }),
        "remediation_plan": RemediationPlan(action=action, reason="test"),
    }
    result = nodes.execute_node(state)["execution_result"]
    assert result["success"] is False
    if approved and allowed:
        assert result["supported"] is False
    patch.assert_not_called()


def pod(name, created, *, ready=False, running=False, waiting=None, terminating=False):
    state = NS(running=NS() if running else None,
               waiting=NS(reason=waiting) if waiting else None, terminated=None)
    status = NS(name="payment-service", ready=ready, state=state)
    return NS(metadata=NS(name=name, creation_timestamp=datetime.fromtimestamp(created, timezone.utc),
                          deletion_timestamp=datetime.now(timezone.utc) if terminating else None),
              status=NS(container_statuses=[status], conditions=[
                  NS(type="Ready", status="True" if ready else "False")
              ]))


@pytest.fixture
def core_api(monkeypatch):
    api = Mock()
    monkeypatch.setattr(tools, "load_cluster", Mock())
    monkeypatch.setattr(tools.client, "CoreV1Api", Mock(return_value=api))
    return api


def test_verifier_recovers_running_ready_pod(core_api):
    core_api.list_namespaced_pod.return_value = NS(items=[pod("new", 2, ready=True, running=True)])
    result = tools.verify_payment_service_recovery("operion-sandbox", timeout_seconds=0)
    assert result == {"recovered": True, "pod_name": "new", "ready": True,
                      "current_state": "running", "waiting_reason": None,
                      "reason": "payment-service recovered after remediation."}


def test_verifier_requires_pod_ready_condition(core_api):
    candidate = pod("new", 2, ready=True, running=True)
    candidate.status.conditions[0].status = "False"
    core_api.list_namespaced_pod.return_value = NS(items=[candidate])
    result = tools.verify_payment_service_recovery("operion-sandbox", timeout_seconds=0)
    assert result["recovered"] is False
    assert result["ready"] is False


def test_verifier_ignores_terminating_pods(core_api):
    core_api.list_namespaced_pod.return_value = NS(items=[
        pod("old", 3, ready=True, running=True, terminating=True),
        pod("new", 2, ready=False, waiting="ContainerCreating"),
    ])
    result = tools.verify_payment_service_recovery("operion-sandbox", timeout_seconds=0)
    assert result["recovered"] is False
    assert result["pod_name"] == "new"


def test_verifier_retries_transient_states(core_api, monkeypatch):
    core_api.list_namespaced_pod.side_effect = [
        NS(items=[pod("new", 2, waiting="ContainerCreating")]),
        NS(items=[pod("new", 2, waiting="CrashLoopBackOff")]),
        NS(items=[pod("new", 2, ready=True, running=True)]),
    ]
    monkeypatch.setattr(tools.time, "sleep", Mock())
    result = tools.verify_payment_service_recovery("operion-sandbox", timeout_seconds=10)
    assert result["recovered"] is True
    assert core_api.list_namespaced_pod.call_count == 3


def test_verifier_times_out(core_api, monkeypatch):
    core_api.list_namespaced_pod.return_value = NS(items=[pod("new", 2, waiting="CrashLoopBackOff")])
    monkeypatch.setattr(tools.time, "monotonic", Mock(side_effect=[0, 1]))
    result = tools.verify_payment_service_recovery("operion-sandbox", timeout_seconds=1)
    assert result["recovered"] is False
    assert result["waiting_reason"] == "CrashLoopBackOff"
    assert result["reason"] == "Recovery verification timed out."
