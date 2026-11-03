import asyncio
import httpx
import pytest
from dataclasses import replace
from fastapi import HTTPException

from test_audio import ASRFixture, VadFixture, fixture_app, packet
from test_live import Clock
from screen_assistant_server.app import create_app
from test_audio import enabled_settings


@pytest.mark.parametrize('phase', ['settle', 'queued', 'active_slot'])
def test_final_audio_then_stop_preserves_pending_fusion(tmp_path, monkeypatch, phase):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0 if phase == 'active_slot' else 30000)

    async def run():
        clock = Clock()
        app, _ = fixture_app(tmp_path, clock)
        class ThreeSegments:
            def speech(self, pcm):
                return [(0, 16000), (16000, 32000), (32000, 96000)]
        async with app.router.lifespan_context(app):
            app.state.audio.configure_for_test(ThreeSegments, ASRFixture())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                if phase == 'queued':
                    from types import SimpleNamespace
                    for _ in range(app.state.live.fusion_concurrency):
                        other = (await client.post('/v1/live/sessions', json={})).json()
                        assert app.state.live.schedule_fusion(other['session_id'], other['session_token'],
                                                              SimpleNamespace(app=app))
                if phase == 'active_slot':
                    for _ in range(app.state.live.fusion_concurrency):
                        await app.state.live.fusion_slots.acquire()
                clock.now += 10000
                for seq in range(10):
                    r = await client.post(url + '/audio', headers=headers,
                                          json=packet(seq, start=seq * 600, final=seq == 9))
                    assert r.status_code == 200, r.text
                assert len(r.json()['transcripts']) == 6
                assert r.json()['fusion_schedule'] == 'scheduled'
                if phase == 'queued':
                    assert sid in app.state.live.fusion_queued
                else:
                    assert sid in app.state.live.fusion_active
                candidate = await app.state.live.run('fusion_candidate', sid, session['session_token'])
                assert candidate is not None and candidate['topic'] == 'speech_pace'
                stopping = asyncio.create_task(client.post(url + '/stop', headers=headers))
                if phase == 'active_slot':
                    async with asyncio.timeout(2):
                        while sid not in app.state.live.fusion_stopping:
                            await asyncio.sleep(0)
                    for _ in range(app.state.live.fusion_concurrency):
                        app.state.live.fusion_slots.release()
                stopped = await asyncio.wait_for(stopping, 3)
                assert stopped.status_code == 200, stopped.text
                events = (await client.get(url + '/events', headers=headers)).json()['events']
                assert len(events) == 1
                assert events[0]['phenomenon'] == '近期连续主播转写的文字率偏快'
                assert await app.state.live.run('fusion_candidate', sid, session['session_token']) is None
    asyncio.run(run())


def test_stop_during_model_ignores_late_result_and_fuses_audio_once(tmp_path, monkeypatch):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0)

    async def run():
        clock = Clock()
        app = create_app(replace(enabled_settings(tmp_path / 'model.sqlite3'),
                                 tianyi_api_key='controlled-test'), live_clock=clock)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class ControlledModel:
            async def suggest_live(self, text, *args, **kwargs):
                calls.append(text)
                if len(calls) == 1:
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                return '可以顺着这个问题聊两句。'
            async def aclose(self):
                pass

        async with app.router.lifespan_context(app):
            app.state.model = ControlledModel()
            app.state.audio.configure_for_test(VadFixture, ASRFixture())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                clock.now += 10000
                original_schedule = app.state.live.schedule_fusion
                app.state.live.schedule_fusion = lambda *args: False
                question = {'event_id': 'question', 'input_mode': 'live_observation',
                            'source': 'ios_replaykit_ocr', 'coverage': 'ocr_visible_sample',
                            'reliability': 'known', 'text': '主播你是哪里人？'}
                assert (await client.post(url + '/comments', headers=headers, json=question)).status_code == 200
                app.state.live.schedule_fusion = original_schedule
                assert (await client.post(url + '/audio', headers=headers, json=packet())).status_code == 200
                final = await client.post(url + '/audio', headers=headers,
                                          json=packet(1, pcm_base64='', spans=[], final=True))
                assert final.json()['fusion_schedule'] == 'scheduled'
                await asyncio.wait_for(entered.wait(), 2)
                stopping = asyncio.create_task(client.post(url + '/stop', headers=headers))
                try:
                    async with asyncio.timeout(2):
                        while sid not in app.state.live.fusion_stopping:
                            await asyncio.sleep(0)
                    release.set()
                    assert (await asyncio.wait_for(stopping, 3)).status_code == 200
                    events = (await client.get(url + '/events', headers=headers)).json()['events']
                    assert len(events) == 1
                    assert len(calls) == 2
                finally:
                    release.set()
                    if not stopping.done():
                        stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
    asyncio.run(run())


