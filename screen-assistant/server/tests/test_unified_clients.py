"""统一集成的两端HTTP契约与回复冷却、训练机会组合回归。"""
import asyncio
import socket
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from screen_assistant_server.app import create_app
from test_live import settings


@pytest.mark.parametrize('app_source,observation_source,other_source', [
    ('android_app', 'android_media_projection_ocr', 'ios_replaykit_ocr'),
    ('ios_app', 'ios_replaykit_ocr', 'android_media_projection_ocr'),
])
def test_clients_reply_cooldown_preserves_bounded_training_and_stop(
        tmp_path, monkeypatch, app_source, observation_source, other_source):
    def blocked(*args, **kwargs):
        raise AssertionError('集成回归禁止外部连接')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    start = 1_790_000_000_000
    clock = [start]
    cfg = replace(settings(tmp_path / 'unified.sqlite3'), tianyi_api_key='controlled')
    calls = []

    class Model:
        async def reply_to_comment(self, text, *, nickname, priority_eligible):
            calls.append((text, nickname))
            return '谢谢喜欢。'
        async def aclose(self):
            pass

    app = create_app(cfg, live_clock=lambda: clock[0])
    with TestClient(app) as client:
        client.portal.call(app.state.model.aclose)
        app.state.model = Model()
        created = client.post('/v1/live/sessions', json={'douyin_account': 'unified-fixture'})
        assert created.status_code == 201
        session = created.json()
        assert session['capabilities'] == {'observation_batch_metadata': True, 'typed_prompts': True}
        url = '/v1/live/sessions/' + session['session_id']
        headers = {'X-Live-Session-Token': session['session_token']}
        signal = {'source': app_source}
        assert client.post(url + '/start', headers=headers, json=signal).status_code == 200
        assert client.post(url + '/start', headers=headers, json=signal).json()['status'] == 'duplicate'

        async def idle():
            async with asyncio.timeout(5):
                while app.state.live.fusion_active or app.state.live.fusion_queue:
                    await asyncio.sleep(.005)

        def row(event, source=observation_source):
            return {'kind': 'comment', 'event_id': event, 'source': source,
                    'observed_at_ms': clock[0], 'availability': 'available',
                    'coverage': 'ocr_visible_sample', 'reliability': 'known', 'confidence': .98,
                    'capture_batch_id': event, 'line_index': 0, 'capture_comment_count': 1,
                    'content': {'text': '阿青：这首歌很好听',
                                'text_position': {'x': .1, 'y': .5, 'width': .4, 'height': .04}}}

        for event, offset in [('first', 0), ('unlock', 1_000), ('activity', 20_000),
                              ('due', 32_000), ('unlock-activity', 33_000)]:
            clock[0] = start + offset
            result = client.post(url + '/observations', headers=headers, json=row(event))
            assert result.status_code == 200
            client.portal.call(idle)
        assert calls == [('这首歌很好听', '阿青')]
        events = client.get(url + '/events', headers=headers).json()['events']
        replies = [x for x in events if x['prompt_type'] == 'comment_reply']
        notices = [x for x in events if x['prompt_type'] == 'operation_notice']
        assert len(replies) == 1 and len(notices) == 2
        assert [x['expires_at_ms'] for x in notices] == [start + 30_000, start + 50_000]
        assert notices[1]['sources'][0]['observed_at_ms'] == start + 20_000
        page = client.get(url + '/prompts', headers=headers).json()
        assert page['server_time_ms'] == clock[0]
        assert page['server_processing_ms'] >= 0
        assert all(x['expires_at_ms'] > clock[0] for x in page['prompts'])
        wrong = client.post(url + '/observations', headers=headers, json=row('wrong-source', other_source))
        assert wrong.status_code == 422
        assert wrong.json()['detail'] == 'observation source does not match app session'
        clock[0] = start + 90_000
        assert client.post(url + '/observations', headers=headers, json=row('after-cooldown')).status_code == 200
        client.portal.call(idle)
        assert len(calls) == 2
        stopped = client.post(url + '/stop', headers=headers, json=signal)
        assert stopped.status_code == 200 and stopped.json()['signal_source'] == app_source
        duplicate = client.post(url + '/stop', headers=headers, json=signal)
        assert duplicate.json()['stopped_at_ms'] == stopped.json()['stopped_at_ms']
        assert client.post(url + '/observations', headers=headers, json=row('late')).status_code == 409
