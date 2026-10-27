from __future__ import annotations

import subprocess
from urllib.error import HTTPError

import pytest

from screen_assistant_server.backstage_bridge import (
    BackstageBridge, BackstageFatalError, BridgeConfig, CliInvocation,
)


def accepted(payload):
    return {"status": "accepted", "task_id": payload["task_id"],
            "result_status": payload["status"]}


def binding_evidence(douyin_account, room_id="room-test"):
    return {
        "douyin_account": douyin_account,
        "room_id": room_id,
        "observed_at": "2026-10-27T12:00:00+08:00",
        "locator": "synthetic same-session binding evidence",
    }


def test_binding_evidence_account_must_match_record_identity():
    record = {
        "douyin_account": "expected-account",
        "binding_evidence": {
            "douyin_account": "different-account",
            "room_id": "room-test",
            "observed_at": "2026-10-27T12:00:00+08:00",
            "locator": "synthetic same-session binding evidence",
        },
    }

    assert BackstageBridge._binding_evidence_problem(record) == "cli_binding_account_mismatch"


def test_binding_evidence_requires_nested_observed_at():
    record = {
        "douyin_account": "expected-account",
        "binding_observed_at": "2026-10-27T12:00:00+08:00",
        "binding_evidence": {
            "douyin_account": "expected-account",
            "room_id": "room-test",
            "locator": "synthetic same-session binding evidence",
        },
    }

    assert BackstageBridge._binding_evidence_problem(record) == "cli_binding_evidence_missing"