def test_audio_arriving_during_fusion_remains_pending_for_stop(tmp_path, monkeypatch):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0)

    async def run():
        clock = Clock()
        app = create_app(replace(enabled_settings(tmp_path / 'generations.sqlite3'),
                                 tianyi_api_key='controlled-test'), live_clock=clock)
        first_entered, first_release, second_entered, second_release = (asyncio.Event() for _ in range(4))
        calls = []
        class ControlledModel:
            async def suggest_live(self, text, *args, **kwargs):
                calls.append(text)
                if len(calls) == 1:
                    first_entered.set()
                    await first_release.wait()
                    return ''
                if len(calls) == 2:
                    second_entered.set()
                    try:
                        await second_release.wait()
                    except asyncio.CancelledError:
                        await second_release.wait()
                return '可以顺着这个问题聊两句。'
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = ControlledModel()
            app.state.audio.configure_for_test(VadFixture, ASRFixture())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                clock.now += 10000
                original_schedule = app.state.live.schedule_fusion
                app.state.live.schedule_fusion = lambda *args: False
                question = {'event_id': 'question', 'input_mode': 'live_observation',
                            'source': 'ios_replaykit_ocr', 'coverage': 'ocr_visible_sample',
                            'reliability': 'known', 'text': '主播你是哪里人？'}
                assert (await client.post(url + '/comments', headers=headers, json=question)).status_code == 200
                app.state.live.schedule_fusion = original_schedule
                for seq in (0, 2):
                    assert (await client.post(url + '/audio', headers=headers,
                                              json=packet(seq, start=seq * 300))).status_code == 200
                    final = await client.post(url + '/audio', headers=headers,
                                              json=packet(seq + 1, pcm_base64='', spans=[], final=True))
                    assert final.json()['fusion_schedule'] == 'scheduled'
                    if seq == 0:
                        await asyncio.wait_for(first_entered.wait(), 2)
                first_release.set()
                await asyncio.wait_for(second_entered.wait(), 2)
                stopping = asyncio.create_task(client.post(url + '/stop', headers=headers))
                try:
                    async with asyncio.timeout(2):
                        while sid not in app.state.live.fusion_stopping:
                            await asyncio.sleep(0)
                    second_release.set()
                    assert (await asyncio.wait_for(stopping, 3)).status_code == 200
                    events = (await client.get(url + '/events', headers=headers)).json()['events']
                    assert len(events) == 1
                    assert len(calls) == 3
                finally:
                    first_release.set()
                    second_release.set()
                    if not stopping.done():
                        stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
    asyncio.run(run())


def test_audio_model_timeout_finishes_generation_without_stop_retry(tmp_path, monkeypatch):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0)
    async def run():
        clock = Clock()
        app = create_app(replace(enabled_settings(tmp_path / 'timeout.sqlite3'),
                                 tianyi_api_key='controlled-test', model_timeout_seconds=0.05), live_clock=clock)
        entered = asyncio.Event()
        calls = []
        class TimedModel:
            async def suggest_live(self, text, *args, **kwargs):
                calls.append(text)
                entered.set()
                await asyncio.Event().wait()
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = TimedModel()
            app.state.audio.configure_for_test(VadFixture, ASRFixture())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                clock.now += 10000
                original_schedule = app.state.live.schedule_fusion
                app.state.live.schedule_fusion = lambda *args: False
                question = {'event_id': 'question', 'input_mode': 'live_observation',
                            'source': 'ios_replaykit_ocr', 'coverage': 'ocr_visible_sample',
                            'reliability': 'known', 'text': '主播你是哪里人？'}
                assert (await client.post(url + '/comments', headers=headers, json=question)).status_code == 200
                app.state.live.schedule_fusion = original_schedule
                assert (await client.post(url + '/audio', headers=headers, json=packet())).status_code == 200
                final = await client.post(url + '/audio', headers=headers,
                                          json=packet(1, pcm_base64='', spans=[], final=True))
                assert final.json()['fusion_schedule'] == 'scheduled'
                await asyncio.wait_for(entered.wait(), 2)
                task = app.state.live.fusion_active[sid]
                await asyncio.wait_for(task, 2)
                assert (await client.post(url + '/stop', headers=headers)).status_code == 200
                assert len(calls) == 1
                assert (await client.get(url + '/events', headers=headers)).json()['events'] == []
    asyncio.run(run())


