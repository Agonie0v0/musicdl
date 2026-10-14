"""Private chat commands, source search cards and durable file delivery."""
import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import struct
import time
import unicodedata
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from error_messages import explain_error
from telegram_local import LocalBotAPI, LOCAL_URL


HELP = ('musicdl · 功能菜单\n\n'
        '搜索音乐\n直接发送歌名或歌手，例如：稻香 周杰伦\n也可发送 /search 稻香 周杰伦\n\n'
        '试听与下载\n切换音源查看结果，点击编号下载。完成后收到音频或原文件；超限时提供下载链接。\n\n'
        '我的任务\n/status 查看进度，并通过按钮继续、取消或获取下载链接。\n\n'
        '翻页：下一页 / 上一页 / 第2页。下载：下载 1。\n\n'
        '/menu 随时打开功能菜单。Telegram 仅支持白名单用户私聊；企业微信留空白名单时允许应用可见成员。')
ALIASES = {'搜索': 'search', '下载': 'download', '状态': 'status', '取消': 'cancel',
           '继续': 'resume', '链接': 'link', '帮助': 'help', '菜单': 'help', 'menu': 'help', 'start': 'help',
           '下一页': 'next', '上一页': 'prev', 'next': 'next', 'prev': 'prev', '翻页': 'results'}
TG_COMMANDS = [{'command': key, 'description': value} for key, value in (
    ('start', '打开功能菜单'), ('menu', '功能菜单'), ('search', '搜索音乐：/search 歌名 歌手'),
    ('status', '我的任务与下载进度'), ('download', '下载指定搜索结果'),
    ('link', '获取任务下载链接'), ('file', '重新发送已完成文件'), ('cancel', '取消下载任务'), ('resume', '继续下载任务'), ('help', '使用帮助'))]
STATUS_LABELS = {'queued': '排队中', 'resolving': '解析中', 'downloading': '下载中', 'enriching': '整理文件中',
                 'complete': '下载完成', 'failed': '下载失败', 'paused': '已暂停', 'cancelled': '已取消'}
SOURCE_LABELS = {'migu': '咪咕', 'netease': '网易云', 'qq': 'QQ音乐', 'kuwo': '酷我', 'qianqian': '千千',
                 'KugouMusicClient': '酷狗', 'MiguMusicClient': '咪咕', 'NeteaseMusicClient': '网易云',
                 'QQMusicClient': 'QQ音乐', 'KuwoMusicClient': '酷我', 'QianqianMusicClient': '千千'}
CONFIG_DEFAULTS = {'public_url': '', 'link_ttl_seconds': 86400, 'bot_sources': '',
                   'tg_token': '', 'tg_allowed_users': '', 'tg_proxy': '', 'tg_api_url': 'https://api.telegram.org',
                   'tg_local_enabled': False, 'tg_local_api_id': '', 'tg_local_api_hash': '',
                   'wecom_corp_id': '', 'wecom_agent_id': '', 'wecom_secret': '',
                   'wecom_token': '', 'wecom_aes_key': '', 'wecom_allowed_users': '',
                   'discord_webhook': '', 'feishu_webhook': '', 'feishu_sign_secret': '',
                   'dingtalk_webhook': '', 'dingtalk_sign_secret': ''}
WEBHOOK_CHANNELS = ('discord', 'feishu', 'dingtalk')
SECRET_FIELDS = {'tg_token', 'tg_proxy', 'tg_local_api_hash', 'wecom_secret', 'wecom_token', 'wecom_aes_key',
                 *(channel + '_webhook' for channel in WEBHOOK_CHANNELS), 'feishu_sign_secret', 'dingtalk_sign_secret'}


def public_origin(value):
    base = value.strip()
    if base and '://' not in base:
        base = 'http://' + base
    base = base.rstrip('/')
    try:
        url = urlsplit(base)
        if (url.scheme not in {'http', 'https'} or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or url.path
                or any(char.isspace() for char in base) or '\\' in base or url.port == 0):
            raise ValueError()
    except ValueError:
        raise ValueError('访问地址必须是 HTTP / HTTPS 域名或 IP，可带端口，不能包含路径、账号或查询参数') from None
    return base


def concise(value, limit=32):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def button_rows(buttons, columns=3):
    return [buttons[index:index + columns] for index in range(0, len(buttons), columns)]


def search_page_size(channel):
    return 8 if channel == 'tg' else 5


def valid_size(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def format_size(value):
    if not valid_size(value):
        return '大小未知'
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} B' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def track_size(track):
    if valid_size(track.get('size_bytes')) and track['size_bytes'] > 0:
        return format_size(track['size_bytes'])
    label = str(track.get('size_label') or '').strip()
    # Provider placeholders and zero estimates do not describe a real file.
    if re.fullmatch(r'\d+(?:\.\d+)?\s*(?:[KMGT]i?B|bytes?|B)', label, re.I) and float(re.match(r'[\d.]+', label)[0]) > 0:
        return concise(label, 24)
    return '大小未知'


def job_progress(job):
    downloaded, total = job.get('bytes'), job.get('total_bytes')
    if not valid_size(downloaded):
        return '文件大小：' + track_size(job['track'])
    if valid_size(total) and total > 0:
        return f'{format_size(downloaded)} / {format_size(total)} · {min(100, downloaded / total * 100):.1f}%'
    if job['status'] == 'complete':
        return '文件大小：' + format_size(downloaded)
    return f'已下载 {format_size(downloaded)} · 总大小未知'


def telegram_size_error(error):
    return bool(re.search(r'error 413\b|file (?:is )?too (?:big|large)|(?:request entity|request payload|payload) too large',
                          str(error).replace('_', ' '), re.I))


def quality_rank(quality):
    codec = str(quality.get('codec') or '').lower()
    lossless = bool(quality.get('lossless') or codec in {'flac', 'wav', 'alac', 'ape', 'wv', 'tta', 'dsf', 'dff'}
                    or '无损' in quality.get('label', '') or ':flac:' in quality.get('id', ''))
    bitrate = quality.get('bitrate')
    return lossless, bitrate if valid_size(bitrate) else 0


