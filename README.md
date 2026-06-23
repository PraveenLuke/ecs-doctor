# ecs-doctor

[![PyPI version](https://img.shields.io/pypi/v/ecs-doctor.svg)](https://pypi.org/project/ecs-doctor/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

**Diagnose why your ECS service is failing — in one command.**

> Designed and built by [Praveen Rajkoilraj](https://github.com/PraveenLuke).

---

## The Problem

ECS troubleshooting today means manually correlating multiple AWS data sources every incident, by hand:

1. **ECS service events** — was there a placement failure? a deployment rollback? a deadlock?
2. **Stopped task reasons + container exit codes** — OOM? image pull failure? missing secret?
3. **CloudWatch Logs** — what was the application printing before it crashed?
4. **ALB target health** — is the load balancer even reaching the container?
5. **CloudWatch Metrics** — is CPU or memory trending toward exhaustion?
6. **Task definition + network config** — is the Fargate CPU/memory combo invalid? are security groups blocking egress?

`ecs-doctor` aggregates all of these into a single confidence-scored root-cause report with a suggested fix.

---

## Installation

```bash
# Recommended: isolated install via pipx
pipx install ecs-doctor

# Or with pip
pip install ecs-doctor

# With web UI support
pip install "ecs-doctor[web]"

# With interactive browser (arrow-key cluster/service selection)
pip install "ecs-doctor[interactive]"

# Everything
pip install "ecs-doctor[web,interactive]"
```

### Development install

```bash
git clone https://github.com/PraveenLuke/ecs-doctor
cd ecs-doctor
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Quick Start

```bash
# Run a full diagnosis
ecs-doctor diagnose --cluster prod-cluster --service payments-service

# Specify region
ecs-doctor diagnose --cluster prod-cluster --service payments-service --region us-west-2

# Use a named AWS profile
ecs-doctor diagnose --cluster prod-cluster --service payments-service --profile staging

# Machine-readable JSON (for CI, Slack bots, incident tooling)
ecs-doctor diagnose --cluster prod-cluster --service payments-service --json

# Faster run — skip CloudWatch metrics (no cloudwatch:GetMetricData needed)
ecs-doctor diagnose --cluster prod-cluster --service payments-service --no-metrics

# Skip task definition config panel
ecs-doctor diagnose --cluster prod-cluster --service payments-service --no-config

# Stream live logs from running tasks (Ctrl+C to stop)
ecs-doctor diagnose --cluster prod-cluster --service payments-service --stream-logs
```

---

## Commands

### `diagnose` — Run all diagnostic checks

```
ecs-doctor diagnose [OPTIONS]

Options:
  --cluster TEXT    ECS cluster name or ARN  [required]
  --service TEXT    ECS service name  [required]
  --region TEXT     AWS region (overrides profile/env default)
  --profile TEXT    AWS named profile from ~/.aws/credentials
  --json            Emit machine-readable JSON instead of the rich report
  --stream-logs     Stream live logs from running tasks  (cannot combine with --json)
  --no-metrics      Skip CloudWatch metrics (faster, fewer permissions needed)
  --no-config       Skip task definition config display
```

### `browse` — Interactive wizard (requires `[interactive]` extra)

```bash
ecs-doctor browse
```

Launches an arrow-key wizard that guides you through:
1. Choosing an **authentication method** — AWS Profile, Access Keys, or Default Chain
2. Selecting an **AWS region**
3. Listing all **clusters** in the account and selecting one
4. Listing all **services** in that cluster and selecting one
5. Choosing **output format** — rich terminal report or JSON

Useful when you don't know the cluster/service name, or when exploring an unfamiliar account.

### `serve` — Web UI (requires `[web]` extra)

```bash
# Start the web server (default: http://0.0.0.0:8080)
ecs-doctor serve

# Custom host/port
ecs-doctor serve --host 127.0.0.1 --port 9090

# Auto-reload on code changes (dev mode)
ecs-doctor serve --reload
```

Opens a browser UI at `http://localhost:8080` where you can enter cluster/service/region, run a diagnosis, and stream live logs — all without the CLI.

---

## Authentication

`ecs-doctor` uses the standard **boto3 credential chain** — the same one used by the AWS CLI:

| Method | How to configure |
|--------|-----------------|
| Environment variables | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` |
| AWS named profile | `ecs-doctor diagnose --profile my-profile` |
| ECS task role | Automatic when running inside Fargate/ECS |
| EC2 instance role | Automatic when running on EC2 |
| OIDC / Web Identity | Automatic via `AWS_WEB_IDENTITY_TOKEN_FILE` (GitHub Actions, EKS) |

If the tool cannot resolve credentials at all, it exits with a clear error message listing all supported methods.

---

## Example Output

```
────────────── ECS Doctor — prod-cluster / payments-service ──────────────

╭─ Root Cause ─────────────────────────────────────────────────────────╮
│                                                                       │
│  Container is being OOM-killed (out of memory)                        │
│                                                                       │
│  Confidence: 97%                                                      │
│                                                                       │
│  Suggested fix:                                                       │
│  Increase the container memory reservation in the task definition.    │
│  Enable CloudWatch Container Insights to track memory utilization.    │
│  Profile the application for memory leaks — common causes include     │
│  unbounded caches, unclosed DB connections, JVM heap misconfiguration.│
│                                                                       │
╰───────────────────────────────────────────────────────────────────────╯

  Source        Type            Severity   Message
  stop_reasons  oom_killed      CRITICAL   Container 'app' OOM-killed (exit 137). (3 tasks)
  logs          log_crash_sig   CRITICAL   [app] OOM in logs detected (task abc123)
  events        task_thrashing  CRITICAL   Crash loop: 4 starts and 4 stops in last 20 events

(1 additional finding not shown — run with --json to see all.)

  Metric              Average    Maximum
  CPU Utilization     12.4%      18.1%
  Memory Utilization  94.2%      99.8%     ← anomaly flagged

  Desired / Running / Pending: 3 / 0 / 0
  Launch type: FARGATE  Platform: LATEST
  Deployment: min 100% / max 200%  Circuit breaker: on

  Container   Image              CPU   Memory   Log Group
  app         payments:v1.2.3    256   512      /ecs/payments

Diagnosis completed in 843ms.
```

### JSON output (`--json`)

```json
{
  "request": {
    "cluster": "prod-cluster",
    "service": "payments-service",
    "region": "us-east-1",
    "account_id": "123456789012"
  },
  "root_cause": {
    "cause": "Container is being OOM-killed (out of memory)",
    "confidence": 0.97,
    "suggested_fix": "Increase the container memory reservation...",
    "evidence": [...]
  },
  "all_findings": [...],
  "metrics": {
    "cpu_avg_percent": 12.4,
    "cpu_max_percent": 18.1,
    "memory_avg_percent": 94.2,
    "memory_max_percent": 99.8
  },
  "service_config": { ... },
  "task_config": { ... },
  "duration_ms": 843
}
```

---

## What Gets Diagnosed

### Diagnosers

| Diagnoser | AWS APIs used | What it catches |
|-----------|--------------|-----------------|
| **events** | `ecs:DescribeServices` | Placement failures, health check failures, deployment rollbacks, crash loops (thrashing), deployment config deadlock |
| **stop_reasons** | `ecs:ListTasks`, `ecs:DescribeTasks` | OOM (exit 137/139), image pull failures, missing secrets, non-zero exits, premature exit 0, SIGTERM not handled (exit 143), Spot interruption, TaskFailedToStart |
| **logs** | `logs:GetLogEvents` | Python/Java/Go/Node/Rust/.NET/PHP/Ruby crashes, connection refused, DNS failures, TLS errors, wrong CPU architecture, missing files, DB deadlocks, OOM in logs, disk full, read-only filesystem, EFS/NFS mount failures |
| **alb_health** | `elasticloadbalancing:DescribeTargetHealth` | Unhealthy targets — health check timeout, connection refused, non-2xx response |
| **metrics** | `cloudwatch:GetMetricData` | CPU or memory utilization above 85% (last 3 hours) |
| **config** | `ecs:DescribeTaskDefinition` | Invalid Fargate CPU/memory combination, service deployment configuration |
| **network** | `ec2:Describe*` | Security groups blocking egress, no NAT Gateway in route table, ENI not attached |

### Root Cause Categories (scored by confidence)

- Container OOM-killed (memory exhaustion)
- Cannot pull container image (registry auth, rate limit, bad tag)
- Task initialization failure (missing secret, SSM parameter, config resource)
- Insufficient cluster capacity (placement failure)
- ALB targets unhealthy (timeout, connection refused, non-2xx)
- Health check failing (container or ALB level)
- Deployment failed — circuit breaker triggered rollback
- Deployment config deadlock (min=100%, max=100%, running=0)
- Application crash-looping (task thrashing)
- Application exiting with non-zero code
- Container not handling SIGTERM (graceful shutdown failure)
- Fargate Spot task interrupted by AWS
- Task failed to start before startTimeout
- Application crash signature in logs
- High CPU or memory utilization (anomaly threshold 85%)
- Network connectivity blocked (security group, no NAT, ENI)
- Disk error or EFS mount failure

### Crash Patterns in Logs

The log diagnoser matches against 25+ patterns:

| Pattern | Severity |
|---------|----------|
| Python `Traceback (most recent call last)` | HIGH |
| Java `Exception in thread` | HIGH |
| Go `panic:` | HIGH |
| Node.js `UnhandledPromiseRejection` | HIGH |
| Rust `thread '...' panicked at` | HIGH |
| .NET `System.Exception:` / `Unhandled exception` | HIGH |
| PHP `Fatal error:` | HIGH |
| `exec format error` (wrong CPU architecture) | CRITICAL |
| `out of memory` / `cannot allocate memory` | CRITICAL |
| `connection refused` | MEDIUM |
| `no such host` (DNS failure) | MEDIUM |
| `certificate expired` / `SSL error` | MEDIUM |
| `FATAL:` (DB fatal) | HIGH |
| `deadlock detected` | HIGH |
| `no space left on device` (disk full) | CRITICAL |
| `read-only file system` | HIGH |
| `disk quota exceeded` | HIGH |
| `mount.nfs` / `nfs: server not responding` (EFS failure) | CRITICAL |
| `exec: ... permission denied` (entrypoint not executable) | HIGH |
| `no such file or directory` | HIGH |
| `SecretNotFound` / `secret not found` | HIGH |

---

## Running as a Container / on Fargate

The `deploy/` directory contains production-ready deployment files.

### Docker

```bash
# Build
docker build -f deploy/Dockerfile -t ecs-doctor .

# Run locally
docker run -p 8080:8080 \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -v ~/.aws:/home/ecsdoctor/.aws:ro \
  ecs-doctor

# Open http://localhost:8080
```

### Deploy to Fargate

1. Push the image to ECR:
   ```bash
   aws ecr create-repository --repository-name ecs-doctor
   docker tag ecs-doctor:latest <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/ecs-doctor:latest
   docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/ecs-doctor:latest
   ```

2. Edit `deploy/task-definition.json` — replace `ACCOUNT_ID` and `REGION` placeholders.

3. Create the IAM roles referenced in the task definition — use `deploy/iam-policy.json` as the task role policy.

4. Register and run the task definition:
   ```bash
   aws ecs register-task-definition --cli-input-json file://deploy/task-definition.json
   aws ecs create-service \
     --cluster your-cluster \
     --service-name ecs-doctor \
     --task-definition ecs-doctor \
     --desired-count 1 \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=ENABLED}"
   ```

The web UI will be available on port 8080 of the task's public IP or via an ALB.

---

## Required IAM Permissions

Full minimum policy (save as `deploy/iam-policy.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeServices",
        "ecs:DescribeTasks",
        "ecs:DescribeTaskDefinition",
        "ecs:DescribeClusters",
        "ecs:ListTasks",
        "ecs:ListClusters",
        "ecs:ListServices"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:GetLogEvents",
        "logs:FilterLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:*:*:log-group:/ecs/*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["elasticloadbalancing:DescribeTargetHealth"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeRouteTables",
        "ec2:DescribeNatGateways",
        "ec2:DescribeNetworkInterfaces"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

**Minimum permissions for a fast scan** (skip `--no-metrics` not needed, just omit CloudWatch + EC2):

If you only have ECS + Logs + ELB permissions, run with `--no-metrics`. `ecs-doctor` will gracefully skip any check it lacks permissions for and report exactly which IAM action and resource ARN you'd need to add.

---

## Project Structure

```
ecs_doctor/
├── cli.py                  # Click CLI — diagnose, browse, serve subcommands
├── engine.py               # Shared orchestration layer (DiagnosisRequest/Result)
├── models.py               # Finding, RootCause, MetricSnapshot, TaskConfig dataclasses
├── aggregator.py           # Confidence scoring and root-cause ranking
├── _aws.py                 # ServiceDataCache, IAM error helpers
├── streaming.py            # Live log streaming generator (CLI + web SSE)
├── wizard.py               # Interactive questionary-based cluster/service browser
└── diagnosers/
    ├── events.py           # ECS service events + deployment deadlock detection
    ├── stop_reasons.py     # Task stop reason and container exit code classifier
    ├── logs.py             # CloudWatch log crash pattern matcher (25+ patterns)
    ├── alb_health.py       # ALB target health checker
    ├── metrics.py          # CloudWatch CPU/memory utilization (last 3h)
    ├── config.py           # Task definition + service config extractor + Fargate validation
    └── network.py          # Security group, NAT gateway, ENI attachment checks

web/
├── app.py                  # FastAPI application factory
├── routes/
│   ├── diagnose.py         # GET / (form), POST /diagnose (HTMX), GET /api/diagnose (JSON)
│   ├── stream.py           # GET /api/stream-logs (Server-Sent Events)
│   └── health.py           # GET /healthz → {"status":"ok"}
├── templates/
│   ├── base.html           # HTMX CDN, layout shell
│   ├── index.html          # Diagnosis form
│   └── report.html         # Results: root cause, metrics, config, evidence, log stream
└── static/
    ├── style.css           # Dark theme
    └── app.js              # SSE EventSource for live log streaming

deploy/
├── Dockerfile              # python:3.12-slim, non-root user, port 8080
├── task-definition.json    # Fargate 256 CPU / 512 MB, awsvpc, /healthz health check
└── iam-policy.json         # Minimum task role policy
```

---

## Development

Requires **Python 3.12+**.

```bash
# Install with dev + all optional extras
pip install -e ".[dev,web,interactive]"

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=ecs_doctor --cov-report=term-missing

# Run with uv (if uv is installed)
uv run pytest tests/ -q
```

### Running the Web UI locally

```bash
pip install -e ".[web]"
ecs-doctor serve --reload
# Open http://localhost:8080
```

### Adding a new diagnoser

1. Create `ecs_doctor/diagnosers/my_check.py` with a `diagnose_my_check(...)` function that returns `list[Finding]`
2. Add new `FindingType` values to `ecs_doctor/models.py`
3. Add hypothesis entries to `ecs_doctor/aggregator.py`
4. Call the new function from `ecs_doctor/engine.py` `run_diagnosis()`
5. Add tests in `tests/test_my_check.py`

---

## License

MIT — see [LICENSE](LICENSE).
