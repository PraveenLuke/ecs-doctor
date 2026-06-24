# Show HN: ecs-doctor – diagnose why your ECS service is failing in one command

**Title:** Show HN: ecs-doctor – one command to diagnose a broken ECS service

**URL:** https://github.com/PraveenLuke/ecs-doctor

---

**Body (post text):**

ECS debugging has always meant jumping between 5+ places: the ECS console for events, CloudWatch Logs for crash signatures, ALB target groups for health check failures, task definitions for config mistakes, and VPC route tables for network issues. On a bad day that's a 20-minute investigation before you even know where to look.

ecs-doctor pulls all of those signals together in parallel and collapses them into a single confidence-scored root cause:

```
$ ecs-doctor diagnose --cluster prod --service payments

╭──────────────────────────── Root Cause ────────────────────────────╮
│  Container is being OOM-killed (out of memory)     97% confidence  │
│                                                                     │
│  Suggested fix: Increase the container memory reservation in the   │
│  task definition. Profile the application for memory leaks —       │
│  common causes include unbounded caches, unclosed DB connections,  │
│  JVM heap settings.                                                 │
╰─────────────────────────────────────────────────────────────────────╯

Source         Type             Severity  Message
stop_reasons   oom_killed       CRITICAL  Container 'app' exit 137 (3 tasks)
logs           log_crash_sig    CRITICAL  OOM detected in CloudWatch Logs
events         task_thrashing   CRITICAL  4 starts / 4 stops in last 20 events
```

It runs 7 checks in parallel (service events, stop reasons, CloudWatch Logs, ALB health, CloudWatch metrics, task config, network connectivity) and finishes in under a second.

```bash
pipx install ecs-doctor
ecs-doctor diagnose --cluster prod --service payments
```

Works with any ECS launch type (EC2, Fargate, Fargate Spot). Uses your existing AWS credentials/profiles. Read-only IAM permissions only.

Built it after spending too many late nights manually correlating these signals across incidents. Would love feedback on what other checks would be useful.

GitHub: https://github.com/PraveenLuke/ecs-doctor
PyPI: https://pypi.org/project/ecs-doctor/
