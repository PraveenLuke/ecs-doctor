"""Tests for ecs_doctor.diagnosers.network."""
from __future__ import annotations

from tests.conftest import (
    ACCOUNT,
    CLUSTER,
    REGION,
    SERVICE,
    access_denied_error,
    make_ecs_client,
    make_service_cache,
)

from ecs_doctor.diagnosers.network import _has_outbound_internet, diagnose_network
from ecs_doctor.models import FindingType, Severity

_SUBNET_ID = "subnet-12345"
_SG_ID = "sg-67890"


def _svc(subnets=None, sgs=None):
    return {
        "services": [
            {
                "networkConfiguration": {
                    "awsvpcConfiguration": {
                        "subnets": subnets or [_SUBNET_ID],
                        "securityGroups": sgs or [_SG_ID],
                    }
                },
                "loadBalancers": [],
            }
        ]
    }


def _route_tables(has_nat=True, has_igw=False):
    routes = []
    if has_nat:
        routes.append({"NatGatewayId": "nat-abc123", "DestinationCidrBlock": "0.0.0.0/0"})
    if has_igw:
        routes.append({"GatewayId": "igw-def456", "DestinationCidrBlock": "0.0.0.0/0"})
    if not routes:
        routes.append({"GatewayId": "local", "DestinationCidrBlock": "10.0.0.0/16"})
    return {"RouteTables": [{"Routes": routes}]}


def _security_groups(has_egress=True):
    egress = (
        [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
        if has_egress
        else []
    )
    return {"SecurityGroups": [{"GroupId": _SG_ID, "IpPermissionsEgress": egress}]}


def _call(ecs, ec2):
    return diagnose_network(make_service_cache(ecs), ecs, ec2, CLUSTER, SERVICE, REGION, ACCOUNT)


# ---------------------------------------------------------------------------
# _has_outbound_internet
# ---------------------------------------------------------------------------


class TestHasOutboundInternet:
    def test_nat_gateway_route_returns_true(self):
        routes = [{"NatGatewayId": "nat-abc", "DestinationCidrBlock": "0.0.0.0/0"}]
        assert _has_outbound_internet(routes) is True

    def test_igw_route_returns_true(self):
        routes = [{"GatewayId": "igw-abc", "DestinationCidrBlock": "0.0.0.0/0"}]
        assert _has_outbound_internet(routes) is True

    def test_local_only_returns_false(self):
        routes = [{"GatewayId": "local", "DestinationCidrBlock": "10.0.0.0/8"}]
        assert _has_outbound_internet(routes) is False

    def test_empty_routes_returns_false(self):
        assert _has_outbound_internet([]) is False

    def test_transit_gateway_returns_false(self):
        routes = [{"TransitGatewayId": "tgw-abc", "DestinationCidrBlock": "0.0.0.0/0"}]
        assert _has_outbound_internet(routes) is False


# ---------------------------------------------------------------------------
# diagnose_network
# ---------------------------------------------------------------------------


class TestDiagnoseNetwork:
    def test_healthy_network_returns_no_findings(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=True),
            describe_security_groups=_security_groups(has_egress=True),
        )
        assert _call(ecs, ec2) == []

    def test_access_denied_on_describe_services_returns_iam_finding(self):
        ecs = make_ecs_client(describe_services=access_denied_error("DescribeServices"))
        ec2 = make_ecs_client()
        findings = _call(ecs, ec2)
        assert any(f.type == FindingType.IAM_DENIED for f in findings)

    def test_no_service_returns_empty(self):
        ecs = make_ecs_client(describe_services={"services": []})
        ec2 = make_ecs_client()
        assert _call(ecs, ec2) == []

    def test_no_network_config_and_no_tasks_returns_empty(self):
        ecs = make_ecs_client(
            describe_services={"services": [{"networkConfiguration": {}, "loadBalancers": []}]},
        )
        ecs.list_tasks.return_value = {"taskArns": []}
        ec2 = make_ecs_client()
        assert _call(ecs, ec2) == []

    def test_no_nat_gateway_returns_network_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=False, has_igw=False),
            describe_security_groups=_security_groups(has_egress=True),
        )
        findings = _call(ecs, ec2)
        assert any(f.type == FindingType.NETWORK_CONNECTIVITY for f in findings)
        f = next(x for x in findings if x.type == FindingType.NETWORK_CONNECTIVITY)
        assert "subnet" in f.message.lower() or "NAT" in f.message or "route" in f.message.lower()

    def test_igw_route_is_accepted(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=False, has_igw=True),
            describe_security_groups=_security_groups(has_egress=True),
        )
        assert _call(ecs, ec2) == []

    def test_no_sg_egress_returns_network_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=True),
            describe_security_groups=_security_groups(has_egress=False),
        )
        findings = _call(ecs, ec2)
        assert any(f.type == FindingType.NETWORK_CONNECTIVITY for f in findings)
        f = next(x for x in findings if x.type == FindingType.NETWORK_CONNECTIVITY)
        assert (
            "security group" in f.message.lower()
            or "egress" in f.message.lower()
            or "outbound" in f.message.lower()
        )

    def test_access_denied_on_route_tables_returns_iam_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=access_denied_error("DescribeRouteTables"),
            describe_security_groups=_security_groups(has_egress=True),
        )
        findings = _call(ecs, ec2)
        assert any(f.type == FindingType.IAM_DENIED for f in findings)

    def test_access_denied_on_describe_security_groups_returns_iam_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=True),
            describe_security_groups=access_denied_error("DescribeSecurityGroups"),
        )
        findings = _call(ecs, ec2)
        assert any(f.type == FindingType.IAM_DENIED for f in findings)

    def test_empty_route_tables_returns_no_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables={"RouteTables": []},
            describe_security_groups=_security_groups(has_egress=True),
        )
        assert _call(ecs, ec2) == []

    def test_empty_security_groups_returns_no_finding(self):
        ecs = make_ecs_client(describe_services=_svc())
        ec2 = make_ecs_client(
            describe_route_tables=_route_tables(has_nat=True),
            describe_security_groups={"SecurityGroups": []},
        )
        assert _call(ecs, ec2) == []


