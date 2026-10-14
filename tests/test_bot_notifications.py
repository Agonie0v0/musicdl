"""Bot trust boundaries, encrypted callbacks, command ownership and recovery."""
import asyncio
import base64
import copy
import hashlib
import struct
import time
from unittest.mock import Mock, AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bot_notifications import DownloadLinks, WecomCrypt, install
from bot_notifications import BotService
from bot_notifications import format_size, track_size, job_progress, rank_tracks, TG_COMMANDS


class Store:
    def __init__(self):
        self.records = {}

    def get(self, kind, key, default=None):
        return copy.deepcopy(self.records.get((kind, key), default))

    def put(self, kind, key, value):
        self.records[kind, key] = copy.deepcopy(value)

    def values(self, kind):
        return [copy.deepcopy(v) for (k, _), v in self.records.items() if k == kind]

    def remove(self, kind, key):
        self.records.pop((kind, key), None)


@pytest.mark.parametrize('value, expected', [(1000, '1000 B'), (1048576, '1.0 MB'),
    (1073741824, '1.0 GB'), (None, '大小未知'), (float('nan'), '大小未知'),
    (float('inf'), '大小未知'), (-1, '大小未知'), (True, '大小未知')])
def test_size_formatting(value, expected):
    assert format_size(value) == expected


def test_provider_size_and_transfer_progress():
    assert track_size({'size_bytes': 2 * 1048576, 'size_label': 'unknown'}) == '2.0 MB'
    assert track_size({'size_label': '35.2 MB'}) == '35.2 MB'
    for value in ('NULL', 'unknown', 'None', '0 MB', '0 B', 'bad\nsize'):
        assert track_size({'size_label': value}) == '大小未知'
    job = {'track': {}, 'status': 'downloading', 'bytes': 1048576, 'total_bytes': 4194304}
    assert job_progress(job) == '1.0 MB / 4.0 MB · 25.0%'
    assert job_progress({**job, 'total_bytes': None}) == '已下载 1.0 MB · 总大小未知'


@pytest.fixture
def setup_bot(monkeypatch, tmp_path):
    for key in ('TG_TOKEN', 'TG_ALLOWED_USERS', 'TG_PROXY', 'WECOM_CORP_ID', 'WECOM_AGENT_ID',
                'WECOM_SECRET', 'WECOM_TOKEN', 'WECOM_AES_KEY', 'WECOM_ALLOWED_USERS', 'BOT_SOURCES'):
        monkeypatch.delenv('MUSICDL_' + key, raising=False)
    monkeypatch.setenv('MUSICDL_TG_TOKEN', 'test-token')
    monkeypatch.setenv('MUSICDL_TG_ALLOWED_USERS', '123,456')
    monkeypatch.setenv('MUSICDL_PUBLIC_URL', 'https://music.example.com')
    monkeypatch.setenv('MUSICDL_LINK_TTL_SECONDS', '86400')
    store = Store()
    track = {'id': 'track1', 'title': '测试音乐', 'artist': '歌手', 'source': 'netease',
             'qualities': [{'id': 'engine:flac:0', 'label': 'FLAC 无损'}]}
    audio = tmp_path / '测试音乐.flac'
    audio.write_bytes(b'0123456789' * 100)

    def resolve(token):
        target = (tmp_path / token).resolve()
        if not target.is_relative_to(tmp_path) or not target.is_file():
            raise HTTPException(404, 'file unavailable')
        return target

    async def search(keyword, sources):
        return [track], False

    def enqueue(value, ident):
        job = {'id': ident, 'track': value, 'status': 'queued', 'file': None}
        store.put('download', ident, job)
        return job

    app = FastAPI()
    bot = install(app, get=store.get, put=store.put, values=store.values, remove=store.remove,
                  search=search, enqueue=Mock(side_effect=enqueue), control=Mock(), resolve=resolve, log=Mock())
    return app, bot, store, audio, track


def completed_job(audio, track):
    return {'id': 'a' * 32, 'status': 'complete', 'file': audio.name, 'track': track}


