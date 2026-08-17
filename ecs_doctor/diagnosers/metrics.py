
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from ecs_doctor._aws import iam_finding, is_access_denied
from ecs_doctor.models import Finding, FindingType, MetricPoint, MetricSnapshot, Severity

_METRIC_NAMESPACE = "AWS/ECS"
_CPU_METRIC = "CPUUtilization"
_MEMORY_METRIC = "MemoryUtilization"
_DEFAULT_PERIOD = 300
_DEFAULT_LOOKBACK_HOURS = 3
_CPU_ALERT_THRESHOLD = 85.0
_MEMORY_ALERT_THRESHOLD = 85.0
_MEMORY_CRITICAL_THRESHOLD = 85.0
_MEMORY_MAX_THRESHOLD = 95.0
_CPU_MAX_THRESHOLD = 95.0
_ALB_NAMESPACE = "AWS/ApplicationELB"
_ALB_5XX_METRIC = "HTTPCode_Target_5XX_Count"
_ALB_UNHEALTHY_METRIC = "UnHealthyHostCount"


def _alb_dimension_from_arn(arn: str, kind: str) -> str | None:
    """Extract ALB/TG CloudWatch dimension from an ARN.

    loadbalancer/app/name/id → app/name/id
    targetgroup/name/id → targetgroup/name/id
    """
    marker = f"{kind}/"
    idx = arn.find(marker)
    if idx < 0:
        return None
    return arn[idx:]


def _alb_metric_queries(elbv2_client, tg_arn: str, period_seconds: int) -> list[dict]:
    try:
        resp = elbv2_client.describe_target_groups(TargetGroupArns=[tg_arn])
    except ClientError:
        return []
    groups = resp.get("TargetGroups") or []
    if not isinstance(groups, list) or not groups:
        return []
    lb_arns = groups[0].get("LoadBalancerArns") or []
    if not lb_arns:
        return []
    lb_dim = _alb_dimension_from_arn(lb_arns[0], "app")
    tg_dim = _alb_dimension_from_arn(tg_arn, "targetgroup")
    if not lb_dim or not tg_dim:
        return []
    dimensions = [
        {"Name": "LoadBalancer", "Value": lb_dim},
        {"Name": "TargetGroup", "Value": tg_dim},
    ]

    def _query(query_id: str, metric: str, stat: str) -> dict:
        return {
            "Id": query_id,
            "MetricStat": {
                "Metric": {
                    "Namespace": _ALB_NAMESPACE,
                    "MetricName": metric,
                    "Dimensions": dimensions,
                },
                "Period": period_seconds,
                "Stat": stat,
            },
        }

    return [
        _query("alb_5xx", _ALB_5XX_METRIC, "Sum"),
        _query("alb_unhealthy", _ALB_UNHEALTHY_METRIC, "Maximum"),
    ]


def _first_target_group_arn(service_cache, cluster: str, service: str, region: str, account_id: str) -> str | None:
    from ecs_doctor._aws import _AccessDeniedCached
    try:
        svc = service_cache.get_service(cluster, service, region, account_id)
    except _AccessDeniedCached:
        return None
    if not svc:
        return None
    for lb in svc.get("loadBalancers", []):
        tg_arn = lb.get("targetGroupArn")
        if tg_arn:
            return tg_arn
    return None


def _build_metric_queries(
    cluster: str,
    service: str,
    period_seconds: int,
) -> list[dict]:
    dimensions = [
        {"Name": "ClusterName", "Value": cluster},
        {"Name": "ServiceName", "Value": service},
    ]

    def _query(query_id: str, metric: str, stat: str) -> dict:
        return {
            "Id": query_id,
            "MetricStat": {
                "Metric": {
                    "Namespace": _METRIC_NAMESPACE,
                    "MetricName": metric,
                    "Dimensions": dimensions,
                },
                "Period": period_seconds,
                "Stat": stat,
            },
        }

    return [
        _query("cpu_avg", _CPU_METRIC, "Average"),
        _query("cpu_max", _CPU_METRIC, "Maximum"),
        _query("mem_avg", _MEMORY_METRIC, "Average"),
        _query("mem_max", _MEMORY_METRIC, "Maximum"),
    ]


