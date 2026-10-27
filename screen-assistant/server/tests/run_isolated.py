"""运行全部 server 测试；只启动/关闭本进程拥有的临时 loopback Redis。

从 server 执行：python tests/run_isolated.py [-q ...]
需要 PATH 中有 redis-server；不使用 Docker、现有 Redis 或真实模型。
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import redis


def main() -> int:
    executable = shutil.which("redis-server")
    if not executable:
        raise SystemExit("redis-server not found; install a local test binary first")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="sa-m2-redis-") as directory:
        with (Path(directory) / "redis.log").open("wb") as log:
            process = subprocess.Popen(
                [executable, "--bind", "127.0.0.1", "--port", str(port),
                 "--save", "", "--appendonly", "no", "--dir", directory],
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            connection = redis.Redis(host="127.0.0.1", port=port,
                                     socket_timeout=0.2, socket_connect_timeout=0.2)
            try:
                deadline = time.monotonic() + 8
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("isolated Redis exited before readiness")
                    try:
                        if connection.ping():
                            break
                    except redis.RedisError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("isolated Redis readiness timed out")
                    time.sleep(0.05)
                # Windows MSYS 的内部 PID 不等于 Windows PID；核对随机独有目录。
                actual_dir = connection.config_get("dir")["dir"]
                if os.name == "nt":
                    actual_dir = re.sub(r"^/cygdrive/([A-Za-z])/", r"\1:/", actual_dir)
                if Path(actual_dir).resolve() != Path(directory).resolve():
                    raise RuntimeError(f"isolated Redis directory identity mismatch: {actual_dir!r}")
                env = dict(os.environ, TEST_REDIS_URL=f"redis://127.0.0.1:{port}/9")
                args = sys.argv[1:] or ["-q"]
                return subprocess.call([sys.executable, "-m", "pytest", "tests", *args],
                                       env=env)
            finally:
                connection.close()
                # 只终止本入口创建的进程树，不按名称杀其他 Redis。
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
