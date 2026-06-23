from ecs_doctor.aggregator import aggregate
from ecs_doctor.models import Finding, FindingType, RootCause, Severity


def _f(ftype: FindingType, severity: Severity = Severity.HIGH) -> Finding:
    return Finding(type=ftype, message="test", severity=severity, source="test")


# ---------------------------------------------------------------------------
# Empty / no findings
# ---------------------------------------------------------------------------

def test_no_findings_returns_zero_confidence():
    result = aggregate([])
    assert result.confidence == 0.0
    assert result.evidence == []
    assert result.suggested_fix != ""


# ---------------------------------------------------------------------------
# Single-finding root causes
# ---------------------------------------------------------------------------

def test_oom_finding_returns_memory_root_cause():
    result = aggregate([_f(FindingType.OOM_KILLED, Severity.CRITICAL)])
    assert "memory" in result.cause.lower() or "OOM" in result.cause
    assert result.confidence > 0.5
    assert result.evidence[0].type == FindingType.OOM_KILLED
    assert result.suggested_fix != ""


def test_image_pull_failure():
    result = aggregate([_f(FindingType.IMAGE_PULL_FAILURE, Severity.CRITICAL)])
    assert "image" in result.cause.lower() or "pull" in result.cause.lower()
    assert result.confidence > 0.5


def test_secrets_init_failure():
    result = aggregate([_f(FindingType.SECRETS_INIT_FAILURE, Severity.CRITICAL)])
    assert "secret" in result.cause.lower() or "config" in result.cause.lower()


def test_placement_failure():
    result = aggregate([_f(FindingType.PLACEMENT_FAILURE, Severity.HIGH)])
    assert "capacity" in result.cause.lower() or "schedule" in result.cause.lower()


def test_alb_unhealthy():
    result = aggregate([_f(FindingType.ALB_UNHEALTHY, Severity.CRITICAL)])
    assert "alb" in result.cause.lower() or "target" in result.cause.lower()


def test_health_check_fail():
    result = aggregate([_f(FindingType.HEALTH_CHECK_FAIL, Severity.HIGH)])
    assert "health" in result.cause.lower()


def test_deployment_rollback():
    result = aggregate([_f(FindingType.DEPLOYMENT_ROLLBACK, Severity.CRITICAL)])
    assert "deployment" in result.cause.lower() or "rollback" in result.cause.lower()


def test_premature_exit():
    result = aggregate([_f(FindingType.PREMATURE_EXIT, Severity.HIGH)])
    assert "exit" in result.cause.lower() or "cmd" in result.cause.lower()


def test_task_thrashing():
    result = aggregate([_f(FindingType.TASK_THRASHING, Severity.CRITICAL)])
    assert "crash" in result.cause.lower() or "loop" in result.cause.lower()


def test_non_zero_exit():
    result = aggregate([_f(FindingType.NON_ZERO_EXIT, Severity.HIGH)])
    assert "crash" in result.cause.lower() or "exit" in result.cause.lower()


def test_essential_exited():
    result = aggregate([_f(FindingType.ESSENTIAL_EXITED, Severity.HIGH)])
    assert "essential" in result.cause.lower() or "container" in result.cause.lower()


def test_graceful_shutdown_fail():
    result = aggregate([_f(FindingType.GRACEFUL_SHUTDOWN_FAIL, Severity.MEDIUM)])
    assert "sigterm" in result.cause.lower() or "shutdown" in result.cause.lower()


def test_log_crash_signature():
    result = aggregate([_f(FindingType.LOG_CRASH_SIGNATURE, Severity.HIGH)])
    assert "log" in result.cause.lower() or "crash" in result.cause.lower()


def test_iam_denied():
    result = aggregate([_f(FindingType.IAM_DENIED, Severity.CRITICAL)])
    assert "iam" in result.cause.lower() or "permission" in result.cause.lower()


# ---------------------------------------------------------------------------
# Scoring and confidence
# ---------------------------------------------------------------------------

def test_more_findings_increases_confidence():
    one = aggregate([_f(FindingType.OOM_KILLED, Severity.CRITICAL)])
    three = aggregate([_f(FindingType.OOM_KILLED, Severity.CRITICAL)] * 3)
    assert three.confidence > one.confidence


def test_confidence_is_valid_probability():
    # Confidence is capped via 1 - exp(-x), which asymptotically approaches 1.0
    # but must always be a valid probability in [0.0, 1.0]
    findings = [_f(FindingType.OOM_KILLED, Severity.CRITICAL)] * 3
    result = aggregate(findings)
    assert 0.0 <= result.confidence <= 1.0


def test_confidence_rounded_to_2_decimal_places():
    result = aggregate([_f(FindingType.OOM_KILLED, Severity.CRITICAL)])
    assert result.confidence == round(result.confidence, 2)


def test_severity_multiplier_affects_confidence():
    critical = aggregate([_f(FindingType.OOM_KILLED, Severity.CRITICAL)])
    low = aggregate([_f(FindingType.OOM_KILLED, Severity.LOW)])
    assert critical.confidence > low.confidence


def test_highest_scoring_hypothesis_wins():
    findings = [
        _f(FindingType.OOM_KILLED, Severity.CRITICAL),   # base 0.95 * 1.25
        _f(FindingType.LOG_CRASH_SIGNATURE, Severity.LOW),  # base 0.65 * 0.5
    ]
    result = aggregate(findings)
    assert "memory" in result.cause.lower() or "OOM" in result.cause


def test_evidence_contains_only_winning_hypothesis_findings():
    findings = [
        _f(FindingType.OOM_KILLED, Severity.CRITICAL),
        _f(FindingType.OOM_KILLED, Severity.HIGH),
        _f(FindingType.LOG_CRASH_SIGNATURE, Severity.MEDIUM),
    ]
    result = aggregate(findings)
    assert all(f.type == FindingType.OOM_KILLED for f in result.evidence)
    assert len(result.evidence) == 2


def test_no_hypothesis_match_falls_back():
    # If _HYPOTHESIS didn't cover a type, scores would be empty
    # Simulate by passing only IAM_DENIED (which has low weight but IS in hypothesis)
    result = aggregate([_f(FindingType.IAM_DENIED, Severity.LOW)])
    assert result.cause != ""
    assert result.confidence >= 0.0