def _parse_metric_results(results: list[dict]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for r in results:
        values[r["Id"]] = r.get("Values", [])
    return values


def _safe_avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _build_snapshot(
    cluster: str,
    service: str,
    values: dict[str, list[float]],
    timestamps: dict[str, list],
    lookback_hours: int,
    period_seconds: int,
) -> MetricSnapshot:
    cpu_avg_pts = [
        MetricPoint(
            timestamp=str(ts),
            average=v,
            maximum=v,
            unit="Percent",
        )
        for ts, v in zip(timestamps.get("cpu_avg", []), values.get("cpu_avg", []))
    ]
    mem_avg_pts = [
        MetricPoint(
            timestamp=str(ts),
            average=v,
            maximum=v,
            unit="Percent",
        )
        for ts, v in zip(timestamps.get("mem_avg", []), values.get("mem_avg", []))
    ]
    return MetricSnapshot(
        cluster=cluster,
        service=service,
        period_seconds=period_seconds,
        lookback_hours=lookback_hours,
        cpu_avg_percent=_safe_avg(values.get("cpu_avg", [])),
        cpu_max_percent=max(values["cpu_max"], default=None) if values.get("cpu_max") else None,
        memory_avg_percent=_safe_avg(values.get("mem_avg", [])),
        memory_max_percent=max(values["mem_max"], default=None) if values.get("mem_max") else None,
        cpu_datapoints=cpu_avg_pts,
        memory_datapoints=mem_avg_pts,
    )


def _anomaly_findings(snapshot: MetricSnapshot, cluster: str, service: str) -> list[Finding]:
    findings: list[Finding] = []

    if snapshot.cpu_avg_percent is not None and snapshot.cpu_avg_percent > _CPU_ALERT_THRESHOLD:
        findings.append(Finding(
            type=FindingType.HIGH_CPU_UTILIZATION,
            message=(
                f"Average CPU utilization is {snapshot.cpu_avg_percent:.1f}% "
                f"(threshold: {_CPU_ALERT_THRESHOLD}%) over the last "
                f"{snapshot.lookback_hours}h for {cluster}/{service}."
            ),
            severity=Severity.HIGH,
            raw_data={
                "cpu_avg_percent": snapshot.cpu_avg_percent,
                "cpu_max_percent": snapshot.cpu_max_percent,
                "lookback_hours": snapshot.lookback_hours,
            },
            source="metrics",
        ))
    elif (
        snapshot.cpu_max_percent is not None
        and snapshot.cpu_max_percent >= _CPU_MAX_THRESHOLD
    ):
        findings.append(Finding(
            type=FindingType.HIGH_CPU_UTILIZATION,
            message=(
                f"CPU utilization spiked to {snapshot.cpu_max_percent:.1f}% "
                f"(spike threshold: {_CPU_MAX_THRESHOLD}%) over the last "
                f"{snapshot.lookback_hours}h for {cluster}/{service}. "
                "A spike this high throttles the container and can cause health check timeouts."
            ),
            severity=Severity.HIGH,
            raw_data={
                "cpu_avg_percent": snapshot.cpu_avg_percent,
                "cpu_max_percent": snapshot.cpu_max_percent,
                "lookback_hours": snapshot.lookback_hours,
            },
            source="metrics",
        ))

    if snapshot.memory_avg_percent is not None and snapshot.memory_avg_percent > _MEMORY_ALERT_THRESHOLD:
        findings.append(Finding(
            type=FindingType.HIGH_MEMORY_UTILIZATION,
            message=(
                f"Average memory utilization is {snapshot.memory_avg_percent:.1f}% "
                f"(threshold: {_MEMORY_CRITICAL_THRESHOLD}%) over the last "
                f"{snapshot.lookback_hours}h for {cluster}/{service}. "
                "OOM kill risk is elevated."
            ),
            severity=Severity.CRITICAL,
            raw_data={
                "memory_avg_percent": snapshot.memory_avg_percent,
                "memory_max_percent": snapshot.memory_max_percent,
                "lookback_hours": snapshot.lookback_hours,
            },
            source="metrics",
        ))
    elif (
        snapshot.memory_max_percent is not None
        and snapshot.memory_max_percent >= _MEMORY_MAX_THRESHOLD
    ):
        findings.append(Finding(
            type=FindingType.HIGH_MEMORY_UTILIZATION,
            message=(
                f"Memory utilization spiked to {snapshot.memory_max_percent:.1f}% "
                f"(spike threshold: {_MEMORY_MAX_THRESHOLD}%) over the last "
                f"{snapshot.lookback_hours}h for {cluster}/{service}. "
                "A transient spike this high can trigger an OOM kill."
            ),
            severity=Severity.HIGH,
            raw_data={
                "memory_avg_percent": snapshot.memory_avg_percent,
                "memory_max_percent": snapshot.memory_max_percent,
                "lookback_hours": snapshot.lookback_hours,
            },
            source="metrics",
        ))

    return findings


def diagnose_metrics(
    cw_client,
    cluster: str,
    service: str,
    region: str,
    account_id: str,
    lookback_hours: int = _DEFAULT_LOOKBACK_HOURS,
    period_seconds: int = _DEFAULT_PERIOD,
    service_cache=None,
    elbv2_client=None,
) -> tuple[list[Finding], MetricSnapshot | None]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=lookback_hours)

    queries = _build_metric_queries(cluster, service, period_seconds)
    if service_cache is not None and elbv2_client is not None:
        tg_arn = _first_target_group_arn(service_cache, cluster, service, region, account_id)
        if tg_arn:
            queries.extend(_alb_metric_queries(elbv2_client, tg_arn, period_seconds))

    try:
        resp = cw_client.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start,
            EndTime=now,
        )
    except ClientError as exc:
        if is_access_denied(exc):
            resource = f"arn:aws:cloudwatch:{region}:{account_id}:*"
            return [iam_finding("cloudwatch:GetMetricData", resource, "metrics")], None
        raise

    results: list[dict] = resp.get("MetricDataResults", [])
    values = _parse_metric_results(results)
    timestamps = {r["Id"]: r.get("Timestamps", []) for r in results}

    snapshot = _build_snapshot(
        cluster, service, values, timestamps, lookback_hours, period_seconds
    )
    findings = _anomaly_findings(snapshot, cluster, service)
    alb_5xx_values = values.get("alb_5xx", [])
    if alb_5xx_values and sum(alb_5xx_values) > 0:
        total = round(sum(alb_5xx_values), 1)
        findings.append(Finding(
            type=FindingType.ALB_TARGET_5XX,
            message=(
                f"ALB targets returned {total:.0f} HTTP 5xx responses over the last "
                f"{lookback_hours}h for {cluster}/{service}."
            ),
            severity=Severity.HIGH,
            raw_data={"alb_5xx_sum": total, "lookback_hours": lookback_hours},
            source="metrics",
        ))
    alb_unhealthy_values = values.get("alb_unhealthy", [])
    if alb_unhealthy_values and max(alb_unhealthy_values) > 0:
        peak = round(max(alb_unhealthy_values), 1)
        findings.append(Finding(
            type=FindingType.ALB_UNHEALTHY,
            message=(
                f"ALB UnHealthyHostCount reached {peak:.0f} over the last "
                f"{lookback_hours}h for {cluster}/{service}."
            ),
            severity=Severity.HIGH,
            raw_data={"alb_unhealthy_max": peak, "lookback_hours": lookback_hours},
            source="metrics",
        ))
    return findings, snapshot
