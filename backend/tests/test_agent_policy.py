from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.models import PolicyDecision, RemediationAction, RemediationPlan
from app.agent.policy import ALLOWED_ENV_UPDATES, MAX_MEMORY_MIB, parse_mib, validate_plan
from app.agent.state import IncidentState
from app.models.evidence import Evidence
from app.models.incident import IncidentContext


def make_plan(action: RemediationAction, **parameters: Any) -> RemediationPlan:
    return RemediationPlan(action=action, reason="Observed incident", evidence_ids=["E1"],
                           parameters=parameters)


@pytest.fixture
def inc001() -> IncidentContext:
    return IncidentContext(
        incident_id="INC-001", service="payment-service", namespace="operion-sandbox",
        evidence=[
            Evidence(source="kubernetes", category="deployment_config", summary="Deployment",
                     service="payment-service", namespace="operion-sandbox",
                     data={"deployment_name": "payment-service", "containers": [
                         {"name": "payment-service", "environment_variables": [
                             {"name": "UNRELATED", "value": "safe"},
                         ]},
                     ]}),
            Evidence(source="logs", category="application_log", summary="Config error",
                     service="payment-service", namespace="operion-sandbox",
                     data={"logs": "CONFIG_ERROR: PAYMENT_PROVIDER_URL environment variable is required"}),
            Evidence(source="kubernetes", category="pod_status", summary="CrashLoop",
                     service="payment-service", namespace="operion-sandbox",
                     data={"pod_name": "payment-1", "ready": False, "current_state": "waiting",
                           "waiting_reason": "CrashLoopBackOff"}),
        ],
    )


def test_none_is_allowed_without_evidence() -> None:
    decision = validate_plan(RemediationPlan(action="none", reason="No change needed"))
    assert decision.allowed
    assert decision.reason == "No mutation requested."
    assert decision.normalized_parameters == {}


@pytest.mark.parametrize("action", [
    "patch_environment_variable", "update_memory_limit", "update_readiness_probe",
    "restart_deployment",
])
def test_mutation_requires_evidence(action: RemediationAction) -> None:
    decision = validate_plan(RemediationPlan(action=action, reason="No evidence"), "128Mi")
    assert not decision.allowed
    assert "evidence" in decision.reason.lower()
    assert decision.normalized_parameters == {}


def test_blank_evidence_is_rejected() -> None:
    plan = RemediationPlan(action="restart_deployment", reason="Empty", evidence_ids=[" "])
    assert not validate_plan(plan).allowed


def test_allowed_environment_update(inc001: IncidentContext) -> None:
    decision = validate_plan(make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL"),
                             incident=inc001)
    assert decision.allowed
    assert decision.normalized_parameters == {
        "name": "PAYMENT_PROVIDER_URL", "value": ALLOWED_ENV_UPDATES["PAYMENT_PROVIDER_URL"],
    }


@pytest.mark.parametrize("missing", ["deployment", "variable_absence", "logs",
                                      "config_error", "variable_in_log", "pod", "failure"])
def test_inc001_precondition_fails_closed(inc001: IncidentContext, missing: str) -> None:
    deployment, logs, pod = inc001.evidence
    if missing == "deployment":
        inc001.evidence.remove(deployment)
    elif missing == "variable_absence":
        deployment.data["containers"][0]["environment_variables"].append(
            {"name": "PAYMENT_PROVIDER_URL", "value": "already-set"}
        )
    elif missing == "logs":
        inc001.evidence.remove(logs)
    elif missing == "config_error":
        logs.data["logs"] = "PAYMENT_PROVIDER_URL environment variable is required"
    elif missing == "variable_in_log":
        logs.data["logs"] = "CONFIG_ERROR: OTHER_VARIABLE is required"
    elif missing == "pod":
        inc001.evidence.remove(pod)
    else:
        pod.data.update(current_state="waiting", waiting_reason="ContainerCreating")
    decision = validate_plan(make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL"),
                             incident=inc001)
    assert decision.allowed is False
    assert decision.normalized_parameters == {}


def test_inc001_requires_same_log_line(inc001: IncidentContext) -> None:
    inc001.evidence[1].data["logs"] = "CONFIG_ERROR: OTHER_VARIABLE\nPAYMENT_PROVIDER_URL missing"
    assert not validate_plan(make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL"),
                             incident=inc001).allowed


def test_inc001_requires_target_scope(inc001: IncidentContext) -> None:
    inc001.evidence[2].namespace = "other-namespace"
    assert not validate_plan(make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL"),
                             incident=inc001).allowed


