
from botocore.exceptions import ClientError

from ecs_doctor._aws import ServiceDataCache, _AccessDeniedCached, iam_finding, is_access_denied, service_resource_arn
from ecs_doctor.models import Finding, FindingType, Severity

_HTTPS_PORT = 443
_HTTP_PORT = 80
_NFS_PORT = 2049
_INTERNET_GATEWAY_PREFIX = "igw-"
_NAT_GATEWAY_PREFIX = "nat-"
_SOURCE = "network"
_NACL_DENY = "deny"
_NACL_EGRESS = True
_ECR_ENDPOINT_SUFFIXES = frozenset({"ecr.api", "ecr.dkr", "s3"})


def _has_outbound_internet(routes: list[dict]) -> bool:
    """Return True if any route provides internet access (IGW or NAT)."""
    for route in routes:
        target = (
            route.get("GatewayId", "")
            or route.get("NatGatewayId", "")
            or route.get("TransitGatewayId", "")
        )
        if target.startswith(_INTERNET_GATEWAY_PREFIX) or target.startswith(_NAT_GATEWAY_PREFIX):
            return True
    return False


def _has_vpc_endpoints(ec2_client, vpc_id: str, region: str) -> bool:
    """Return True if the VPC has the core endpoints needed for ECR, S3, and secrets.

    If present, tasks in private subnets without NAT can still pull images and read secrets.
    """
    try:
        resp = ec2_client.describe_vpc_endpoints(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "state", "Values": ["available"]},
            ]
        )
    except ClientError:
        return False

    endpoint_services: set[str] = {ep.get("ServiceName", "") for ep in resp.get("VpcEndpoints", [])}
    required = {f"com.amazonaws.{region}.{suffix}" for suffix in _ECR_ENDPOINT_SUFFIXES}
    return required.issubset(endpoint_services)


def _check_nacl(ec2_client, subnet_id: str, region: str, account_id: str) -> Finding | None:
    """Return a finding if a NACL explicitly denies outbound traffic on port 443 or 80."""
    try:
        resp = ec2_client.describe_network_acls(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
        )
    except ClientError as exc:
        if is_access_denied(exc):
            return iam_finding(
                "ec2:DescribeNetworkAcls",
                f"arn:aws:ec2:{region}:{account_id}:network-acl/*",
                _SOURCE,
            )
        raise

    nacls = resp.get("NetworkAcls", [])
    if not nacls:
        return None

    egress_entries = [e for e in nacls[0].get("Entries", []) if e.get("Egress") is _NACL_EGRESS]
    for entry in sorted(egress_entries, key=lambda e: e.get("RuleNumber", 32767)):
        if entry.get("RuleAction", "").lower() != _NACL_DENY:
            continue
        from_port = entry.get("PortRange", {}).get("From", 0)
        to_port = entry.get("PortRange", {}).get("To", 0)
        if from_port <= _HTTPS_PORT <= to_port or from_port <= _HTTP_PORT <= to_port:
            return Finding(
                type=FindingType.NETWORK_ACL_DENY,
                message=(
                    f"Network ACL rule {entry.get('RuleNumber')} in subnet {subnet_id} "
                    f"explicitly DENIES outbound traffic on ports {from_port}–{to_port}. "
                    "Tasks cannot reach ECR, Secrets Manager, or external APIs."
                ),
                severity=Severity.HIGH,
                raw_data={"subnet_id": subnet_id, "nacl_entry": entry},
                source=_SOURCE,
            )
    return None


