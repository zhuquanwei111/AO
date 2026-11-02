"""隔离验证单任务恢复，不连接真实服务。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3

import pytest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from screen_assistant_server.live_store import LiveStore
from test_live import Clock, create, settings


DAY = 86_400_000


def test_recover_old_unclaimed_task_is_authorized_idempotent_and_preserves_session(tmp_path):
    clock = Clock()
    config = settings(tmp_path / "recovery.sqlite3")
    config = config.__class__(**{**config.__dict__, "backstage_worker_token": "worker-test"})
    with TestClient(create_app(config, live_clock=clock)) as client:
        old, headers = create(client, douyin_account="fixture-old")
        url = f"/v1/live/sessions/{old['session_id']}"
        task = client.post(url + "/start", headers=headers, json={"source": "android_app"}).json()
        tid = task["task_id"]
        created = clock.now
        payload = {"expected_status": "waiting_for_stop", "expected_created_at_ms": created}
        path = f"/v1/live/sessions/backstage/tasks/{tid}/recover"
        worker = {"X-Backstage-Worker-Token": "worker-test"}
        clock.now += DAY - 1
        assert client.post(path, headers=worker, json=payload).status_code == 409
        clock.now += 1
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path, headers=worker, json={**payload, "expected_created_at_ms": 2**63}).status_code == 422
        assert client.post(path, headers=worker, json={**payload, "expected_status": "capturing"}).status_code == 422
        assert client.post(path, headers=worker, json={**payload, "expected_created_at_ms": created + 1}).status_code == 409
        recovered = client.post(path, headers=worker, json=payload)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "recovered"
        duplicate = client.post(path, headers=worker, json=payload)
        assert duplicate.status_code == 200 and duplicate.json()["status"] == "duplicate"
        assert duplicate.json()["recovered_at_ms"] == recovered.json()["recovered_at_ms"]
        summary = client.get(url + "/summary", headers=headers).json()
        assert summary["stopped_at_ms"] is None
        assert summary["backstage_recap"]["status"] == "not_connected"
        assert summary["backstage_recap"]["metrics"] is None
        assert client.post("/v1/live/sessions/backstage/tasks/claim", headers=worker).json()["status"] == "idle"
        assert client.post(url + "/stop", headers=headers, json={"source": "ios_app"}).status_code == 409
        stop = client.post(url + "/stop", headers=headers, json={"source": "android_app"})
        assert stop.status_code == 200 and stop.json()["backstage_status"] == "not_connected"
        clock.now += 1
        assert client.post(url + "/stop", headers=headers, json={"source": "android_app"}).json() == stop.json()
        fresh, fresh_headers = create(client, douyin_account="fixture-new")
        start = client.post(f"/v1/live/sessions/{fresh['session_id']}/start", headers=fresh_headers,
                            json={"source": "android_app"}).json()
        assert start["task_status"] == "waiting_for_stop"
    with LiveStoreForTest(config.live_db_path, clock) as store:
        row = store.db.execute("SELECT * FROM live_backstage_tasks WHERE task_id=?", (tid,)).fetchone()
        assert row["recovery_reason"] == "stale_unclaimed"
        assert row["recovered_at_ms"] == created + DAY
        assert row["stopped_at_ms"] == created + DAY and row["result_hash"] is None


class LiveStoreForTest:
    def __init__(self, path, clock):
        self.store = LiveStore(path, clock)

    def __enter__(self):
        return self.store

    def __exit__(self, *_):
        self.store.db.close()


def test_claim_and_recovery_compete_atomically(tmp_path):
    clock = Clock()
    path = str(tmp_path / "race.sqlite3")
    with LiveStoreForTest(path, clock) as setup:
        created = setup.create("", "fixture")
        tid = setup.start_backstage(created["session_id"], created["session_token"], "android_app")["task_id"]
    original_time = clock.now
    clock.now += DAY
    barrier = Barrier(2)

    def run(action):
        with LiveStoreForTest(path, clock) as store:
            barrier.wait()
            try:
                return action(store)
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(run, lambda store: store.claim_backstage_task())
        recover = pool.submit(run, lambda store: store.recover_backstage_task(tid, original_time))
        claim, recover = claim.result(), recover.result()
    if isinstance(recover, dict):
        assert recover["status"] == "recovered" and claim["status"] == "idle"
    else:
        assert recover == 409 and claim["status"] == "claimed"


@pytest.mark.parametrize("mutation", [
    "claimed_at_ms=1", "stopped_at_ms=1", "received_at_ms=1",
    "result_hash='existing'", "result_json='{}'", "status='capturing'",
])
def test_recovery_rejects_changed_task_without_writing(tmp_path, mutation):
    clock = Clock()
    path = str(tmp_path / "changed.sqlite3")
    with LiveStoreForTest(path, clock) as store:
        session = store.create("", "fixture")
        task = store.start_backstage(session["session_id"], session["session_token"], "android_app")
        created = clock.now
        clock.now += DAY
        with store.db:
            store.db.execute(f"UPDATE live_backstage_tasks SET {mutation} WHERE task_id=?", (task["task_id"],))
        before = tuple(store.db.execute("SELECT * FROM live_backstage_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
        with pytest.raises(HTTPException) as error:
            store.recover_backstage_task(task["task_id"], created)
        assert error.value.status_code == 409
        after = tuple(store.db.execute("SELECT * FROM live_backstage_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
        assert after == before


def test_old_task_schema_is_migrated_without_changing_existing_rows(tmp_path):
    path = str(tmp_path / "old.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE live_backstage_tasks (
            task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
            douyin_account TEXT NOT NULL, status TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            claimed_at_ms INTEGER, stopped_at_ms INTEGER, received_at_ms INTEGER,
            start_signal_source TEXT NOT NULL DEFAULT 'ios_app', stop_signal_source TEXT,
            result_hash TEXT, result_json TEXT)""")
        db.execute("INSERT INTO live_backstage_tasks (task_id,session_id,douyin_account,status,created_at_ms) VALUES (?,?,?,?,?)",
                   ("old-task", "old-session", "fixture", "waiting_for_stop", 12))
    with LiveStoreForTest(path, Clock()) as store:
        row = store.db.execute("SELECT * FROM live_backstage_tasks WHERE task_id='old-task'").fetchone()
        assert row["status"] == "waiting_for_stop" and row["created_at_ms"] == 12
        assert row["recovered_at_ms"] is None and row["recovery_reason"] is None
