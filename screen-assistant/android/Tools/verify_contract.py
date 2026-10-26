"""复用共享create_app/Settings的独立HTTP夹具；不触碰现服务/模型/设备。

用准备好server测试依赖的Python运行，参数为已冻结源码包的server目录。
测试依赖沿server/requirements.txt；Java客户端通过Android专属Gradle任务调用。
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-source", type=Path, required=True)
    args = parser.parse_args()
    source = args.server_source.resolve()
    if not (source / "screen_assistant_server" / "app.py").is_file():
        parser.error("参数不是已核对server源码目录")
    sys.path.insert(0, str(source))
    import uvicorn
    from screen_assistant_server.app import create_app
    from screen_assistant_server.config import Settings

    android = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="android-contract-") as directory:
        # 显式测试配置，绝不继承业务密钥；Redis仅创建未连接客户端，未调用answer。
        settings = Settings(redis_url="redis://127.0.0.1:1/0", tianyi_api_key="",
                            tianyi_base_url="http://model.invalid", tianyi_model="unused",
                            dedup_ttl_seconds=86400, max_sentence_chars=2000,
                            model_timeout_seconds=1, model_connect_timeout_seconds=1,
                            live_db_path=str(Path(directory) / "live.sqlite3"), audio_enabled=False)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            if not server.started:
                raise RuntimeError("隔离测试服务未启动")
            command = [str(android / "gradlew.bat"), ":app:verifyLiveContract", "--console=plain",
                       f"-PliveContractBase=http://127.0.0.1:{port}"]
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            if os.name == "nt":
                command = ["cmd.exe", "/d", "/c", *command]
            result = subprocess.run(command, cwd=android, check=False, timeout=90,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
            try:
                output = result.stdout.decode("utf-8")
            except UnicodeDecodeError:
                output = result.stdout.decode("gb18030")  # Windows JDK17/Gradle控制台可能输出本机代码页
            print(output, flush=True)
            if result.returncode or "PASS: Android" not in output:
                raise RuntimeError(f"Java客户端协议探针未通过，exit={result.returncode}")
            return 0
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            listener.close()
            if thread.is_alive():
                raise RuntimeError("隔离测试服务未正常退出")


if __name__ == "__main__":
    raise SystemExit(main())
