# dev.to article draft

**Title:** How I built a one-command ECS debugger in Python

**Tags:** aws, python, devops, opensource

---

## The problem

Every time an ECS service breaks in production, you end up doing the same ritual:

1. Open the ECS console, click through to the service events tab
2. Switch to CloudWatch Logs, filter for the right log group, search for errors
3. Open the EC2 console, check the ALB target group health
4. Go back to ECS, open the task definition, look for misconfigurations
5. Open the VPC console, check route tables and security groups

That's five different AWS consoles, 20+ clicks, and at least 5 minutes before you even know what you're looking at. During a production incident, every one of those minutes is expensive.

I've been through this ritual too many times. So I built **ecs-doctor**.

## What ecs-doctor does

```bash
pipx install ecs-doctor
ecs-doctor diagnose --cluster prod --service payments
```

It hits all the relevant AWS APIs in parallel and collapses the results into a single confidence-scored root cause:

```
╭──────────────────────────── Root Cause ────────────────────────────╮
│  Container is being OOM-killed (out of memory)     97% confidence  │
│                                                                     │
│  Suggested fix: Increase the container memory reservation in the   │
│  task definition. Profile for memory leaks — common causes include │
│  unbounded caches, unclosed DB connections, JVM heap settings.     │
╰─────────────────────────────────────────────────────────────────────╯
```

Under the root cause you get the ranked list of supporting evidence:

```
Source         Type             Severity  Message
stop_reasons   oom_killed       CRITICAL  Container 'app' exit 137 (3 tasks)
logs           log_crash_sig    CRITICAL  OOM detected in CloudWatch Logs
events         task_thrashing   CRITICAL  4 starts / 4 stops in last 20 events
metrics        high_memory      HIGH      Memory avg 94.2%, max 99.8%
```

Seven checks, one second, one answer.

## How it works

### Parallel AWS calls

The main engine fires all seven diagnosers concurrently using `asyncio.gather`. Each diagnoser is independent and only calls the APIs it needs:

```python
results = await asyncio.gather(
    diagnose_events(cluster, service, ecs),
    diagnose_stop_reasons(cluster, service, ecs),
    diagnose_logs(cluster, service, ecs, logs),
    diagnose_alb_health(cluster, service, ecs, elbv2),
    diagnose_metrics(cluster, service, cw),
    diagnose_config(cluster, service, ecs),
    diagnose_network(cluster, service, ecs, ec2),
    return_exceptions=True,
)
```

Each diagnoser returns a list of `Finding` objects with a type, severity, message, and confidence weight.

### Confidence scoring

The aggregator scores all findings against a hypothesis table. Each `FindingType` maps to a root cause hypothesis with a weight. When multiple findings point to the same hypothesis (e.g., OOM kill in stop reasons + OOM in logs + high memory metric), the weights compound and the confidence climbs.

```python
_HYPOTHESIS = {
    FindingType.OOM_KILLED:        Hypothesis("Container is being OOM-killed", weight=0.6),
    FindingType.LOG_CRASH_SIG:     Hypothesis("Crash detected in logs",        weight=0.4),
    FindingType.HIGH_MEMORY:       Hypothesis("Memory pressure",                weight=0.3),
    ...
}
```

The aggregator picks the highest-scoring hypothesis as the root cause.

### Data-driven pattern matching

Rather than long if-chains, each diagnoser uses a rules table. For example, the stop reasons diagnoser maps ECS stop codes to finding types:

```python
_TASK_STOP_CODE_MAP = {
    "OutOfMemoryError:Container killed": FindingType.OOM_KILLED,
    "CannotPullContainerError":          FindingType.IMAGE_PULL_FAILURE,
    "Essential container in task exited": FindingType.EXIT_CODE,
    ...
}
```

Adding a new failure pattern is a one-line table entry. No branching logic to maintain.

## What it catches

- OOM kills, exit codes, task thrashing
- Image pull failures (bad tag, ECR auth, rate limit)
- Missing secrets (Secrets Manager / Parameter Store permission denied)
- ALB health check misconfiguration
- Deployment stalls (not enough healthy % to drain)
- CPU and memory pressure from CloudWatch metrics
- Fargate CPU/memory combination misconfigurations
- Missing NAT gateway or IGW (tasks can't reach internet or AWS APIs)
- Security group egress rules blocking outbound traffic

## Try it

```bash
pipx install ecs-doctor
ecs-doctor diagnose --cluster my-cluster --service my-service
```

Or with the web UI:

```bash
pip install "ecs-doctor[web]"
ecs-doctor serve
```

Source: https://github.com/PraveenLuke/ecs-doctor

---

What ECS failure modes have you hit that aren't on that list? I'm actively adding patterns — open an issue or PR.
