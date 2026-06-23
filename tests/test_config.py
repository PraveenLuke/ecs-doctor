"""Tests for ecs_doctor.diagnosers.config."""
from __future__ import annotations

from tests.conftest import (
    ACCOUNT,
    CLUSTER,
    REGION,
    SERVICE,
    access_denied_error,
    make_ecs_client,
    make_service_cache,
)

from ecs_doctor.diagnosers.config import (
    _extract_health_check,
    _mask_env_value,
    _validate_fargate_cpu_memory,
    diagnose_config,
)
from ecs_doctor.models import FindingType, Severity

_TASK_DEF_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/my-td:1"


def _svc(task_def_arn=_TASK_DEF_ARN):
    return {
        "services": [
            {
                "serviceArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/{CLUSTER}/{SERVICE}",
                "serviceName": SERVICE,
                "clusterArn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{CLUSTER}",
                "taskDefinition": task_def_arn,
                "loadBalancers": [],
                "desiredCount": 2,
                "runningCount": 1,
                "pendingCount": 0,
                "launchType": "FARGATE",
                "deploymentConfiguration": {
                    "minimumHealthyPercent": 100,
                    "maximumPercent": 200,
                },
            }
        ]
    }


def _td(cpu="256", memory="512", requires=None):
    return {
        "taskDefinition": {
            "taskDefinitionArn": _TASK_DEF_ARN,
            "family": "my-td",
            "revision": 1,
            "cpu": cpu,
            "memory": memory,
            "networkMode": "awsvpc",
            "requiresCompatibilities": requires or ["FARGATE"],
            "containerDefinitions": [
                {
                    "name": "app",
                    "image": "my-image:latest",
                    "cpu": 256,
                    "memory": 512,
                    "essential": True,
                    "environment": [
                        {"name": "DEBUG", "value": "true"},
                        {"name": "DB_PASSWORD", "value": "secret123"},
                        {"name": "API_KEY", "value": "abcdef"},
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {"awslogs-group": "/ecs/my-service"},
                    },
                }
            ],
        }
    }


# ---------------------------------------------------------------------------
# _mask_env_value
# ---------------------------------------------------------------------------


class TestMaskEnvValue:
    def test_password_key_masked(self):
        assert _mask_env_value("DB_PASSWORD", "hunter2") == "***MASKED***"

    def test_api_key_masked(self):
        assert _mask_env_value("API_KEY", "abcdef") == "***MASKED***"

    def test_secret_key_masked(self):
        assert _mask_env_value("SECRET_TOKEN", "xyz") == "***MASKED***"

    def test_token_key_masked(self):
        assert _mask_env_value("AUTH_TOKEN", "tok") == "***MASKED***"

    def test_safe_key_passthrough(self):
        assert _mask_env_value("DEBUG", "true") == "true"
        assert _mask_env_value("PORT", "8080") == "8080"
        assert _mask_env_value("LOG_LEVEL", "info") == "info"


# ---------------------------------------------------------------------------
# _extract_health_check
# ---------------------------------------------------------------------------


class TestExtractHealthCheck:
    def test_none_returns_none(self):
        assert _extract_health_check(None) is None

    def test_empty_dict_returns_none(self):
        assert _extract_health_check({}) is None

    def test_valid_health_check_extracted(self):
        hc = {
            "command": ["CMD", "curl", "/health"],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 10,
        }
        result = _extract_health_check(hc)
        assert result is not None
        assert result.command == ["CMD", "curl", "/health"]
        assert result.interval_seconds == 30
        assert result.timeout_seconds == 5
        assert result.retries == 3
        assert result.start_period_seconds == 10

    def test_defaults_applied_when_fields_missing(self):
        result = _extract_health_check({"command": ["CMD-SHELL", "true"]})
        assert result is not None
        assert result.interval_seconds == 30
        assert result.timeout_seconds == 5
        assert result.retries == 3
        assert result.start_period_seconds == 0


# ---------------------------------------------------------------------------
# _validate_fargate_cpu_memory
# ---------------------------------------------------------------------------