def _check_subnet_egress(ec2_client, subnet_id: str, region: str, account_id: str) -> Finding | None:
    """Check if the subnet has a route to the internet."""
    try:
        resp = ec2_client.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
        )
    except ClientError as exc:
        if is_access_denied(exc):
            return iam_finding("ec2:DescribeRouteTables", f"arn:aws:ec2:{region}:{account_id}:route-table/*", _SOURCE)
        raise

    route_tables = resp.get("RouteTables", [])
    if not route_tables:
        return None

    routes = route_tables[0].get("Routes", [])
    if _has_outbound_internet(routes):
        return None

    vpc_id = route_tables[0].get("VpcId", "")
    if vpc_id and _has_vpc_endpoints(ec2_client, vpc_id, region):
        return None

    return Finding(
        type=FindingType.NETWORK_CONNECTIVITY,
        message=(
            f"Subnet {subnet_id} has no route to the internet (no IGW or NAT Gateway) "
            "and no VPC endpoints for ECR/S3. "
            "Tasks cannot pull images, reach Secrets Manager, or call external APIs."
        ),
        severity=Severity.HIGH,
        raw_data={"subnet_id": subnet_id, "routes": routes, "vpc_id": vpc_id},
        source=_SOURCE,
    )


def _check_security_group_egress(ec2_client, sg_id: str, region: str, account_id: str) -> Finding | None:
    """Check if the security group has any outbound rules."""
    try:
        resp = ec2_client.describe_security_groups(GroupIds=[sg_id])
    except ClientError as exc:
        if is_access_denied(exc):
            return iam_finding("ec2:DescribeSecurityGroups", f"arn:aws:ec2:{region}:{account_id}:security-group/*", _SOURCE)
        raise

    groups = resp.get("SecurityGroups", [])
    if not groups:
        return None

    egress = groups[0].get("IpPermissionsEgress", [])
    if not egress:
        return Finding(
            type=FindingType.NETWORK_CONNECTIVITY,
            message=(
                f"Security group {sg_id} has no outbound rules. "
                f"Tasks cannot reach ECR, Secrets Manager, CloudWatch, or any external service."
            ),
            severity=Severity.HIGH,
            raw_data={"security_group_id": sg_id},
            source=_SOURCE,
        )
    return None


def _get_task_network_details(
    ecs_client,
    cluster: str,
    service: str,
) -> tuple[list[str], list[str]]:
    """Return (subnet_ids, security_group_ids) from a running or stopped task."""
    try:
        running = ecs_client.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING")
        arns = running.get("taskArns", [])
        if not arns:
            stopped = ecs_client.list_tasks(cluster=cluster, serviceName=service, desiredStatus="STOPPED", maxResults=1)
            arns = stopped.get("taskArns", [])
        if not arns:
            return [], []

        tasks_resp = ecs_client.describe_tasks(cluster=cluster, tasks=arns[:1])
        task = tasks_resp.get("tasks", [{}])[0]
    except ClientError:
        return [], []

    subnet_ids: list[str] = []
    sg_ids: list[str] = []

    for attachment in task.get("attachments", []):
        if attachment.get("type") != "ElasticNetworkInterface":
            continue
        for detail in attachment.get("details", []):
            if detail.get("name") == "subnetId":
                subnet_ids.append(detail["value"])

    vpc_config = task.get("vpcConfiguration", {})
    subnet_ids = subnet_ids or vpc_config.get("subnets", [])
    sg_ids = vpc_config.get("securityGroups", [])

    return subnet_ids, sg_ids


def diagnose_network(
    service_cache: ServiceDataCache,
    ecs_client,
    ec2_client,
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
            _SOURCE,
        )]

    if not svc:
        return []

    network_config = svc.get("networkConfiguration", {}).get("awsvpcConfiguration", {})
    subnet_ids: list[str] = network_config.get("subnets", [])
    sg_ids: list[str] = network_config.get("securityGroups", [])

    if not subnet_ids and not sg_ids:
        subnet_ids, sg_ids = _get_task_network_details(ecs_client, cluster, service)

    if not subnet_ids and not sg_ids:
        return []

    findings: list[Finding] = []

    for subnet_id in subnet_ids[:2]:
        finding = _check_subnet_egress(ec2_client, subnet_id, region, account_id)
        if finding:
            findings.append(finding)
            break

    for subnet_id in subnet_ids[:1]:
        nacl_finding = _check_nacl(ec2_client, subnet_id, region, account_id)
        if nacl_finding:
            findings.append(nacl_finding)

    for sg_id in sg_ids[:2]:
        finding = _check_security_group_egress(ec2_client, sg_id, region, account_id)
        if finding:
            findings.append(finding)
            break

    return findings
