"""
Run this script to produce realistic ecs-doctor output without AWS credentials.
Used to record the demo GIF / SVG.

Usage:
    # Interactive (for asciinema recording):
    python docs/demo_script.py

    # Capture to SVG (requires rich):
    python docs/demo_script.py --export-svg docs/demo.svg
"""
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.table import Table

from ecs_doctor.cli import (
    _render_root_cause,
    _render_evidence_table,
    _render_metrics,
    _render_service_config,
    _render_task_config,
    console,
)
from ecs_doctor.engine import DiagnosisRequest, DiagnosisResult
from ecs_doctor.models import (
    ContainerConfig,
    DeploymentConfig,
    Finding,
    FindingType,
    MetricSnapshot,
    RootCause,
    ServiceConfig,
    Severity,
    TaskConfig,
)

# ---------------------------------------------------------------------------
# Fake data — realistic OOM scenario
# ---------------------------------------------------------------------------

_REQUEST = DiagnosisRequest(
    cluster="prod",
    service="payments",
    region="us-east-1",
    account_id="123456789012",
)

_FINDINGS = [
    Finding(
        type=FindingType.OOM_KILLED,
        message="Container 'app' exit 137 (3 tasks in last hour)",
        severity=Severity.CRITICAL,
        source="stop_reasons",
    ),
    Finding(
        type=FindingType.LOG_CRASH_SIGNATURE,
        message="OOM kill detected in CloudWatch Logs (/ecs/payments)",
        severity=Severity.CRITICAL,
        source="logs",
    ),
    Finding(
        type=FindingType.TASK_THRASHING,
        message="4 starts / 4 stops in last 20 service events",
        severity=Severity.CRITICAL,
        source="events",
    ),
    Finding(
        type=FindingType.HIGH_MEMORY_UTILIZATION,
        message="Memory avg 94.2%, max 99.8% over last 3 hours",
        severity=Severity.HIGH,
        source="metrics",
    ),
]

_ROOT_CAUSE = RootCause(
    cause="Container is being OOM-killed (out of memory)",
    confidence=0.97,
    evidence=_FINDINGS,
    suggested_fix=(
        "Increase the container memory reservation in the task definition. "
        "Profile the application for memory leaks — common causes include "
        "unbounded caches, unclosed DB connections, JVM heap settings."
    ),
)

_METRICS = MetricSnapshot(
    cluster="prod",
    service="payments",
    period_seconds=300,
    lookback_hours=3,
    cpu_avg_percent=12.4,
    cpu_max_percent=18.1,
    memory_avg_percent=94.2,
    memory_max_percent=99.8,
)

_SERVICE_CONFIG = ServiceConfig(
    service_arn="arn:aws:ecs:us-east-1:123456789012:service/prod/payments",
    service_name="payments",
    cluster_arn="arn:aws:ecs:us-east-1:123456789012:cluster/prod",
    desired_count=3,
    running_count=1,
    pending_count=2,
    launch_type="FARGATE",
    platform_version="1.4.0",
    deployment_config=DeploymentConfig(
        minimum_healthy_percent=50,
        maximum_percent=200,
        circuit_breaker_enabled=True,
        rollback_on_failure=True,
    ),
    capacity_provider_strategy=[],
    health_check_grace_period_seconds=30,
)

_TASK_CONFIG = TaskConfig(
    task_definition_arn="arn:aws:ecs:us-east-1:123456789012:task-definition/payments:42",
    family="payments",
    revision=42,
    cpu="1024",
    memory="512",
    network_mode="awsvpc",
    launch_type="FARGATE",
    execution_role_arn="arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
    task_role_arn="arn:aws:iam::123456789012:role/payments-task-role",
    containers=[
        ContainerConfig(
            name="app",
            image="123456789012.dkr.ecr.us-east-1.amazonaws.com/payments:latest",
            cpu=1024,
            memory=512,
            memory_reservation=None,
            essential=True,
            environment={"LOG_LEVEL": "info", "PORT": "8080"},
            health_check=None,
            log_driver="awslogs",
            log_group="/ecs/payments",
        )
    ],
)

