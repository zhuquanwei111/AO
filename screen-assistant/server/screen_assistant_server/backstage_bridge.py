"""将单个 App 后台任务接到既有 Windows 取数 CLI。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


METRIC_KEYS = ("累计观众", "新增粉丝", "评论人数", "送礼人数", "获得音浪")


class BackstageFatalError(RuntimeError):
    """服务鉴权或API协议错误，必须停止桥进程等待运维处理。"""


class BackstageTransientError(RuntimeError):
    """暂时性网络或服务故障，可沿用轮询间隔重试。"""


class BackstageCliRecordError(ValueError):
    """CLI record不属于当前场次或不符合当前回写契约。"""


@dataclass(frozen=True)
class CliInvocation:
    output: dict | None
    returncode: int | None
    failure_reason: str | None = None


@dataclass(frozen=True)
class BridgeConfig:
    server_url: str
    worker_token: str
    cli_python: str
    cli_entry: str
    data_dir: str | None = None
    models_dir: str | None = None
    scratch_dir: str | None = None
    gate_a: str | None = None
    poll_seconds: float = 2.0
    request_timeout: float = 15.0
    cli_timeout: float = 480.0

    def __post_init__(self):
        if not 60.0 <= self.cli_timeout <= 600.0:
            raise ValueError("cli timeout must be between 60 and 600 seconds")

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise ValueError(f"missing required setting: {name}")
            return value

        def optional(name: str) -> str | None:
            return os.environ.get(name, "").strip() or None

        cli_timeout = float(os.environ.get("BACKSTAGE_CLI_TIMEOUT_SECONDS", "480"))
        if not 60.0 <= cli_timeout <= 600.0:
            raise ValueError("BACKSTAGE_CLI_TIMEOUT_SECONDS must be between 60 and 600")

        return cls(
            server_url=required("BACKSTAGE_SERVER_URL").rstrip("/"),
            worker_token=required("BACKSTAGE_WORKER_TOKEN"),
            cli_python=required("BACKSTAGE_CLI_PYTHON"),
            cli_entry=required("BACKSTAGE_CLI_ENTRY"),
            data_dir=optional("BACKSTAGE_CLI_DATA"),
            models_dir=optional("BACKSTAGE_CLI_MODELS"),
            scratch_dir=optional("BACKSTAGE_CLI_SCRATCH"),
            gate_a=optional("BACKSTAGE_CLI_GATE_A"),
            poll_seconds=max(0.5, float(os.environ.get("BACKSTAGE_POLL_SECONDS", "2"))),
            cli_timeout=cli_timeout,
        )


class BackstageBridge:
    """单实例轮询器；HTTP 与 CLI 调用点可替换为无桌面的受控测试桩。"""

    def __init__(self, config: BridgeConfig, *,
                 http: Callable | None = None,
                 cli: Callable | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.http = http or self._http
        self.cli = cli or self._cli
        self.sleep = sleep

    def _http(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.config.server_url + "/" + path.lstrip("/")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(url, data=body, method=method, headers={
            "X-Backstage-Worker-Token": self.config.worker_token,
            "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=self.config.request_timeout) as response:
                try:
                    payload = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise BackstageFatalError("backstage API returned invalid JSON") from None
                if not isinstance(payload, dict):
                    raise BackstageFatalError("backstage API response must be an object")
                return payload
        except HTTPError as exc:
            if exc.code in {408, 429, 502, 504}:
                raise BackstageTransientError(f"backstage HTTP temporary status {exc.code}") from None
            raise BackstageFatalError(f"backstage HTTP rejected request: {exc.code}") from None
        except (URLError, TimeoutError):
            raise BackstageTransientError("backstage HTTP request temporarily unavailable") from None

    def _cli_args(self, command: str, task: dict) -> list[str]:
        args = [self.config.cli_python, self.config.cli_entry, command,
                f"--task={task['task_id']}"]
        start_source = task.get("start_signal_source") or "ios_app"
        stop_source = task.get("stop_signal_source") or start_source
        if (start_source not in {"ios_app", "android_app"}
                or stop_source not in {"ios_app", "android_app"}
                or stop_source != start_source):
            raise ValueError("backstage app signal source mismatch")
        if command == "start":
            args.extend((f"--account={task['douyin_account']}",
                         f"--session-id={task['session_id']}",
                         f"--source={start_source}", "--desktop-authorized"))
        elif command == "stop":
            args.extend((f"--source={stop_source}", "--desktop-authorized"))
        for flag, value in (("--data", self.config.data_dir),
                            ("--models", self.config.models_dir),
                            ("--scratch", self.config.scratch_dir),
                            ("--gate-a", self.config.gate_a)):
            if value:
                args.extend((flag, value))
        return args

    def _cli(self, command: str, task: dict) -> CliInvocation:
        entry = Path(self.config.cli_entry)
        child_env = {key: value for key, value in os.environ.items()
                     if key != "BACKSTAGE_WORKER_TOKEN" and not key.startswith("AO_")}
        try:
            process_options = {
                "cwd": str(entry.parent), "env": child_env,
                "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                "text": True, "encoding": "utf-8", "errors": "replace",
            }
            if os.name == "nt":
                process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                process_options["start_new_session"] = True
            process = subprocess.Popen(self._cli_args(command, task), **process_options)
            try:
                stdout, _ = process.communicate(timeout=self.config.cli_timeout)
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process)
                return CliInvocation(None, getattr(process, "returncode", None), "cli_timeout")
        except OSError:
            return CliInvocation(None, None, "cli_execution_unavailable")
        try:
            output = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return CliInvocation(None, process.returncode, "cli_result_unavailable")
        if not isinstance(output, dict):
            return CliInvocation(None, process.returncode, "cli_result_unavailable")
        return CliInvocation(output, process.returncode)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        """Stop only this timed-out CLI process and its children, then reap it boundedly."""
        if process.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=5.0, check=False,
                    )
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except (OSError, subprocess.SubprocessError):
                pass
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
        try:
            process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                pass

    @staticmethod
    def result_payload(task: dict, result: dict, *, allow_missing_stop_source: bool = False) -> dict:
        record = result.get("record") if isinstance(result, dict) else None
        if not isinstance(record, dict) and isinstance(result, dict):
            record = result
        if not isinstance(record, dict):
            raise BackstageCliRecordError("cli_record_missing")
        if (record.get("session_id") != task.get("session_id")
                or record.get("douyin_account") != task.get("douyin_account")):
            raise BackstageCliRecordError("cli_record_identity_mismatch")

        start_source = task.get("start_signal_source")
        stop_source = task.get("stop_signal_source")
        if (task.get("stop_signal_received") is not True
                or start_source not in {"ios_app", "android_app"}
                or stop_source != start_source):
            raise BackstageFatalError("backstage task stop/source contract mismatch")
        if record.get("start_signal_source") != start_source:
            raise BackstageCliRecordError("cli_record_source_mismatch")
        record_stop_source = record.get("stop_signal_source")
        if (record_stop_source != stop_source
                and not (allow_missing_stop_source and record_stop_source is None)):
            raise BackstageCliRecordError("cli_record_source_mismatch")

        status = record.get("status")
        if status not in {"captured_locally", "partial", "needs_input", "failed"}:
            raise BackstageCliRecordError("cli_record_status_invalid")
        metrics_in = record.get("metrics")
        if metrics_in is None:
            metrics_in = {}
        elif not isinstance(metrics_in, dict):
            raise BackstageCliRecordError("cli_metrics_invalid")
        metrics = {key: metrics_in.get(key) for key in METRIC_KEYS}
        if any(value is not None and
               (type(value) is not int or value < 0 or value > 2**53)
               for value in metrics.values()):
            raise BackstageCliRecordError("cli_metrics_invalid")
        if status == "captured_locally" and any(value is None for value in metrics.values()):
            status = "partial"

        missing_in = record.get("missing_reasons", record.get("missing_fields", {}))
        if isinstance(missing_in, list):
            missing = {key: "未读取" for key in missing_in if key in METRIC_KEYS}
        elif isinstance(missing_in, dict):
            missing = {key: str(value)[:160] for key, value in missing_in.items()
                       if key in METRIC_KEYS}
        else:
            missing = {}
        record_reason = record.get("reason")
        if isinstance(record_reason, str) and record_reason.strip():
            for key, value in metrics.items():
                if value is None:
                    if key not in missing or missing[key] == "未读取":
                        missing[key] = record_reason.strip()[:160]
        for key, value in metrics.items():
            if value is None:
                missing.setdefault(key, "执行器未提供该项")

        raw_in = record.get("raw_displays", {})
        approximate_in = record.get("approximate_metrics", [])
        if raw_in is None:
            raw_in = {}
        if approximate_in is None:
            approximate_in = []
        if not isinstance(raw_in, dict) or not isinstance(approximate_in, list):
            raise BackstageCliRecordError("cli_metric_metadata_invalid")
        raw_displays = {key: str(value)[:160] for key, value in raw_in.items()
                        if key in METRIC_KEYS and isinstance(value, (str, int, float))}
        approximate = [key for key in approximate_in if key in METRIC_KEYS]
        if any(not isinstance(key, str) for key in approximate_in):
            raise BackstageCliRecordError("cli_metric_metadata_invalid")

        captured_at = record.get("metrics_captured_at")
        if captured_at is not None and (
                not isinstance(captured_at, str) or len(captured_at) > 64):
            raise BackstageCliRecordError("cli_metric_timestamp_invalid")
        if any(value is not None for value in metrics.values()):
            if not captured_at:
                raise BackstageCliRecordError("cli_metric_timestamp_missing")
            try:
                from datetime import datetime
                parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            except ValueError:
                raise BackstageCliRecordError("cli_metric_timestamp_invalid") from None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise BackstageCliRecordError("cli_metric_timestamp_invalid")

        capture_method = record.get("capture_method") or "现有后台取数 CLI"
        value_semantics = record.get("value_semantics") or "下播信号后读取的本场后台左侧汇总，不代表平台最终结算"
        if not isinstance(capture_method, str) or not isinstance(value_semantics, str):
            raise BackstageCliRecordError("cli_record_text_invalid")
        if (status in {"needs_input", "failed"}
                and isinstance(record_reason, str) and record_reason.strip()
                and any(value is not None for value in metrics.values())):
            value_semantics = f"CLI失败原因：{record_reason.strip()[:160]}；{value_semantics}"
        room_id = record.get("room_id")
        if room_id is not None and (not isinstance(room_id, str) or len(room_id) > 128):
            raise BackstageCliRecordError("cli_record_text_invalid")
        return {
            "task_id": task["task_id"], "douyin_account": task["douyin_account"],
            "status": status, "room_id": room_id, "metrics": metrics,
            "raw_displays": raw_displays, "approximate_metrics": approximate,
            "metrics_captured_at": captured_at, "stop_signal_received": True,
            "start_signal_source": start_source, "stop_signal_source": stop_source,
            "capture_method": capture_method[:160],
            "value_semantics": value_semantics[:200], "missing_fields": missing,
        }

    def _task(self, task_id: str) -> dict:
        response = self.http("GET", f"v1/live/sessions/backstage/tasks/{task_id}")
        if not isinstance(response, dict) or response.get("status") != "ok":
            raise BackstageFatalError("backstage task response is invalid")
        task = response.get("task")
        if not isinstance(task, dict) or task.get("task_id") != task_id:
            raise BackstageFatalError("backstage task identity mismatch")
        return task

    @staticmethod
    def _invocation(value) -> CliInvocation:
        if isinstance(value, CliInvocation):
            return value
        if isinstance(value, dict):
            # Injected test/adapter callables use the established dict shape;
            # the real subprocess path always supplies its actual exit code.
            return CliInvocation(value, 0)
        return CliInvocation(None, None, "cli_result_unavailable")

    @staticmethod
    def _record(invocation: CliInvocation) -> dict | None:
        if not isinstance(invocation.output, dict):
            return None
        record = invocation.output.get("record")
        return record if isinstance(record, dict) else None

    @staticmethod
    def _record_problem(task: dict, record: dict | None, *, allow_missing_stop_source: bool) -> str | None:
        if record is None:
            return "cli_record_missing"
        if (record.get("session_id") != task.get("session_id")
                or record.get("douyin_account") != task.get("douyin_account")):
            return "cli_record_identity_mismatch"
        expected_source = task.get("start_signal_source")
        if record.get("start_signal_source") != expected_source:
            return "cli_record_source_mismatch"
        record_stop_source = record.get("stop_signal_source")
        if (record_stop_source != expected_source
                and not (allow_missing_stop_source and record_stop_source is None)):
            return "cli_record_source_mismatch"
        return None

    @staticmethod
    def _has_binding_candidate(record: dict) -> bool:
        """A present binding object is unresolved state even if malformed; do not rerun capture."""
        return bool(record.get("binding_evidence"))

    @staticmethod
    def _binding_evidence_problem(record: dict) -> str | None:
        evidence = record.get("binding_evidence")
        if not isinstance(evidence, dict):
            return "cli_binding_evidence_missing"
        if not all(isinstance(evidence.get(key), str) and evidence[key].strip()
                   for key in ("room_id", "observed_at", "locator")):
            return "cli_binding_evidence_missing"
        if evidence.get("douyin_account") != record.get("douyin_account"):
            return "cli_binding_account_mismatch"
        return None

    @staticmethod
    def _reason(invocation: CliInvocation, default: str) -> str:
        output = invocation.output if isinstance(invocation.output, dict) else {}
        record = output.get("record") if isinstance(output.get("record"), dict) else {}
        for value in (record.get("reason"), output.get("reason"), invocation.failure_reason):
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
        return default[:160]

    @classmethod
    def _payload_record(cls, invocation: CliInvocation) -> dict | None:
        record = cls._record(invocation)
        if record is None:
            return None
        result = dict(record)
        output = invocation.output or {}
        if not result.get("reason") and isinstance(output.get("reason"), str):
            result["reason"] = output["reason"]
        return result

    @classmethod
    def _null_failure_payload(cls, task: dict, reason: str) -> dict:
        source = task.get("start_signal_source")
        if (task.get("stop_signal_received") is not True
                or source not in {"ios_app", "android_app"}
                or task.get("stop_signal_source") != source):
            raise BackstageFatalError("backstage task stop/source contract mismatch")
        reason = (reason or "CLI未返回可验证结果").strip()[:160]
        return {
            "task_id": task["task_id"], "douyin_account": task["douyin_account"],
            "status": "needs_input", "room_id": None,
            "metrics": {key: None for key in METRIC_KEYS}, "raw_displays": {},
            "approximate_metrics": [], "metrics_captured_at": None,
            "stop_signal_received": True,
            "start_signal_source": source, "stop_signal_source": source,
            "capture_method": "后台取数 CLI 未取得可核验结果",
            "value_semantics": "本场后台数据未知；" + reason[:160],
            "missing_fields": {key: reason for key in METRIC_KEYS},
        }

    def _post_result(self, task: dict, payload: dict) -> None:
        response = self.http(
            "POST", f"v1/live/sessions/{task['session_id']}/backstage/result", payload,
        )
        if not isinstance(response, dict):
            raise BackstageFatalError("backstage result acknowledgement is invalid")
        if (response.get("status") not in {"accepted", "duplicate"}
                or response.get("task_id") != task["task_id"]
                or response.get("result_status") != payload["status"]):
            raise BackstageFatalError("backstage result acknowledgement mismatch")

    def _write_cli_failure(self, task: dict, identity: tuple[str, str], reason: str) -> None:
        task = self._wait_for_app_stop(task, identity)
        self._post_result(task, self._null_failure_payload(task, reason))

    def _wait_for_app_stop(self, task: dict, identity: tuple[str, str]) -> dict:
        """等待原 App 场次 stop；校验身份/source 后返回终态 task。"""
        current = task
        while current.get("stop_signal_received") is not True:
            current = self._task(task["task_id"])
            if (current.get("session_id"), current.get("douyin_account")) != identity:
                raise BackstageFatalError("backstage task identity mismatch")
            if current.get("start_signal_source") != task.get("start_signal_source"):
                raise BackstageFatalError("backstage task source changed")
            if current.get("stop_signal_received") is not True:
                self.sleep(self.config.poll_seconds)

        expected_source = task.get("start_signal_source")
        if current.get("stop_signal_source") != expected_source:
            raise BackstageFatalError("backstage task source mismatch")
        return current

    def run_once(self) -> bool:
        claimed = self.http("POST", "v1/live/sessions/backstage/tasks/claim")
        if not isinstance(claimed, dict):
            raise BackstageFatalError("backstage claim response is invalid")
        task = claimed.get("task")
        if claimed.get("status") == "idle" and task is None:
            return False
        if (claimed.get("status") != "claimed" or not isinstance(task, dict)
                or not all(isinstance(task.get(key), str) and task[key]
                           for key in ("task_id", "session_id", "douyin_account"))
                or task.get("start_signal_source") not in {"ios_app", "android_app"}):
            raise BackstageFatalError("invalid claimed backstage task")
        if task.get("stop_signal_received") is True and (
                task.get("stop_signal_source") != task.get("start_signal_source")):
            raise BackstageFatalError("claimed backstage task source mismatch")

        identity = (task["session_id"], task["douyin_account"])
        start = self._invocation(self.cli("start", task))
        start_record = self._record(start)
        start_problem = self._record_problem(
            task, start_record, allow_missing_stop_source=True,
        )
        if start_problem and start_record is not None:
            self._write_cli_failure(task, identity, start_problem)
            return True

        start_status = start_record.get("status") if start_record else None
        if start_record and start_status in {"needs_input", "failed"}:
            task = self._wait_for_app_stop(task, identity)
            start_reason = self._reason(start, "cli_start_failed")
            if start_status == "needs_input" and self._has_binding_candidate(start_record):
                # A bound but unresolved record may cause CLI stop to re-enter capture.
                # Keep it isolated and report unknown values after the real App stop.
                if start.returncode != 2:
                    payload = self._null_failure_payload(
                        task, f"cli_start_exit_status_mismatch: {start_reason}",
                    )
                else:
                    try:
                        payload = self.result_payload(
                            task, {"record": self._payload_record(start)},
                            allow_missing_stop_source=True,
                        )
                    except BackstageCliRecordError as exc:
                        payload = self._null_failure_payload(task, str(exc))
                self._post_result(task, payload)
                return True

            # For an unbound needs_input or terminal failed record, persist the
            # real App stop through the CLI. The CLI's stop path closes the local
            # record without starting another desktop capture for an unbound task.
            stop = self._invocation(self.cli("stop", task))
            stop_record = self._record(stop)
            stop_problem = self._record_problem(
                task, stop_record, allow_missing_stop_source=False,
            )
            stop_status = stop_record.get("status") if stop_record else None
            if start.returncode != 2:
                reason = f"cli_start_exit_status_mismatch: {start_reason}"
                payload = self._null_failure_payload(task, reason)
            elif stop_problem:
                reason = (f"{stop_problem}: {self._reason(stop, start_reason)}"
                          if stop_record else self._reason(stop, start_reason))
                payload = self._null_failure_payload(task, reason)
            elif stop.returncode != 2 or stop_status not in {"needs_input", "failed"}:
                reason = f"cli_stop_finalization_mismatch: {self._reason(stop, start_reason)}"
                payload = self._null_failure_payload(task, reason)
            else:
                try:
                    payload = self.result_payload(task, {"record": self._payload_record(stop)})
                except BackstageCliRecordError as exc:
                    payload = self._null_failure_payload(task, str(exc))
            self._post_result(task, payload)
            return True

        if (start_record and start_status == "waiting_for_stop"
                and start.returncode == 0):
            binding_problem = self._binding_evidence_problem(start_record)
            if binding_problem:
                self._write_cli_failure(task, identity, binding_problem)
                return True
            task = self._wait_for_app_stop(task, identity)
        else:
            reason = self._reason(start, "cli_start_no_bound_record")
            if start_record and start_status == "waiting_for_stop":
                reason = "cli_exit_status_mismatch"
            elif start_record and start_status not in {"needs_input", "failed"}:
                reason = "cli_start_status_invalid"
            self._write_cli_failure(task, identity, reason)
            return True

        stop = self._invocation(self.cli("stop", task))
        stop_record = self._record(stop)
        stop_problem = self._record_problem(task, stop_record, allow_missing_stop_source=False)
        if stop_problem:
            reason = stop_problem if stop_record else self._reason(stop, stop_problem)
            self._post_result(task, self._null_failure_payload(task, reason))
            return True
        stop_status = stop_record.get("status")
        if stop_status in {"needs_input", "failed"}:
            if stop.returncode != 2:
                payload = self._null_failure_payload(
                    task, f"cli_stop_exit_status_mismatch: {self._reason(stop, stop_status)}",
                )
            else:
                try:
                    payload = self.result_payload(task, {"record": self._payload_record(stop)})
                except BackstageCliRecordError as exc:
                    payload = self._null_failure_payload(task, str(exc))
            self._post_result(task, payload)
            return True
        if (stop.returncode != 0 or stop_status not in
                {"waiting_for_stop", "captured_locally", "partial"}):
            reason = "cli_exit_status_mismatch" if stop_record else self._reason(
                stop, "cli_stop_no_record",
            )
            self._post_result(task, self._null_failure_payload(task, reason))
            return True

        result = self._invocation(self.cli("result", task))
        result_record = self._record(result)
        result_problem = self._record_problem(task, result_record, allow_missing_stop_source=False)
        if result_problem:
            reason = result_problem if result_record else self._reason(result, result_problem)
            self._post_result(task, self._null_failure_payload(task, reason))
            return True
        result_status = result_record.get("status")
        if (result.returncode != 0
                or result_status not in {"captured_locally", "partial", "needs_input", "failed"}):
            self._post_result(task, self._null_failure_payload(
                task, "cli_exit_status_mismatch" if result_record else
                self._reason(result, "cli_result_no_record"),
            ))
            return True
        try:
            payload = self.result_payload(task, {"record": self._payload_record(result)})
        except BackstageCliRecordError as exc:
            payload = self._null_failure_payload(task, str(exc))
        self._post_result(task, payload)
        return True

    def serve_forever(self) -> None:
        while True:
            try:
                if not self.run_once():
                    self.sleep(self.config.poll_seconds)
            except BackstageFatalError as exc:
                print(f"backstage bridge stopped: {exc}", file=sys.stderr)
                raise SystemExit(2) from None
            except BackstageTransientError as exc:
                print(f"backstage bridge paused: {type(exc).__name__}", file=sys.stderr)
                self.sleep(self.config.poll_seconds)


def main() -> None:
    BackstageBridge(BridgeConfig.from_env()).serve_forever()


if __name__ == "__main__":
    main()
