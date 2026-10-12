"""Private chat commands and durable link-only delivery for WeCom and Telegram."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response


HELP = ('命令：\n搜索 歌名 歌手（/search）\n下载 搜索编号 序号（/download）\n'
        '状态（/status）\n取消 任务编号（/cancel）\n继续 任务编号（/resume）\n'
        '链接 任务编号（/link）\n帮助（/help）\n仅支持私聊；下载完成后发送原文件链接。')
ALIASES = {'搜索': 'search', '下载': 'download', '状态': 'status', '取消': 'cancel',
           '继续': 'resume', '链接': 'link', '帮助': 'help', 'start': 'help'}


def xml_fields(raw):
    # Reject DTD/entity declarations before ElementTree expands anything.
    if len(raw) > 65536 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('invalid XML')
    node = ET.fromstring(raw)
    if node.tag != 'xml':
        raise ValueError('invalid XML root')
    return {child.tag: child.text or '' for child in node}


class WecomCrypt:
    def __init__(self, token, aes_key, corp_id):
        if len(aes_key) != 43:
            raise ValueError('MUSICDL_WECOM_AES_KEY 必须为 43 位 EncodingAESKey')
        self.key = base64.b64decode(aes_key + '=', validate=True)
        if len(self.key) != 32:
            raise ValueError('无效的 EncodingAESKey')
        self.token, self.corp_id = token, corp_id.encode()

    def decrypt(self, encrypted, signature, timestamp, nonce):
        if not all(isinstance(x, str) and x for x in (encrypted, signature, timestamp, nonce)):
            raise ValueError('missing callback fields')
        expected = hashlib.sha1(''.join(sorted([self.token, timestamp, nonce, encrypted])).encode()).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError('invalid callback signature')
        ciphertext = base64.b64decode(encrypted, validate=True)
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.key[:16])).decryptor()
        plain = decryptor.update(ciphertext) + decryptor.finalize()
        pad = plain[-1] if plain else 0
        if not 1 <= pad <= 32 or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError('invalid padding')
        plain = plain[:-pad]
        if len(plain) < 20:
            raise ValueError('invalid callback body')
        length = struct.unpack('!I', plain[16:20])[0]
        if length > len(plain) - 20 or plain[20 + length:] != self.corp_id:
            raise ValueError('invalid callback receiver')
        return plain[20:20 + length]


class DownloadLinks:
    def __init__(self, get, put, resolve, base_url, ttl):
        self.get, self.put, self.resolve = get, put, resolve
        self.base_url, self.ttl = base_url.rstrip('/'), ttl
        key = get('bot_setting', 'link_key')
        if not key:
            key = secrets.token_hex(32)
            put('bot_setting', 'link_key', key)
        self.key = bytes.fromhex(key)

    def signature(self, ident, expires):
        return hmac.new(self.key, f'{ident}:{expires}'.encode(), hashlib.sha256).hexdigest()

    def create(self, job):
        if job.get('status') != 'complete' or not job.get('file'):
            raise HTTPException(409, '任务尚未完成或文件已删除')
        target = self.resolve(job['file'])
        stat = target.stat()
        ident, expires = secrets.token_hex(16), int(time.time()) + self.ttl
        self.put('bot_link', ident, {'id': ident, 'path': job['file'], 'expires': expires,
                                    'size': stat.st_size, 'mtime': stat.st_mtime_ns})
        return f'{self.base_url}/downloads/{ident}?' + urlencode(
            {'expires': expires, 'signature': self.signature(ident, expires)})

    def verify(self, ident, expires, signature):
        if not re.fullmatch(r'[a-f0-9]{32}', ident) or not re.fullmatch(r'[a-f0-9]{64}', signature):
            raise HTTPException(403, '下载链接无效')
        if expires <= int(time.time()) or not hmac.compare_digest(self.signature(ident, expires), signature):
            raise HTTPException(403, '下载链接无效或已过期，请向机器人重新获取')
        record = self.get('bot_link', ident)
        if not record or record['expires'] != expires:
            raise HTTPException(403, '下载链接无效')
        target = self.resolve(record['path'])
        stat = target.stat()
        if (stat.st_size, stat.st_mtime_ns) != (record['size'], record['mtime']):
            raise HTTPException(410, '文件已变化，请重新获取链接')
        return target


class BotService:
    def __init__(self, get, put, values, remove, search, enqueue, control, resolve, log):
        self.get, self.put, self.values, self.remove = get, put, values, remove
        self.search, self.enqueue, self.control, self.log = search, enqueue, control, log
        self.tg_token = os.environ.get('MUSICDL_TG_TOKEN', '').strip()
        self.tg_users = self.env_set('MUSICDL_TG_ALLOWED_USERS')
        self.corp = os.environ.get('MUSICDL_WECOM_CORP_ID', '').strip()
        self.agent = os.environ.get('MUSICDL_WECOM_AGENT_ID', '').strip()
        self.secret = os.environ.get('MUSICDL_WECOM_SECRET', '').strip()
        self.wecom_users = self.env_set('MUSICDL_WECOM_ALLOWED_USERS')
        self.crypt = None
        self.enabled = bool(self.tg_token or self.corp or self.agent or self.secret)
        base = os.environ.get('MUSICDL_PUBLIC_URL', '').strip().rstrip('/')
        if self.enabled:
            url = urlsplit(base)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
                raise ValueError('机器人需要 MUSICDL_PUBLIC_URL=https://域名（不含路径、凭据或查询参数）')
            if self.tg_token and not self.tg_users:
                raise ValueError('请配置 MUSICDL_TG_ALLOWED_USERS（Telegram 数字用户 ID）')
            if self.tg_token and any(not re.fullmatch(r'[1-9][0-9]*', u) for u in self.tg_users):
                raise ValueError('MUSICDL_TG_ALLOWED_USERS 必须是数字用户 ID')
            if self.corp or self.agent or self.secret:
                if not self.corp or not self.agent.isdigit() or not self.secret or not self.wecom_users:
                    raise ValueError('企业微信需要 CorpID、AgentID、Secret 和用户白名单')
                token = os.environ.get('MUSICDL_WECOM_TOKEN', '').strip()
                if not token:
                    raise ValueError('请配置 MUSICDL_WECOM_TOKEN')
                self.crypt = WecomCrypt(token, os.environ.get('MUSICDL_WECOM_AES_KEY', '').strip(), self.corp)
        ttl = int(os.environ.get('MUSICDL_LINK_TTL_SECONDS', '86400'))
        if not 60 <= ttl <= 604800:
            raise ValueError('下载链接有效期须为 60–604800 秒')
        self.links = DownloadLinks(get, put, resolve, base, ttl) if self.enabled else None
        self.sources = list(filter(None, (s.strip() for s in os.environ.get('MUSICDL_BOT_SOURCES', '').split(','))))
        self.tg_http, self.wecom_http = requests.Session(), requests.Session()
        proxy = os.environ.get('MUSICDL_TG_PROXY', '').strip()
        if proxy:
            self.tg_http.trust_env = False
            self.tg_http.proxies = {'http': proxy, 'https': proxy}
        self.access_token, self.token_until = '', 0
        self.tasks = []
        self.pending = asyncio.Queue(maxsize=100)
        self.seen_rate = {}

    @staticmethod
    def env_set(name):
        return {x.strip() for x in os.environ.get(name, '').split(',') if x.strip()}

    def allowed(self, channel, user):
        return user in (self.tg_users if channel == 'tg' else self.wecom_users)

    def reply(self, message, text, url=None, buttons=None, job_id=None):
        # WeCom text messages have a byte limit; split without cutting UTF-8.
        chunks, current = [], ''
        for line in text.splitlines(keepends=True):
            for char in line:
                if len((current + char).encode()) > 1800:
                    chunks.append(current)
                    current = ''
                current += char
        chunks.append(current)
        for index, chunk in enumerate(chunks):
            ident = secrets.token_hex(16)
            last = index == len(chunks) - 1
            self.put('bot_outbox', ident, {'id': ident, 'channel': message['channel'], 'user': message['user'],
                                         'text': chunk, 'url': url if last else None, 'buttons': buttons if last else None,
                                         'job_id': job_id if last else None, 'created': time.time_ns(), 'attempts': 0, 'next': 0})

    def accept(self, channel, user, message_id, text):
        if not self.allowed(channel, user):
            return False
        ident = hashlib.sha256(f'{channel}:{user}:{message_id}'.encode()).hexdigest()
        if self.get('bot_inbox', ident):
            return True
        key, now = f'{channel}:{user}', time.time()
        last = self.seen_rate.get(key, 0)
        if self.pending.full() or now - last < 1:
            raise HTTPException(429, '命令过于频繁，请稍后重试')
        self.seen_rate[key] = now
        message = {'id': ident, 'channel': channel, 'user': user, 'text': text[:500],
                   'done': False, 'created': int(now)}
        self.put('bot_inbox', ident, message)
        self.pending.put_nowait(message)
        return True

    async def command(self, message):
        text = message['text'].strip()
        command, _, arg = text.partition(' ')
        command = ALIASES.get(command.lstrip('/').split('@')[0].lower(), command.lstrip('/').lower())
        owner = f'{message["channel"]}:{message["user"]}'
        if command == 'help':
            self.reply(message, HELP)
        elif command == 'search':
            if not 1 <= len(arg.strip()) <= 200:
                raise HTTPException(400, '请输入 1–200 字的搜索关键词：搜索 歌名 歌手')
            self.reply(message, '正在搜索，请稍候。')
            tracks, partial = await self.search(arg.strip(), self.sources)
            # Cap chat output; the full multi-source result remains in the web UI.
            tracks = [t for t in tracks if not t.get('episodes')][:10]
            if not tracks:
                self.reply(message, '没有可下载结果，请换关键词或检查音源设置。')
                return
            search_id = message['id'][:8]
            self.put('bot_search', f'{owner}:{search_id}', {'id': f'{owner}:{search_id}', 'expires': int(time.time()) + 1800, 'tracks': tracks})
            lines = [f'搜索编号：{search_id}（30 分钟内有效）']
            for i, track in enumerate(tracks, 1):
                lines.append(f'{i}. {track["title"][:70]} · {track["artist"][:50]} · {track["source"]} · {track["qualities"][0]["label"]}')
            lines.append(f'下载：下载 {search_id} 1')
            if partial:
                lines.append('部分音源失败或超时，以上为已获得的结果。')
            buttons = [[{'text': f'下载 {i}', 'callback_data': f'/download {search_id} {i}'}] for i in range(1, len(tracks) + 1)]
            self.reply(message, '\n'.join(lines), buttons=buttons)
        elif command == 'download':
            args = arg.split()
            if len(args) != 2 or not re.fullmatch(r'[a-f0-9]{8}', args[0]) or not args[1].isdigit():
                raise HTTPException(400, '格式：下载 搜索编号 序号')
            result = self.get('bot_search', f'{owner}:{args[0]}')
            index = int(args[1]) - 1
            if not result or result['expires'] <= time.time() or not 0 <= index < len(result['tracks']):
                raise HTTPException(400, '搜索结果已过期或序号无效，请重新搜索')
            active = [t for t in self.values('bot_task') if t['owner'] == owner and
                      (self.get('download', t['id']) or {}).get('status') in {'queued', 'downloading', 'resolving', 'enriching'}]
            if len(active) >= 5:
                raise HTTPException(429, '最多同时保留 5 个活动任务，请等待或取消旧任务')
            ident = message['id'][:32]
            job = self.get('download', ident)
            if not job:
                job = await asyncio.to_thread(self.enqueue, result['tracks'][index], ident)
            self.put('bot_task', ident, {'id': ident, 'owner': owner, 'channel': message['channel'], 'user': message['user'], 'notified': None})
            self.reply(message, f'已加入下载队列：{job["track"]["title"]}\n任务编号：{ident}')
        elif command == 'status':
            jobs = [(t, self.get('download', t['id'])) for t in self.values('bot_task') if t['owner'] == owner][:10]
            lines = [f'{j["track"]["title"]} · {j["status"]}\n任务编号：{t["id"]}' for t, j in jobs if j]
            self.reply(message, '\n'.join(lines) or '暂无任务。')
        elif command in {'link', 'cancel', 'resume'}:
            ident = arg.strip()
            owned = self.get('bot_task', ident) if re.fullmatch(r'[a-f0-9]{32}', ident) else None
            if not owned or owned['owner'] != owner:
                raise HTTPException(404, '任务不存在或不属于你')
            job = self.get('download', ident)
            if not job:
                raise HTTPException(404, '任务记录已删除')
            if command == 'link':
                url = await asyncio.to_thread(self.links.create, job)
                self.reply(message, f'{job["track"]["title"]}\n下载原文件（链接持有者可下载，请勿转发）', url=url, job_id=ident)
            else:
                await asyncio.to_thread(self.control, ident, command)
                if command == 'resume':
                    owned['notified'] = None
                    self.put('bot_task', ident, owned)
                self.reply(message, '已取消任务。' if command == 'cancel' else '已继续下载。')
        else:
            self.reply(message, HELP)

    async def commands(self):
        while True:
            message = await self.pending.get()
            try:
                if self.allowed(message['channel'], message['user']):
                    await self.command(message)
            except asyncio.CancelledError:
                # Keep the durable inbox pending so shutdown never loses a command.
                raise
            except HTTPException as error:
                self.reply(message, str(error.detail))
            except Exception as error:
                self.log('error', f'机器人命令失败：{type(error).__name__}', category='bot')
                self.reply(message, '命令执行失败，请查看服务运行日志后重试。')
            message['done'] = True
            self.put('bot_inbox', message['id'], message)
            self.pending.task_done()

    def telegram(self, method, body, timeout=15):
        response = self.tg_http.post(f'https://api.telegram.org/bot{self.tg_token}/{method}', json=body, timeout=timeout)
        data = response.json()
        if not data.get('ok'):
            raise RuntimeError(f'Telegram API error {data.get("error_code")}')
        return data['result']

    async def poll_telegram(self):
        while True:
            try:
                offset = self.get('bot_setting', 'tg_offset', 0)
                updates = await asyncio.to_thread(self.telegram, 'getUpdates',
                    {'offset': offset, 'timeout': 20, 'allowed_updates': ['message', 'callback_query']}, 30)
                for update in updates:
                    callback = update.get('callback_query')
                    message = (callback or {}).get('message') if callback else update.get('message')
                    sender = callback.get('from', {}) if callback else (message or {}).get('from', {})
                    user = str(sender.get('id', ''))
                    private = bool(message and message.get('chat', {}).get('type') == 'private' and
                                   str(message['chat'].get('id')) == user)
                    text = callback.get('data', '') if callback else (message or {}).get('text', '')
                    if private and text:
                        self.accept('tg', user, str(update['update_id']), text)
                    self.put('bot_setting', 'tg_offset', update['update_id'] + 1)
                    if callback and private and self.allowed('tg', user):
                        try:
                            await asyncio.to_thread(self.telegram, 'answerCallbackQuery', {'callback_query_id': callback['id']})
                        except Exception:
                            pass
            except Exception as error:
                self.log('warning', f'Telegram 接收失败，将重试：{type(error).__name__}（检查代理、Token 和 webhook 是否关闭）', category='bot')
                await asyncio.sleep(5)

    def send(self, item):
        if item['channel'] == 'tg':
            body = {'chat_id': item['user'], 'text': item['text'], 'link_preview_options': {'is_disabled': True}}
            if item.get('url'):
                body['reply_markup'] = {'inline_keyboard': [[{'text': '下载到手机', 'url': item['url']}]]}
            elif item.get('buttons'):
                body['reply_markup'] = {'inline_keyboard': item['buttons']}
            self.telegram('sendMessage', body)
            return
        if time.time() >= self.token_until:
            response = self.wecom_http.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken',
                params={'corpid': self.corp, 'corpsecret': self.secret}, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('errcode', 0) or not data.get('access_token'):
                raise RuntimeError(f'WeCom token error {data.get("errcode")}')
            self.access_token = data['access_token']
            self.token_until = time.time() + max(1, int(data['expires_in']) - 120)
        body = {'touser': item['user'], 'agentid': int(self.agent), 'enable_duplicate_check': 1, 'duplicate_check_interval': 1800}
        if item.get('url'):
            body.update(msgtype='news', news={'articles': [{'title': '下载到手机', 'description': item['text'], 'url': item['url']}]})
        else:
            body.update(msgtype='text', text={'content': item['text']})
        response = self.wecom_http.post('https://qyapi.weixin.qq.com/cgi-bin/message/send',
            params={'access_token': self.access_token}, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('errcode', 0) or data.get('invaliduser'):
            if data.get('errcode') in {40014, 42001}:
                self.token_until = 0
            raise RuntimeError(f'WeCom send error {data.get("errcode")}')

    async def deliveries(self):
        while True:
            try:
                for task in self.values('bot_task'):
                    if not self.allowed(task['channel'], task['user']):
                        continue
                    job = self.get('download', task['id'])
                    if not job or job['status'] not in {'complete', 'failed', 'paused', 'cancelled'} or task['notified'] == job['status']:
                        continue
                    if job['status'] == 'complete':
                        try:
                            url = await asyncio.to_thread(self.links.create, job)
                            text = f'下载完成：{job["track"]["title"]}\n任务编号：{job["id"]}\n链接有效期 {self.links.ttl // 60} 分钟；过期后发送：链接 {job["id"]}\n链接持有者可下载，请勿转发。'
                        except (HTTPException, OSError):
                            url, text = None, f'文件已删除或变化，请重新下载。任务编号：{job["id"]}'
                    else:
                        url, text = None, f'{job["track"]["title"]}：{job["status"]}\n任务编号：{job["id"]}\n可发送：继续 {job["id"]}'
                    self.reply(task, text, url=url, job_id=job['id'] if url else None)
                    task['notified'] = job['status']
                    self.put('bot_task', task['id'], task)
                for item in sorted(self.values('bot_outbox'), key=lambda x: x['created']):
                    if item['next'] > time.time():
                        continue
                    if not self.allowed(item['channel'], item['user']):
                        self.remove('bot_outbox', item['id'])
                        continue
                    if item.get('url') and item.get('job_id') and int(parse_qs(urlsplit(item['url']).query)['expires'][0]) <= time.time() + 60:
                        # A long outage must not deliver an already expired link.
                        try:
                            item['url'] = await asyncio.to_thread(self.links.create, self.get('download', item['job_id']) or {})
                        except (HTTPException, OSError):
                            item.update(url=None, job_id=None, text='文件已删除或不可访问，请重新下载。')
                    try:
                        await asyncio.to_thread(self.send, item)
                        self.remove('bot_outbox', item['id'])
                        await asyncio.sleep(1)
                    except Exception as error:
                        item['attempts'] += 1
                        item['next'] = int(time.time()) + min(3600, 5 * 2 ** min(item['attempts'], 10))
                        self.put('bot_outbox', item['id'], item)
                        self.log('warning', f'机器人通知失败，稍后重试：{type(error).__name__}', category='bot')
                # Keep replay protection for seven days; prune expired link/search records.
                now = time.time()
                for kind in ('bot_link', 'bot_search', 'bot_inbox'):
                    for record in self.values(kind):
                        expired = record.get('expires', now + 1) <= now if kind != 'bot_inbox' else record['done'] and record['created'] < now - 604800
                        if expired:
                            self.remove(kind, record['id'])
            except Exception as error:
                self.log('error', f'机器人通知处理失败：{type(error).__name__}', category='bot')
            await asyncio.sleep(3)

    async def start(self):
        if not self.enabled:
            return
        self.tasks = [asyncio.create_task(self.commands()), asyncio.create_task(self.deliveries())]
        for message in reversed(self.values('bot_inbox')):
            if not message['done']:
                await self.pending.put(message)
        if self.tg_token:
            self.tasks.append(asyncio.create_task(self.poll_telegram()))
        self.log('info', '机器人已启用：仅私聊、白名单用户、下载链接交付', category='bot')

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tg_http.close()
        self.wecom_http.close()


def install(app, **services):
    bot = BotService(**services)
    router = APIRouter()

    @router.api_route('/downloads/{ident}', methods=['GET', 'HEAD'])
    def download(ident: str, expires: int, signature: str):
        if not bot.links:
            raise HTTPException(404, '机器人未启用')
        target = bot.links.verify(ident, expires, signature)
        return FileResponse(target, filename=target.name, headers={
            'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/bot/wecom')
    def verify_wecom(msg_signature: str, timestamp: str, nonce: str, echostr: str):
        if not bot.crypt:
            raise HTTPException(404, '企业微信未启用')
        try:
            content = bot.crypt.decrypt(echostr, msg_signature, timestamp, nonce)
        except (ValueError, IndexError):
            raise HTTPException(403, '回调验证失败')
        return Response(content, media_type='text/plain')

    @router.post('/bot/wecom')
    async def receive_wecom(request: Request, msg_signature: str, timestamp: str, nonce: str):
        if not bot.crypt:
            raise HTTPException(404, '企业微信未启用')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise HTTPException(413, '消息过大')
        try:
            if abs(int(timestamp) - time.time()) > 600:
                raise ValueError('stale callback')
            fields = xml_fields(bytes(raw))
            fields = xml_fields(bot.crypt.decrypt(fields['Encrypt'], msg_signature, timestamp, nonce))
            if fields.get('ToUserName') != bot.corp or fields.get('AgentID') != bot.agent:
                raise ValueError('wrong application')
            if fields.get('MsgType') == 'text':
                if not fields.get('MsgId') or not fields.get('FromUserName'):
                    raise ValueError('missing message identity')
                bot.accept('wecom', fields['FromUserName'], fields['MsgId'], fields.get('Content', ''))
        except (ValueError, KeyError, ET.ParseError, IndexError):
            raise HTTPException(403, '回调验证失败')
        # Acknowledge before search/download; replies use the application send API.
        return Response('success', media_type='text/plain')

    app.include_router(router)
    app.router.add_event_handler('startup', bot.start)
    # Stop command intake before the shared engine pauses workers on shutdown.
    app.router.on_shutdown.insert(0, bot.stop)
    app.state.bots = bot
    return bot
