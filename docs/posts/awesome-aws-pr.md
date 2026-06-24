# awesome-aws PR draft

**Target repo:** https://github.com/donnemartin/awesome-aws
**Your fork:** fork it first, then open a PR

## PR title
Add ecs-doctor to ECS section

## PR body

Adds `ecs-doctor` to the Elastic Container Service section.

`ecs-doctor` is a CLI tool that diagnoses broken ECS services in one command. It runs 7 parallel AWS API checks (service events, stop reasons, CloudWatch Logs, ALB health, metrics, task config, and network) and collapses findings into a single confidence-scored root cause with a suggested fix. Works with EC2, Fargate, and Fargate Spot.

- GitHub: https://github.com/PraveenLuke/ecs-doctor
- PyPI: https://pypi.org/project/ecs-doctor/
- License: MIT

---

## Diff to add (find the ECS section in README.md and add this line)

In the **Elastic Container Service** section, add:

```
* [ecs-doctor ★N](https://github.com/PraveenLuke/ecs-doctor) - CLI that diagnoses why an ECS service is failing — events, logs, ALB health, metrics, config, and network in a single confidence-scored report.
```

---

## Steps to submit

1. Go to https://github.com/donnemartin/awesome-aws
2. Click **Fork** (top right)
3. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/awesome-aws
   cd awesome-aws
   ```
4. Open `README.md`, find the **Elastic Container Service** section
5. Add the line above in alphabetical order by tool name (ecs-doctor → under 'e')
6. Commit and push:
   ```bash
   git add README.md
   git commit -m "Add ecs-doctor to ECS section"
   git push origin main
   ```
7. Open a PR from your fork to donnemartin/awesome-aws

---

## Other awesome lists to submit to

- https://github.com/agarrharr/awesome-cli-apps — add under "Utilities" or "Developer tools"
- https://github.com/aws-samples/awesome-ecs — if it exists
- https://github.com/shuaibiyy/awesome-terraform (if you add Terraform module support later)
