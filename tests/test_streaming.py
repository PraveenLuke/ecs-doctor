from unittest.mock import MagicMock

from ecs_doctor.streaming import _poll_stream


def test_first_poll_reads_from_stream_tail():
    logs = MagicMock()
    logs.get_log_events.return_value = {"events": [], "nextForwardToken": "token-1"}
    _poll_stream(logs, "/ecs/app", "ecs/app/taskid", None)
    kwargs = logs.get_log_events.call_args.kwargs
    assert kwargs["startFromHead"] is False
