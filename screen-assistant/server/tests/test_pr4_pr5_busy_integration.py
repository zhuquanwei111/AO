import asyncio
from dataclasses import replace

import httpx

from screen_assistant_server.app import create_app
from test_live import Clock, settings


def test_busy_backstage_and_full_fusion_preserve_observation_and_claim(tmp_path, monkeypatch):
    import screen_assistant_server.live as live_module
    monkeypatch.setattr(live_module, 'FUSION_SETTLE_MS', 0)

    async def run():
        clock = Clock()
        cfg = replace(settings(tmp_path / 'combined.sqlite3'), backstage_worker_token='worker-test',
                      tianyi_api_key='controlled-test', model_timeout_seconds=5,
                      live_fusion_concurrency=1, live_fusion_queue_limit=1)
        app = create_app(cfg, live_clock=clock)
        entered, release = asyncio.Event(), asyncio.Event()
        class Model:
            async def suggest_live(self, text, *args, **kwargs):
                entered.set()
                await release.wait()
                return '可以顺着这个问题聊两句。'
            async def aclose(self):
                pass
        async with app.router.lifespan_context(app):
            app.state.model = Model()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                async def session(account):
                    result = (await client.post('/v1/live/sessions', json={'douyin_account': account})).json()
                    return result, {'X-Live-Session-Token': result['session_token']}, f"/v1/live/sessions/{result['session_id']}"
                first, h1, u1 = await session('first-account')
                task = (await client.post(u1 + '/start', headers=h1)).json()['task_id']
                worker = {'X-Backstage-Worker-Token': 'worker-test'}
                assert (await client.post('/v1/live/sessions/backstage/tasks/claim', headers=worker)).json()['task']['task_id'] == task
                second, h2, u2 = await session('second-account')
                busy_start = await client.post(u2 + '/start', headers=h2)
                assert busy_start.status_code == 200 and busy_start.json()['task_status'] == 'not_connected'
                third, h3, u3 = await session('third-account')
                def comment(key):
                    return {'event_id': key, 'input_mode': 'live_observation', 'source': 'ios_replaykit_ocr',
                            'coverage': 'ocr_visible_sample', 'reliability': 'known', 'text': '主播你是哪里人？'}
                try:
                    assert (await client.post(u1 + '/comments', headers=h1, json=comment('a'))).json()['reason'] == 'scheduled'
                    await asyncio.wait_for(entered.wait(), 2)
                    assert (await client.post(u3 + '/comments', headers=h3, json=comment('b'))).json()['reason'] == 'scheduled'
                    result = await client.post(u2 + '/comments', headers=h2, json=comment('c'))
                    assert result.status_code == 200
                    assert result.json()['status'] == 'accepted' and result.json()['reason'] == 'analysis_busy'
                    assert result.json()['prompt_id'] is None
                    count = app.state.live.store.db.execute(
                        'SELECT COUNT(*) FROM live_observations WHERE session_id=?', (second['session_id'],)
                    ).fetchone()[0]
                    assert count == 1
                    assert (await client.get(u2 + '/summary', headers=h2)).json()['backstage_recap']['status'] == 'not_connected'
                    assert (await client.post('/v1/live/sessions/backstage/tasks/claim', headers=worker)).json()['task']['task_id'] == task
                    assert (await client.post(u2 + '/stop', headers=h2)).json()['backstage_status'] == 'not_connected'
                finally:
                    release.set()
    asyncio.run(run())