def test_bridge_rejects_binding_account_mismatch_after_real_app_stop():
    task = {
        "task_id": "task-binding-account", "session_id": "session-binding-account",
        "douyin_account": "expected-account", "status": "capturing",
        "start_signal_source": "ios_app", "stop_signal_source": "ios_app",
        "stop_signal_received": True, "stopped_at_ms": 100,
    }
    calls = []
    cli_calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, claimed_task):
        cli_calls.append(command)
        assert command == "start"
        return {"record": {
            "session_id": claimed_task["session_id"],
            "douyin_account": claimed_task["douyin_account"],
            "status": "waiting_for_stop",
            "start_signal_source": "ios_app",
            "binding_evidence": binding_evidence("different-account"),
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert all("cli_binding_account_mismatch" in reason
               for reason in payload["missing_fields"].values())


def test_bridge_uses_one_cli_task_and_posts_result_to_origin_session():
    task = {
        "task_id": "task-001", "session_id": "session-001",
        "douyin_account": "test-account", "status": "waiting_for_stop",
        "start_signal_source": "ios_app", "stop_signal_received": False,
        "stopped_at_ms": None, "result_status": None,
    }
    result_record = {
        "status": "partial", "session_id": "session-001",
        "douyin_account": "test-account",
        "start_signal_source": "ios_app", "stop_signal_source": "ios_app",
        "metrics": {"累计观众": 12000, "新增粉丝": 2, "评论人数": None,
                    "送礼人数": 1, "获得音浪": 25000},
        "raw_displays": {"累计观众": "约1.2万"},
        "approximate_metrics": ["累计观众"],
        "metrics_captured_at": "2026-10-27T12:00:00+08:00",
        "room_id": "room-test", "capture_method": "受控CLI替身",
        "value_semantics": "下播信号后读取的本场左侧汇总",
        "missing_reasons": {"评论人数": "页面未显示"},
    }
    calls = []
    gets = 0

    def http(method, path, payload=None):
        nonlocal gets
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/task-001"):
            gets += 1
            current = dict(task)
            current["stop_signal_received"] = gets >= 2
            current["stopped_at_ms"] = 1_790_000_001_000 if gets >= 2 else None
            current["status"] = "capturing" if gets >= 2 else "waiting_for_stop"
            if gets >= 2:
                current["stop_signal_source"] = "ios_app"
            return {"status": "ok", "task": current}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    cli_calls = []

    def cli(command, claimed_task):
        cli_calls.append((command, claimed_task["task_id"], claimed_task["session_id"]))
        if command == "result":
            return {"record": result_record}
        if command == "start":
            return {"record": {
                "status": "waiting_for_stop", "session_id": claimed_task["session_id"],
                "douyin_account": claimed_task["douyin_account"],
                "start_signal_source": claimed_task["start_signal_source"],
                "binding_evidence": binding_evidence(claimed_task["douyin_account"]),
            }}
        return {"record": {
            "status": "waiting_for_stop", "session_id": claimed_task["session_id"],
            "douyin_account": claimed_task["douyin_account"],
            "start_signal_source": claimed_task["start_signal_source"],
            "stop_signal_source": claimed_task["start_signal_source"],
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://127.0.0.1:8000/prefix", "worker-secret",
                     "python.exe", "recap_glm.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == [
        ("start", "task-001", "session-001"),
        ("stop", "task-001", "session-001"),
        ("result", "task-001", "session-001"),
    ]
    posted = calls[-1][2]
    assert calls[-1][1] == "v1/live/sessions/session-001/backstage/result"
    assert posted["task_id"] == "task-001"
    assert posted["douyin_account"] == "test-account"
    assert posted["metrics"] == result_record["metrics"]
    assert posted["stop_signal_received"] is True
    assert posted["start_signal_source"] == posted["stop_signal_source"] == "ios_app"
    assert posted["missing_fields"] == {"评论人数": "页面未显示"}


def test_bridge_unbound_start_posts_needs_input_after_same_app_stop():
    task = {
        "task_id": "task-unbound", "session_id": "session-unbound",
        "douyin_account": "test-account", "status": "waiting_for_stop",
        "start_signal_source": "android_app", "stop_signal_source": None,
        "stop_signal_received": False,
    }
    http_calls = []
    cli_calls = []
    gets = 0

    def http(method, path, payload=None):
        nonlocal gets
        http_calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/task-unbound"):
            gets += 1
            current = dict(task)
            if gets >= 2:
                current.update({
                    "status": "capturing", "stop_signal_source": "android_app",
                    "stop_signal_received": True, "stopped_at_ms": 1,
                })
            return {"status": "ok", "task": current}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, claimed_task):
        cli_calls.append((command, claimed_task["task_id"],
                          claimed_task["start_signal_source"]))
        return {"status": "needs_input", "reason": "edge_launch_unconfirmed"}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )

    assert bridge.run_once() is True

    assert [call[0] for call in http_calls] == ["POST", "GET", "GET", "POST"]
    assert cli_calls == [("start", "task-unbound", "android_app")]
    assert task["start_signal_source"] == "android_app"
    payload = http_calls[-1][2]
    assert payload["task_id"] == "task-unbound"
    assert payload["douyin_account"] == "test-account"
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {
        "累计观众": None, "新增粉丝": None, "评论人数": None,
        "送礼人数": None, "获得音浪": None,
    }
    assert payload["metrics_captured_at"] is None
    assert payload["stop_signal_received"] is True
    assert payload["start_signal_source"] == payload["stop_signal_source"] == "android_app"
    assert set(payload["missing_fields"]) == set(payload["metrics"])
    assert all(reason == "edge_launch_unconfirmed"
               for reason in payload["missing_fields"].values())


def test_start_needs_input_is_stopped_after_app_stop_then_cli_failure_is_preserved():
    task = {
        "task_id": "task-failed", "session_id": "session-failed",
        "douyin_account": "account", "status": "waiting_for_stop",
        "start_signal_source": "ios_app", "stop_signal_received": False,
    }
    calls = []
    gets = 0

    def http(method, path, payload=None):
        nonlocal gets
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET":
            gets += 1
            return {"status": "ok", "task": {
                **task, "stop_signal_received": gets >= 2,
                "stop_signal_source": "ios_app" if gets >= 2 else None,
                "stopped_at_ms": 2 if gets >= 2 else None,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    start_failure = {
        "session_id": "session-failed", "douyin_account": "account",
        "status": "needs_input", "reason": "edge_launch_unconfirmed",
        "metrics": {}, "start_signal_source": "ios_app", "stop_signal_source": None,
    }
    stopped_failure = {**start_failure, "status": "failed",
                       "stop_signal_source": "ios_app"}
    cli_calls = []

    def cli(command, cli_task):
        cli_calls.append(command)
        if command == "start":
            return CliInvocation({"record": start_failure}, 2)
        assert command == "stop"
        assert cli_task["stop_signal_received"] is True
        assert cli_task["stop_signal_source"] == "ios_app"
        return CliInvocation({"record": stopped_failure}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start", "stop"]
    assert [method for method, _, _ in calls] == ["POST", "GET", "GET", "POST"]
    payload = calls[-1][2]
    assert payload["status"] == "failed"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert payload["start_signal_source"] == payload["stop_signal_source"] == "ios_app"
    assert all(value == "edge_launch_unconfirmed"
               for value in payload["missing_fields"].values())


def test_start_failed_record_is_closed_idempotently_by_cli_stop_after_real_app_stop():
    task = {"task_id": "task-already-failed", "session_id": "session-already-failed",
            "douyin_account": "account", "status": "waiting_for_stop",
            "start_signal_source": "android_app", "stop_signal_received": True,
            "stop_signal_source": "android_app", "stopped_at_ms": 2}
    calls = []
    cli_calls = []
    failure = {"session_id": task["session_id"], "douyin_account": "account",
               "status": "failed", "reason": "edge_launch_unconfirmed",
               "metrics": {}, "start_signal_source": "android_app",
               "stop_signal_source": None}

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, cli_task):
        cli_calls.append(command)
        if command == "start":
            return CliInvocation({"record": failure}, 2)
        assert command == "stop"
        assert cli_task["stop_signal_received"] is True
        return CliInvocation({"record": {**failure, "stop_signal_source": "android_app"}}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start", "stop"]
    payload = calls[-1][2]
    assert payload["status"] == "failed"
    assert payload["start_signal_source"] == payload["stop_signal_source"] == "android_app"
    assert all(value == "edge_launch_unconfirmed"
               for value in payload["missing_fields"].values())


def test_bound_needs_input_is_not_stopped_or_recaptured_after_app_stop():
    task = {"task_id": "task-bound-uncertain", "session_id": "session-bound-uncertain",
            "douyin_account": "account", "status": "waiting_for_stop",
            "start_signal_source": "ios_app", "stop_signal_received": True,
            "stop_signal_source": "ios_app", "stopped_at_ms": 2}
    calls = []
    cli_calls = []
    failure = {"session_id": task["session_id"], "douyin_account": "account",
               "status": "needs_input", "reason": "mcp_timeout_outcome_unknown",
               "metrics": None, "raw_displays": None, "approximate_metrics": None,
               "missing_reasons": {"累计观众": "outcome_unknown"},
               "start_signal_source": "ios_app", "stop_signal_source": None,
               "binding_evidence": binding_evidence("account")}

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, _task):
        cli_calls.append(command)
        return CliInvocation({"record": failure}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert payload["metrics_captured_at"] is None
    assert payload["missing_fields"]["累计观众"] == "outcome_unknown"
    assert all(value == "mcp_timeout_outcome_unknown"
               for key, value in payload["missing_fields"].items()
               if key != "累计观众")


def test_waiting_for_stop_without_complete_binding_evidence_is_not_a_success():
    task = {"task_id": "task-no-binding", "session_id": "session-no-binding",
            "douyin_account": "account", "status": "waiting_for_stop",
            "start_signal_source": "ios_app", "stop_signal_received": False}
    calls = []
    cli_calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET":
            return {"status": "ok", "task": {
                **task, "status": "capturing", "stop_signal_received": True,
                "stop_signal_source": "ios_app", "stopped_at_ms": 3,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, _task):
        cli_calls.append(command)
        return {"record": {
            "session_id": task["session_id"], "douyin_account": "account",
            "status": "waiting_for_stop", "start_signal_source": "ios_app",
            "binding_evidence": {"room_id": "room-only"},
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert all(value == "cli_binding_evidence_missing"
               for value in payload["missing_fields"].values())


def test_nonzero_start_with_active_record_cannot_enter_success_path():
    task = {
        "task_id": "task-contradiction", "session_id": "session-contradiction",
        "douyin_account": "account", "status": "waiting_for_stop",
        "start_signal_source": "ios_app", "stop_signal_received": False,
    }
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET":
            return {"status": "ok", "task": {
                **task, "stop_signal_received": True,
                "stop_signal_source": "ios_app", "stopped_at_ms": 3,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    active = {"session_id": "session-contradiction", "douyin_account": "account",
              "status": "waiting_for_stop", "start_signal_source": "ios_app",
              "binding_evidence": binding_evidence("account")}
    cli_calls = []

    def cli(command, _task):
        cli_calls.append(command)
        return CliInvocation({"record": active}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert all(value == "cli_exit_status_mismatch"
               for value in payload["missing_fields"].values())


def test_nonzero_stop_preserves_same_task_failure_record_without_result_call():
    task = {"task_id": "stop-failed", "session_id": "session-stop-failed",
            "douyin_account": "account", "status": "capturing",
            "start_signal_source": "android_app", "stop_signal_source": "android_app",
            "stop_signal_received": True, "stopped_at_ms": 9}
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    failure_record = {
        "session_id": "session-stop-failed", "douyin_account": "account",
        "status": "needs_input", "reason": "signal_source_conflict",
        "metrics": {}, "start_signal_source": "android_app",
        "stop_signal_source": "android_app",
    }
    cli_calls = []

    def cli(command, claimed_task):
        cli_calls.append(command)
        if command == "start":
            return {"record": {
                "session_id": claimed_task["session_id"],
                "douyin_account": claimed_task["douyin_account"],
                "status": "waiting_for_stop",
                "start_signal_source": "android_app",
                "binding_evidence": binding_evidence(claimed_task["douyin_account"]),
            }}
        return CliInvocation({"record": failure_record}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start", "stop"]
    assert [method for method, _, _ in calls] == ["POST", "POST"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["start_signal_source"] == payload["stop_signal_source"] == "android_app"
    assert all(value == "signal_source_conflict"
               for value in payload["missing_fields"].values())


def test_bridge_rejects_unbound_failure_with_wrong_stop_source():
    task = {
        "task_id": "task-unbound", "session_id": "session-unbound",
        "douyin_account": "test-account", "status": "waiting_for_stop",
        "start_signal_source": "android_app", "stop_signal_source": None,
        "stop_signal_received": False,
    }
    http_calls = []

    def http(method, path, payload=None):
        http_calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/task-unbound"):
            return {"status": "ok", "task": {
                **task, "status": "capturing", "stop_signal_received": True,
                "stop_signal_source": "ios_app", "stopped_at_ms": 1,
            }}
        raise AssertionError((method, path))

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http,
        cli=lambda command, _: {"status": "needs_input", "reason": "cli_result_unavailable"},
        sleep=lambda _: None,
    )

    with pytest.raises(BackstageFatalError, match="task source"):
        bridge.run_once()

    assert [call[0] for call in http_calls] == ["POST", "GET"]


def test_bridge_discards_wrong_start_record_and_writes_task_scoped_null_after_stop():
    task = {
        "task_id": "task-bound", "session_id": "session-bound",
        "douyin_account": "test-account", "status": "waiting_for_stop",
        "start_signal_source": "android_app", "stop_signal_source": None,
        "stop_signal_received": False,
    }
    bad_start = {"record": {
        "status": "waiting_for_stop", "session_id": "other-session",
        "douyin_account": "test-account", "start_signal_source": "ios_app",
    }}

    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET":
            return {"status": "ok", "task": {
                **task, "status": "capturing", "stop_signal_received": True,
                "stop_signal_source": "android_app", "stopped_at_ms": 1,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http,
        cli=lambda command, _: bad_start,
        sleep=lambda _: None,
    )

    assert bridge.run_once() is True
    assert [method for method, _, _ in calls] == ["POST", "GET", "POST"]
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert payload["start_signal_source"] == payload["stop_signal_source"] == "android_app"
    assert "identity" in payload["missing_fields"]["累计观众"]


def test_bridge_preserves_unknown_capture_time_for_empty_result():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "stop_signal_received": True, "start_signal_source": "ios_app",
            "stop_signal_source": "ios_app"}
    record = {"status": "needs_input", "session_id": "s", "douyin_account": "a",
              "metrics": {}, "missing_fields": ["累计观众"],
              "start_signal_source": "ios_app", "stop_signal_source": "ios_app"}
    payload = BackstageBridge.result_payload(task, record)
    assert payload["metrics_captured_at"] is None
    assert payload["metrics"]["累计观众"] is None
    assert payload["missing_fields"]["累计观众"] == "未读取"


def test_bridge_forwards_android_app_signal_source_without_ios_relabeling():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "start_signal_source": "android_app", "stop_signal_source": "android_app",
            "stop_signal_received": True}
    bridge = BackstageBridge(BridgeConfig("http://localhost", "worker", "python", "cli.py"))
    assert "--source=android_app" in bridge._cli_args("start", task)
    assert "--source=android_app" in bridge._cli_args("stop", task)
    payload = bridge.result_payload(task, {
        "status": "needs_input", "session_id": "s", "douyin_account": "a",
        "metrics": {},
        "start_signal_source": "android_app", "stop_signal_source": "android_app",
    })
    assert payload["start_signal_source"] == "android_app"
    assert payload["stop_signal_source"] == "android_app"


def test_android_app_task_uses_existing_bridge_and_posts_to_same_session():
    task = {
        "task_id": "android-task", "session_id": "android-session",
        "douyin_account": "android-account", "status": "waiting_for_stop",
        "start_signal_source": "android_app", "stop_signal_source": None,
        "stop_signal_received": False, "stopped_at_ms": None,
    }
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/android-task"):
            return {"status": "ok", "task": {
                **task, "status": "capturing", "stop_signal_received": True,
                "stop_signal_source": "android_app", "stopped_at_ms": 100,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    cli_sources = []

    def cli(command, task_snapshot):
        cli_sources.append((command, task_snapshot.get("start_signal_source"),
                            task_snapshot.get("stop_signal_source")))
        if command == "result":
            return {"record": {"session_id": "android-session",
                               "douyin_account": "android-account",
                               "status": "needs_input", "metrics": {},
                               "start_signal_source": "android_app",
                               "stop_signal_source": "android_app"}}
        if command == "start":
            return {"record": {
                "status": "waiting_for_stop", "session_id": task_snapshot["session_id"],
                "douyin_account": task_snapshot["douyin_account"],
                "start_signal_source": task_snapshot["start_signal_source"],
                "binding_evidence": binding_evidence(task_snapshot["douyin_account"]),
            }}
        return {"record": {
            "status": "waiting_for_stop", "session_id": task_snapshot["session_id"],
            "douyin_account": task_snapshot["douyin_account"],
            "start_signal_source": task_snapshot["start_signal_source"],
            "stop_signal_source": task_snapshot["start_signal_source"],
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://127.0.0.1:8000", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert [command for command, _, _ in cli_sources] == ["start", "stop", "result"]
    assert cli_sources[0][1] == "android_app"
    assert cli_sources[1][2] == "android_app"
    result_request = calls[-1][2]
    assert calls[-1][1] == "v1/live/sessions/android-session/backstage/result"
    assert result_request["start_signal_source"] == result_request["stop_signal_source"] == "android_app"


def test_bridge_converts_cli_result_identity_drift_to_task_null_result():
    claim = {"task_id": "t", "session_id": "s1", "douyin_account": "a",
             "status": "waiting_for_stop", "start_signal_source": "ios_app",
             "stop_signal_received": True, "stop_signal_source": "ios_app",
             "stopped_at_ms": 3}
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": claim}
        if method == "GET":
            return {"status": "ok", "task": claim}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    cli_calls = []

    def cli(command, task):
        cli_calls.append(command)
        if command == "result":
            return {"record": {"session_id": "s2", "douyin_account": "a",
                               "status": "needs_input", "metrics": {},
                               "start_signal_source": "ios_app",
                               "stop_signal_source": "ios_app"}}
        if command == "start":
            return {"record": {
                "status": "waiting_for_stop", "session_id": task["session_id"],
                "douyin_account": task["douyin_account"],
                "start_signal_source": task["start_signal_source"],
                "binding_evidence": binding_evidence(task["douyin_account"]),
            }}
        return {"record": {
            "status": "waiting_for_stop", "session_id": task["session_id"],
            "douyin_account": task["douyin_account"],
            "start_signal_source": task["start_signal_source"],
            "stop_signal_source": task["start_signal_source"],
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "secret", "python", "cli.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == ["start", "stop", "result"]
    posted = next(payload for _, path, payload in calls
                  if path.endswith("/backstage/result"))
    assert posted["status"] == "needs_input"
    assert posted["metrics"] == {key: None for key in posted["metrics"]}
    assert "identity" in posted["missing_fields"]["累计观众"]


def test_bridge_discards_simulated_record_for_real_app_task_and_posts_null():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "status": "waiting_for_stop", "start_signal_source": "android_app",
            "stop_signal_source": "android_app", "stop_signal_received": True,
            "stopped_at_ms": 100}
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "GET":
            return {"status": "ok", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, claimed_task):
        if command == "result":
            return {"record": {
                "session_id": "s", "douyin_account": "a", "status": "partial",
                "metrics": {}, "start_signal_source": "test_simulated",
                "stop_signal_source": "test_simulated",
            }}
        if command == "start":
            return {"record": {
                "status": "waiting_for_stop", "session_id": claimed_task["session_id"],
                "douyin_account": claimed_task["douyin_account"],
                "start_signal_source": claimed_task["start_signal_source"],
                "binding_evidence": binding_evidence(claimed_task["douyin_account"]),
            }}
        return {"record": {
            "session_id": claimed_task["session_id"],
            "douyin_account": claimed_task["douyin_account"],
            "status": "waiting_for_stop",
            "start_signal_source": claimed_task["start_signal_source"],
            "stop_signal_source": claimed_task["start_signal_source"],
        }}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    posted = next(payload for _, path, payload in calls
                  if path.endswith("/backstage/result"))
    assert posted["status"] == "needs_input"
    assert posted["start_signal_source"] == posted["stop_signal_source"] == "android_app"
    assert "source" in posted["missing_fields"]["累计观众"]


def test_http_200_with_wrong_result_ack_is_service_protocol_failure():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "status": "capturing", "start_signal_source": "ios_app",
            "stop_signal_source": "ios_app", "stop_signal_received": True,
            "stopped_at_ms": 5}

    def http(method, path, payload=None):
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return {"status": "accepted", "task_id": "other",
                    "result_status": payload["status"]}
        raise AssertionError((method, path))

    def cli(command, _task):
        if command == "start":
            return {"record": {"session_id": "s", "douyin_account": "a",
                                "status": "waiting_for_stop",
                                "start_signal_source": "ios_app",
                                "binding_evidence": binding_evidence("a")}}
        record = {"session_id": "s", "douyin_account": "a",
                  "status": "needs_input", "metrics": {},
                  "start_signal_source": "ios_app", "stop_signal_source": "ios_app"}
        return {"record": record}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    with pytest.raises(BackstageFatalError, match="result acknowledgement"):
        bridge.run_once()


def test_read_only_result_must_exit_zero_even_when_record_says_failed():
    task = {"task_id": "result-exit", "session_id": "session-result-exit",
            "douyin_account": "account", "status": "capturing",
            "start_signal_source": "ios_app", "stop_signal_source": "ios_app",
            "stop_signal_received": True, "stopped_at_ms": 4}
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": task}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    def cli(command, _task):
        if command == "start":
            return {"record": {
                "session_id": task["session_id"], "douyin_account": "account",
                "status": "waiting_for_stop", "start_signal_source": "ios_app",
                "binding_evidence": binding_evidence(task["douyin_account"]),
            }}
        if command == "stop":
            return {"record": {
                "session_id": task["session_id"], "douyin_account": "account",
                "status": "partial", "metrics": {},
                "start_signal_source": "ios_app", "stop_signal_source": "ios_app",
            }}
        return CliInvocation({"record": {
            "session_id": task["session_id"], "douyin_account": "account",
            "status": "failed", "reason": "stored_failure", "metrics": {},
            "start_signal_source": "ios_app", "stop_signal_source": "ios_app",
        }}, 2)

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    payload = calls[-1][2]
    assert payload["status"] == "needs_input"
    assert payload["metrics"] == {key: None for key in payload["metrics"]}
    assert all(value == "cli_exit_status_mismatch"
               for value in payload["missing_fields"].values())


def test_worker_auth_http_error_is_fatal_not_a_poll_retry(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise HTTPError("http://localhost/claim", 403, "forbidden", {}, None)

    monkeypatch.setattr("screen_assistant_server.backstage_bridge.urlopen", forbidden)
    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
    )
    with pytest.raises(BackstageFatalError, match="403"):
        bridge.http("POST", "v1/live/sessions/backstage/tasks/claim")


def test_service_protocol_error_exits_bridge_without_poll_retry():
    def invalid_response(*_args, **_kwargs):
        raise BackstageFatalError("backstage API response is invalid")

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=invalid_response,
        sleep=lambda _seconds: pytest.fail("fatal service error must not be retried"),
    )
    with pytest.raises(SystemExit) as exc:
        bridge.serve_forever()
    assert exc.value.code == 2


def test_task_failure_does_not_stop_loop_from_claiming_next_task():
    tasks = [
        {"task_id": "t1", "session_id": "s1", "douyin_account": "a1",
         "status": "waiting_for_stop", "start_signal_source": "android_app",
         "stop_signal_received": False},
        {"task_id": "t2", "session_id": "s2", "douyin_account": "a2",
         "status": "capturing", "start_signal_source": "ios_app",
         "stop_signal_source": "ios_app", "stop_signal_received": True,
         "stopped_at_ms": 6},
    ]
    claim_count = 0
    calls = []

    def http(method, path, payload=None):
        nonlocal claim_count
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            if claim_count == len(tasks):
                return {"status": "idle", "task": None}
            task = tasks[claim_count]
            claim_count += 1
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/t1"):
            return {"status": "ok", "task": {
                **tasks[0], "stop_signal_received": True,
                "stop_signal_source": "android_app", "stopped_at_ms": 7,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return accepted(payload)
        raise AssertionError((method, path))

    cli_calls = []

    def cli(command, task):
        cli_calls.append((command, task["task_id"]))
        if task["task_id"] == "t1":
            return CliInvocation({"status": "needs_input", "reason": "busy"}, 2)
        if command == "start":
            return {"record": {"session_id": "s2", "douyin_account": "a2",
                                "status": "waiting_for_stop",
                                "start_signal_source": "ios_app",
                                "binding_evidence": binding_evidence("a2")}}
        if command == "stop":
            return {"record": {"session_id": "s2", "douyin_account": "a2",
                                "status": "waiting_for_stop",
                                "start_signal_source": "ios_app",
                                "stop_signal_source": "ios_app"}}
        return {"record": {"session_id": "s2", "douyin_account": "a2",
                            "status": "needs_input", "metrics": {},
                            "start_signal_source": "ios_app",
                            "stop_signal_source": "ios_app"}}

    class StopLoop(Exception):
        pass

    def sleep(_seconds):
        raise StopLoop

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=sleep,
    )
    with pytest.raises(StopLoop):
        bridge.serve_forever()
    assert claim_count == 2
    assert cli_calls == [("start", "t1"), ("start", "t2"),
                         ("stop", "t2"), ("result", "t2")]
    posted = [payload for method, path, payload in calls
              if method == "POST" and path.endswith("/backstage/result")]
    assert posted[0]["missing_fields"]["累计观众"] == "busy"
    assert len(posted) == 2


def test_cli_process_uses_argument_list_and_never_receives_worker_token(monkeypatch):
    config = BridgeConfig("http://localhost", "worker-secret", "python.exe", "recap.py")
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a;& arbitrary"}
    calls = []

    class FakeProcess:
        pid = 1234
        returncode = 2

        def communicate(self, timeout=None):
            assert timeout == 480.0
            return '{"record":{"status":"needs_input"}}', ""

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setenv("BACKSTAGE_WORKER_TOKEN", "worker-secret")
    monkeypatch.setenv("AO_SESSION_ID", "ao-session-test")
    monkeypatch.setattr("screen_assistant_server.backstage_bridge.subprocess.Popen", popen)
    bridge = BackstageBridge(config)
    bridge.cli = bridge._cli
    result = bridge.cli("start", task)
    assert isinstance(result, CliInvocation)
    assert result.returncode == 2
    assert result.output["record"]["status"] == "needs_input"

    args, kwargs = calls[0]
    assert "--source=ios_app" in args
    assert "--desktop-authorized" in args
    assert "--account=a;& arbitrary" in args
    assert kwargs.get("shell", False) is False
    assert "BACKSTAGE_WORKER_TOKEN" not in kwargs["env"]
    assert "AO_SESSION_ID" not in kwargs["env"]
    assert "worker-secret" not in args and "worker-secret" not in kwargs["env"].values()


def test_cli_timeout_terminates_its_process_tree_and_returns_needs_input(monkeypatch):
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "start_signal_source": "ios_app", "stop_signal_source": "ios_app"}
    cleanup = []

    class TimedOutProcess:
        pid = 2345

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired("recap_glm.py", timeout)

    process = TimedOutProcess()
    monkeypatch.setattr(
        "screen_assistant_server.backstage_bridge.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        BackstageBridge, "_terminate_process_tree",
        staticmethod(lambda current: cleanup.append(current)),
    )
    bridge = BackstageBridge(BridgeConfig("http://localhost", "worker", "python", "recap.py"))
    result = bridge._cli("start", task)
    assert result == CliInvocation(None, None, "cli_timeout")
    assert cleanup == [process]


def test_bridge_cli_timeout_config_is_bounded(monkeypatch):
    monkeypatch.setenv("BACKSTAGE_SERVER_URL", "http://localhost")
    monkeypatch.setenv("BACKSTAGE_WORKER_TOKEN", "worker")
    monkeypatch.setenv("BACKSTAGE_CLI_PYTHON", "python")
    monkeypatch.setenv("BACKSTAGE_CLI_ENTRY", "recap.py")
    monkeypatch.setenv("BACKSTAGE_CLI_TIMEOUT_SECONDS", "540")
    assert BridgeConfig.from_env().cli_timeout == 540
    monkeypatch.setenv("BACKSTAGE_CLI_TIMEOUT_SECONDS", "601")
    with pytest.raises(ValueError, match="between 60 and 600"):
        BridgeConfig.from_env()
