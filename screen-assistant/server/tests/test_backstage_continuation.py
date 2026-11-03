"""串行桥接续真实API任务；不执行桌面或真实模型。"""
from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from screen_assistant_server.app import create_app
from screen_assistant_server.live_store import LiveStore
from test_live import Clock, create, settings


def test_new_account_waits_for_previous_result_then_is_claimable(tmp_path):
    clock = Clock()
    config = settings(tmp_path / 'queue.sqlite3')
    config = config.__class__(**{**config.__dict__, 'backstage_worker_token': 'fixture-worker'})
    worker = {'X-Backstage-Worker-Token': 'fixture-worker'}
    with TestClient(create_app(config, live_clock=clock)) as client:
        first, first_headers = create(client, douyin_account='account-A')
        first_url = '/v1/live/sessions/' + first['session_id']
        first_task = client.post(first_url + '/start', headers=first_headers,
                                 json={'source': 'android_app'}).json()
        assert client.post('/v1/live/sessions/backstage/tasks/claim', headers=worker).json()['task']['task_id'] == first_task['task_id']
        client.post(first_url + '/stop', headers=first_headers, json={'source': 'android_app'})
        clock.now += 1
        second, second_headers = create(client, douyin_account='account-B')
        second_url = '/v1/live/sessions/' + second['session_id']
        second_task = client.post(second_url + '/start', headers=second_headers,
                                  json={'source': 'android_app'}).json()
        assert second_task['task_status'] == 'waiting_for_stop'
        duplicate = client.post(second_url + '/start', headers=second_headers,
                                json={'source': 'android_app'}).json()
        assert duplicate['task_id'] == second_task['task_id']
        assert duplicate['task_status'] == 'waiting_for_stop'
        payload = {'task_id': first_task['task_id'], 'douyin_account': 'account-A',
                   'status': 'partial', 'room_id': 'room-A',
                   'metrics': {key: None for key in ('累计观众', '新增粉丝', '评论人数', '送礼人数', '获得音浪')},
                   'stop_signal_received': True, 'start_signal_source': 'android_app',
                   'stop_signal_source': 'android_app', 'capture_method': 'fixture',
                   'value_semantics': 'fixture'}
        assert client.post(first_url + '/backstage/result', headers=worker, json=payload).status_code == 200
        claimed = client.post('/v1/live/sessions/backstage/tasks/claim', headers=worker).json()['task']
        assert claimed['task_id'] == second_task['task_id']
        assert claimed['session_id'] == second['session_id']
        assert claimed['douyin_account'] == 'account-B'
        assert claimed['stop_signal_received'] is False


def test_stopped_unclaimed_task_never_rebinds_later_scene(tmp_path):
    clock = Clock()
    config = settings(tmp_path / 'stopped.sqlite3')
    config = config.__class__(**{**config.__dict__, 'backstage_worker_token': 'fixture-worker'})
    worker = {'X-Backstage-Worker-Token': 'fixture-worker'}
    with TestClient(create_app(config, live_clock=clock)) as client:
        old, old_headers = create(client, douyin_account='account-A')
        old_url = '/v1/live/sessions/' + old['session_id']
        client.post(old_url + '/start', headers=old_headers, json={'source': 'android_app'})
        stopped = client.post(old_url + '/stop', headers=old_headers, json={'source': 'android_app'})
        assert stopped.json()['backstage_status'] == 'not_connected'
        assert client.post(old_url + '/stop', headers=old_headers, json={'source': 'android_app'}).json() == stopped.json()
        clock.now += 1
        fresh, fresh_headers = create(client, douyin_account='account-B')
        fresh_url = '/v1/live/sessions/' + fresh['session_id']
        task = client.post(fresh_url + '/start', headers=fresh_headers, json={'source': 'android_app'}).json()
        claimed = client.post('/v1/live/sessions/backstage/tasks/claim', headers=worker).json()['task']
        assert claimed['task_id'] == task['task_id']
        old_summary = client.get(old_url + '/summary', headers=old_headers).json()['backstage_recap']
        assert old_summary['status'] == 'not_connected'
        assert old_summary['metrics'] is None


def test_stop_and_claim_compete_without_reviving_unclaimed_task(tmp_path):
    clock = Clock()
    path = str(tmp_path / 'race.sqlite3')
    store = LiveStore(path, clock)
    scene = store.create('', 'account-A')
    task = store.start_backstage(scene['session_id'], scene['session_token'], 'android_app')
    store.close()
    barrier = Barrier(2)
    def compete(operation):
        local = LiveStore(path, clock)
        try:
            barrier.wait(timeout=5)
            if operation == 'stop':
                return local.stop(scene['session_id'], scene['session_token'], 'android_app')
            return local.claim_backstage_task()
        finally:
            local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        stopping = pool.submit(compete, 'stop')
        claiming = pool.submit(compete, 'claim')
        stopped, claimed = stopping.result(timeout=10), claiming.result(timeout=10)
    store = LiveStore(path, clock)
    try:
        if claimed['status'] == 'idle':
            assert stopped['backstage_status'] == 'not_connected'
            assert store.claim_backstage_task()['status'] == 'idle'
        else:
            assert claimed['task']['task_id'] == task['task_id']
            final = store.backstage_task(task['task_id'])['task']
            assert final['status'] == 'capturing'
            assert final['stop_signal_received'] is True
            assert final['douyin_account'] == 'account-A'
    finally:
        store.close()
