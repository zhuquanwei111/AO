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


class BackstageStartError(RuntimeError):
    """CLI start 未绑定到已领取的 App 场次，必须显式停止桥接。"""


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
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"backstage HTTP status {exc.code}") from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise RuntimeError("backstage HTTP request failed") from None

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

    def _cli(self, command: str, task: dict) -> dict:
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
                return {"status": "needs_input", "reason": "cli_timeout"}
        except OSError:
            return {"status": "needs_input", "reason": "cli_execution_unavailable"}
        try:
            output = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return {"status": "needs_input", "reason": "cli_result_unavailable"}
        if isinstance(output, dict) and isinstance(output.get("record"), dict):
            return output
        return output if isinstance(output, dict) else {
            "status": "needs_input", "reason": "cli_result_unavailable"
        }

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
    def result_payload(task: dict, result: dict) -> dict:
        record = result.get("record") if isinstance(result.get("record"), dict) else result
        if record.get("session_id") not in (None, task["session_id"]):
            raise ValueError("backstage session identity mismatch")
        if record.get("douyin_account") not in (None, task["douyin_account"]):
            raise ValueError("backstage account identity mismatch")

        start_source = task.get("start_signal_source") or "ios_app"
        stop_source = task.get("stop_signal_source") or start_source
        if (start_source not in {"ios_app", "android_app"} or stop_source != start_source):
            raise ValueError("backstage app signal source mismatch")
        if (record.get("start_signal_source") != start_source
                or record.get("stop_signal_source") != stop_source):
            raise ValueError("backstage record signal source mismatch")

        status = record.get("status")
        if status not in {"captured_locally", "partial", "needs_input", "failed"}:
            status = "needs_input"
        metrics_in = record.get("metrics")
        metrics_in = metrics_in if isinstance(metrics_in, dict) else {}
        metrics = {key: metrics_in.get(key) for key in METRIC_KEYS}
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
        for key, value in metrics.items():
            if value is None:
                missing.setdefault(key, "执行器未提供该项")

        captured_at = record.get("metrics_captured_at")
        if any(value is not None for value in metrics.values()) and not captured_at:
            raise ValueError("backstage metric timestamp missing")
        return {
            "task_id": task["task_id"], "douyin_account": task["douyin_account"],
            "status": status, "room_id": record.get("room_id"), "metrics": metrics,
            "raw_displays": {key: str(value)[:160] for key, value in
                             (record.get("raw_displays") or {}).items() if key in METRIC_KEYS},
            "approximate_metrics": [key for key in record.get("approximate_metrics", [])
                                    if key in METRIC_KEYS],
            "metrics_captured_at": captured_at,
            "stop_signal_received": True,
            "start_signal_source": start_source, "stop_signal_source": stop_source,
            "capture_method": str(record.get("capture_method") or "现有后台取数 CLI")[:160],
            "value_semantics": str(record.get("value_semantics") or
                                   "下播信号后读取的本场后台左侧汇总，不代表平台最终结算")[:200],
            "missing_fields": missing,
        }

    def _task(self, task_id: str) -> dict:
        response = self.http("GET", f"v1/live/sessions/backstage/tasks/{task_id}")
        task = response.get("task")
        if not isinstance(task, dict) or task.get("task_id") != task_id:
            raise ValueError("backstage task identity mismatch")
        return task

    @staticmethod
    def _require_bound_start(task: dict, result: dict) -> None:
        record = result.get("record") if isinstance(result, dict) else None
        source = task.get("start_signal_source")
        if (not isinstance(record, dict)
                or record.get("status") != "waiting_for_stop"
                or record.get("session_id") != task["session_id"]
                or record.get("douyin_account") != task["douyin_account"]
                or record.get("start_signal_source") != source):
            raise BackstageStartError("backstage CLI start did not bind the claimed session")

    def run_once(self) -> bool:
        claimed = self.http("POST", "v1/live/sessions/backstage/tasks/claim")
        task = claimed.get("task")
        if claimed.get("status") == "idle" or task is None:
            return False
        if (claimed.get("status") != "claimed" or not isinstance(task, dict)
                or not all(isinstance(task.get(key), str) and task[key]
                           for key in ("task_id", "session_id", "douyin_account"))
                or task.get("start_signal_source") not in {"ios_app", "android_app"}):
            raise ValueError("invalid claimed backstage task")

        identity = (task["session_id"], task["douyin_account"])
        start_result = self.cli("start", task)
        self._require_bound_start(task, start_result)
        while True:
            current = self._task(task["task_id"])
            if (current.get("session_id"), current.get("douyin_account")) != identity:
                raise ValueError("backstage task identity mismatch")
            if current.get("stop_signal_received") is True:
                task = current
                break
            self.sleep(self.config.poll_seconds)

        self.cli("stop", task)
        result = self.cli("result", task)
        payload = self.result_payload(task, result)
        self.http("POST", f"v1/live/sessions/{task['session_id']}/backstage/result", payload)
        return True

    def serve_forever(self) -> None:
        while True:
            try:
                if not self.run_once():
                    self.sleep(self.config.poll_seconds)
            except BackstageStartError:
                # 不重领同一个未绑定任务，也不输出 CLI 正文或本场身份。
                print("backstage bridge stopped: cli_start_not_bound", file=sys.stderr)
                raise SystemExit(2) from None
            except (OSError, RuntimeError, ValueError) as exc:
                # 不输出任务账号、令牌、原始截图或结果正文。
                print(f"backstage bridge paused: {type(exc).__name__}", file=sys.stderr)
                self.sleep(self.config.poll_seconds)


def main() -> None:
    BackstageBridge(BridgeConfig.from_env()).serve_forever()


if __name__ == "__main__":
    main()
