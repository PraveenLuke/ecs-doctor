
from typing import Any

from botocore.exceptions import ClientError

from ecs_doctor._aws import ServiceDataCache, _AccessDeniedCached, iam_finding, is_access_denied, service_resource_arn
from ecs_doctor.models import Finding, FindingType, Severity

_REASON_MAP: dict[str, tuple[str, str]] = {
    "Target.Timeout": (
        "Health check timed out — app not responding within timeout period",
        "Check security groups allow ALB → task on the container port. "
        "Increase the health check timeout in the target group settings.",
    ),
    "Target.ConnectionError": (
        "Connection refused on health check port — app may have crashed or is listening on the wrong port",
        "Verify the container is listening on the declared containerPort. "
        "Check app logs for startup errors.",
    ),
    "Target.FailedHealthChecks": (
        "Health check path returned a non-2xx status code",
        "Confirm the health check path (e.g. /health) exists and returns HTTP 200. "
        "Review app logs during health check requests.",
    ),
    "Target.NotInUse": (
        "Target is not in use (may be draining or deregistered)",
        "Check if the ECS service successfully registered the task with the target group.",
    ),
    "Elb.InternalError": (
        "ELB internal error during health check",
        "Retry — if persistent, check AWS Service Health Dashboard.",
    ),
}

_DEFAULT_UNHEALTHY = (
    "Unhealthy target",
    "Investigate target group health check configuration.",
)


def _finding_for_target(
    state: str,
    reason_code: str,
    description: str,
    target: dict,
    tg_arn: str,
) -> Finding | None:
    target_id = f"{target.get('Id', '?')}:{target.get('Port', '?')}"
    raw: dict[str, Any] = {
        "target": target,
        "reason": reason_code,
        "description": description,
        "tg_arn": tg_arn,
    }

    if state == "unhealthy":
        human, _ = _REASON_MAP.get(reason_code, (f"Unhealthy — {description}", ""))
        return Finding(
            type=FindingType.ALB_UNHEALTHY,
            message=f"Target {target_id} is unhealthy. {human}",
            severity=Severity.CRITICAL,
            raw_data=raw,
            source="alb_health",
        )

    if state == "initial":
        return Finding(
            type=FindingType.HEALTH_CHECK_FAIL,
            message=(
                f"Target {target_id} is in 'initial' state — "
                "still waiting for first health check to pass. "
                "If this persists, check healthCheckGracePeriodSeconds."
            ),
            severity=Severity.LOW,
            raw_data={"target": target, "state": state, "tg_arn": tg_arn},
            source="alb_health",
        )

    if state == "draining":
        return Finding(
            type=FindingType.HEALTH_CHECK_FAIL,
            message=(
                f"Target {target_id} is draining — connections being drained before deregistration. "
                "This is expected during task replacement; if persistent, the task may be crash-looping."
            ),
            severity=Severity.LOW,
            raw_data={"target": target, "state": state, "tg_arn": tg_arn},
            source="alb_health",
        )

    if state == "unused" and reason_code == "Target.InvalidState":
        return Finding(
            type=FindingType.ALB_UNHEALTHY,
            message=(
                f"Target {target_id} is in an invalid state — possible protocol mismatch "
                "(e.g. HTTP target group performing health checks on an HTTPS endpoint). "
                "Verify the target group protocol matches the container's listener."
            ),
            severity=Severity.MEDIUM,
            raw_data=raw,
            source="alb_health",
        )

    if reason_code == "Target.DeregistrationInProgress":
        return Finding(
            type=FindingType.HEALTH_CHECK_FAIL,
            message=(
                f"Target {target_id} is deregistering — connection draining in progress. "
                "If persistent, the task may be crash-looping and re-registering repeatedly."
            ),
            severity=Severity.LOW,
            raw_data=raw,
            source="alb_health",
        )

    if state == "unused" and not reason_code:
        return Finding(
            type=FindingType.HEALTH_CHECK_FAIL,
            message=(
                f"Target {target_id} is registered but unused — "
                "no listener rule routes traffic to this target group."
            ),
            severity=Severity.LOW,
            raw_data=raw,
            source="alb_health",
        )

    return None


def diagnose_alb_health(
    service_cache: ServiceDataCache,
    elbv2_client,
    cluster: str,
    service: str,
    region: str,
    account_id: str,
) -> list[Finding]:
    try:
        svc = service_cache.get_service(cluster, service, region, account_id)
    except _AccessDeniedCached:
        return [iam_finding(
            "ecs:DescribeServices",
            service_resource_arn(region, account_id, cluster, service),
            "alb_health",
        )]

    if not svc:
        return []

    load_balancers: list[dict[str, Any]] = svc.get("loadBalancers", [])
    if not load_balancers:
        return []

    findings: list[Finding] = []
    for lb in load_balancers:
        tg_arn = lb.get("targetGroupArn")
        if tg_arn:
            findings.extend(_check_target_group(elbv2_client, tg_arn))
    return findings


def _check_target_group(elbv2_client, tg_arn: str) -> list[Finding]:
    """Fetch and evaluate health for a single target group."""
    try:
        health_resp = elbv2_client.describe_target_health(TargetGroupArn=tg_arn)
    except ClientError as exc:
        if is_access_denied(exc):
            return [iam_finding(
                "elasticloadbalancing:DescribeTargetHealth",
                tg_arn,
                "alb_health",
            )]
        raise

    descriptions = health_resp.get("TargetHealthDescriptions", [])
    if not descriptions:
        return [Finding(
            type=FindingType.NO_ALB_TARGETS,
            message=(
                f"Target group {tg_arn} has no registered targets. "
                "ECS may have failed to register tasks — check that the container port in the task "
                "definition matches the load balancer configuration and that tasks reached RUNNING state."
            ),
            severity=Severity.HIGH,
            raw_data={"tg_arn": tg_arn},
            source="alb_health",
        )]

    findings: list[Finding] = []
    for desc in descriptions:
        health = desc.get("TargetHealth", {})
        finding = _finding_for_target(
            state=health.get("State", ""),
            reason_code=health.get("Reason", ""),
            description=health.get("Description", ""),
            target=desc.get("Target", {}),
            tg_arn=tg_arn,
        )
        if finding:
            findings.append(finding)
    return findings
