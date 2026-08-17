from __future__ import annotations

import threading

from tests.conftest import ACCOUNT, CLUSTER, REGION, SERVICE, access_denied_error, make_ecs_client

from ecs_doctor._aws import ServiceDataCache, _AccessDeniedCached

_TD_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/my-td:1"
_TD_RESP = {"taskDefinition": {"taskDefinitionArn": _TD_ARN, "containerDefinitions": []}}
_SVC_RESP = {"services": [{"serviceName": SERVICE, "taskDefinition": _TD_ARN}]}


def test_get_service_is_cached():
    ecs = make_ecs_client(describe_services=_SVC_RESP)
    cache = ServiceDataCache(ecs)
    assert cache.get_service(CLUSTER, SERVICE, REGION, ACCOUNT)["serviceName"] == SERVICE
    assert cache.get_service(CLUSTER, SERVICE, REGION, ACCOUNT)["serviceName"] == SERVICE
    assert ecs.describe_services.call_count == 1


def test_get_task_definition_is_cached():
    ecs = make_ecs_client(describe_services=_SVC_RESP, describe_task_definition=_TD_RESP)
    cache = ServiceDataCache(ecs)
    first = cache.get_task_definition(_TD_ARN)
    second = cache.get_task_definition(_TD_ARN)
    assert first["taskDefinitionArn"] == _TD_ARN
    assert second["taskDefinitionArn"] == _TD_ARN
    assert ecs.describe_task_definition.call_count == 1


def test_get_task_definition_access_denied_raises_cached():
    ecs = make_ecs_client(
        describe_task_definition=access_denied_error("DescribeTaskDefinition"),
    )
    cache = ServiceDataCache(ecs)
    try:
        cache.get_task_definition(_TD_ARN)
        raise AssertionError("expected _AccessDeniedCached")
    except _AccessDeniedCached:
        pass
    try:
        cache.get_task_definition(_TD_ARN)
        raise AssertionError("expected _AccessDeniedCached")
    except _AccessDeniedCached:
        pass
    assert ecs.describe_task_definition.call_count == 1


def test_concurrent_get_service_calls_describe_once():
    started = threading.Event()
    release = threading.Event()
    ecs = make_ecs_client()

    def _describe(**_kwargs):
        started.set()
        release.wait(timeout=2)
        return _SVC_RESP

    ecs.describe_services.side_effect = _describe
    cache = ServiceDataCache(ecs)
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            cache.get_service(CLUSTER, SERVICE, REGION, ACCOUNT)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    assert started.wait(timeout=2)
    release.set()
    for t in threads:
        t.join()
    assert errors == []
    assert ecs.describe_services.call_count == 1
