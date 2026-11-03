"""验证桥单实例锁跨进程释放，不连接任何服务。"""
import os
import subprocess
import sys
import uuid

from screen_assistant_server.backstage_bridge import bridge_instance_lock


def test_single_instance_rejects_competing_process_and_releases():
    name = 'sa-test-' + uuid.uuid4().hex
    code = (
        'import sys; from screen_assistant_server.backstage_bridge import bridge_instance_lock, BackstageFatalError\n'
        'try:\n'
        ' with bridge_instance_lock(sys.argv[1]): pass\n'
        'except BackstageFatalError: sys.exit(7)\n'
    )
    allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL'}
    env = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    with bridge_instance_lock(name):
        first = subprocess.run([sys.executable, '-c', code, name], env=env, capture_output=True, timeout=10)
        assert first.returncode == 7
    second = subprocess.run([sys.executable, '-c', code, name], env=env, capture_output=True, timeout=10)
    assert second.returncode == 0


def test_single_instance_releases_after_process_termination():
    name = 'sa-test-' + uuid.uuid4().hex
    code = ('import sys,time; from screen_assistant_server.backstage_bridge import bridge_instance_lock\n'
            'with bridge_instance_lock(sys.argv[1]):\n print("ready",flush=True); time.sleep(30)\n')
    allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL'}
    env = {key:value for key,value in os.environ.items() if key.upper() in allowed}
    process = subprocess.Popen([sys.executable, '-u', '-c', code, name], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=True)
        else:
            process.terminate()
        process.wait(timeout=10)
        with bridge_instance_lock(name):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
