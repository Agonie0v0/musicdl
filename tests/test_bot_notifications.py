"""Bot trust boundaries, encrypted callbacks, command ownership and recovery."""
import asyncio
import base64
import copy
import hashlib
import struct
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from bot_notifications import DownloadLinks, WecomCrypt, install


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
    job = completed_job(audio, track)
    store.put('download', job['id'], job)
    store.put('bot_task', job['id'], {'id': job['id'], 'owner': 'tg:123', 'channel': 'tg', 'user': '123', 'notified': None})
    bot.send = Mock(side_effect=requests.ConnectionError('offline'))

    async def scenario():
        worker = asyncio.create_task(bot.deliveries())
        for _ in range(100):
            await asyncio.sleep(.01)
            if store.values('bot_outbox') and store.values('bot_outbox')[0]['attempts'] == 1:
                break
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert len(store.values('bot_outbox')) == 1
        assert store.get('bot_task', job['id'])['notified'] == 'complete'
        bot.enqueue.assert_not_called()
        item = store.values('bot_outbox')[0]
        assert item['url'] and item['job_id'] == job['id']
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


def test_both_channels_send_links_only_and_wecom_refreshes_expired_token(setup_bot):
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
    monkeypatch.setenv('MUSICDL_PUBLIC_URL', 'http://music.example.com')
    with pytest.raises(ValueError, match='PUBLIC_URL'):
        type(bot)(store.get, store.put, store.values, store.remove, bot.search, bot.enqueue, bot.control, bot.links.resolve, bot.log)
