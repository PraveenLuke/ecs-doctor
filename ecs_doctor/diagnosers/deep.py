"""Optional --deep probes: ECR image existence and Secrets/SSM existence.

These calls are slow or narrow, so they run only when the default diagnosis
already found CannotPullContainerImage or ResourceInitializationError.
"""
from __future__ import annotations

import re

from botocore.exceptions import ClientError

from ecs_doctor._aws import ServiceDataCache, _AccessDeniedCached, iam_finding, is_access_denied
from ecs_doctor.models import Finding, FindingType, Severity

_SOURCE = "deep"

_ECR_IMAGE_RE = re.compile(
    r"(?P<account>\d+)\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com(?:\.cn)?/"
    r"(?P<repository>[^:@\s]+)"
    r"(?:@(?P<digest>sha256:[0-9a-f]+)|:(?P<tag>[^@\s]+))?",
    re.IGNORECASE,
)
_SECRET_ARN_RE = re.compile(
    r"arn:aws(?:-cn|-us-gov)?:secretsmanager:[a-z0-9-]+:\d+:secret:[^\s,;]+",
    re.IGNORECASE,
)
_SSM_ARN_RE = re.compile(
    r"arn:aws(?:-cn|-us-gov)?:ssm:[a-z0-9-]+:\d+:parameter/[^\s,;]+",
    re.IGNORECASE,
)
_IMAGE_MISSING_CODES = frozenset({"ImageNotFoundException", "RepositoryNotFoundException"})
_SECRET_MISSING_CODES = frozenset({"ResourceNotFoundException"})
_SSM_MISSING_CODES = frozenset({"ParameterNotFound"})


def _error_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


def parse_ecr_image(uri: str) -> dict[str, str | None] | None:
    match = _ECR_IMAGE_RE.search(uri.strip())
    if not match:
        return None
    return {
        "repository": match.group("repository"),
        "tag": match.group("tag"),
        "digest": match.group("digest"),
        "region": match.group("region"),
        "account": match.group("account"),
        "uri": match.group(0),
    }


def parse_secret_arns(text: str) -> list[str]:
    found = _SECRET_ARN_RE.findall(text) + _SSM_ARN_RE.findall(text)
    return list(dict.fromkeys(found))


def _task_definition_images(service_cache: ServiceDataCache, cluster: str, service: str, region: str, account_id: str) -> list[str]:
    try:
        svc = service_cache.get_service(cluster, service, region, account_id)
    except _AccessDeniedCached:
        return []
    if not svc or not svc.get("taskDefinition"):
        return []
    try:
        td = service_cache.get_task_definition(svc["taskDefinition"])
    except _AccessDeniedCached:
        return []
    return [
        c.get("image", "")
        for c in td.get("containerDefinitions", [])
        if c.get("image")
    ]


def _images_to_probe(
    findings: list[Finding],
    service_cache: ServiceDataCache,
    cluster: str,
    service: str,
    region: str,
    account_id: str,
) -> list[str]:
    images: list[str] = []
    for finding in findings:
        if finding.type != FindingType.IMAGE_PULL_FAILURE:
            continue
        reason = str(finding.raw_data.get("sample_stoppedReason") or "")
        for match in _ECR_IMAGE_RE.finditer(reason):
            images.append(match.group(0))
        images.extend(_task_definition_images(service_cache, cluster, service, region, account_id))
    return list(dict.fromkeys(images))


def _secret_arns_to_probe(findings: list[Finding]) -> list[str]:
    arns: list[str] = []
    for finding in findings:
        if finding.type != FindingType.SECRETS_INIT_FAILURE:
            continue
        reason = str(finding.raw_data.get("sample_stoppedReason") or "")
        arns.extend(parse_secret_arns(reason))
        arns.extend(parse_secret_arns(finding.message))
    return list(dict.fromkeys(arns))