def test_search_cards_pagination_and_download_indices(setup_bot):
    _, bot, store, _, track = setup_bot
    tracks = [{**track, 'id': f'track{i}', 'title': f'歌曲{i}\n' + '长标题' * 30,
               'size_bytes': 1048576 * (i + 1)} for i in range(10)]
    bot.search = AsyncMock(return_value=(tracks, True))

    async def scenario():
        message = {'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '稻香 周杰伦'}
        await bot.command(message)
        bot.search.assert_awaited_once_with('稻香 周杰伦', bot.sources)
        first = store.values('bot_outbox')[-1]
        assert '第 1/2 页' in first['text'] and '共 10 首' in first['text']
        assert '8. 歌曲7' in first['text'] and '9. 歌曲8' not in first['text']
        assert 'FLAC 无损 · 1.0 MB' in first['text'] and 'FLAC 无损 · 8.0 MB' in first['text']
        assert '部分音源' in first['text']
        assert '歌曲0 长标题' in first['text'] and '…' in first['text']
        assert [b['text'] for b in first['buttons'][0]] == [str(i) for i in range(1, 9)]
        assert all(len(b['callback_data'].encode()) <= 64 for row in first['buttons'] for b in row if 'callback_data' in b)
        await bot.command({**message, 'text': '/results aaaaaaaa 2', 'reply_message_id': 42})
        second = store.values('bot_outbox')[-1]
        assert '第 2/2 页' in second['text'] and '9. 歌曲8' in second['text']
        assert 'FLAC 无损 · 9.0 MB' in second['text'] and 'FLAC 无损 · 10.0 MB' in second['text']
        assert second['edit_message_id'] == 42
        assert second['buttons'][0][0]['callback_data'] == '/download aaaaaaaa 9'
        bot.telegram = Mock()
        bot.send(second)
        assert bot.telegram.call_args.args[0] == 'editMessageText'
        assert bot.telegram.call_args.args[1]['message_id'] == 42
        for user, page in [('456', 2), ('123', 3)]:
            with pytest.raises(HTTPException):
                await bot.command({**message, 'user': user, 'text': f'/results aaaaaaaa {page}'})
        await bot.command({**message, 'id': 'b' * 64, 'text': '/download aaaaaaaa 6'})
        assert bot.enqueue.call_args.args[0]['id'] == 'track5'
        assert '6.0 MB' in store.values('bot_outbox')[-1]['text']
        result = store.get('bot_search', 'tg:123:aaaaaaaa')
        result['expires'] = 0
        store.put('bot_search', result['id'], result)
        with pytest.raises(HTTPException):
            await bot.command({**message, 'text': '/results aaaaaaaa 1'})

    asyncio.run(scenario())


def test_menu_commands_and_task_progress(setup_bot):
    _, bot, store, audio, track = setup_bot

    async def scenario():
        message = {'id': 'b' * 64, 'channel': 'tg', 'user': '123', 'text': ''}
        for command in ('/start', '/menu', '菜单', '/help', '/search'):
            await bot.command({**message, 'text': command})
            item = store.values('bot_outbox')[-1]
            assert item['buttons'][0][1]['callback_data'] == '/status'
            assert '稻香 周杰伦' in item['text']
        job = {**completed_job(audio, track), 'status': 'downloading', 'bytes': 1048576, 'total_bytes': 4194304}
        store.put('download', job['id'], job)
        store.put('bot_task', job['id'], {'id': job['id'], 'owner': 'tg:123'})
        await bot.command({**message, 'text': '/status'})
        item = store.values('bot_outbox')[-1]
        assert '25.0%' in item['text'] and job['id'] not in item['text']
        assert item['buttons'][0][0]['callback_data'] == '/cancel ' + job['id']
        store.put('download', job['id'], {**job, 'status': 'complete'})
        await bot.command({**message, 'text': '/status'})
        item = store.values('bot_outbox')[-1]
        assert '文件大小：1000 B' in item['text']  # actual file beats stale transfer metadata
        assert item['buttons'][0][0]['callback_data'] == '/link ' + job['id']
        bot.telegram = Mock(return_value=True)
        await bot.register_telegram_menu()
        assert [call.args[0] for call in bot.telegram.call_args_list] == ['setMyCommands', 'setMyCommands', 'setChatMenuButton']
        assert bot.telegram.call_args_list[0].args[1]['commands'] == TG_COMMANDS
        assert bot.telegram.call_args_list[0].args[1]['scope'] == {'type': 'all_private_chats'}
        assert bot.telegram.call_args_list[1].args[1]['language_code'] == 'zh'
        assert bot.telegram.call_args.args[1] == {'menu_button': {'type': 'commands'}}

    asyncio.run(scenario())


def test_channel_text_limits_and_wecom_pagination(setup_bot):
    _, bot, store, _, track = setup_bot
    message = {'id': 'b' * 64, 'channel': 'wecom', 'user': 'member'}
    bot.search_page(message, 'aaaaaaaa', {'tracks': [track] * 10, 'expires': time.time() + 1800}, 2)
    assert '下一页 / 上一页 / 第2页' in store.values('bot_outbox')[-1]['text']
    assert 'aaaaaaaa' not in store.values('bot_outbox')[-1]['text']
    text = '中🎵' * 2000
    for channel, limit, encoding, divisor in [('wecom', 1800, 'utf-8', 1), ('tg', 4000, 'utf-16-le', 2)]:
        before = len(store.values('bot_outbox'))
        bot.reply({**message, 'channel': channel}, text, edit_message_id=42)
        chunks = store.values('bot_outbox')[before:]
        assert ''.join(item['text'] for item in chunks) == text
        assert all(len(item['text'].encode(encoding)) // divisor <= limit for item in chunks)
        assert all('edit_message_id' not in item for item in chunks)


def test_telegram_unchanged_edit_is_success(setup_bot):
    _, bot, _, _, _ = setup_bot
    bot.tg_http = Mock()
    bot.tg_http.post.return_value.json.return_value = {'ok': False, 'error_code': 400, 'description': 'Bad Request: message is not modified'}
    assert bot.telegram('editMessageText', {}) is True
    with pytest.raises(RuntimeError):
        bot.telegram('sendMessage', {})


def test_ranking_prioritizes_relevant_lossless_before_cap(setup_bot):
    _, bot, store, _, track = setup_bot
    lossy = {'id': 'mp3', 'codec': 'mp3', 'label': 'MP3', 'bitrate': 320}
    lossless = {'id': 'flac', 'codec': 'flac', 'label': 'FLAC 无损', 'lossless': True}
    song = {**track, 'title': '稻香', 'artist': '周杰伦'}
    mp3 = {**song, 'id': 'exact-mp3', 'qualities': [lossy]}
    flac = {**song, 'id': 'exact-flac', 'qualities': [lossy, lossless]}
    remix = {**song, 'id': 'remix', 'title': '稻香 Remix', 'qualities': [lossless]}
    unrelated = {**track, 'id': 'unrelated', 'title': '其他歌曲', 'artist': '其他歌手', 'qualities': [lossless]}
    ordered = rank_tracks([unrelated, remix, mp3, flac], '稻香 周杰伦')
    assert [t['id'] for t in ordered] == ['exact-flac', 'exact-mp3', 'remix', 'unrelated']
    assert ordered[0]['qualities'][0]['id'] == 'flac'
    assert flac['qualities'][0]['id'] == 'mp3'  # native track remains untouched
    bot.search = AsyncMock(return_value=([unrelated] * 15 + [flac], False))
    asyncio.run(bot.command({'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '稻香 周杰伦'}))
    assert store.get('bot_search', 'tg:123:aaaaaaaa')['tracks'][0]['id'] == 'exact-flac'


def test_short_pagination_and_download_use_owned_latest_session(setup_bot):
    _, bot, store, _, track = setup_bot
    bot.search = AsyncMock(return_value=([{**track, 'id': str(i)} for i in range(10)], False))
    async def scenario():
        message = {'id': 'a' * 64, 'channel': 'wecom', 'user': 'alice', 'text': '搜索 测试音乐'}
        await bot.command(message)
        await bot.command({**message, 'text': '下一页'})
        assert '第 2/2 页' in store.values('bot_outbox')[-1]['text']
        await bot.command({**message, 'text': '下一页'})
        assert '最后一页' in store.values('bot_outbox')[-1]['text']
        await bot.command({**message, 'text': '上一页'})
        assert '第 1/2 页' in store.values('bot_outbox')[-1]['text']
        for command in ('第2页', '结果 2', '翻页 2', '/results 2'):
            await bot.command({**message, 'text': command})
            assert store.get('bot_session', 'wecom:alice')['page'] == 2
        with pytest.raises(HTTPException):
            await bot.command({**message, 'user': 'bob', 'text': '下一页'})
        await bot.command({**message, 'id': 'b' * 64, 'text': '下载 6'})
        assert bot.enqueue.call_args.args[0]['id'] == '5'
        assert not bot.control.called
        # A new search replaces the short-command context without reusing old results.
        await bot.command({**message, 'id': 'c' * 64, 'text': '搜索 新歌'})
        assert store.get('bot_session', 'wecom:alice')['search_id'] == 'cccccccc'
        bot.enqueue.assert_called_once()
        result = store.get('bot_search', 'wecom:alice:cccccccc')
        result['expires'] = 0
        store.put('bot_search', result['id'], result)
        with pytest.raises(HTTPException):
            await bot.command({**message, 'text': '下载 1'})
    asyncio.run(scenario())


def test_wecom_empty_allowlist_accepts_all_authenticated_members(setup_bot):
    app, bot, store, _, _ = setup_bot
    client = TestClient(app)
    draft = {'wecom_enabled': True, 'wecom_corp_id': 'corp123', 'wecom_agent_id': '100001',
             'wecom_secret': 'secret', 'wecom_token': 'token', 'wecom_aes_key': base64.b64encode(b'k' * 32).decode().rstrip('='),
             'wecom_allowed_users': ''}
    assert client.put('/api/bots/config', json=draft).status_code == 200
    current = app.state.bots
    assert current.allowed('wecom', 'alice') and current.allowed('wecom', 'bob')
    assert not current.allowed('wecom', '')
    assert not current.allowed('tg', '999')
    current.wecom_users = {'alice'}
    assert current.allowed('wecom', 'alice') and not current.allowed('wecom', 'bob')
    assert client.put('/api/bots/config', json={'tg_allowed_users': ''}).status_code == 400


def test_signed_file_stream_range_head_and_tampering(setup_bot, monkeypatch):
    app, bot, store, audio, track = setup_bot
    link = bot.links.create(completed_job(audio, track))
    parsed = urlsplit(link)
    path = parsed.path + '?' + parsed.query
    client = TestClient(app)
    assert client.get(path).content == audio.read_bytes()
    assert client.head(path).headers['content-length'] == '1000'
    response = client.get(path, headers={'Range': 'bytes=100-199'})
    assert response.status_code == 206
    assert response.content == audio.read_bytes()[100:200]
    assert response.headers['content-range'] == 'bytes 100-199/1000'
    assert response.headers['cache-control'] == 'private, no-store'
    query = parse_qs(parsed.query)
    signature = query['signature'][0]
    assert client.get(path.replace(signature, '0' * 64)).status_code == 403
    assert client.get(path.replace(parsed.path.split('/')[-1], 'f' * 32)).status_code == 403
    expires = int(query['expires'][0])
    assert client.get(path.replace(str(expires), str(expires + 1))).status_code == 403
    # Signing secret and grants survive a restart.
    restored = DownloadLinks(store.get, store.put, bot.links.resolve, 'https://music.example.com', 86400)
    assert restored.verify(parsed.path.split('/')[-1], expires, signature) == audio
    monkeypatch.setattr('bot_notifications.time.time', lambda: expires + 1)
    assert client.get(path).status_code == 403


def test_link_never_serves_replaced_or_deleted_file(setup_bot):
    app, bot, _, audio, track = setup_bot
    link = urlsplit(bot.links.create(completed_job(audio, track)))
    path = link.path + '?' + link.query
    audio.write_bytes(b'replacement')
    assert TestClient(app).get(path).status_code == 410
    audio.unlink()
    assert TestClient(app).get(path).status_code == 404
    with pytest.raises(HTTPException):
        bot.links.create({'status': 'downloading', 'file': audio.name})


def test_file_larger_than_chat_upload_limit_uses_range_without_size_cap(setup_bot):
    app, bot, _, audio, track = setup_bot
    size = 64 * 1024 * 1024
    with audio.open('wb') as handle:
        handle.truncate(size)
    link = urlsplit(bot.links.create(completed_job(audio, track)))
    path = link.path + '?' + link.query
    client = TestClient(app)
    assert int(client.head(path).headers['content-length']) == size
    response = client.get(path, headers={'Range': f'bytes={size - 1}-{size - 1}'})
    assert response.status_code == 206
    assert response.content == b'\0'


def encrypt_callback(bot, content, receiver=None):
    key = bot.crypt.key
    raw = b'0123456789abcdef' + struct.pack('!I', len(content)) + content + (receiver or bot.corp.encode())
    pad = 32 - len(raw) % 32
    raw += bytes([pad]) * pad
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(cipher.update(raw) + cipher.finalize()).decode()
    timestamp, nonce = str(int(time.time())), 'nonce'
    signature = hashlib.sha1(''.join(sorted([bot.crypt.token, timestamp, nonce, encrypted])).encode()).hexdigest()
    return encrypted, {'timestamp': timestamp, 'nonce': nonce, 'msg_signature': signature}


def test_wecom_url_validation_encryption_dedup_and_wrong_application(setup_bot):
    app, bot, store, _, _ = setup_bot
    bot.corp, bot.agent, bot.wecom_users = 'corp123', '100001', {'alice'}
    bot.crypt = WecomCrypt('verify-token', base64.b64encode(b'k' * 32).decode().rstrip('='), bot.corp)
    client = TestClient(app)
    encrypted, query = encrypt_callback(bot, b'hello')
    assert client.get('/bot/wecom', params={**query, 'echostr': encrypted}).text == 'hello'
    assert client.get('/bot/wecom', params={**query, 'echostr': encrypted, 'msg_signature': 'bad'}).status_code == 403

    def send(user='alice', agent='100001', receiver=None, msg_id='1'):
        content = f'<xml><ToUserName>corp123</ToUserName><FromUserName>{user}</FromUserName><AgentID>{agent}</AgentID><MsgType>text</MsgType><MsgId>{msg_id}</MsgId><Content>帮助</Content></xml>'.encode()
        encrypted, query = encrypt_callback(bot, content, receiver)
        return client.post('/bot/wecom', params=query, content=f'<xml><Encrypt>{encrypted}</Encrypt></xml>')

    assert send().status_code == 200
    assert send().status_code == 200
    assert bot.pending.qsize() == 1
    assert len(store.values('bot_inbox')) == 1
    assert send(user='outsider').status_code == 200
    assert bot.pending.qsize() == 1
    assert send(agent='100002').status_code == 403
    assert send(receiver=b'other-corp').status_code == 403
    assert client.post('/bot/wecom', params=query, content='<!DOCTYPE xml [<!ENTITY x "attack">]><xml/>').status_code == 403
    assert client.post('/bot/wecom', params=query, content=b'x' * 65537).status_code == 413


def test_commands_bind_results_and_tasks_to_user(setup_bot):
    _, bot, store, audio, track = setup_bot

    async def scenario():
        message = {'id': 'a' * 64, 'channel': 'tg', 'user': '123', 'text': '/search 测试'}
        await bot.command(message)
        results = store.values('bot_outbox')[-1]
        assert results['buttons'][0][0]['callback_data'] == '/download aaaaaaaa 1'
        download = {**message, 'id': 'b' * 64, 'text': '/download aaaaaaaa 1'}
        with pytest.raises(HTTPException):
            await bot.command({**download, 'user': '456'})
        await bot.command(download)
        assert bot.enqueue.call_count == 1
        await bot.command(download)
        assert bot.enqueue.call_count == 1
        ident = 'b' * 32
        with pytest.raises(HTTPException):
            await bot.command({**message, 'user': '456', 'text': f'/cancel {ident}'})
        await bot.command({**message, 'text': f'/cancel {ident}'})
        bot.control.assert_called_once_with(ident, 'cancel')
        job = completed_job(audio, track)
        job['id'] = ident
        store.put('download', ident, job)
        await bot.command({**message, 'text': f'/link {ident}'})
        assert store.values('bot_outbox')[-1]['url'].startswith('https://music.example.com/downloads/')
        assert bot.accept('tg', 'outsider', '9', '/help') is False
        assert bot.accept('tg', '123', '9', '/help') is True
        assert bot.accept('tg', '123', '9', '/help') is True
        assert bot.pending.qsize() == 1

    asyncio.run(scenario())


def test_completion_delivery_retries_without_downloading_again(setup_bot, monkeypatch):
    _, bot, store, audio, track = setup_bot
    # Server-rejected uploads use renewable signed links.
    with audio.open('r+b') as handle:
        handle.truncate(51 * 1024 * 1024)
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    store.put('bot_task', job['id'], {'id': job['id'], 'owner': 'tg:123', 'channel': 'tg', 'user': '123', 'notified': None})
    bot.telegram = Mock(side_effect=[RuntimeError('Telegram API error 413: file too big'), requests.ConnectionError('offline')])

    async def scenario():
        worker = asyncio.create_task(bot.deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.values('bot_outbox'):
                break
        file_worker = asyncio.create_task(bot.file_deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.values('bot_outbox') and store.values('bot_outbox')[0]['attempts'] == 1:
                break
        worker.cancel()
        file_worker.cancel()
        await asyncio.gather(worker, file_worker, return_exceptions=True)
        assert len(store.values('bot_outbox')) == 1
        assert store.get('bot_task', job['id'])['notified'] == 'complete'
        bot.enqueue.assert_not_called()
        item = store.values('bot_outbox')[0]
        assert item['url'] and item['job_id'] == job['id']
        assert '文件大小：51.0 MB' in item['text']
        assert job['id'] not in item['text']
        old_url = item['url']
        # Simulate a notification outage longer than the link's lifetime.
        item['next'] = 0
        store.put('bot_outbox', item['id'], item)
        now = time.time()
        monkeypatch.setattr('bot_notifications.time.time', lambda: now + 90000)
        bot.send = Mock()
        worker = asyncio.create_task(bot.deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if not store.values('bot_outbox'):
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert not store.values('bot_outbox')
        assert bot.send.call_args.args[0]['url'] != old_url
        bot.enqueue.assert_not_called()

    asyncio.run(scenario())


def test_shutdown_preserves_pending_command_for_restart(setup_bot):
    _, bot, store, _, _ = setup_bot

    async def scenario():
        bot.accept('tg', '123', '10', '/search 测试')
        entered = asyncio.Event()

        async def slow_command(message):
            entered.set()
            await asyncio.Event().wait()

        bot.command = slow_command
        worker = asyncio.create_task(bot.commands())
        await entered.wait()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert store.values('bot_inbox')[0]['done'] is False

    asyncio.run(scenario())


def test_telegram_ignores_groups_and_persists_offset_after_inbox(setup_bot):
    _, bot, store, _, _ = setup_bot
    updates = [
        {'update_id': 1, 'message': {'chat': {'id': -99, 'type': 'group'}, 'from': {'id': 123}, 'text': '/help'}},
        {'update_id': 2, 'message': {'chat': {'id': 456, 'type': 'private'}, 'from': {'id': 123}, 'text': '/help'}},
        {'update_id': 3, 'message': {'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 123}, 'text': '/help'}}]
    bot.telegram = Mock(return_value=updates)

    async def scenario():
        worker = asyncio.create_task(bot.poll_telegram())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.get('bot_setting', 'tg_offset') == 4:
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert bot.pending.qsize() == 1
        assert store.get('bot_setting', 'tg_offset') == 4

    asyncio.run(scenario())


def test_telegram_pagination_callback_retains_message_id(setup_bot):
    _, bot, store, _, _ = setup_bot
    update = {'update_id': 8, 'callback_query': {'id': 'callback', 'from': {'id': 123},
        'message': {'message_id': 42, 'chat': {'id': 123, 'type': 'private'}},
        'data': '/results aaaaaaaa 2'}}
    def telegram(method, body, timeout=15):
        return [update] if method == 'getUpdates' else True
    bot.telegram = Mock(side_effect=telegram)

    async def scenario():
        worker = asyncio.create_task(bot.poll_telegram())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.get('bot_setting', 'tg_offset') == 9:
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        message = store.values('bot_inbox')[0]
        assert message['reply_message_id'] == 42
        assert message['text'] == '/results aaaaaaaa 2'
        assert any(call.args[0] == 'answerCallbackQuery' for call in bot.telegram.call_args_list)

    asyncio.run(scenario())


def test_link_fallbacks_and_wecom_refreshes_expired_token(setup_bot):
    _, bot, _, _, _ = setup_bot
    bot.telegram = Mock()
    item = {'channel': 'tg', 'user': '123', 'text': '完成', 'url': 'https://music.example.com/downloads/test'}
    bot.send(item)
    method, body = bot.telegram.call_args.args
    assert method == 'sendMessage'
    assert body['reply_markup']['inline_keyboard'][0][0]['url'] == item['url']
    bot.corp, bot.agent, bot.secret = 'corp', '100001', 'secret'
    bot.wecom_http = Mock()
    bot.wecom_http.get.return_value.json.return_value = {'access_token': 'access', 'expires_in': 7200}
    bot.wecom_http.post.return_value.json.return_value = {'errcode': 0}
    bot.send({**item, 'channel': 'wecom', 'user': 'alice'})
    body = bot.wecom_http.post.call_args.kwargs['json']
    assert body['msgtype'] == 'news'
    assert body['news']['articles'][0]['url'] == item['url']
    assert body['touser'] == 'alice' and body['agentid'] == 100001
    bot.wecom_http.post.return_value.json.return_value = {'errcode': 42001}
    with pytest.raises(RuntimeError, match='42001'):
        bot.send({**item, 'channel': 'wecom', 'user': 'alice'})
    assert bot.token_until == 0


def test_fail_closed_config_and_utf8_message_limit(setup_bot, monkeypatch):
    _, bot, store, _, _ = setup_bot
    bot.reply({'channel': 'wecom', 'user': 'alice'}, '测试音乐' * 1000)
    messages = store.values('bot_outbox')
    assert ''.join(x['text'] for x in messages) == '测试音乐' * 1000
    assert all(len(x['text'].encode()) <= 1800 for x in messages)
    monkeypatch.setenv('MUSICDL_TG_ALLOWED_USERS', '')
    with pytest.raises(ValueError, match='ALLOWED_USERS'):
        type(bot)(store.get, store.put, store.values, store.remove, bot.search, bot.enqueue, bot.control, bot.links.resolve, bot.log)
    monkeypatch.setenv('MUSICDL_TG_ALLOWED_USERS', '123')
    monkeypatch.setenv('MUSICDL_PUBLIC_URL', 'ftp://music.example.com')
    with pytest.raises(ValueError, match='访问地址'):
        type(bot)(store.get, store.put, store.values, store.remove, bot.search, bot.enqueue, bot.control, bot.links.resolve, bot.log)


def test_web_config_masks_secrets_preserves_blank_and_reloads_after_restart(setup_bot, monkeypatch):
    app, bot, store, audio, track = setup_bot
    link = urlsplit(bot.links.create(completed_job(audio, track)))
    monkeypatch.setattr(BotService, 'start', AsyncMock())
    monkeypatch.setattr(BotService, 'stop', AsyncMock())
    client = TestClient(app)
    config = client.get('/api/bots/config')
    assert 'test-token' not in config.text and 'tg_token' not in config.json()
    assert config.json()['configured']['tg_token'] is True
    assert type(config.json()['link_ttl_seconds']) is int
    changed = client.put('/api/bots/config', json={'tg_token': '', 'tg_allowed_users': '789'})
    assert changed.status_code == 200
    assert app.state.bots is not bot
    assert app.state.bots.tg_token == 'test-token'
    assert app.state.bots.tg_users == {'789'}
    current = app.state.bots
    failed = client.put('/api/bots/config', json={'tg_allowed_users': 'username'})
    assert failed.status_code == 400
    assert app.state.bots is current and current.tg_users == {'789'}
    disabled = client.put('/api/bots/config', json={'tg_enabled': False})
    assert disabled.status_code == 200 and disabled.json()['status']['tg'] is False
    assert client.get(link.path + '?' + link.query).status_code == 200
    assert store.get('bot_setting', 'config')['tg_token'] == 'test-token'
    restored = BotService(store.get, store.put, store.values, store.remove, bot.search,
                          bot.enqueue, bot.control, bot.links.resolve, bot.log)
    assert not restored.enabled and restored.config['tg_token'] == 'test-token'
    cleared = client.put('/api/bots/config', json={'clear_secrets': ['tg_token']})
    assert cleared.status_code == 200
    assert cleared.json()['configured']['tg_token'] is False
    assert client.put('/api/bots/config', json={'tg_enabled': True}).status_code == 400


def test_web_connection_tests_do_not_send_messages_and_redact_failures(setup_bot, monkeypatch):
    app, bot, _, _, _ = setup_bot
    client = TestClient(app)
    telegram = Mock(return_value={'username': 'testbot'})
    monkeypatch.setattr(bot, 'telegram', telegram)
    assert client.post('/api/bots/test', json={'channel': 'tg'}).status_code == 200
    telegram.assert_called_once_with('getMe', {})
    telegram.side_effect = RuntimeError('HTTP 401 test-token https://api.telegram.org/bottest-token/getMe')
    result = client.post('/api/bots/test', json={'channel': 'tg'})
    assert result.status_code == 502 and '401' in result.text
    assert 'test-token' not in result.text and 'https://' not in result.text
    assert 'test-token' not in str(bot.log.call_args)
    assert client.post('/api/bots/test', json={'channel': 'wecom'}).status_code == 400


def test_web_wecom_config_replaces_callback_credentials_without_restart(setup_bot, monkeypatch):
    app, bot, _, _, _ = setup_bot
    monkeypatch.setattr(BotService, 'start', AsyncMock())
    monkeypatch.setattr(BotService, 'stop', AsyncMock())
    client = TestClient(app)
    draft = {'wecom_enabled': True, 'wecom_corp_id': 'corp123', 'wecom_agent_id': '100001',
             'wecom_secret': 'private-secret', 'wecom_allowed_users': 'alice',
             'wecom_token': 'verify-token', 'wecom_aes_key': 'bad-key'}
    assert client.put('/api/bots/config', json=draft).status_code == 400
    assert app.state.bots is bot
    draft['wecom_aes_key'] = base64.b64encode(b'k' * 32).decode().rstrip('=')
    result = client.put('/api/bots/config', json=draft)
    assert result.status_code == 200 and result.json()['status']['wecom'] is True
    assert 'private-secret' not in result.text and draft['wecom_aes_key'] not in result.text
    encrypted, query = encrypt_callback(app.state.bots, b'hello')
    assert client.get('/bot/wecom', params={**query, 'echostr': encrypted}).text == 'hello'


def test_stop_waits_for_old_telegram_long_poll_before_reconfiguration(setup_bot):
    import threading
    _, bot, _, _, _ = setup_bot
    entered, release = threading.Event(), threading.Event()
    def telegram(method, body, timeout=15):
        if method in {'setMyCommands', 'setChatMenuButton'}:
            return True
        assert method == 'getUpdates'
        entered.set()
        release.wait(5)
        return []
    bot.telegram = telegram
    async def check():
        await bot.start()
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(bot.stop())
        await asyncio.sleep(.05)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 2)
    try:
        asyncio.run(check())
    finally:
        release.set()


def test_telegram_project_proxy_follows_changes_and_direct_mode(setup_bot):
    _, bot, _, _, _ = setup_bot
    proxy = ['http://proxy.example:7890']
    bot.project_proxy = lambda: proxy[0]
    bot.config['tg_proxy_mode'] = 'project'
    bot.tg_http = Mock()
    bot.tg_http.post.return_value.json.return_value = {'ok': True, 'result': {}}
    for address in ['http://proxy.example:7890', 'http://new-proxy.example:8080', '']:
        proxy[0] = address
        bot.telegram('getMe', {})
        assert bot.tg_http.post.call_args.kwargs['proxies'] == {'http': address or None, 'https': address or None}
    bot.config['tg_proxy_mode'] = 'direct'
    proxy[0] = 'http://proxy.example:7890'
    bot.telegram('getMe', {})
    assert bot.tg_http.post.call_args.kwargs['proxies'] == {'http': None, 'https': None}


def test_proxy_mode_migration_save_and_restart(setup_bot, monkeypatch):
    app, bot, store, _, _ = setup_bot
    monkeypatch.setattr(BotService, 'start', AsyncMock())
    monkeypatch.setattr(BotService, 'stop', AsyncMock())
    client = TestClient(app)
    store.put('bot_setting', 'config', {'tg_proxy': 'http://old-proxy.example:7890'})
    def restore():
        return BotService(store.get, store.put, store.values, store.remove, bot.search,
                          bot.enqueue, bot.control, bot.links.resolve, bot.log,
                          project_proxy=lambda: 'http://project-proxy.example:8080')
    legacy = restore()
    assert legacy.config['tg_proxy_mode'] == 'custom'
    assert legacy.telegram_proxy() == 'http://old-proxy.example:7890'
    response = client.put('/api/bots/config', json={'tg_proxy_mode': 'project'})
    assert response.status_code == 200
    assert response.json()['tg_proxy_mode'] == 'project'
    restored = restore()
    assert restored.telegram_proxy() == 'http://project-proxy.example:8080'
    assert client.put('/api/bots/config', json={'tg_proxy_mode': 'invalid'}).status_code == 400
    assert store.get('bot_setting', 'config')['tg_proxy_mode'] == 'project'


@pytest.mark.parametrize('address, expected', [
    ('http://192.168.1.20:8765', 'http://192.168.1.20:8765'),
    ('192.168.1.20:8765', 'http://192.168.1.20:8765'),
    ('  http://music.example.com:8080/  ', 'http://music.example.com:8080'),
    ('https://music.example.com/', 'https://music.example.com'),
    ('[2001:db8::1]:8765', 'http://[2001:db8::1]:8765'),
])
def test_http_and_ip_origin_save_and_signed_download(setup_bot, monkeypatch, address, expected):
    app, bot, store, audio, track = setup_bot
    monkeypatch.setattr(BotService, 'start', AsyncMock())
    monkeypatch.setattr(BotService, 'stop', AsyncMock())
    client = TestClient(app)
    response = client.put('/api/bots/config', json={'public_url': address})
    assert response.status_code == 200, response.text
    assert response.json()['public_url'] == expected
    assert response.json()['callback_url'] == expected + '/bot/wecom'
    assert store.get('bot_setting', 'config')['public_url'] == expected
    link = app.state.bots.links.create(completed_job(audio, track))
    assert link.startswith(expected + '/downloads/')
    parsed = urlsplit(link)
    assert client.get(parsed.path + '?' + parsed.query).content == audio.read_bytes()


@pytest.mark.parametrize('address', ['ftp://music.example.com', 'http://',
    'http://user:password@music.example.com', 'http://music.example.com/path',
    'http://music.example.com?x=1', 'http://music.example.com#test',
    'http://192.168.1.20:bad', 'http://192.168.1.20:65536', 'http://bad host:8765'])
def test_invalid_origin_keeps_saved_service(setup_bot, address):
    app, bot, store, _, _ = setup_bot
    response = TestClient(app).put('/api/bots/config', json={'public_url': address})
    assert response.status_code == 400
    assert '访问地址' in response.json()['detail']
    assert app.state.bots is bot
    assert store.get('bot_setting', 'config') is None


def test_draft_test_uses_current_token_without_saving_or_enabling(setup_bot, monkeypatch):
    app, bot, store, _, _ = setup_bot
    before = copy.deepcopy(store.records)
    start, send = AsyncMock(), Mock()
    monkeypatch.setattr(BotService, 'start', start)
    monkeypatch.setattr(BotService, 'send', send)
    def check(candidate, method, body, timeout=15):
        assert candidate.tg_token == 'draft-token'
        assert candidate.telegram_proxy() == 'http://draft-proxy.example:8080'
        assert method == 'getMe' and body == {}
        return {'username': 'draftbot'}
    monkeypatch.setattr(BotService, 'telegram', check)
    client = TestClient(app)
    draft = {'public_url': '', 'tg_enabled': False, 'tg_token': 'draft-token',
             'tg_allowed_users': '', 'tg_proxy_mode': 'custom', 'tg_proxy': 'http://draft-proxy.example:8080'}
    response = client.post('/api/bots/test', json={'channel': 'tg', 'config': draft})
    assert response.status_code == 200, response.text
    assert store.records == before and app.state.bots is bot
    assert bot.tg_token == 'test-token'
    start.assert_not_called()
    send.assert_not_called()
    # Saving still requires an address and an allowlist before command intake starts.
    assert client.put('/api/bots/config', json={**draft, 'tg_enabled': True}).status_code == 400


def test_draft_test_redacts_unsaved_credentials_and_honors_clear(setup_bot, monkeypatch):
    app, bot, store, _, _ = setup_bot
    def fail(candidate, *args):
        raise RuntimeError('HTTP 401 draft-secret https://api.telegram.org/botdraft-secret/getMe')
    monkeypatch.setattr(BotService, 'telegram', fail)
    client = TestClient(app)
    response = client.post('/api/bots/test', json={'channel': 'tg', 'config': {'tg_token': 'draft-secret'}})
    assert response.status_code == 502
    assert 'draft-secret' not in response.text and 'draft-secret' not in str(bot.log.call_args)
    response = client.post('/api/bots/test', json={'channel': 'tg', 'config': {'clear_secrets': ['tg_token']}})
    assert response.status_code == 400 and 'Token' in response.text
    assert store.get('bot_setting', 'config') is None


def test_wecom_draft_connection_needs_no_callback_config(setup_bot, monkeypatch):
    app, bot, store, _, _ = setup_bot
    def check(candidate):
        assert candidate.corp == 'draft-corp' and candidate.secret == 'draft-secret'
        assert candidate.crypt is None
        return 'access-token'
    monkeypatch.setattr(BotService, 'wecom_access', check)
    response = TestClient(app).post('/api/bots/test', json={'channel': 'wecom', 'config': {
        'wecom_corp_id': 'draft-corp', 'wecom_agent_id': '100001', 'wecom_secret': 'draft-secret',
        'public_url': '', 'wecom_enabled': False, 'wecom_token': '', 'wecom_aes_key': ''}})
    assert response.status_code == 200, response.text
    assert app.state.bots is bot and store.get('bot_setting', 'config') is None


def test_invalid_draft_proxy_does_not_echo_credentials(setup_bot):
    app, bot, _, _, _ = setup_bot
    response = TestClient(app).post('/api/bots/test', json={'channel': 'tg', 'config': {
        'tg_proxy_mode': 'custom', 'tg_proxy': 'http://draft-user:draft-password@proxy.example：8080'}})
    assert response.status_code == 400
    assert 'draft-password' not in response.text and 'draft-user' not in response.text
    assert 'draft-password' not in str(bot.log.call_args)
