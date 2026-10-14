"""Source searches stay interactive and file delivery preserves the original bytes."""
import asyncio
import base64
import hashlib
import hmac
import time
from unittest.mock import Mock

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_bot_notifications import setup_bot, completed_job
from bot_notifications import BotService
from local_library import download_stem


def test_partial_source_card_is_visible_before_completion_and_numbers_stay_stable(setup_bot):
    _, bot, store, _, track = setup_bot
    async def scenario():
        published, finish = asyncio.Event(), asyncio.Event()
        bot.source_catalog = lambda: [{'id': 'netease', 'label': '网易云'}]
        async def search(keyword, sources, on_track=None):
            on_track(track)
            published.set()
            await finish.wait()
            return [dict(track, id='later-track'), track], False
        bot.search = bot.search_progress = search
        message = {'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '/search partial'}
        await bot.command(message)
        await published.wait()
        result = store.get('bot_search', 'tg:123:aaaaaaaa')
        assert result['source_states'][0]['state'] == 'searching'
        assert result['source_states'][0]['tracks'][0]['id'] == track['id']
        assert any(track['title'] in item['text'] for item in store.values('bot_outbox'))
        finish.set()
        await bot.search_tasks['tg:123']
        result = store.get('bot_search', 'tg:123:aaaaaaaa')
        assert [t['id'] for t in result['source_states'][0]['tracks']] == [track['id'], 'later-track']
    asyncio.run(scenario())


def test_independent_sources_switch_while_pending_and_bind_old_buttons(setup_bot):
    _, bot, store, _, track = setup_bot
    async def scenario():
        fast, slow = asyncio.Event(), asyncio.Event()
        bot.source_catalog = lambda: [{'id': 'netease', 'label': '网易云'}, {'id': 'qq', 'label': 'QQ音乐'},
                                      {'id': 'kuwo', 'label': '酷我'}, {'id': 'kugou', 'label': '酷狗'}]
        async def search(keyword, sources):
            await (fast if sources == ['netease'] else slow).wait()
            return [{**track, 'id': sources[0] + str(i), 'title': f'歌曲{i}', 'source': sources[0],
                     'size_bytes': (i + 1) * 1024 * 1024} for i in range(10)], False
        bot.search = search
        message = {'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '/search 测试'}
        await bot.command(message)
        item = store.values('bot_outbox')[-1]
        assert '搜索中' in item['text']
        assert [b['text'] for row in item['buttons'] for b in row][:2] == ['✓ 网易云', 'QQ音乐']
        assert len(item['buttons'][0]) == 4
        fast.set()
        for _ in range(10):
            await asyncio.sleep(0)
        first = store.values('bot_outbox')[-1]
        assert '共 10 首' in first['text']
        assert len(first['text'].splitlines()) == 10
        assert '搜索源：网易云 · 测试' in first['text']
        assert 'FLAC 无损 · 1.0 MB' in first['text'] and 'FLAC 无损 · 8.0 MB' in first['text']
        assert '8. 歌曲7' in first['text'] and '9. 歌曲8' not in first['text']
        assert [b['text'] for b in first['buttons'][0]] == [str(i) for i in range(1, 9)]
        assert first['buttons'][1][0]['text'] == '下一页'
        assert len(first['buttons'][2]) == 4
        assert not slow.is_set()
        old_button = store.values('bot_outbox')[-1]['buttons'][0][0]['callback_data']
        bot.telegram = Mock(return_value={'message_id': 42})
        bot.send(store.values('bot_outbox')[-1])
        await bot.command({**message, 'text': '/source aaaaaaaa 1', 'reply_message_id': 42})
        assert 'QQ音乐' in store.values('bot_outbox')[-1]['text'] and '搜索中' in store.values('bot_outbox')[-1]['text']
        bot.send(store.values('bot_outbox')[-1])
        assert bot.telegram.call_args.args[0] == 'editMessageText'
        assert bot.telegram.call_args.args[1]['message_id'] == 42
        await bot.command({**message, 'id': 'b' * 64, 'text': old_button})
        assert bot.enqueue.call_args.args[0]['id'] == 'netease0'
        with pytest.raises(HTTPException):
            await bot.command({**message, 'user': '456', 'text': '/source aaaaaaaa 0'})
        slow.set()
        await bot.search_tasks['tg:123']
        assert '共 10 首' in store.values('bot_outbox')[-1]['text']
        await bot.command({**message, 'text': '/results aaaaaaaa 2 0', 'reply_message_id': 42})
        assert '第 2/2 页' in store.values('bot_outbox')[-1]['text']
        assert '网易云' in store.values('bot_outbox')[-1]['text']
        assert store.values('bot_outbox')[-1]['buttons'][0][0]['callback_data'] == '/download aaaaaaaa 9 0'
        assert 'FLAC 无损 · 9.0 MB' in store.values('bot_outbox')[-1]['text']
        await bot.command({**message, 'text': '下一页'})
        assert '最后一页' in store.values('bot_outbox')[-1]['text']
        await bot.command({**message, 'text': '上一页'})
        assert '第 1/2 页' in store.values('bot_outbox')[-1]['text']
    asyncio.run(scenario())


@pytest.mark.parametrize('extension, method, field', [('flac', 'sendAudio', 'audio'), ('mp3', 'sendAudio', 'audio'),
                                                     ('m4a', 'sendAudio', 'audio'), ('wav', 'sendDocument', 'document')])
def test_original_file_upload_and_custom_api(setup_bot, extension, method, field):
    _, bot, store, audio, track = setup_bot
    target = audio.with_suffix('.' + extension)
    if target != audio:
        target.write_bytes(audio.read_bytes())
    job = completed_job(target, track)
    store.put('download', job['id'], job)
    bot.config['tg_api_url'] = 'http://127.0.0.1:8081'
    bot.tg_http = Mock()
    def post(url, **kwargs):
        assert url == f'http://127.0.0.1:8081/bottest-token/{method}'
        encoder = kwargs['data']
        assert encoder.fields[field][0] == target.name
        if extension == 'flac':
            assert encoder.fields[field][2] == 'audio/flac'
        assert encoder.fields['chat_id'] == '123'
        payload = encoder.read()
        assert target.read_bytes() in payload
        assert kwargs['headers']['Content-Type'].startswith('multipart/form-data; boundary=')
        if field == 'audio':
            assert encoder.fields['performer'] == track['artist']
        return Mock(json=lambda: {'ok': True, 'result': {'message_id': 50}})
    bot.tg_http.post.side_effect = post
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.send(store.values('bot_outbox')[-1])
    bot.enqueue.assert_not_called()


def test_new_search_cancels_previous_and_old_card_stops_loading(setup_bot):
    _, bot, store, _, _ = setup_bot
    async def scenario():
        pending = asyncio.Event()
        bot.source_catalog = lambda: [{'id': 'netease', 'label': '网易云'}]
        async def search(keyword, sources):
            await pending.wait()
            return [], False
        bot.search = search
        message = {'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '/search 旧歌'}
        await bot.command(message)
        previous = bot.search_tasks['tg:123']
        await asyncio.sleep(0)
        await bot.command({**message, 'id': 'b' * 64, 'text': '/search 新歌'})
        await asyncio.gather(previous, return_exceptions=True)
        assert previous.cancelled()
        assert store.get('bot_search', 'tg:123:aaaaaaaa')['source_states'][0]['state'] == 'failed'
        pending.set()
        await bot.search_tasks['tg:123']
        assert store.get('bot_session', 'tg:123')['search_id'] == 'bbbbbbbb'
    asyncio.run(scenario())


def test_tg_large_files_are_queued_for_upload_and_wecom_keeps_its_limit(setup_bot):
    _, bot, store, audio, track = setup_bot
    with audio.open('r+b') as handle:
        handle.truncate(51 * 1024 * 1024)
    job = completed_job(audio, track)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    assert store.values('bot_outbox')[-1]['file']
    assert not store.values('bot_outbox')[-1]['url']
    bot.config['tg_api_url'] = 'http://127.0.0.1:8081'
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    assert store.values('bot_outbox')[-1]['file']
    bot.queue_file({'channel': 'wecom', 'user': 'alice'}, job)
    assert store.values('bot_outbox')[-1]['url']


def test_file_retry_is_durable_without_redownload(setup_bot):
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.send = Mock(side_effect=requests.ConnectionError('offline'))
    async def scenario():
        worker = asyncio.create_task(bot.file_deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.values('bot_outbox')[0]['attempts']:
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        item = store.values('bot_outbox')[0]
        assert item['attempts'] == 1 and item['file']
        item['next'] = 0
        store.put('bot_outbox', item['id'], item)
        bot.send = Mock()
        worker = asyncio.create_task(bot.file_deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if not store.values('bot_outbox'):
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert not store.values('bot_outbox')
        bot.enqueue.assert_not_called()
    asyncio.run(scenario())


def test_wecom_upload_uses_cached_media_on_send_retry(setup_bot):
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.corp, bot.agent, bot.secret = 'corp', '100001', 'secret'
    bot.wecom_http = Mock()
    bot.wecom_http.get.return_value.json.return_value = {'access_token': 'access', 'expires_in': 7200}
    bot.wecom_http.post.side_effect = [Mock(json=lambda: {'media_id': 'media'}),
                                     Mock(json=lambda: {'errcode': 42001}), Mock(json=lambda: {'errcode': 0})]
    bot.queue_file({'channel': 'wecom', 'user': 'alice'}, job)
    item = store.values('bot_outbox')[-1]
    with pytest.raises(RuntimeError):
        bot.send(item)
    assert bot.token_until == 0
    bot.send(item)
    assert len([call for call in bot.wecom_http.post.call_args_list if call.args[0].endswith('/media/upload')]) == 1
    assert bot.wecom_http.post.call_args.kwargs['json']['file']['media_id'] == 'media'


@pytest.mark.parametrize('channel, url', [('discord', 'https://discord.com/api/webhooks/123/token'),
    ('feishu', 'https://open.feishu.cn/open-apis/bot/v2/hook/token'), ('dingtalk', 'https://oapi.dingtalk.com/robot/send?access_token=token')])
def test_webhook_payload_signing_and_notification_dedup(setup_bot, monkeypatch, channel, url):
    _, bot, store, audio, track = setup_bot
    monkeypatch.setattr('bot_notifications.time.time', lambda: 1700000000)
    bot.config.update({channel + '_enabled': True, channel + '_webhook': url, channel + '_sign_secret': 'secret'})
    bot.wecom_http = Mock()
    bot.wecom_http.post.return_value.json.return_value = {'errcode': 0}
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_webhook_notifications()
    bot.queue_webhook_notifications()
    assert len(store.values('bot_outbox')) == 1
    item = store.values('bot_outbox')[0]
    bot.send(item)
    kwargs = bot.wecom_http.post.call_args.kwargs
    body = kwargs['json']
    if channel == 'discord':
        assert body['allowed_mentions'] == {'parse': []} and item['url'] in body['content']
        assert kwargs['params']['wait'] == 'true'
    elif channel == 'feishu':
        assert body['msg_type'] == 'text'
        assert body['sign'] == base64.b64encode(hmac.new(b'1700000000\nsecret', b'', hashlib.sha256).digest()).decode()
    else:
        assert body['msgtype'] == 'text'
        assert kwargs['params']['sign'] == base64.b64encode(hmac.new(b'secret', b'1700000000000\nsecret', hashlib.sha256).digest()).decode()


def test_webhook_validation_and_no_message_config_check(setup_bot, monkeypatch):
    app, _, _, _, _ = setup_bot
    monkeypatch.setattr(BotService, 'start', Mock(side_effect=lambda: asyncio.sleep(0)))
    monkeypatch.setattr(BotService, 'stop', Mock(side_effect=lambda: asyncio.sleep(0)))
    client = TestClient(app)
    response = client.put('/api/bots/config', json={'feishu_enabled': True, 'feishu_webhook': 'http://127.0.0.1/secret'})
    assert response.status_code == 400 and 'secret' not in response.text
    draft = {'feishu_webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/token', 'feishu_sign_secret': 'private-key'}
    with pytest.MonkeyPatch.context() as patch:
        post = Mock(side_effect=AssertionError('must not post'))
        patch.setattr(requests.Session, 'post', post)
        response = client.post('/api/bots/test', json={'channel': 'feishu', 'config': draft})
        assert response.status_code == 200 and '未发送消息' in response.json()['detail']
        post.assert_not_called()
    response = client.put('/api/bots/config', json={**draft, 'feishu_enabled': True})
    assert response.status_code == 200 and 'private-key' not in response.text and 'hook/token' not in response.text


def test_tg_direct_files_need_no_public_url(setup_bot, monkeypatch):
    app, _, _, _, _ = setup_bot
    monkeypatch.setattr(BotService, 'start', Mock(side_effect=lambda: asyncio.sleep(0)))
    monkeypatch.setattr(BotService, 'stop', Mock(side_effect=lambda: asyncio.sleep(0)))
    response = TestClient(app).put('/api/bots/config', json={'public_url': ''})
    assert response.status_code == 200
    assert app.state.bots.links is None


def test_download_filename_sanitizes_paths_and_byte_length():
    assert download_stem('红尘客栈', '周杰伦') == '红尘客栈 - 周杰伦'
    assert download_stem('../曲目:*?', '歌/手') == '_曲目___ - 歌_手'
    assert download_stem('', '') == '未知歌曲 - 未知歌手'
    assert download_stem('CON.song', '歌手') == '_CON.song - 歌手'
    assert len(download_stem('长' * 200, '名' * 200).encode('utf-8')) <= 203


@pytest.mark.parametrize('api_url', ['https://api.telegram.org', 'http://127.0.0.1:8081'])
def test_large_upload_streams_without_reading_whole_file(setup_bot, api_url):
    _, bot, store, audio, track = setup_bot
    with audio.open('r+b') as handle:
        handle.truncate(100 * 1024 * 1024)
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.config['tg_api_url'] = api_url
    bot.tg_http = Mock()
    def post(url, **kwargs):
        assert url == api_url + '/bottest-token/sendAudio'
        encoder = kwargs['data']
        assert encoder.len > 100 * 1024 * 1024
        assert encoder.fields['audio'][1].tell() == 0
        chunk = encoder.read(8192)
        assert len(chunk) == 8192
        assert encoder.fields['audio'][1].tell() < 8192
        return Mock(json=lambda: {'ok': True, 'result': {'audio': {'mime_type': 'audio/flac'}}})
    bot.tg_http.post.side_effect = post
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.send(store.values('bot_outbox')[-1])
    messages = [call.args[1] for call in bot.log.call_args_list]
    assert any('超过 50 MB' in text and '104857600 字节' in text for text in messages)
    assert any('sendAudio 上传成功' in text and 'API 消息类型 audio，MIME audio/flac' in text for text in messages)


@pytest.mark.parametrize('reason', ['Telegram API error 413: file too big',
    'Telegram API error 400: Bad Request: file is too big',
    'Telegram API error 400: FILE_TOO_LARGE', 'Telegram API error 413: Request Entity Too Large'])
def test_server_size_rejection_falls_back_to_link_without_reupload(setup_bot, reason):
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.telegram = Mock(side_effect=[RuntimeError(reason), True])
    item = store.values('bot_outbox')[-1]
    bot.send(item)
    assert [call.args[0] for call in bot.telegram.call_args_list] == ['sendAudio', 'sendMessage']
    assert not item['file']
    assert bot.telegram.call_args.args[0] == 'sendMessage'
    assert bot.telegram.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['url'].startswith('https://music.example.com/downloads/')
    text = bot.telegram.call_args.args[1]['text']
    assert reason not in text
    assert '文件超过当前接口允许的上传大小' in text
    assert any(reason in call.args[1] and '拒绝文件大小' in call.args[1] for call in bot.log.call_args_list)


def test_flac_format_rejection_sends_original_document_from_the_start(setup_bot):
    _, bot, store, audio, track = setup_bot
    original = audio.read_bytes()
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.tg_http = Mock()
    methods = []
    def post(url, **kwargs):
        method = url.rsplit('/', 1)[-1]
        methods.append(method)
        field = 'audio' if method == 'sendAudio' else 'document'
        encoder = kwargs['data']
        assert encoder.fields[field][0] == audio.name
        assert encoder.fields[field][1].tell() == 0
        assert original in encoder.read()
        if method == 'sendAudio':
            assert encoder.fields[field][2] == 'audio/flac'
            return Mock(json=lambda: {'ok': False, 'error_code': 400, 'description': 'Bad Request: AUDIO_CONTENT_TYPE_INVALID'})
        return Mock(json=lambda: {'ok': True, 'result': {'document': {'mime_type': 'audio/flac'}}})
    bot.tg_http.post.side_effect = post
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.send(store.values('bot_outbox')[-1])
    assert methods == ['sendAudio', 'sendDocument']
    assert audio.read_bytes() == original
    messages = [call.args[1] for call in bot.log.call_args_list]
    assert any('AUDIO_CONTENT_TYPE_INVALID' in text and '改用 sendDocument' in text for text in messages)
    assert any('sendDocument 上传成功' in text for text in messages)
    bot.enqueue.assert_not_called()


@pytest.mark.parametrize('error', [requests.ReadTimeout('upload timed out'),
    RuntimeError('Telegram API error 401: Unauthorized'), RuntimeError('Telegram API error 400: Bad Request: chat not found')])
def test_upload_transport_and_auth_errors_do_not_fall_back(setup_bot, error):
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.telegram = Mock(side_effect=error)
    item = store.values('bot_outbox')[-1]
    with pytest.raises(type(error)):
        bot.send(item)
    assert bot.telegram.call_count == 1
    assert item['file'] and not item['url']


def test_size_rejection_link_notification_retry_does_not_upload_again(setup_bot):
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.telegram = Mock(side_effect=[RuntimeError('Telegram API error 400: file is too big'), requests.ConnectionError('offline')])
    async def scenario():
        worker = asyncio.create_task(bot.file_deliveries())
        try:
            for _ in range(100):
                await asyncio.sleep(.01)
                if store.values('bot_outbox')[0]['attempts']:
                    break
            item = store.values('bot_outbox')[0]
            assert item['attempts'] == 1 and not item['file'] and item['url']
            assert 'file is too big' not in item['text']
            assert '文件超过当前接口允许的上传大小' in item['text']
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        bot.telegram.side_effect = None
        bot.telegram.return_value = True
        bot.send(item)
        assert [call.args[0] for call in bot.telegram.call_args_list] == ['sendAudio', 'sendMessage', 'sendMessage']
        bot.enqueue.assert_not_called()
    asyncio.run(scenario())


def test_size_rejection_without_public_url_reports_the_actual_error(setup_bot):
    _, bot, store, audio, track = setup_bot
    bot.links = None
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    bot.telegram = Mock(side_effect=[RuntimeError('Telegram API error 413: file too big'), True])
    bot.send(store.values('bot_outbox')[-1])
    text = bot.telegram.call_args.args[1]['text']
    assert '413: file too big' not in text
    assert '文件超过当前接口允许的上传大小' in text and 'Web 下载原文件' in text


def test_slow_file_upload_does_not_block_search_card_delivery(setup_bot):
    import threading
    _, bot, store, audio, track = setup_bot
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    bot.queue_file({'channel': 'tg', 'user': '123'}, job)
    uploaded, started = threading.Event(), threading.Event()
    texts = []
    def send(item):
        if item.get('file'):
            started.set()
            assert uploaded.wait(2)
        else:
            texts.append(item['text'])
    bot.send = send
    async def scenario():
        file_worker = asyncio.create_task(bot.file_deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if started.is_set():
                break
        assert started.is_set()
        bot.reply({'channel': 'tg', 'user': '123'}, '搜索中')
        worker = asyncio.create_task(bot.deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if texts:
                break
        assert texts == ['搜索中'] and not uploaded.is_set()
        uploaded.set()
        for _ in range(100):
            await asyncio.sleep(.01)
            if not any(i.get('file') for i in store.values('bot_outbox')):
                break
        file_worker.cancel()
        worker.cancel()
        await asyncio.gather(file_worker, worker, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        uploaded.set()
