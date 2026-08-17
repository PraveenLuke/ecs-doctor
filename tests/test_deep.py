"""Tests for optional --deep ECR / Secrets Manager probes."""
from __future__ import annotations

from unittest.mock import MagicMock

from tests.conftest import ACCOUNT, CLUSTER, REGION, SERVICE, access_denied_error, make_ecs_client, make_service_cache

from ecs_doctor.diagnosers.deep import (
    diagnose_deep,
    parse_ecr_image,
    parse_secret_arns,
)
from ecs_doctor.models import Finding, FindingType, Severity

_TD_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/my-td:1"
_ECR_IMAGE = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/payments:1.4.2"
_SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:db-password-AbCdEf"
_SSM_ARN = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/app/db-password"


def _pull_finding(reason: str = f"CannotPullContainerError: {_ECR_IMAGE}") -> Finding:
    return Finding(
        type=FindingType.IMAGE_PULL_FAILURE,
        message="Cannot pull container image",
        severity=Severity.CRITICAL,
        raw_data={"sample_stoppedReason": reason, "stopCode": "CannotPullContainerImage"},
        source="stop_reasons",
    )


def _secret_finding(reason: str = f"unable to retrieve secret {_SECRET_ARN}") -> Finding:
    return Finding(
        type=FindingType.SECRETS_INIT_FAILURE,
        message="Task failed to initialize resources",
        severity=Severity.CRITICAL,
        raw_data={"sample_stoppedReason": reason, "stopCode": "ResourceInitializationError"},
        source="stop_reasons",
    )


def _cache_with_image(image: str = _ECR_IMAGE) -> object:
    ecs = make_ecs_client(
        describe_services={"services": [{"taskDefinition": _TD_ARN}]},
        describe_task_definition={
            "taskDefinition": {
                "containerDefinitions": [{"name": "app", "image": image}],
            }
        },
    )
    return make_service_cache(ecs)


def test_parse_ecr_image_with_tag():
    parsed = parse_ecr_image(_ECR_IMAGE)
    assert parsed is not None
    assert parsed["repository"] == "payments"
    assert parsed["tag"] == "1.4.2"
    assert parsed["region"] == REGION
    assert parsed["digest"] is None