def _probe_ecr_image(ecr_client, image_uri: str, region: str, account_id: str) -> list[Finding]:
    parsed = parse_ecr_image(image_uri)
    if parsed is None:
        return []

    image_ids: list[dict[str, str]] = []
    if parsed["digest"]:
        image_ids.append({"imageDigest": parsed["digest"]})
    elif parsed["tag"]:
        image_ids.append({"imageTag": parsed["tag"]})
    else:
        image_ids.append({"imageTag": "latest"})

    repo = parsed["repository"]
    try:
        ecr_client.describe_images(repositoryName=repo, imageIds=image_ids)
    except ClientError as exc:
        code = _error_code(exc)
        if code in _IMAGE_MISSING_CODES:
            label = parsed["tag"] or parsed["digest"] or "latest"
            return [Finding(
                type=FindingType.IMAGE_NOT_FOUND,
                message=(
                    f"ECR image '{repo}:{label}' does not exist. "
                    "The task cannot pull a tag or digest that is not in the repository."
                ),
                severity=Severity.CRITICAL,
                raw_data={"repository": repo, "tag": parsed["tag"], "digest": parsed["digest"], "uri": parsed["uri"]},
                source=_SOURCE,
            )]
        if is_access_denied(exc):
            return [iam_finding(
                "ecr:DescribeImages",
                f"arn:aws:ecr:{region}:{account_id}:repository/{repo}",
                _SOURCE,
            )]
        raise

    return [Finding(
        type=FindingType.IMAGE_PULL_FAILURE,
        message=(
            f"ECR image '{repo}:{parsed['tag'] or parsed['digest']}' exists. "
            "Pull failure is likely NAT/VPC endpoint, execution-role ecr:BatchGetImage, or KMS."
        ),
        severity=Severity.HIGH,
        raw_data={"repository": repo, "tag": parsed["tag"], "digest": parsed["digest"], "exists": True},
        source=_SOURCE,
    )]


def _probe_secret(secrets_client, arn: str) -> list[Finding]:
    try:
        secrets_client.describe_secret(SecretId=arn)
    except ClientError as exc:
        code = _error_code(exc)
        if code in _SECRET_MISSING_CODES:
            return [Finding(
                type=FindingType.SECRET_NOT_FOUND,
                message=(
                    f"Secrets Manager secret does not exist: {arn}. "
                    "Fix the valueFrom ARN in the task definition."
                ),
                severity=Severity.CRITICAL,
                raw_data={"secretArn": arn},
                source=_SOURCE,
            )]
        if is_access_denied(exc):
            return [iam_finding("secretsmanager:DescribeSecret", arn, _SOURCE)]
        raise
    return [Finding(
        type=FindingType.SECRETS_INIT_FAILURE,
        message=(
            f"Secrets Manager secret exists ({arn}). "
            "Init failure is likely execution-role secretsmanager:GetSecretValue, KMS, or VPC endpoint."
        ),
        severity=Severity.HIGH,
        raw_data={"secretArn": arn, "exists": True},
        source=_SOURCE,
    )]


def _probe_ssm(ssm_client, arn: str) -> list[Finding]:
    name = arn.split(":parameter", 1)[-1]
    if not name.startswith("/"):
        name = "/" + name
    try:
        ssm_client.get_parameter(Name=name)
    except ClientError as exc:
        code = _error_code(exc)
        if code in _SSM_MISSING_CODES:
            return [Finding(
                type=FindingType.SECRET_NOT_FOUND,
                message=(
                    f"SSM parameter does not exist: {arn}. "
                    "Fix the valueFrom ARN in the task definition."
                ),
                severity=Severity.CRITICAL,
                raw_data={"parameterArn": arn},
                source=_SOURCE,
            )]
        if is_access_denied(exc):
            return [iam_finding("ssm:GetParameter", arn, _SOURCE)]
        raise
    return [Finding(
        type=FindingType.SECRETS_INIT_FAILURE,
        message=(
            f"SSM parameter exists ({arn}). "
            "Init failure is likely execution-role ssm:GetParameter, KMS, or VPC endpoint."
        ),
        severity=Severity.HIGH,
        raw_data={"parameterArn": arn, "exists": True},
        source=_SOURCE,
    )]


def diagnose_deep(
    findings: list[Finding],
    service_cache: ServiceDataCache,
    ecr_client,
    secrets_client,
    ssm_client,
    cluster: str,
    service: str,
    region: str,
    account_id: str,
) -> list[Finding]:
    extra: list[Finding] = []
    if ecr_client is not None:
        for image in _images_to_probe(findings, service_cache, cluster, service, region, account_id):
            extra.extend(_probe_ecr_image(ecr_client, image, region, account_id))

    if secrets_client is not None or ssm_client is not None:
        for arn in _secret_arns_to_probe(findings):
            if ":secretsmanager:" in arn and secrets_client is not None:
                extra.extend(_probe_secret(secrets_client, arn))
            elif ":ssm:" in arn and ssm_client is not None:
                extra.extend(_probe_ssm(ssm_client, arn))
    return extra