def rank_tracks(tracks, keyword):
    def normalize(value):
        return ''.join(c for c in unicodedata.normalize('NFKC', str(value)).casefold() if c.isalnum())
    terms = [normalize(term) for term in keyword.split() if normalize(term)]
    ranked = []
    for track in tracks:
        if track.get('episodes'):
            continue
        qualities = sorted(track.get('qualities') or [], key=quality_rank, reverse=True)
        if not qualities:
            continue
        title, artist = normalize(track['title']), normalize(track.get('artist', ''))
        matched = sum(term in title + artist for term in terms)
        exact = any(title == term for term in terms)
        similarity = max((SequenceMatcher(None, term, title).ratio() for term in terms), default=0)
        lossless, bitrate = quality_rank(qualities[0])
        ranked.append(((-matched, -exact, -lossless, -similarity, -bitrate), {**track, 'qualities': qualities}))
    # Stable ties preserve provider order. Rank the full result before the cap.
    return [track for _, track in sorted(ranked, key=lambda item: item[0])]


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
    def __init__(self, get, put, values, remove, search, enqueue, control, resolve, log, config=None, project_proxy=None, test_channel=None, source_catalog=None, search_progress=None, local_api=None):
        self.get, self.put, self.values, self.remove = get, put, values, remove
        self.search, self.enqueue, self.control, self.log = search, enqueue, control, log
        self.search_progress = search_progress
        self.resolve, self.source_catalog = resolve, source_catalog
        self.config = {key: os.environ.get('MUSICDL_' + key.upper(), default) for key, default in CONFIG_DEFAULTS.items()}
        self.config.update(get('bot_setting', 'config', {}) if config is None else config)
        cfg = self.config
        self.local_api = local_api or LocalBotAPI(log)
        cfg['tg_local_enabled'] = str(cfg['tg_local_enabled']).lower() in {'true', '1'}
        self.project_proxy = project_proxy or (lambda: '')
        cfg.setdefault('tg_proxy_mode', 'custom' if cfg['tg_proxy'] else 'direct')
        if cfg['tg_proxy_mode'] not in {'direct', 'project', 'custom'}:
            raise ValueError('无效的 Telegram 代理模式')
        cfg.setdefault('tg_enabled', bool(cfg['tg_token']))
        cfg.setdefault('wecom_enabled', bool(cfg['wecom_corp_id'] or cfg['wecom_agent_id'] or cfg['wecom_secret']))
        for channel in WEBHOOK_CHANNELS:
            cfg.setdefault(channel + '_enabled', bool(cfg[channel + '_webhook']))
        if test_channel:
            cfg['tg_enabled'], cfg['wecom_enabled'] = test_channel == 'tg', test_channel == 'wecom'
            for channel in WEBHOOK_CHANNELS:
                cfg[channel + '_enabled'] = test_channel == channel
        cfg['tg_api_url'] = public_origin(cfg['tg_api_url'])
        for channel in WEBHOOK_CHANNELS:
            if not cfg[channel + '_enabled']:
                continue
            url = urlsplit(cfg[channel + '_webhook'])
            hosts = {'discord': {'discord.com', 'discordapp.com'}, 'feishu': {'open.feishu.cn', 'open.larksuite.com'},
                     'dingtalk': {'oapi.dingtalk.com'}}[channel]
            paths = {'discord': r'/api(?:/v\d+)?/webhooks/\d+/[^/]+', 'feishu': r'/open-apis/bot/v2/hook/[^/]+',
                     'dingtalk': r'/robot/send'}
            if (url.scheme != 'https' or url.hostname not in hosts or url.username or url.password
                    or url.port not in (None, 443) or not re.fullmatch(paths[channel], url.path) or url.fragment
                    or (channel == 'dingtalk' and not parse_qs(url.query).get('access_token'))):
                raise ValueError(f'{channel} Webhook 地址无效，请使用平台生成的 HTTPS 地址')
        self.tg_token = cfg['tg_token'].strip() if cfg['tg_enabled'] else ''
        self.tg_users = {x.strip() for x in cfg['tg_allowed_users'].split(',') if x.strip()}
        self.corp = cfg['wecom_corp_id'].strip() if cfg['wecom_enabled'] else ''
        self.agent = cfg['wecom_agent_id'].strip() if cfg['wecom_enabled'] else ''
        self.secret = cfg['wecom_secret'].strip() if cfg['wecom_enabled'] else ''
        self.wecom_users = {x.strip() for x in cfg['wecom_allowed_users'].split(',') if x.strip()}
        self.crypt = None
        self.enabled = bool(cfg['tg_enabled'] or cfg['wecom_enabled'] or any(cfg[c + '_enabled'] for c in WEBHOOK_CHANNELS))
        base = ''
        if not test_channel and cfg['public_url'].strip():
            base = public_origin(cfg['public_url'])
            cfg['public_url'] = base
        if cfg['tg_enabled']:
            if not self.tg_token:
                raise ValueError('请填写 Telegram Bot Token')
            if cfg['tg_local_enabled']:
                self.local_api.credentials(cfg)
            if not test_channel and (not self.tg_users or any(not re.fullmatch(r'[1-9][0-9]*', u) for u in self.tg_users)):
                raise ValueError('请配置 MUSICDL_TG_ALLOWED_USERS（Telegram 数字用户 ID 白名单）')
        if cfg['wecom_enabled']:
            if not self.corp or not self.agent.isdigit() or not self.secret:
                raise ValueError('企业微信需要 CorpID、AgentID 和 Secret')
            if not test_channel:
                if not base:
                    raise ValueError('企业微信需要访问地址以生成消息回调 URL')
                token = cfg['wecom_token'].strip()
                if not token:
                    raise ValueError('请配置 MUSICDL_WECOM_TOKEN')
                self.crypt = WecomCrypt(token, cfg['wecom_aes_key'].strip(), self.corp)
        ttl = int(cfg['link_ttl_seconds']) if not test_channel else 86400
        if not 60 <= ttl <= 604800:
            raise ValueError('下载链接有效期须为 60–604800 秒')
        cfg['link_ttl_seconds'] = ttl
        # Disabling command intake must not invalidate already issued links.
        self.links = DownloadLinks(get, put, resolve, base, ttl) if base else None
        self.sources = list(filter(None, (s.strip() for s in cfg['bot_sources'].split(','))))
        proxy = self.telegram_proxy() if not test_channel or test_channel == 'tg' else ''
        if proxy:
            try:
                url = urlsplit(proxy)
                if url.scheme not in {'http', 'https'} or not url.hostname or url.port == 0:
                    raise ValueError()
            except ValueError:
                raise ValueError('Telegram 代理必须为有效的 HTTP / HTTPS 地址和端口') from None
        self.tg_http, self.wecom_http = requests.Session(), requests.Session()
        self.tg_http.trust_env = False
        self.access_token, self.token_until = '', 0
        self.tasks = []
        self.search_tasks = {}
        self.tg_poll = None
        self.file_send, self.file_item = None, None
        self.pending = asyncio.Queue(maxsize=100)
        self.seen_rate = {}

    def telegram_proxy(self):
        mode = self.config['tg_proxy_mode']
        return (self.project_proxy() if mode == 'project' else
                self.config['tg_proxy'] if mode == 'custom' else '').strip()

    def allowed(self, channel, user):
        if channel == 'tg':
            return bool(self.tg_token) and user in self.tg_users
        if channel in WEBHOOK_CHANNELS:
            return bool(self.config[channel + '_enabled']) and user == 'downloads'
        return channel == 'wecom' and bool(self.crypt) and bool(user) and (not self.wecom_users or user in self.wecom_users)

    def public_config(self):
        return {**{key: value for key, value in self.config.items() if key not in SECRET_FIELDS},
                'local_api_status': self.local_api.status(),
                'configured': {key: bool(self.config[key]) for key in SECRET_FIELDS},
                'status': {'tg': bool(self.tg_token), 'wecom': bool(self.crypt),
                           **{c: bool(self.config[c + '_enabled']) for c in WEBHOOK_CHANNELS}},
                'callback_url': self.config['public_url'].rstrip('/') + '/bot/wecom' if self.config['public_url'] else ''}

    def error_detail(self, error):
        detail = f'{type(error).__name__}: {error}'
        for key in SECRET_FIELDS:
            if self.config[key]:
                detail = detail.replace(self.config[key], '[已隐藏]')
        return re.sub(r'https?://[^\s<>\'\"]+', '[地址已隐藏]', detail)[:2000]

    def error_reason(self, error):
        return explain_error(error) or '通知发送失败，请查看运行日志中的原始信息并检查网络、凭据或服务状态。'

    def telegram_url(self):
        return LOCAL_URL if self.config['tg_local_enabled'] else self.config['tg_api_url']

    def reply(self, message, text, url=None, buttons=None, job_id=None, edit_message_id=None, file=False, card_key=None):
        # WeCom text messages have a byte limit; split without cutting UTF-8.
        chunks, current = [], ''
        limit = 1800 if message['channel'] in ('wecom', *WEBHOOK_CHANNELS) else 4000
        for line in text.splitlines(keepends=True):
            for char in line:
                size = len((current + char).encode()) if message['channel'] == 'wecom' else len((current + char).encode('utf-16-le')) // 2
                if size > limit:
                    chunks.append(current)
                    current = ''
                current += char
        chunks.append(current)
        for index, chunk in enumerate(chunks):
            ident = secrets.token_hex(16)
            last = index == len(chunks) - 1
            item = {'id': ident, 'channel': message['channel'], 'user': message['user'],
                    'text': chunk, 'url': url if last else None, 'buttons': buttons if last else None,
                    'job_id': job_id if last else None, 'created': time.time_ns(), 'attempts': 0, 'next': 0}
            if last and file:
                item['file'] = True
            if last and card_key:
                item['card_key'] = card_key
                for pending in self.values('bot_outbox'):
                    if pending.get('card_key') == card_key:
                        self.remove('bot_outbox', pending['id'])
            if edit_message_id and len(chunks) == 1:
                item['edit_message_id'] = edit_message_id
            self.put('bot_outbox', ident, item)

    def menu_buttons(self):
        buttons = [[{'text': '搜索音乐', 'callback_data': '/search'}, {'text': '我的任务', 'callback_data': '/status'}]]
        if self.links:
            buttons.append([{'text': '打开网页试听', 'url': self.links.base_url}])
        buttons.append([{'text': '使用帮助', 'callback_data': '/help'}])
        return buttons

    def task_buttons(self, ident, status, prefix=''):
        buttons = []
        if status == 'complete':
            if self.links:
                buttons.append({'text': prefix + '获取链接', 'callback_data': f'/link {ident}'})
            buttons.append({'text': prefix + '发送文件', 'callback_data': f'/file {ident}'})
        if status in {'paused', 'failed'}:
            buttons.append({'text': prefix + '继续下载', 'callback_data': f'/resume {ident}'})
        if status in {'queued', 'resolving', 'downloading', 'enriching', 'paused', 'failed'}:
            buttons.append({'text': prefix + '取消任务', 'callback_data': f'/cancel {ident}'})
        return buttons

    def search_page(self, message, search_id, result, page=1):
        if 'source_states' in result:
            return self.source_page(message, search_id, result, page)
        tracks = result['tracks']
        size = search_page_size(message['channel'])
        pages = (len(tracks) + size - 1) // size
        if not 1 <= page <= pages:
            raise HTTPException(400, '搜索页码无效')
        start = (page - 1) * size
        visible = tracks[start:start + size]
        lines = [f'搜索结果 · 第 {page}/{pages} 页 · 共 {len(tracks)} 首']
        for index, track in enumerate(visible, start + 1):
            source = SOURCE_LABELS.get(track['source'], track['source'].removesuffix('MusicClient'))
            if message['channel'] == 'tg':
                lines.append(f'{index}. {concise(track["title"], 48)} {concise(track["artist"], 24)} · '
                             f'{concise(track["qualities"][0]["label"], 20)} · {track_size(track)}')
            else:
                lines.extend([f'{index}. {concise(track["title"])}',
                              f'{concise(track["artist"], 24)} · {concise(source, 16)}',
                              f'{concise(track["qualities"][0]["label"], 20)} · {track_size(track)}', ''])
        if message['channel'] == 'wecom':
            lines.append(f'先打开网页试听确认版本。下载示例：下载 {start + 1}')
        if message['channel'] == 'wecom' and pages > 1:
            lines.append('翻页：下一页 / 上一页 / 第2页（30 分钟内有效）')
        if result.get('partial'):
            lines.append('部分音源失败或超时，以上为已返回结果。')
        buttons = button_rows([{'text': str(index), 'callback_data': f'/download {search_id} {index}'}
                               for index in range(start + 1, start + len(visible) + 1)], size)
        navigation = []
        if page > 1:
            navigation.append({'text': '上一页', 'callback_data': f'/results {search_id} {page - 1}'})
        if page < pages:
            navigation.append({'text': '下一页', 'callback_data': f'/results {search_id} {page + 1}'})
        if navigation:
            buttons.append(navigation)
        if self.links:
            buttons.append([{'text': '打开网页试听', 'url': self.links.base_url}])
        buttons.append([{'text': '功能菜单', 'callback_data': '/menu'}, {'text': '我的任务', 'callback_data': '/status'}])
        self.reply(message, '\n'.join(lines), buttons=buttons, edit_message_id=message.get('reply_message_id'))
        owner = f'{message["channel"]}:{message["user"]}'
        self.put('bot_session', owner, {'id': owner, 'search_id': search_id, 'page': page, 'expires': result['expires']})

    def source_page(self, message, search_id, result, page=1):
        selected = result['selected']
        source = result['source_states'][selected]
        tracks = source['tracks']
        size = search_page_size(message['channel'])
        pages = max(1, (len(tracks) + size - 1) // size)
        start = (page - 1) * size
        visible = tracks[start:start + size]
        if not 1 <= page <= pages:
            raise HTTPException(400, '搜索页码无效')
        result['page'] = page
        if message.get('reply_message_id'):
            result['message_id'] = message['reply_message_id']
        self.put('bot_search', result['id'], result)
        lines = [f'搜索源：{source["label"]} · {concise(result["keyword"], 60)}',
                 f'第 {page}/{pages} 页 · 共 {len(tracks)} 首']
        if source['state'] == 'searching':
            lines.append('搜索中… 可先切换其他音源，完成后自动更新。')
        if not tracks and source['state'] != 'searching':
            lines.append('搜索已停止、失败或超时，请重新搜索或检查音源设置。' if source['state'] == 'failed' else '没有可下载结果，请换关键词或切换音源。')
        elif tracks:
            for index, track in enumerate(visible, start + 1):
                title = f'{index}. {concise(track["title"], 48)} {concise(track.get("artist"), 24)}'
                details = f'{concise(track["qualities"][0]["label"], 20)} · {track_size(track)}'
                lines.append(title + ' · ' + details if message['channel'] == 'tg' else title)
                if message['channel'] == 'wecom':
                    lines.append(details)
            if source['state'] == 'failed':
                lines.append('该音源部分结果已返回，其余失败或超时。')
        buttons = button_rows([{'text': str(i), 'callback_data': f'/download {search_id} {i} {selected}'}
                               for i in range(start + 1, start + len(visible) + 1)], size)
        navigation = []
        if page > 1:
            navigation.append({'text': '上一页', 'callback_data': f'/results {search_id} {page - 1} {selected}'})
        if page < pages:
            navigation.append({'text': '下一页', 'callback_data': f'/results {search_id} {page + 1} {selected}'})
        if navigation:
            buttons.append(navigation)
        buttons.extend(button_rows([{'text': ('✓ ' if i == selected else '') + concise(s['label'], 18),
                                     'callback_data': f'/source {search_id} {i}'} for i, s in enumerate(result['source_states'])], 4))
        buttons.append([{'text': '我的任务', 'callback_data': '/status'}, {'text': '功能菜单', 'callback_data': '/menu'}])
        if message['channel'] == 'wecom':
            lines.append('\n切换音源：音源 序号，例如 音源 2。下载：下载 1。翻页：下一页 / 上一页。')
            lines.extend(f'{i + 1}. {s["label"]}' for i, s in enumerate(result['source_states']))
        self.reply(message, '\n'.join(lines), buttons=buttons, card_key=result['id'] if message['channel'] == 'tg' else None)
        owner = f'{message["channel"]}:{message["user"]}'
        self.put('bot_session', owner, {'id': owner, 'search_id': search_id, 'page': page, 'expires': result['expires']})

    def launch_source_search(self, message, search_id, result):
        owner = f'{message["channel"]}:{message["user"]}'
        previous = self.search_tasks.get(owner)
        if previous:
            previous.cancel()
            old = self.get('bot_search', previous.get_name())
            if old:
                for source in old['source_states']:
                    if source['state'] == 'searching':
                        source['state'] = 'failed'
                self.put('bot_search', old['id'], old)

        async def run_source(index, source):
            if source['state'] != 'searching':
                return
            streamed, last_update = [], 0.0
            def progress(track):
                nonlocal last_update
                if len(streamed) >= 50 or any(t['id'] == track['id'] for t in streamed):
                    return
                streamed.append(track)
                now = time.monotonic()
                if last_update and now - last_update < 1:
                    return
                last_update = now
                current = self.get('bot_search', result['id'])
                if not current or current['expires'] <= time.time():
                    return
                current['source_states'][index]['tracks'] = list(streamed)
                self.put('bot_search', current['id'], current)
                if current['selected'] == index:
                    self.source_page(message, search_id, current, current.get('page', 1))
            try:
                if self.search_progress and self.search is self.search_progress:
                    tracks, partial = await self.search_progress(result['keyword'], [source['id']], on_track=progress)
                else:
                    tracks, partial = await self.search(result['keyword'], [source['id']])
                # Preserve the numbers users have already seen on a partial card.
                seen = {t['id'] for t in streamed}
                ordered = streamed + [t for t in rank_tracks(tracks, result['keyword']) if t['id'] not in seen]
                state = {'tracks': ordered[:50], 'state': 'failed' if partial else 'complete'}
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.log('warning', f'机器人音源搜索失败：{source["label"]} · {self.error_detail(error)}', category='bot')
                state = {'tracks': [], 'state': 'failed'}
            current = self.get('bot_search', result['id'])
            if not current or current['expires'] <= time.time():
                return
            current['source_states'][index].update(state)
            self.put('bot_search', current['id'], current)
            if current['selected'] == index:
                self.source_page(message, search_id, current, current.get('page', 1))

        async def run():
            await asyncio.gather(*(run_source(i, s) for i, s in enumerate(result['source_states'])))
        self.search_tasks[owner] = asyncio.create_task(run(), name=result['id'])

    def current_search(self, owner):
        session = self.get('bot_session', owner)
        result = self.get('bot_search', f'{owner}:{session["search_id"]}') if session else None
        if not result or result['expires'] <= time.time():
            raise HTTPException(400, '没有有效搜索结果，请先发送歌名重新搜索')
        return session, result

    def accept(self, channel, user, message_id, text, reply_message_id=None):
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
        if reply_message_id:
            message['reply_message_id'] = reply_message_id
        self.put('bot_inbox', ident, message)
        self.pending.put_nowait(message)
        return True

    async def command(self, message):
        text = message['text'].strip()
        command, _, arg = text.partition(' ')
        command = ALIASES.get(command.lstrip('/').split('@')[0].lower(), command.lstrip('/').lower())
        if command == '音源':
            command = 'source'
        if match := re.fullmatch(r'第\s*([0-9]+)\s*页', text):
            command, arg = 'results', match[1]
        if command == '结果':
            command = 'results'
        if text and not text.startswith('/') and command not in {'help', 'search', 'download', 'status', 'link', 'file', 'source', 'cancel', 'resume', 'results', 'next', 'prev'}:
            command, arg = 'search', text
        command_label = command if command in {'help', 'search', 'download', 'status', 'link', 'file', 'source', 'cancel', 'resume', 'results', 'next', 'prev'} else '未知命令'
        self.log('info', f'{message["channel"]} 开始处理命令：{command_label}', category='bot', task_id=message['id'])
        owner = f'{message["channel"]}:{message["user"]}'
        if command == 'help':
            self.reply(message, HELP, buttons=self.menu_buttons())
        elif command == 'search':
            if not arg.strip():
                self.reply(message, '搜索音乐\n\n请直接发送歌名或歌手，例如：\n稻香 周杰伦\n\n也可使用：/search 稻香 周杰伦', buttons=self.menu_buttons())
                return
            if not 1 <= len(arg.strip()) <= 200:
                raise HTTPException(400, '请输入 1–200 字的搜索关键词：搜索 歌名 歌手')
            if self.source_catalog:
                catalog = self.source_catalog()
                sources = [s for s in catalog if not self.sources or s['id'] in self.sources or s.get('engine_name') in self.sources]
                known = {s['id'] for s in sources} | {s.get('engine_name') for s in sources}
                if not sources or any(s not in known for s in self.sources):
                    raise HTTPException(400, '没有有效的机器人音源，请检查音源设置')
                search_id = message['id'][:8]
                result = {'id': f'{owner}:{search_id}', 'expires': int(time.time()) + 1800, 'keyword': arg.strip(),
                          'selected': 0, 'page': 1, 'source_states': [{**s, 'tracks': [], 'state': 'searching'} for s in sources]}
                self.source_page({k: v for k, v in message.items() if k != 'reply_message_id'}, search_id, result)
                self.launch_source_search({k: v for k, v in message.items() if k != 'reply_message_id'}, search_id, result)
                return
            self.reply(message, '正在搜索，请稍候。')
            tracks, partial = await self.search(arg.strip(), self.sources)
            # Cap chat output; the full multi-source result remains in the web UI.
            tracks = rank_tracks(tracks, arg.strip())[:10]
            if not tracks:
                self.reply(message, '没有可下载结果，请换关键词或检查音源设置。')
                return
            search_id = message['id'][:8]
            result = {'id': f'{owner}:{search_id}', 'expires': int(time.time()) + 1800, 'tracks': tracks, 'partial': partial}
            self.put('bot_search', result['id'], result)
            # Search progress is a separate reply; only pagination edits a card.
            self.search_page({k: v for k, v in message.items() if k != 'reply_message_id'}, search_id, result)
        elif command == 'source':
            args = arg.split()
            if len(args) == 1 and args[0].isdigit():
                session, result = self.current_search(owner)
                search_id, index = session['search_id'], int(args[0]) - 1
            elif len(args) == 2 and re.fullmatch(r'[a-f0-9]{8}', args[0]) and args[1].isdigit():
                search_id, index = args[0], int(args[1])
                result = self.get('bot_search', f'{owner}:{search_id}')
            else:
                raise HTTPException(400, '发送 音源 序号，例如：音源 2')
            if not result or result['expires'] <= time.time() or not 0 <= index < len(result.get('source_states', [])):
                raise HTTPException(400, '音源序号无效或搜索已过期')
            result['selected'] = index
            self.source_page(message, search_id, result)
        elif command in {'results', 'next', 'prev'}:
            args = arg.split()
            if command in {'next', 'prev'} or (len(args) == 1 and args[0].isdigit()):
                session, result = self.current_search(owner)
                search_id = session['search_id']
                page = session['page'] + (1 if command == 'next' else -1) if command in {'next', 'prev'} else int(args[0])
                tracks = result['source_states'][result['selected']]['tracks'] if 'source_states' in result else result['tracks']
                size = search_page_size(message['channel'])
                pages = max(1, (len(tracks) + size - 1) // size)
                if not 1 <= page <= pages:
                    self.reply(message, '已经是最后一页。' if page > pages else '已经是第一页。')
                    return
            else:
                if len(args) not in (2, 3) or not re.fullmatch(r'[a-f0-9]{8}', args[0]) or not args[1].isdigit():
                    raise HTTPException(400, '发送 下一页 / 上一页 / 第2页')
                search_id, page = args[0], int(args[1])
                result = self.get('bot_search', f'{owner}:{search_id}')
                if not result or result['expires'] <= time.time():
                    raise HTTPException(400, '搜索结果已过期，请重新搜索')
                if len(args) == 3:
                    if not args[2].isdigit() or not 0 <= int(args[2]) < len(result.get('source_states', [])):
                        raise HTTPException(400, '音源序号无效')
                    result['selected'] = int(args[2])
            self.search_page(message, search_id, result, page)
        elif command == 'download':
            args = arg.split()
            if len(args) == 1 and args[0].isdigit():
                session, _ = self.current_search(owner)
                args.insert(0, session['search_id'])
            if len(args) not in (2, 3) or not re.fullmatch(r'[a-f0-9]{8}', args[0]) or not args[1].isdigit():
                raise HTTPException(400, '发送 下载 序号，例如：下载 1；请先搜索音乐')
            result = self.get('bot_search', f'{owner}:{args[0]}')
            index = int(args[1]) - 1
            if len(args) == 3 and not args[2].isdigit():
                raise HTTPException(400, '音源序号无效')
            tracks = []
            if result and 'source_states' in result:
                selected = int(args[2]) if len(args) == 3 and args[2].isdigit() else result['selected']
                if 0 <= selected < len(result['source_states']):
                    tracks = result['source_states'][selected]['tracks']
            elif result:
                tracks = result['tracks']
            if not result or result['expires'] <= time.time() or not 0 <= index < len(tracks):
                raise HTTPException(400, '搜索结果已过期或序号无效，请重新搜索')
            active = [t for t in self.values('bot_task') if t['owner'] == owner and
                      (self.get('download', t['id']) or {}).get('status') in {'queued', 'downloading', 'resolving', 'enriching'}]
            if len(active) >= 5:
                raise HTTPException(429, '最多同时保留 5 个活动任务，请等待或取消旧任务')
            ident = message['id'][:32]
            job = self.get('download', ident)
            if not job:
                job = await asyncio.to_thread(self.enqueue, tracks[index], ident)
            self.put('bot_task', ident, {'id': ident, 'owner': owner, 'channel': message['channel'], 'user': message['user'], 'notified': None})
            self.reply(message, f'已加入下载队列\n\n{concise(job["track"]["title"])}\n文件大小：{track_size(job["track"])}\n任务 {ident[:8]}\n\n完成后自动发送文件，超限时提供链接。', buttons=[self.task_buttons(ident, job['status']), [{'text': '我的任务', 'callback_data': '/status'}]])
        elif command == 'status' or (command in {'link', 'file', 'cancel', 'resume'} and not arg.strip()):
            jobs = [(t, job) for t in self.values('bot_task') if t['owner'] == owner
                    if (job := self.get('download', t['id']))][:5]
            lines, buttons = ['我的任务', ''], []
            for index, (task, job) in enumerate(jobs, 1):
                if not job:
                    continue
                lines.extend([f'{index}. {concise(job["track"]["title"])}',
                              f'{STATUS_LABELS.get(job["status"], job["status"])} · 任务 {task["id"] if message["channel"] == "wecom" else task["id"][:8]}',
                              self.job_size(job) if job['status'] == 'complete' else job_progress(job), ''])
                actions = self.task_buttons(task['id'], job['status'], f'{index}. ')
                if actions:
                    buttons.append(actions)
            buttons.append([{'text': '刷新任务', 'callback_data': '/status'}, {'text': '功能菜单', 'callback_data': '/menu'}])
            if message['channel'] == 'wecom' and len(lines) > 2:
                lines.append('操作：链接 / 继续 / 取消，加上完整任务编号。')
            self.reply(message, '\n'.join(lines) if len(lines) > 2 else '我的任务\n\n暂无任务，先搜索喜欢的音乐。', buttons=buttons)
        elif command in {'link', 'file', 'cancel', 'resume'}:
            ident = arg.strip()
            owned = self.get('bot_task', ident) if re.fullmatch(r'[a-f0-9]{32}', ident) else None
            if not owned or owned['owner'] != owner:
                raise HTTPException(404, '任务不存在或不属于你')
            job = self.get('download', ident)
            if not job:
                raise HTTPException(404, '任务记录已删除')
            if command == 'link':
                if not self.links:
                    raise HTTPException(400, '请在通知设置中填写访问地址后获取链接')
                url = await asyncio.to_thread(self.links.create, job)
                self.reply(message, f'{concise(job["track"]["title"])}\n{self.job_size(job)}\n下载原文件（链接持有者可下载，请勿转发）', url=url, job_id=ident)
            elif command == 'file':
                self.queue_file(message, job)
            else:
                await asyncio.to_thread(self.control, ident, command)
                if command == 'resume':
                    owned['notified'] = None
                    self.put('bot_task', ident, owned)
                self.reply(message, '已取消任务。' if command == 'cancel' else '已继续下载。')
        else:
            self.reply(message, HELP, buttons=self.menu_buttons())

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
                self.log('warning', f'{message["channel"]} 命令被拒绝：HTTP {error.status_code} · {error.detail}', category='bot', task_id=message['id'])
                self.reply(message, str(error.detail))
            except Exception as error:
                self.log('error', f'{message["channel"]} 机器人命令失败：{self.error_detail(error)}', category='bot', task_id=message['id'])
                self.reply(message, '命令执行失败，请查看服务运行日志后重试。')
            message['done'] = True
            self.put('bot_inbox', message['id'], message)
            self.pending.task_done()

    def telegram(self, method, body, timeout=15, files=None, api_url=None):
        # Resolve per request so changing or clearing the project proxy takes effect.
        origin = api_url or self.telegram_url()
        proxy = None if origin == LOCAL_URL else self.telegram_proxy() or None
        if files:
            encoder = MultipartEncoder(fields={**{k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in body.items()}, **files})
            payload = {'data': encoder, 'headers': {'Content-Type': encoder.content_type}}
        else:
            payload = {'json': body}
        response = self.tg_http.post(f'{origin}/bot{self.tg_token}/{method}', **payload, timeout=timeout,
                                     proxies={'http': proxy, 'https': proxy})
        if response.status_code == 413:
            raise RuntimeError('Telegram API error 413: file too big')
        data = response.json()
        if not data.get('ok'):
            if method == 'editMessageText' and data.get('error_code') == 400 and 'message is not modified' in data.get('description', '').lower():
                return True
            raise RuntimeError(f'Telegram API error {data.get("error_code")}: {data.get("description", "请求失败")}')
        return data['result']

    async def register_telegram_menu(self):
        while True:
            try:
                for language in ('', 'zh'):
                    await asyncio.to_thread(self.telegram, 'setMyCommands',
                                            {'commands': TG_COMMANDS, 'scope': {'type': 'all_private_chats'}, 'language_code': language})
                await asyncio.to_thread(self.telegram, 'setChatMenuButton', {'menu_button': {'type': 'commands'}})
                self.log('info', 'Telegram 命令菜单已注册；可发送 /start 或 /menu 打开功能入口', category='bot')
                return
            except Exception as error:
                self.log('warning', f'Telegram 菜单注册失败，将重试：{self.error_detail(error)}；可先发送 /menu 使用功能按钮', category='bot')
                await asyncio.sleep(60)

    async def poll_telegram(self):
        while True:
            try:
                offset = self.get('bot_setting', 'tg_offset', 0)
                self.tg_poll = asyncio.create_task(asyncio.to_thread(self.telegram, 'getUpdates',
                    {'offset': offset, 'timeout': 20, 'allowed_updates': ['message', 'callback_query']}, 30))
                updates = await asyncio.shield(self.tg_poll)
                for update in updates:
                    callback = update.get('callback_query')
                    message = (callback or {}).get('message') if callback else update.get('message')
                    sender = callback.get('from', {}) if callback else (message or {}).get('from', {})
                    user = str(sender.get('id', ''))
                    private = bool(message and message.get('chat', {}).get('type') == 'private' and
                                   str(message['chat'].get('id')) == user)
                    text = callback.get('data', '') if callback else (message or {}).get('text', '')
                    if private and text:
                        self.accept('tg', user, str(update['update_id']), text,
                                    reply_message_id=message.get('message_id') if callback else None)
                    self.put('bot_setting', 'tg_offset', update['update_id'] + 1)
                    if callback and private and self.allowed('tg', user):
                        try:
                            await asyncio.to_thread(self.telegram, 'answerCallbackQuery', {'callback_query_id': callback['id']})
                        except Exception:
                            pass
            except Exception as error:
                self.log('warning', f'Telegram 接收失败，将重试：{self.error_detail(error)}（检查代理、Token 和 webhook 是否关闭）', category='bot')
                await asyncio.sleep(5)

    def wecom_access(self):
        if time.time() >= self.token_until:
            response = self.wecom_http.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken',
                params={'corpid': self.corp, 'corpsecret': self.secret}, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('errcode', 0) or not data.get('access_token'):
                raise RuntimeError(f'WeCom token error {data.get("errcode")}: {data.get("errmsg", "请求失败")}')
            self.access_token = data['access_token']
            self.token_until = time.time() + max(1, int(data['expires_in']) - 120)
        return self.access_token

    def job_size(self, job):
        try:
            return '文件大小：' + format_size(self.resolve(job['file']).stat().st_size)
        except (HTTPException, OSError, KeyError, TypeError):
            return '文件大小：大小未知（文件不可访问）'

    def queue_file(self, message, job):
        if job.get('status') != 'complete' or not job.get('file'):
            raise HTTPException(409, '任务尚未完成或文件已删除')
        target = self.resolve(job['file'])
        limit = 20 * 1024 * 1024
        text = f'下载完成\n{concise(job["track"]["title"])} · {concise(job["track"].get("artist"))}\n{self.job_size(job)}'
        if message['channel'] == 'wecom' and target.stat().st_size > limit:
            if self.links:
                self.reply(message, text + f'\n文件超过该渠道上传上限（{format_size(limit)}），请通过链接下载原文件。',
                           url=self.links.create(job), job_id=job['id'])
            else:
                self.reply(message, text + '\n文件超过上传上限，请在通知设置中填写访问地址后获取链接，或配置自建 Telegram Bot API。')
            return
        self.reply(message, text, job_id=job['id'], file=True)

    def send_file(self, item):
        job = self.get('download', item['job_id']) or {}
        if job.get('status') != 'complete' or not job.get('file'):
            raise HTTPException(409, '文件不可访问，请重新下载')
        target = self.resolve(job['file'])
        if item['channel'] == 'tg':
            audio = target.suffix.lower() in {'.mp3', '.m4a', '.flac'}
            method = 'sendAudio' if audio else 'sendDocument'
            field = 'audio' if audio else 'document'
            size = target.stat().st_size
            self.log('info', f'Telegram 开始上传：{concise(target.name)} · {format_size(size)}（{size} 字节）· {method}' +
                     ('；超过 50 MB，仍尝试直接发送，以 API 实际响应为准' if size > 50 * 1024 * 1024 else ''),
                     category='bot', task_id=job['id'])
            body = {'chat_id': item['user'], 'caption': item['text'][:1000]}
            if audio:
                body.update(title=job['track']['title'], performer=job['track'].get('artist', ''))
            with target.open('rb') as handle:
                try:
                    upload = (target.name, handle, 'audio/flac') if audio and target.suffix.lower() == '.flac' else (target.name, handle)
                    sent = self.telegram(method, body, timeout=(15, 600), files={field: upload})
                except RuntimeError as error:
                    # Only format rejection falls back to a document; transport failures retry in the outbox.
                    if telegram_size_error(error) or not audio or not re.search(r'(audio|file|content|format|media)', str(error), re.I) or 'error 400' not in str(error):
                        raise
                    self.log('warning', f'Telegram sendAudio 拒绝音频格式：{self.error_detail(error)}；改用 sendDocument 发送原文件，不转码',
                             category='bot', task_id=job['id'])
                    handle.seek(0)
                    method = 'sendDocument'
                    sent = self.telegram(method, {'chat_id': item['user'], 'caption': item['text'][:1000]},
                                         timeout=(15, 600), files={'document': (target.name, handle)})
            media_type = next((key for key in ('audio', 'document') if isinstance(sent, dict) and key in sent), '')
            media = sent[media_type] if media_type else {}
            self.log('info', f'Telegram {method} 上传成功：{concise(target.name)} · {format_size(size)}（{size} 字节）' +
                     (f'；API 消息类型 {media_type}，MIME {concise(media.get("mime_type") or "未提供")}' if media_type else ''),
                     category='bot', task_id=job['id'])
            return
        self.wecom_access()
        media_id = item.get('media_id') if item.get('media_until', 0) > time.time() else None
        if not media_id:
            with target.open('rb') as handle:
                encoder = MultipartEncoder(fields={'media': (target.name, handle, 'application/octet-stream')})
                response = self.wecom_http.post('https://qyapi.weixin.qq.com/cgi-bin/media/upload',
                    params={'access_token': self.access_token, 'type': 'file'}, data=encoder,
                    headers={'Content-Type': encoder.content_type}, timeout=(15, 300))
            response.raise_for_status()
            data = response.json()
            if data.get('errcode', 0) or not data.get('media_id'):
                if data.get('errcode') in {40014, 42001}:
                    self.token_until = 0
                raise RuntimeError(f'WeCom upload error {data.get("errcode")}: {data.get("errmsg", "请求失败")}')
            media_id = data['media_id']
            item.update(media_id=media_id, media_until=time.time() + 2 * 86400)
            self.put('bot_outbox', item['id'], item)
        self.wecom_send({'touser': item['user'], 'agentid': int(self.agent), 'msgtype': 'file', 'file': {'media_id': media_id},
                         'enable_duplicate_check': 1, 'duplicate_check_interval': 1800})

    def webhook(self, channel, item=None):
        url = self.config[channel + '_webhook']
        if item is None:
            # Verify configuration locally. Webhooks cannot be tested remotely without posting a message.
            return
        text = 'musicdl · ' + item['text'] + ('\n' + item['url'] if item.get('url') else '')
        body = {'content': text[:2000], 'allowed_mentions': {'parse': []}} if channel == 'discord' else (
            {'msg_type': 'text', 'content': {'text': text}} if channel == 'feishu' else {'msgtype': 'text', 'text': {'content': text}})
        params = {'wait': 'true'} if channel == 'discord' else {}
        secret = self.config.get(channel + '_sign_secret')
        if secret:
            timestamp = str(int(time.time()) * (1000 if channel == 'dingtalk' else 1))
            key = f'{timestamp}\n{secret}'.encode()
            signature = base64.b64encode(hmac.new(secret.encode() if channel == 'dingtalk' else key,
                                                 key if channel == 'dingtalk' else b'', hashlib.sha256).digest()).decode()
            if channel == 'dingtalk':
                params.update(timestamp=timestamp, sign=signature)
            else:
                body.update(timestamp=timestamp, sign=signature)
        response = self.wecom_http.post(url, params=params, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        code = data.get('errcode', data.get('code', data.get('StatusCode', 0)))
        if code:
            raise RuntimeError(f'{channel} Webhook error {code}: {data.get("errmsg", data.get("msg", "请求失败"))}')

    def queue_webhook_notifications(self):
        for channel in WEBHOOK_CHANNELS:
            if not self.config[channel + '_enabled']:
                continue
            for job in self.values('download'):
                if job['status'] not in {'complete', 'failed', 'cancelled'}:
                    continue
                key = f'{channel}:{job["id"]}'
                if self.get('bot_notice', key) == job['status']:
                    continue
                text = f'{STATUS_LABELS[job["status"]]}\n{concise(job["track"]["title"])} · {concise(job["track"].get("artist"))}\n任务 {job["id"][:8]}'
                url = None
                if job['status'] == 'complete':
                    text += '\n' + self.job_size(job)
                    if self.links:
                        try:
                            url = self.links.create(job)
                        except (HTTPException, OSError):
                            text += '\n文件已删除或不可访问。'
                    else:
                        text += '\n可在 Web 音乐文件页下载；填写通知访问地址后可推送链接。'
                self.reply({'channel': channel, 'user': 'downloads'}, text, url=url, job_id=job['id'] if url else None)
                self.put('bot_notice', key, job['status'])

    def send(self, item):
        if item['channel'] in WEBHOOK_CHANNELS:
            return self.webhook(item['channel'], item)
        if item.get('file'):
            try:
                return self.send_file(item)
            except (HTTPException, FileNotFoundError):
                item = {**item, 'file': False, 'text': '文件已删除或不可访问，请重新下载。'}
            except RuntimeError as error:
                if item['channel'] != 'tg' or not telegram_size_error(error):
                    raise
                job = self.get('download', item['job_id']) or {}
                url = self.links.create(job) if self.links else None
                raw_reason = self.error_detail(error)
                reason = self.error_reason(error)
                self.log('warning', f'Telegram 已尝试上传，但接口拒绝文件大小：{self.job_size(job)} · {raw_reason}；' +
                         ('改发原文件下载链接' if url else '未配置访问地址，改发失败原因，请在 Web 下载原文件'),
                         category='bot', task_id=item['job_id'])
                item.update(file=False, url=url, text=item['text'] + f'\nTelegram 拒绝了文件上传：{reason}\n' +
                            ('已改为原文件下载链接。' if url else '请在 Web 下载原文件，或在通知设置中填写访问地址后获取链接。'))
                # The delivery worker persists this mutation on failure; retries then only send the link.
        if item['channel'] == 'tg':
            body = {'chat_id': item['user'], 'text': item['text'], 'link_preview_options': {'is_disabled': True}}
            if item.get('url'):
                body['reply_markup'] = {'inline_keyboard': [[{'text': '下载到手机', 'url': item['url']}],
                    [{'text': '我的任务', 'callback_data': '/status'}, {'text': '功能菜单', 'callback_data': '/menu'}]]}
            elif item.get('buttons'):
                body['reply_markup'] = {'inline_keyboard': [row for row in item['buttons'] if row]}
            card = self.get('bot_search', item['card_key']) if item.get('card_key') else None
            message_id = item.get('edit_message_id') or (card or {}).get('message_id')
            if message_id:
                body['message_id'] = message_id
                self.telegram('editMessageText', body)
            else:
                sent = self.telegram('sendMessage', body)
                if card and isinstance(sent, dict) and sent.get('message_id'):
                    current = self.get('bot_search', item['card_key'])
                    if current:
                        current['message_id'] = sent['message_id']
                        self.put('bot_search', current['id'], current)
            return
        self.wecom_access()
        body = {'touser': item['user'], 'agentid': int(self.agent), 'enable_duplicate_check': 1, 'duplicate_check_interval': 1800}
        if item.get('url'):
            body.update(msgtype='news', news={'articles': [{'title': '下载到手机', 'description': item['text'], 'url': item['url']}]})
        else:
            body.update(msgtype='text', text={'content': item['text']})
        self.wecom_send(body)

    def wecom_send(self, body):
        response = self.wecom_http.post('https://qyapi.weixin.qq.com/cgi-bin/message/send',
            params={'access_token': self.access_token}, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('errcode', 0) or data.get('invaliduser'):
            if data.get('errcode') in {40014, 42001}:
                self.token_until = 0
            raise RuntimeError(f'WeCom send error {data.get("errcode")}: {data.get("errmsg", "请求失败")}')

    async def deliveries(self):
        while True:
            try:
                await asyncio.to_thread(self.queue_webhook_notifications)
                for task in self.values('bot_task'):
                    if not self.allowed(task['channel'], task['user']):
                        continue
                    job = self.get('download', task['id'])
                    if not job or job['status'] not in {'complete', 'failed', 'paused', 'cancelled'} or task['notified'] == job['status']:
                        continue
                    if job['status'] == 'complete':
                        try:
                            await asyncio.to_thread(self.queue_file, task, job)
                        except (HTTPException, OSError):
                            self.reply(task, f'文件已删除或变化，请重新下载。任务编号：{job["id"]}')
                    else:
                        url, text = None, f'{STATUS_LABELS.get(job["status"], job["status"])}\n\n{concise(job["track"]["title"])}\n任务 {job["id"][:8]}\n\n打开 /status 管理任务。'
                        self.reply(task, text, buttons=[self.task_buttons(job['id'], job['status']), [{'text': '我的任务', 'callback_data': '/status'}]])
                    task['notified'] = job['status']
                    self.put('bot_task', task['id'], task)
                for item in sorted(self.values('bot_outbox'), key=lambda x: x['created']):
                    if not self.get('bot_outbox', item['id']):
                        continue
                    if item.get('file') or (item['channel'] == 'wecom' and not self.crypt):
                        continue
                    if (item['channel'] == 'tg' and not self.tg_token) or (item['channel'] in WEBHOOK_CHANNELS and not self.config[item['channel'] + '_enabled']):
                        continue
                    if item['next'] > time.time():
                        continue
                    if not self.allowed(item['channel'], item['user']):
                        self.remove('bot_outbox', item['id'])
                        continue
                    if item.get('url') and not self.links:
                        item.update(url=None, job_id=None, text=item['text'] + '\n通知访问地址已清除，请在 Web 音乐文件页下载。')
                    if item.get('url') and item.get('job_id') and int(parse_qs(urlsplit(item['url']).query)['expires'][0]) <= time.time() + 60:
                        # A long outage must not deliver an already expired link.
                        try:
                            item['url'] = await asyncio.to_thread(self.links.create, self.get('download', item['job_id']) or {})
                        except (HTTPException, OSError):
                            item.update(url=None, job_id=None, text='文件已删除或不可访问，请重新下载。')
                    try:
                        await asyncio.to_thread(self.send, item)
                        self.remove('bot_outbox', item['id'])
                        self.log('info', f'{item["channel"]} 通知发送成功', category='bot', task_id=item.get('job_id') or item['id'])
                        await asyncio.sleep(1)
                    except Exception as error:
                        item['attempts'] += 1
                        item['next'] = int(time.time()) + min(3600, 5 * 2 ** min(item['attempts'], 10))
                        self.put('bot_outbox', item['id'], item)
                        retry_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['next']))
                        self.log('warning', f'{item["channel"]} 通知发送失败，第 {item["attempts"]} 次，将于 {retry_at} 重试：{self.error_detail(error)}', category='bot', task_id=item.get('job_id') or item['id'])
                # Keep replay protection for seven days; prune expired link/search records.
                now = time.time()
                for kind in ('bot_link', 'bot_search', 'bot_session', 'bot_inbox'):
                    for record in self.values(kind):
                        expired = record.get('expires', now + 1) <= now if kind != 'bot_inbox' else record['done'] and record['created'] < now - 604800
                        if expired:
                            self.remove(kind, record['id'])
            except Exception as error:
                self.log('error', f'机器人通知处理失败：{self.error_detail(error)}', category='bot')
            await asyncio.sleep(3)

    async def file_deliveries(self):
        # Attachments use their own worker so a large upload cannot block search card edits.
        while True:
            for item in sorted(self.values('bot_outbox'), key=lambda x: x['created']):
                if not item.get('file') or item['next'] > time.time():
                    continue
                if (item['channel'] == 'tg' and not self.tg_token) or (item['channel'] == 'wecom' and not self.crypt):
                    continue
                if not self.allowed(item['channel'], item['user']):
                    self.remove('bot_outbox', item['id'])
                    continue
                try:
                    self.file_item = item
                    self.file_send = asyncio.create_task(asyncio.to_thread(self.send, item))
                    await asyncio.shield(self.file_send)
                    self.remove('bot_outbox', item['id'])
                    self.log('info', f'{item["channel"]} ' + ('文件发送成功' if item.get('file') else '文件回退通知已发送'),
                             category='bot', task_id=item['job_id'])
                except Exception as error:
                    item['attempts'] += 1
                    item['next'] = int(time.time()) + min(3600, 5 * 2 ** min(item['attempts'], 10))
                    self.put('bot_outbox', item['id'], item)
                    self.log('warning', f'{item["channel"]} 文件发送失败，将重试：{self.error_detail(error)}', category='bot', task_id=item['job_id'])
                self.file_send, self.file_item = None, None
            await asyncio.sleep(3)

    async def start(self):
        if not self.enabled:
            return
        for channel in WEBHOOK_CHANNELS:
            if self.config[channel + '_enabled'] and not self.get('bot_setting', channel + '_initialized'):
                for job in self.values('download'):
                    self.put('bot_notice', f'{channel}:{job["id"]}', job['status'])
                self.put('bot_setting', channel + '_initialized', True)
        self.tasks = [asyncio.create_task(self.commands()), asyncio.create_task(self.deliveries()), asyncio.create_task(self.file_deliveries())]
        for message in reversed(self.values('bot_inbox')):
            if not message['done']:
                await self.pending.put(message)
        if self.tg_token:
            self.tasks.append(asyncio.create_task(self.register_telegram_menu()))
            self.tasks.append(asyncio.create_task(self.poll_telegram()))
        if self.source_catalog:
            for session in self.values('bot_session'):
                result = self.get('bot_search', f'{session["id"]}:{session["search_id"]}')
                channel, user = session['id'].split(':', 1)
                if result and result['expires'] > time.time() and 'source_states' in result and self.allowed(channel, user):
                    self.launch_source_search({'channel': channel, 'user': user}, session['search_id'], result)
        self.log('info', '通知服务已启用：音源独立搜索，下载文件交付与 Webhook 通知', category='bot')

    async def stop(self):
        all_tasks = self.tasks + list(self.search_tasks.values())
        for task in all_tasks:
            task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        # A cancelled to_thread await does not stop the HTTP request. Wait for
        # the old long poll before starting another one with the same token.
        if self.tg_poll:
            await asyncio.gather(self.tg_poll, return_exceptions=True)
        if self.file_send:
            results = await asyncio.gather(self.file_send, return_exceptions=True)
            if not isinstance(results[0], BaseException):
                self.remove('bot_outbox', self.file_item['id'])
        self.tg_http.close()
        self.wecom_http.close()


def install(app, **services):
    services['local_api'] = LocalBotAPI(services['log'])
    bot = BotService(**services)
    router = APIRouter()
    config_lock = asyncio.Lock()

    @router.get('/api/bots/config')
    def read_config():
        return bot.public_config()

    def configured_bot(body, test_channel=None):
        if not isinstance(body, dict):
            raise HTTPException(400, '配置必须为对象')
        cfg = dict(bot.config)
        clear = body.get('clear_secrets', [])
        if not isinstance(clear, list) or any(key not in SECRET_FIELDS for key in clear):
            raise HTTPException(400, '清除密钥字段无效')
        for key, value in body.items():
            if key == 'clear_secrets':
                continue
            if key not in cfg:
                raise HTTPException(400, '不支持的机器人配置字段')
            if key.endswith('_enabled'):
                if not isinstance(value, bool):
                    raise HTTPException(400, '启用状态必须为布尔值')
            elif key == 'link_ttl_seconds':
                if type(value) is not int:
                    raise HTTPException(400, '链接有效期必须为整数秒')
            elif not isinstance(value, str) or len(value) > 4096:
                raise HTTPException(400, '配置字段必须为不超过 4096 字符的文本')
            if key not in SECRET_FIELDS or value:
                cfg[key] = value.strip() if isinstance(value, str) else value
        for key in clear:
            cfg[key] = ''
        try:
            candidate = BotService(**services, config=cfg, test_channel=test_channel)
        except (ValueError, TypeError) as error:
            bot.log('warning', f'通知渠道配置验证失败：{bot.error_detail(error)}', category='bot')
            raise HTTPException(400, '通知渠道配置无效：' + bot.error_detail(error))
        return candidate

    @router.put('/api/bots/config')
    async def write_config(body: dict):
        nonlocal bot
        async with config_lock:
            candidate = configured_bot(body)
            cfg = candidate.config
            previous = bot
            local = services['local_api']
            # Start the child before migration; no Telegram requests are sent by this check.
            try:
                if previous.tg_token and candidate.tg_token and (previous.telegram_url() != candidate.telegram_url()
                                          or previous.tg_token != candidate.tg_token):
                    await asyncio.to_thread(previous.telegram, 'logOut', {})
                await previous.stop()
                if candidate.tg_token and cfg['tg_local_enabled']:
                    await local.start(cfg)
                services['put']('bot_setting', 'config', cfg)
            except Exception as error:
                if previous.tg_token and previous.config['tg_local_enabled']:
                    await local.start(previous.config)
                else:
                    await local.stop()
                await previous.start()
                candidate.tg_http.close()
                candidate.wecom_http.close()
                previous.log('error', f'通知配置未保存：{candidate.error_detail(error)}', category='bot')
                raise HTTPException(502, '配置未保存：' + candidate.error_reason(error))
            bot = candidate
            app.state.bots = bot
            if not (candidate.tg_token and cfg['tg_local_enabled']):
                await local.stop()
            if cfg['tg_token'] != previous.config['tg_token']:
                services['put']('bot_setting', 'tg_offset', 0)
            await bot.start()
            bot.log('info', f'通知渠道配置已保存并生效：Telegram={bool(bot.tg_token)}，企业微信={bool(bot.crypt)}', category='bot')
            return bot.public_config()

    @router.post('/api/bots/test')
    async def test_connection(body: dict):
        async with config_lock:
            channel = body.get('channel')
            if channel not in ('tg', 'wecom', *WEBHOOK_CHANNELS):
                raise HTTPException(400, '不支持的通知渠道')
            candidate = configured_bot(body['config'], test_channel=channel) if 'config' in body else bot
            try:
                if channel == 'tg' and candidate.tg_token:
                    if candidate.config['tg_local_enabled']:
                        try:
                            candidate.local_api.validate(candidate.config)
                        except ValueError as error:
                            raise HTTPException(400, str(error)) from None
                        if not candidate.local_api.matches(candidate.config):
                            return {'detail': '内置服务程序和配置格式检查通过。保存后启动并切换服务，再次测试可验证 Telegram 连接；本次未退出云端或发送消息。'}
                    check = lambda: candidate.telegram('getMe', {})
                elif channel == 'wecom' and candidate.secret:
                    check = candidate.wecom_access
                elif channel in WEBHOOK_CHANNELS and candidate.config[channel + '_enabled']:
                    check = lambda: candidate.webhook(channel)
                else:
                    raise HTTPException(400, '请填写对应渠道的凭据后测试')
                try:
                    await asyncio.to_thread(check)
                except Exception as error:
                    detail = candidate.error_detail(error)
                    bot.log('error', f'{channel} 连接测试失败：{detail}', category='bot')
                    raise HTTPException(502, f'连接测试失败：{candidate.error_reason(error)}（原始错误：{detail}）')
            finally:
                if candidate is not bot:
                    candidate.tg_http.close()
                    candidate.wecom_http.close()
            bot.log('info', f'{channel} ' + ('配置格式检查通过' if channel in WEBHOOK_CHANNELS else '连接测试成功'), category='bot')
            return {'detail': 'Webhook 格式与配置有效，未发送消息；实际权限和平台安全设置会在首次通知时验证。' if channel in WEBHOOK_CHANNELS else
                    '连接测试成功；凭据可用。企业微信回调和白名单成员权限请在平台单独验证。' if channel == 'wecom' else '连接测试成功；机器人凭据可用，未发送消息。'}

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
    async def start():
        if bot.tg_token and bot.config['tg_local_enabled']:
            try:
                await bot.local_api.start(bot.config)
            except Exception as error:
                bot.log('error', f'内置 Telegram 服务未启动：{bot.error_detail(error)}；请在通知设置中修正后保存', category='bot')
        await bot.start()
    async def stop():
        await bot.stop()
        await bot.local_api.stop()
    app.router.add_event_handler('startup', start)
    # Stop command intake before the shared engine pauses workers on shutdown.
    app.router.on_shutdown.insert(0, stop)
    app.state.bots = bot
    return bot
