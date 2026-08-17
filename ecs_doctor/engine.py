
import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ecs_doctor._aws import ServiceDataCache
from ecs_doctor.aggregator import aggregate
from ecs_doctor.diagnosers.alb_health import diagnose_alb_health
from ecs_doctor.diagnosers.events import diagnose_events
from ecs_doctor.diagnosers.logs import diagnose_logs
from ecs_doctor.diagnosers.stop_reasons import diagnose_stop_reasons
from ecs_doctor.models import Finding, FindingType, MetricSnapshot, RootCause, ServiceConfig, Severity, TaskConfig


@dataclass
class DiagnosisRequest:
    cluster: str
    service: str
    region: str
    account_id: str


@dataclass
class DiagnosisResult:
    request: DiagnosisRequest
    root_cause: RootCause
    all_findings: list[Finding]
    service_config: ServiceConfig | None = None
    task_config: TaskConfig | None = None
    metrics: MetricSnapshot | None = None
    duration_ms: int = 0


def to_json_safe(obj: object) -> object:
    """Recursively convert dataclasses and lists to JSON-serialisable dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_json_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [to_json_safe(i) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# Per-future exception isolation helpers
# ---------------------------------------------------------------------------

def _safe_findings(future, source: str) -> list[Finding]:
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        return [Finding(
            type=FindingType.IAM_DENIED,
            message=f"[{source}] Unexpected error: {exc}",
            severity=Severity.LOW,
            source=source,
        )]


def _safe_stop(future) -> tuple[list[Finding], list[str]]:
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        return [Finding(
            type=FindingType.IAM_DENIED,
            message=f"[stop_reasons] Unexpected error: {exc}",
            severity=Severity.LOW,
            source="stop_reasons",
        )], []


def _safe_config(future) -> tuple[list[Finding], ServiceConfig | None, TaskConfig | None]:
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        return [Finding(
            type=FindingType.IAM_DENIED,
            message=f"[config] Unexpected error: {exc}",
            severity=Severity.LOW,
            source="config",
        )], None, None


def _safe_metrics(future) -> tuple[list[Finding], MetricSnapshot | None]:
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        return [Finding(
            type=FindingType.IAM_DENIED,
            message=f"[metrics] Unexpected error: {exc}",
            severity=Severity.LOW,
            source="metrics",
        )], None


# ---------------------------------------------------------------------------
# Private helpers for optional parallel tasks
# ---------------------------------------------------------------------------

def _run_config(
    include_config: bool,
    cache: ServiceDataCache,
    ecs_client,
    kwargs: dict,
) -> tuple[list[Finding], ServiceConfig | None, TaskConfig | None]:
    if not include_config:
        return [], None, None
    from ecs_doctor.diagnosers.config import diagnose_config
    return diagnose_config(service_cache=cache, ecs_client=ecs_client, **kwargs)


def _run_metrics(
    include_metrics: bool,
    cw_client,
    cache: ServiceDataCache,
    elb_client,
    kwargs: dict,
) -> tuple[list[Finding], MetricSnapshot | None]:
    if not include_metrics or cw_client is None:
        return [], None
    from ecs_doctor.diagnosers.metrics import diagnose_metrics
    return diagnose_metrics(
        cw_client=cw_client,
        service_cache=cache,
        elbv2_client=elb_client,
        **kwargs,
    )


def _run_network(
    cache: ServiceDataCache,
    ecs_client,
    ec2_client,
    kwargs: dict,
) -> list[Finding]:
    if ec2_client is None:
        return []
    from ecs_doctor.diagnosers.network import diagnose_network
    return diagnose_network(
        service_cache=cache, ecs_client=ecs_client, ec2_client=ec2_client, **kwargs
    )


def _run_deep(
    deep: bool,
    findings: list[Finding],
    cache: ServiceDataCache,
    ecr_client,
    secrets_client,
    ssm_client,
    kwargs: dict,
) -> list[Finding]:
    if not deep:
        return []
    from ecs_doctor.diagnosers.deep import diagnose_deep
    return diagnose_deep(
        findings=findings,
        service_cache=cache,
        ecr_client=ecr_client,
        secrets_client=secrets_client,
        ssm_client=ssm_client,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Public orchestration entry point
# ---------------------------------------------------------------------------

def run_diagnosis(
    ecs_client,
    logs_client,
    elb_client,
    cw_client,
    request: DiagnosisRequest,
    ec2_client=None,
    include_metrics: bool = True,
    include_config: bool = True,
    deep: bool = False,
    ecr_client=None,
    secrets_client=None,
    ssm_client=None,
) -> DiagnosisResult:
    """Orchestrate all diagnosers in parallel and return a single DiagnosisResult.

    Phase 1 runs independent diagnosers concurrently via ThreadPoolExecutor.
    Logs are submitted as soon as stop_reasons returns task ARNs, overlapping
    remaining Phase 1 work (metrics, network, config).

    --deep probes (ECR image / Secrets / SSM existence) start after stop
    reasons, still overlapping remaining Phase 1 work.

    Called by both cli.py and web routes — zero logic duplicated.
    """
    start_ms = time.monotonic()

    cache = ServiceDataCache(ecs_client)
    kwargs: dict = {
        "cluster": request.cluster,
        "service": request.service,
        "region": request.region,
        "account_id": request.account_id,
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        f_events  = pool.submit(diagnose_events, service_cache=cache, **kwargs)
        f_stop    = pool.submit(diagnose_stop_reasons, ecs_client=ecs_client, **kwargs)
        f_alb     = pool.submit(diagnose_alb_health, service_cache=cache, elbv2_client=elb_client, **kwargs)
        f_config  = pool.submit(_run_config, include_config, cache, ecs_client, kwargs)
        f_metrics = pool.submit(_run_metrics, include_metrics, cw_client, cache, elb_client, kwargs)
        f_network = pool.submit(_run_network, cache, ecs_client, ec2_client, kwargs)

        # Logs only need stop_reasons task ARNs — start them without waiting
        # for metrics/network/config to finish.
        stop_findings, task_arns = _safe_stop(f_stop)
        f_logs = pool.submit(
            diagnose_logs,
            service_cache=cache,
            ecs_client=ecs_client,
            logs_client=logs_client,
            task_arns=task_arns,
            deep=deep,
            **kwargs,
        )
        f_deep = pool.submit(
            _run_deep,
            deep,
            stop_findings,
            cache,
            ecr_client,
            secrets_client,
            ssm_client,
            kwargs,
        )
        log_findings = _safe_findings(f_logs, "logs")
        deep_findings = _safe_findings(f_deep, "deep")

        config_findings, service_config, task_config = _safe_config(f_config)
        metric_findings, metrics = _safe_metrics(f_metrics)

    all_findings: list[Finding] = (
        _safe_findings(f_events, "events")
        + stop_findings
        + log_findings
        + deep_findings
        + _safe_findings(f_alb, "alb_health")
        + config_findings
        + metric_findings
        + _safe_findings(f_network, "network")
    )

    root_cause = aggregate(all_findings)
    duration_ms = int((time.monotonic() - start_ms) * 1000)

    return DiagnosisResult(
        request=request,
        root_cause=root_cause,
        all_findings=all_findings,
        service_config=service_config,
        task_config=task_config,
        metrics=metrics,
        duration_ms=duration_ms,
    )