_RESULT = DiagnosisResult(
    request=_REQUEST,
    root_cause=_ROOT_CAUSE,
    all_findings=_FINDINGS,
    metrics=_METRICS,
    service_config=_SERVICE_CONFIG,
    task_config=_TASK_CONFIG,
    duration_ms=847,
)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _render_report_demo(export_svg: str | None = None) -> None:
    out = Console(record=bool(export_svg))

    out.print()
    out.rule("[bold cyan]ECS Doctor — prod / payments[/bold cyan]")
    out.print()

    # Root cause panel
    color = "red"
    body = Text()
    body.append(_ROOT_CAUSE.cause + "\n\n", style="bold")
    body.append("Confidence: ", style="dim")
    body.append("97%\n\n", style="bold red")
    body.append("Suggested fix:\n", style="italic dim")
    body.append(_ROOT_CAUSE.suggested_fix)
    out.print(Panel(body, title="[bold red]Root Cause[/bold red]", border_style="red", padding=(1, 2)))

    # Evidence table
    table = Table(
        title="[bold]Supporting Evidence[/bold]",
        box=box.ROUNDED,
        show_lines=True,
        expand=False,
    )
    table.add_column("Source", style="dim", no_wrap=True)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Message")
    table.add_row("stop_reasons", "oom_killed",       "[bold red]CRITICAL[/bold red]", "Container 'app' exit 137 (3 tasks in last hour)")
    table.add_row("logs",         "log_crash_sig",    "[bold red]CRITICAL[/bold red]", "OOM kill detected in CloudWatch Logs (/ecs/payments)")
    table.add_row("events",       "task_thrashing",   "[bold red]CRITICAL[/bold red]", "4 starts / 4 stops in last 20 service events")
    table.add_row("metrics",      "high_memory",      "[orange1]HIGH[/orange1]",       "Memory avg 94.2%, max 99.8% over last 3h")
    out.print(table)

    # Metrics
    m = Table(title="[bold]CloudWatch Metrics (last 3h)[/bold]", box=box.SIMPLE, show_lines=False, expand=False)
    m.add_column("Metric", style="cyan")
    m.add_column("Average", justify="right")
    m.add_column("Maximum", justify="right")
    m.add_row("CPU Utilization",    "12.4%", "18.1%")
    m.add_row("Memory Utilization", "[bold red]94.2%[/bold red]", "[bold red]99.8%[/bold red]")
    out.print(m)

    # Service config
    sc_body = Text()
    sc_body.append("Desired / Running / Pending: ", style="dim")
    sc_body.append("3 / 1 / 2\n")
    sc_body.append("Launch type: ", style="dim")
    sc_body.append("FARGATE  Platform: 1.4.0\n")
    sc_body.append("Deployment: ", style="dim")
    sc_body.append("min 50% / max 200%  Circuit breaker: on\n")
    sc_body.append("Health check grace period: 30s", style="dim")
    out.print(Panel(sc_body, title="[bold]Service Configuration[/bold]", border_style="dim", padding=(0, 2)))

    # Task definition
    out.print("[dim]Task CPU:[/dim] 1024  [dim]Memory:[/dim] 512  [dim]Network:[/dim] awsvpc")
    td = Table(title="[bold]Task Definition[/bold]", box=box.SIMPLE, show_lines=False, expand=False)
    td.add_column("Container", style="cyan")
    td.add_column("Image")
    td.add_column("CPU", justify="right")
    td.add_column("Memory", justify="right")
    td.add_column("Log Group", style="dim")
    td.add_row("app", "payments:latest", "1024", "512", "/ecs/payments")
    out.print(td)

    out.print("\n[dim]Diagnosis completed in 847ms.[/dim]\n")

    if export_svg:
        out.save_svg(export_svg, title="ecs-doctor diagnose --cluster prod --service payments")
        print(f"SVG saved to {export_svg}", file=sys.stderr)


def _simulate_typing(cmd: str, delay: float = 0.06) -> None:
    import shutil
    width = shutil.get_terminal_size().columns
    print(f"\033[2m$\033[0m ", end="", flush=True)
    for ch in cmd:
        print(ch, end="", flush=True)
        time.sleep(delay)
    print()
    time.sleep(0.4)


if __name__ == "__main__":
    export_svg = None
    if "--export-svg" in sys.argv:
        idx = sys.argv.index("--export-svg")
        export_svg = sys.argv[idx + 1]

    if not export_svg:
        # Interactive mode — simulate typing then render
        _simulate_typing("ecs-doctor diagnose --cluster prod --service payments")
        console.print("[dim]Running diagnostics on [bold]prod[/bold] / [bold]payments[/bold] in [bold]us-east-1[/bold]…[/dim]")
        time.sleep(0.9)

    _render_report_demo(export_svg=export_svg)