@pytest.mark.parametrize('scenario', ['completed', 'expired', 'other_session'])
def test_stop_respects_completion_expiry_and_session_scope(tmp_path, monkeypatch, scenario):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0 if scenario == 'completed' else 30000)
    async def run():
        clock = Clock()
        app, _ = fixture_app(tmp_path, clock)
        class ThreeSegments:
            def speech(self, pcm):
                return [(0, 16000), (16000, 32000), (32000, 96000)]
        async with app.router.lifespan_context(app):
            app.state.audio.configure_for_test(ThreeSegments, ASRFixture())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                clock.now += 10000
                for seq in range(10):
                    response = await client.post(url + '/audio', headers=headers,
                        json=packet(seq, start=seq * 600, final=seq == 9))
                    assert response.status_code == 200
                assert response.json()['fusion_schedule'] == 'scheduled'
                if scenario == 'completed':
                    task = app.state.live.fusion_active.get(sid)
                    if task:
                        await asyncio.wait_for(task, 2)
                    before = (await client.get(url + '/events', headers=headers)).json()['events']
                    assert len(before) == 1
                elif scenario == 'expired':
                    clock.now += 3_600_000
                else:
                    other = (await client.post('/v1/live/sessions', json={})).json()
                    other_headers = {'X-Live-Session-Token': other['session_token']}
                    other_url = f"/v1/live/sessions/{other['session_id']}"
                    for index in range(2):
                        comment = {'event_id': f'other-{index}', 'input_mode': 'live_observation',
                                   'source': 'ios_replaykit_ocr', 'coverage': 'ocr_visible_sample',
                                   'reliability': 'known', 'text': '主播你是哪里人？'}
                        assert (await client.post(other_url + '/comments', headers=other_headers,
                                                  json=comment)).status_code == 200
                    assert (await client.post(other_url + '/stop', headers=other_headers)).status_code == 200
                    assert (await client.get(other_url + '/events', headers=other_headers)).json()['events'] == []
                assert (await client.post(url + '/stop', headers=headers)).status_code == 200
                after = (await client.get(url + '/events', headers=headers)).json()['events']
                assert len(after) == (0 if scenario == 'expired' else 1)
                if scenario == 'completed':
                    assert after == before
    asyncio.run(run())


@pytest.mark.parametrize('status', [429, 503, 'stop_storage', 403, 500])
def test_tail_candidate_failure_first_stop_and_retry(tmp_path, status, monkeypatch, caplog):
    if status == 429:
        import screen_assistant_server.live_store as store_module
        monkeypatch.setattr(store_module, 'MAX_EVENTS', 0)
    async def run():
        clock = Clock()
        app, _ = fixture_app(tmp_path, clock)
        async with app.router.lifespan_context(app):
            app.state.audio.configure_for_test(VadFixture, ASRFixture())
            app.state.live.schedule_fusion = lambda *args: False
            original_call = app.state.live.call
            async def fail_candidate(method, *args):
                if method == 'save_fusion_candidate' and status != 429:
                    raise HTTPException(status if isinstance(status, int) else 503, 'controlled candidate failure')
                if method == 'stop' and status == 'stop_storage':
                    raise HTTPException(503, 'controlled stop storage failure')
                return await original_call(method, *args)
            app.state.live.call = fail_candidate
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                session = (await client.post('/v1/live/sessions', json={})).json()
                sid = session['session_id']
                headers = {'X-Live-Session-Token': session['session_token']}
                url = f'/v1/live/sessions/{sid}'
                clock.now += 10000
                quality = {'kind': 'quality', 'event_id': 'quality-1', 'source': 'ios_replaykit_ocr',
                           'observed_at_ms': clock.now, 'availability': 'available',
                           'coverage': 'ocr_visible_sample', 'reliability': 'known',
                           'content': {'signal': 'video_input_unclear'}}
                assert (await client.post(url + '/observations', headers=headers, json=quality)).status_code == 200
                assert (await client.post(url + '/audio', headers=headers, json=packet())).status_code == 200
                first = await client.post(url + '/stop', headers=headers)
                if status in (429, 503, 'stop_storage'):
                    assert f'stop_candidate_failed status={429 if status == 429 else 503}' in caplog.text
                else:
                    assert 'stop_candidate_failed' not in caplog.text
                state = await app.state.live.run('authorize', sid, session['session_token'])
                if status in ('stop_storage', 403, 500):
                    assert first.status_code == (503 if status == 'stop_storage' else status)
                    assert state['stopped_at_ms'] is None
                else:
                    assert first.status_code == 200
                    assert state['stopped_at_ms'] is not None
                retry = await client.post(url + '/stop', headers=headers)
                if status == 'stop_storage':
                    assert retry.status_code == 503
                    state = await app.state.live.run('authorize', sid, session['session_token'])
                    assert state['stopped_at_ms'] is None
                else:
                    assert retry.status_code == 200, retry.text
                    if status in (429, 503):
                        assert retry.json()['stopped_at_ms'] == first.json()['stopped_at_ms']
    asyncio.run(run())
