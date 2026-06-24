# r/aws post draft

**Subreddit:** r/aws (also cross-post to r/devops, r/sre)

**Title:** I built a CLI tool that diagnoses broken ECS services in one command — feedback welcome

---

**Body:**

After spending too many late nights manually correlating ECS events, CloudWatch logs, ALB target health, and network configs during incidents, I built **ecs-doctor** — a CLI that pulls all those signals together and gives you a single root-cause diagnosis.

## What it does

Run one command against a broken service:

```bash
pipx install ecs-doctor
ecs-doctor diagnose --cluster prod --service payments
```

It runs 7 checks in parallel against the AWS APIs:

- **Service events** — why a deployment stalled or rolled back
- **Stop reasons** — why your container stopped (OOM, bad image, missing secret, startup crash, etc.)
- **CloudWatch Logs** — crash signatures across Python, Java, Go, Node.js, and more — without you grepping
- **ALB health** — why your load balancer has no healthy targets
- **CloudWatch metrics** — whether CPU or memory pressure is the actual cause
- **Task config** — misconfiguration in your task definition
- **Network** — whether your tasks can reach the internet or AWS services

All findings are scored and collapsed into a single confidence-scored root cause with a suggested fix.

## Why I built it

The standard ECS debugging workflow is: open ECS console → check events → go to CloudWatch Logs → search for errors → check ALB target group → check security groups → check route tables. That's 5 different consoles and 15+ clicks before you know where to look.

During an incident, that time costs real money. I wanted something that could answer "what is wrong with this service?" in under a second.

## Links

- GitHub: https://github.com/PraveenLuke/ecs-doctor
- PyPI: https://pypi.org/project/ecs-doctor/
- Install: `pipx install ecs-doctor`

## Looking for feedback

- What ECS failure modes have you hit that aren't covered above?
- Any checks you'd want to see added?
- Does the output format work for you, or would you prefer something different?

Read-only IAM permissions only — it never writes anything to your account.