# ---------------------------------------------------------------------------
# VPC endpoint suppression — no NAT but ECR/S3 endpoints present
# ---------------------------------------------------------------------------

def _vpc_endpoints_resp(region=REGION, has_required=True):
    if not has_required:
        return {"VpcEndpoints": []}
    return {
        "VpcEndpoints": [
            {"ServiceName": f"com.amazonaws.{region}.ecr.api", "State": "available"},
            {"ServiceName": f"com.amazonaws.{region}.ecr.dkr", "State": "available"},
            {"ServiceName": f"com.amazonaws.{region}.s3", "State": "available"},
        ]
    }


def test_no_nat_but_vpc_endpoints_suppresses_finding():
    ecs = make_ecs_client(describe_services=_svc())
    ec2 = make_ecs_client(
        describe_route_tables={"RouteTables": [{"Routes": [{"GatewayId": "local", "DestinationCidrBlock": "10.0.0.0/16"}], "VpcId": "vpc-abc123"}]},
        describe_vpc_endpoints=_vpc_endpoints_resp(has_required=True),
        describe_security_groups=_security_groups(has_egress=True),
        describe_network_acls={"NetworkAcls": []},
    )
    findings = _call(ecs, ec2)
    assert not any(f.type == FindingType.NETWORK_CONNECTIVITY for f in findings)


def test_no_nat_no_vpc_endpoints_produces_finding():
    ecs = make_ecs_client(describe_services=_svc())
    ec2 = make_ecs_client(
        describe_route_tables={"RouteTables": [{"Routes": [{"GatewayId": "local", "DestinationCidrBlock": "10.0.0.0/16"}], "VpcId": "vpc-abc123"}]},
        describe_vpc_endpoints=_vpc_endpoints_resp(has_required=False),
        describe_security_groups=_security_groups(has_egress=True),
        describe_network_acls={"NetworkAcls": []},
    )
    findings = _call(ecs, ec2)
    assert any(f.type == FindingType.NETWORK_CONNECTIVITY for f in findings)


# ---------------------------------------------------------------------------
# NACL deny check
# ---------------------------------------------------------------------------

def _nacl_resp(rule_action="allow", from_port=443, to_port=443):
    return {
        "NetworkAcls": [
            {
                "Entries": [
                    {
                        "RuleNumber": 100,
                        "Egress": True,
                        "RuleAction": rule_action,
                        "Protocol": "6",
                        "PortRange": {"From": from_port, "To": to_port},
                    }
                ]
            }
        ]
    }


def test_nacl_deny_on_443_produces_finding():
    ecs = make_ecs_client(describe_services=_svc())
    ec2 = make_ecs_client(
        describe_route_tables=_route_tables(has_nat=True),
        describe_security_groups=_security_groups(has_egress=True),
        describe_network_acls=_nacl_resp(rule_action="deny", from_port=0, to_port=65535),
    )
    findings = _call(ecs, ec2)
    assert any(f.type == FindingType.NETWORK_ACL_DENY for f in findings)
    f = next(x for x in findings if x.type == FindingType.NETWORK_ACL_DENY)
    assert f.severity == Severity.HIGH


def test_nacl_allow_produces_no_finding():
    ecs = make_ecs_client(describe_services=_svc())
    ec2 = make_ecs_client(
        describe_route_tables=_route_tables(has_nat=True),
        describe_security_groups=_security_groups(has_egress=True),
        describe_network_acls=_nacl_resp(rule_action="allow", from_port=0, to_port=65535),
    )
    findings = _call(ecs, ec2)
    assert not any(f.type == FindingType.NETWORK_ACL_DENY for f in findings)