def test_inc001_accepts_failing_terminated_pod(inc001: IncidentContext) -> None:
    inc001.evidence[2].data.update(current_state="terminated", waiting_reason=None)
    assert validate_plan(make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL"),
                         incident=inc001).allowed


@pytest.mark.parametrize("name", ["SECRET_KEY", "INCIDENT_MODE", "PATH", None, [], {}])
def test_arbitrary_environment_variable_is_rejected(name: Any) -> None:
    assert not validate_plan(make_plan("patch_environment_variable", name=name)).allowed


def test_arbitrary_environment_value_is_ignored(inc001: IncidentContext) -> None:
    plan = make_plan("patch_environment_variable", name="PAYMENT_PROVIDER_URL",
                     value="http://untrusted.example", namespace="other", secret="injected")
    decision = validate_plan(plan, incident=inc001)
    assert decision.allowed
    assert decision.normalized_parameters == {
        "name": "PAYMENT_PROVIDER_URL", "value": "http://payment-provider.local",
    }
    assert plan.parameters["value"] == "http://untrusted.example"


def test_memory_limit_is_doubled() -> None:
    decision = validate_plan(make_plan("update_memory_limit"), "128Mi")
    assert decision.allowed
    assert decision.normalized_parameters == {"memory_limit": "256Mi"}


def test_memory_limit_at_maximum_is_allowed() -> None:
    decision = validate_plan(make_plan("update_memory_limit"), f"{MAX_MEMORY_MIB // 2}Mi")
    assert decision.allowed
    assert decision.normalized_parameters == {"memory_limit": f"{MAX_MEMORY_MIB}Mi"}


@pytest.mark.parametrize("current", ["513Mi", "1024Mi", "2048Mi"])
def test_memory_limit_above_maximum_is_rejected(current: str) -> None:
    decision = validate_plan(make_plan("update_memory_limit"), current)
    assert not decision.allowed
    assert decision.normalized_parameters == {}


@pytest.mark.parametrize("value", [
    "128", "1Gi", "128M", "128mi", "", "Mi", "0Mi", "-1Mi", "1.5Mi",
    "+128Mi", " 128Mi", "128Mi\n", "1e3Mi", None, 128,
])
def test_invalid_memory_formats_are_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        parse_mib(value)
    assert not validate_plan(make_plan("update_memory_limit"), value).allowed


def test_parse_mib() -> None:
    assert parse_mib("128Mi") == 128


def test_memory_value_proposed_by_llm_is_ignored() -> None:
    decision = validate_plan(make_plan("update_memory_limit", memory_limit="999999Gi"), "128Mi")
    assert decision.allowed
    assert decision.normalized_parameters == {"memory_limit": "256Mi"}


def test_health_readiness_path_is_allowed() -> None:
    decision = validate_plan(make_plan("update_readiness_probe", path="/health"))
    assert decision.allowed
    assert decision.normalized_parameters == {"path": "/health"}


def test_readiness_path_defaults_to_health() -> None:
    decision = validate_plan(make_plan("update_readiness_probe", port=9999))
    assert decision.allowed
    assert decision.normalized_parameters == {"path": "/health"}


@pytest.mark.parametrize("path", ["/arbitrary", "/health?override=1", "", None, [], {}])
def test_arbitrary_readiness_path_is_rejected(path: Any) -> None:
    assert not validate_plan(make_plan("update_readiness_probe", path=path)).allowed


def test_restart_is_allowed_with_evidence() -> None:
    decision = validate_plan(make_plan("restart_deployment", namespace="untrusted"))
    assert decision.allowed
    assert decision.normalized_parameters == {}


def test_unknown_action_is_rejected_by_schema_and_policy() -> None:
    with pytest.raises(ValidationError):
        RemediationPlan(action="delete_deployment", reason="Unsafe")
    bypassed = RemediationPlan.model_construct(action="delete_deployment", reason="Unsafe",
                                               evidence_ids=["E1"])
    assert not validate_plan(bypassed).allowed


def test_schema_defaults_are_independent() -> None:
    first = RemediationPlan(action="none", reason="First")
    second = RemediationPlan(action="none", reason="Second")
    first.evidence_ids.append("E1")
    first.parameters["injected"] = True
    assert second.evidence_ids == []
    assert second.parameters == {}
    assert second.requires_approval is True
    decision = PolicyDecision(allowed=True, reason="First")
    decision.normalized_parameters["injected"] = True
    assert PolicyDecision(allowed=True, reason="Second").normalized_parameters == {}


def test_state_has_only_requested_fields() -> None:
    assert IncidentState.__required_keys__ == {
        "incident", "diagnosis", "remediation_plan", "policy_decision", "approved",
        "execution_result", "verification_result", "iteration", "resolved",
    }