def test_parse_ecr_image_with_digest():
    digest = "sha256:" + "ab" * 32
    parsed = parse_ecr_image(f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/payments@{digest}")
    assert parsed is not None
    assert parsed["repository"] == "payments"
    assert parsed["digest"] == digest


def test_parse_ecr_image_nested_repo():
    parsed = parse_ecr_image(f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/team/payments:latest")
    assert parsed is not None
    assert parsed["repository"] == "team/payments"
    assert parsed["tag"] == "latest"


def test_parse_ecr_image_skips_dockerhub():
    assert parse_ecr_image("nginx:latest") is None
    assert parse_ecr_image("public.ecr.aws/amazonlinux/amazonlinux:2023") is None


def test_parse_secret_arns_from_stopped_reason():
    text = f"ResourceInitializationError: unable to retrieve secret from asm: {_SECRET_ARN}"
    assert parse_secret_arns(text) == [_SECRET_ARN]


def test_parse_secret_arns_includes_ssm():
    text = f"unable to retrieve ssm parameter {_SSM_ARN}"
    assert parse_secret_arns(text) == [_SSM_ARN]


def test_deep_skipped_without_pull_or_secret_findings():
    ecr = MagicMock()
    secrets = MagicMock()
    findings = diagnose_deep(
        findings=[Finding(type=FindingType.OOM_KILLED, message="oom", severity=Severity.HIGH, source="stop_reasons")],
        service_cache=_cache_with_image(),
        ecr_client=ecr,
        secrets_client=secrets,
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert findings == []
    ecr.describe_images.assert_not_called()
    secrets.describe_secret.assert_not_called()


def test_missing_ecr_image_is_not_found():
    ecr = make_ecs_client(describe_images=access_denied_error("DescribeImages", code="ImageNotFoundException"))
    extra = diagnose_deep(
        findings=[_pull_finding()],
        service_cache=_cache_with_image(),
        ecr_client=ecr,
        secrets_client=MagicMock(),
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert any(f.type == FindingType.IMAGE_NOT_FOUND for f in extra)
    f = next(x for x in extra if x.type == FindingType.IMAGE_NOT_FOUND)
    assert f.severity == Severity.CRITICAL
    assert "payments" in f.message


def test_existing_ecr_image_clarifies_pull_is_not_missing_tag():
    ecr = make_ecs_client(describe_images={"imageDetails": [{"imageTags": ["1.4.2"]}]})
    extra = diagnose_deep(
        findings=[_pull_finding()],
        service_cache=_cache_with_image(),
        ecr_client=ecr,
        secrets_client=MagicMock(),
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert extra
    assert all(f.type != FindingType.IMAGE_NOT_FOUND for f in extra)
    f = extra[0]
    assert f.type == FindingType.IMAGE_PULL_FAILURE
    assert f.source == "deep"
    assert "exists" in f.message.lower()
    assert "execution" in f.message.lower() or "NAT" in f.message or "endpoint" in f.message.lower()


def test_ecr_access_denied_is_iam_finding():
    ecr = make_ecs_client(describe_images=access_denied_error("DescribeImages"))
    extra = diagnose_deep(
        findings=[_pull_finding()],
        service_cache=_cache_with_image(),
        ecr_client=ecr,
        secrets_client=MagicMock(),
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert any(f.type == FindingType.IAM_DENIED for f in extra)
    assert "ecr:DescribeImages" in extra[0].message


def test_dockerhub_image_is_not_probed():
    ecr = MagicMock()
    extra = diagnose_deep(
        findings=[_pull_finding("CannotPullContainerError: nginx:latest")],
        service_cache=_cache_with_image("nginx:latest"),
        ecr_client=ecr,
        secrets_client=MagicMock(),
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    ecr.describe_images.assert_not_called()
    assert extra == []


def test_missing_secret_is_not_found():
    secrets = make_ecs_client(describe_secret=access_denied_error("DescribeSecret", code="ResourceNotFoundException"))
    extra = diagnose_deep(
        findings=[_secret_finding()],
        service_cache=_cache_with_image(),
        ecr_client=MagicMock(),
        secrets_client=secrets,
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert any(f.type == FindingType.SECRET_NOT_FOUND for f in extra)
    f = next(x for x in extra if x.type == FindingType.SECRET_NOT_FOUND)
    assert f.severity == Severity.CRITICAL
    assert "db-password" in f.message


def test_existing_secret_clarifies_init_is_not_missing_arn():
    secrets = make_ecs_client(describe_secret={"ARN": _SECRET_ARN, "Name": "db-password"})
    extra = diagnose_deep(
        findings=[_secret_finding()],
        service_cache=_cache_with_image(),
        ecr_client=MagicMock(),
        secrets_client=secrets,
        ssm_client=MagicMock(),
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert extra
    f = extra[0]
    assert f.type == FindingType.SECRETS_INIT_FAILURE
    assert f.source == "deep"
    assert "exists" in f.message.lower()


def test_missing_ssm_parameter_is_not_found():
    ssm = make_ecs_client(get_parameter=access_denied_error("GetParameter", code="ParameterNotFound"))
    extra = diagnose_deep(
        findings=[_secret_finding(f"unable to retrieve {_SSM_ARN}")],
        service_cache=_cache_with_image(),
        ecr_client=MagicMock(),
        secrets_client=MagicMock(),
        ssm_client=ssm,
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert any(f.type == FindingType.SECRET_NOT_FOUND for f in extra)


def test_no_ecr_client_skips_image_probe():
    extra = diagnose_deep(
        findings=[_pull_finding()],
        service_cache=_cache_with_image(),
        ecr_client=None,
        secrets_client=None,
        ssm_client=None,
        cluster=CLUSTER,
        service=SERVICE,
        region=REGION,
        account_id=ACCOUNT,
    )
    assert extra == []