class TestValidateFargateCpuMemory:
    def test_valid_256_512_returns_none(self):
        td = {"requiresCompatibilities": ["FARGATE"], "cpu": "256", "memory": "512"}
        assert _validate_fargate_cpu_memory(td) is None

    def test_valid_1024_2048_returns_none(self):
        td = {"requiresCompatibilities": ["FARGATE"], "cpu": "1024", "memory": "2048"}
        assert _validate_fargate_cpu_memory(td) is None

    def test_invalid_memory_returns_critical_finding(self):
        td = {"requiresCompatibilities": ["FARGATE"], "cpu": "256", "memory": "999"}
        finding = _validate_fargate_cpu_memory(td)
        assert finding is not None
        assert finding.type == FindingType.INVALID_TASK_CONFIG
        assert finding.severity == Severity.CRITICAL

    def test_invalid_cpu_returns_finding(self):
        td = {"requiresCompatibilities": ["FARGATE"], "cpu": "999", "memory": "512"}
        finding = _validate_fargate_cpu_memory(td)
        assert finding is not None
        assert finding.type == FindingType.INVALID_TASK_CONFIG

    def test_non_fargate_skips_validation(self):
        td = {"requiresCompatibilities": ["EC2"], "cpu": "256", "memory": "999"}
        assert _validate_fargate_cpu_memory(td) is None

    def test_non_numeric_cpu_returns_none(self):
        td = {"requiresCompatibilities": ["FARGATE"], "cpu": "notanumber", "memory": "512"}
        assert _validate_fargate_cpu_memory(td) is None


# ---------------------------------------------------------------------------
# diagnose_config
# ---------------------------------------------------------------------------


class TestDiagnoseConfig:
    def _call(self, ecs):
        return diagnose_config(make_service_cache(ecs), ecs, CLUSTER, SERVICE, REGION, ACCOUNT)

    def test_success_returns_configs(self):
        ecs = make_ecs_client(describe_services=_svc(), describe_task_definition=_td())
        findings, svc_cfg, task_cfg = self._call(ecs)
        assert findings == []
        assert svc_cfg is not None
        assert svc_cfg.service_name == SERVICE
        assert svc_cfg.desired_count == 2
        assert task_cfg is not None
        assert task_cfg.family == "my-td"
        assert len(task_cfg.containers) == 1

    def test_env_vars_masked(self):
        ecs = make_ecs_client(describe_services=_svc(), describe_task_definition=_td())
        _, _, task_cfg = self._call(ecs)
        env = task_cfg.containers[0].environment
        assert env["DEBUG"] == "true"
        assert env["DB_PASSWORD"] == "***MASKED***"
        assert env["API_KEY"] == "***MASKED***"

    def test_log_group_extracted(self):
        ecs = make_ecs_client(describe_services=_svc(), describe_task_definition=_td())
        _, _, task_cfg = self._call(ecs)
        assert task_cfg.containers[0].log_group == "/ecs/my-service"

    def test_access_denied_describe_services(self):
        ecs = make_ecs_client(
            describe_services=access_denied_error("DescribeServices")
        )
        findings, svc_cfg, task_cfg = self._call(ecs)
        assert any(f.type == FindingType.IAM_DENIED for f in findings)
        assert svc_cfg is None
        assert task_cfg is None

    def test_access_denied_describe_task_definition(self):
        ecs = make_ecs_client(
            describe_services=_svc(),
            describe_task_definition=access_denied_error("DescribeTaskDefinition"),
        )
        findings, svc_cfg, task_cfg = self._call(ecs)
        assert any(f.type == FindingType.IAM_DENIED for f in findings)
        assert svc_cfg is not None
        assert task_cfg is None

    def test_invalid_fargate_combo_produces_finding(self):
        ecs = make_ecs_client(
            describe_services=_svc(), describe_task_definition=_td(cpu="256", memory="999")
        )
        findings, _, _ = self._call(ecs)
        assert any(f.type == FindingType.INVALID_TASK_CONFIG for f in findings)

    def test_no_service_found_returns_empty(self):
        ecs = make_ecs_client(describe_services={"services": []})
        findings, svc_cfg, task_cfg = self._call(ecs)
        assert findings == []
        assert svc_cfg is None
        assert task_cfg is None

    def test_service_without_task_def_arn(self):
        svc_no_td = {"services": [{"serviceName": SERVICE, "loadBalancers": [],
                                    "desiredCount": 1, "runningCount": 0, "pendingCount": 0,
                                    "serviceArn": "arn:...", "clusterArn": "arn:..."}]}
        ecs = make_ecs_client(describe_services=svc_no_td)
        findings, svc_cfg, task_cfg = self._call(ecs)
        assert findings == []
        assert svc_cfg is not None
        assert task_cfg is None
