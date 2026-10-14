"""API adapter for the redesigned UI, preserving the original engine end to end."""
import asyncio
import copy
import hashlib
import json
import multiprocessing
import os
import pickle
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel, Field
import engine_worker
import local_library
from search_runtime import SearchWorkers, SearchCoordinator, deliver, terminate

BOT_SEARCH_IDLE_TIMEOUT = 90

ALIASES = {'migu': 'MiguMusicClient', 'netease': 'NeteaseMusicClient', 'qq': 'QQMusicClient',
           'kuwo': 'KuwoMusicClient', 'qianqian': 'QianqianMusicClient'}
LABELS = {'migu': '咪咕', 'netease': '网易云', 'qq': 'QQ音乐', 'kuwo': '酷我', 'qianqian': '千千',
          'KugouMusicClient': '酷狗', 'BilibiliMusicClient': '哔哩哔哩', 'GequbaoMusicClient': '歌曲宝',
          'FangpiMusicClient': '放屁音乐网', 'XimalayaMusicClient': '喜马拉雅'}
# A starting selection for deployments in mainland China, not a reachability
# guarantee. Domestic platforms and Apple/iTunes remain direct by default.
PROXY_RECOMMENDED = {'YouTubeMusicClient', 'SpotifyMusicClient', 'SoundCloudMusicClient',
                     'DeezerMusicClient', 'QobuzMusicClient', 'TIDALMusicClient', 'JooxMusicClient',
                     'JamendoMusicClient', 'FMAMusicClient', 'JioSaavnMusicClient', 'OpenGameArtMusicClient',
                     'SunoMusicClient', 'AudiusMusicClient', 'WikimediaCommonsMusicClient',
                     'CCMixterMusicClient', 'MP3JuiceMusicClient', 'MyFreeMP3MusicClient'}


def source_id(name):
    return next((key for key, value in ALIASES.items() if name == value), name)


def source_class(name):
    return ALIASES.get(name, name)


class SearchBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    sources: list[str] | None = None
    refresh: bool = False


class PlaybackEvent(BaseModel):
    track_id: str = Field(min_length=1, max_length=100)
    event: Literal['started', 'failed', 'stalled', 'recovered']
    stage: Literal['prepare', 'format', 'play', 'media', 'buffer']
    detail: str = Field(default='', max_length=2000)
    code: int = Field(default=0, ge=0, le=4)
    network_state: int = Field(default=0, ge=0, le=3)
    ready_state: int = Field(default=0, ge=0, le=4)
    position: float = Field(default=0, ge=0, le=10000000)
    browser: str = Field(default='', max_length=300)


class DownloadItem(BaseModel):
    track_id: str = Field(min_length=1, max_length=100)
    quality_id: str = Field(min_length=1, max_length=100)


class ImportBody(BaseModel):
    source: str = ''
    id: str = Field(min_length=1, max_length=2048)
    name: str = Field(default='', max_length=100)


class PlaylistNameBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FileTagsBody(BaseModel):
    title: str = Field(max_length=300)
    artist: str = Field(max_length=300)
    album: str = Field(max_length=300)


def install(old, root):
    data = Path(os.environ.get('MUSICDL_DATA_DIR', root / 'data')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    private = data / 'engine'
    private.mkdir(exist_ok=True)
    if os.name != 'nt':
        private.chmod(0o700)
    database = data / 'musicdl.db'
    db_lock = threading.RLock()
    db = sqlite3.connect(database, check_same_thread=False)
    if os.name != 'nt':
        database.chmod(0o600)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,updated INTEGER NOT NULL DEFAULT (unixepoch()),PRIMARY KEY(kind,id))')
    db.commit()

    def get(kind, key, default=None):
        with db_lock:
            row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else default

    def put(kind, key, value):
        with db_lock:
            db.execute('INSERT INTO records(kind,id,body) VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=unixepoch()', (kind, key, json.dumps(value, ensure_ascii=False)))
            db.commit()

    def values(kind):
        with db_lock:
            rows = db.execute('SELECT body FROM records WHERE kind=? ORDER BY updated DESC,id DESC LIMIT 10000', (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def remove(kind, key):
        with db_lock:
            db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, key))
            db.commit()

    registered = old.MusicClientBuilder.REGISTERED_MODULES
    catalog = [{'id': source_id(name), 'engine_name': name, 'label': LABELS.get(source_id(name), name.removesuffix('MusicClient')),
                'category': next((cat for cat, names in old.SOURCE_CATEGORIES.items() if name in names), '其他')}
               for name in registered]
    # Original config.json has priority. Never replace its parser settings with
    # the five-provider Rust schema; only migrate that schema when no original exists.
    if not old.CONFIG_PATH.exists() and get('config', 'main'):
        previous = get('config', 'main')
        cfg = old.manager.config
        cfg['music_sources'] = [source_class(s) for s in previous['sources'] if source_class(s) in registered]
        cfg['global_client_cfg']['search_size_per_source'] = previous['results_per_source']
        cfg['download_layout'] = previous.get('download_layout', 'source')
        cfg['lyrics_mode'] = previous.get('lyrics_mode', 'both')
        for key, setting in previous.get('source_settings', {}).items():
            name = source_class(key)
            if setting.get('cookie'):
                cfg.setdefault('cookies', {})[name] = setting['cookie']
            if setting.get('proxy'):
                cfg.setdefault('requests_overrides', {}).setdefault(name, {})['proxies'] = {'http': setting['proxy'], 'https': setting['proxy']}
        old.saveconfig(cfg)

    def config():
        cfg = old.manager.config
        settings = {}
        for name in registered:
            cookie = cfg.get('cookies', {}).get(name, '')
            if isinstance(cookie, dict):
                cookie = '; '.join(f'{k}={v}' for k, v in cookie.items())
            override = cfg.get('requests_overrides', {}).get(name, {})
            proxy = override.get('proxies', {}).get('https', '')
            settings[source_id(name)] = {'cookie': cookie, 'proxy': proxy}
        return {'sources': [source_id(s) for s in cfg['music_sources'] if s in registered],
                'available_sources': [{**source, 'proxy_recommended': source['engine_name'] in PROXY_RECOMMENDED,
                                       'init_error': old.manager.source_errors.get(source['engine_name'])} for source in catalog],
                'engine': f'musicdl {old.MUSICDL_VERSION}',
                'results_per_source': cfg['global_client_cfg'].get('search_size_per_source', 5),
                'search_timeout_seconds': cfg.get('request_timeout', 30),
                'playback_timeout_seconds': cfg.get('request_timeout', 30),
                'download_concurrency': int(os.environ.get('MUSICDL_MAX_DOWNLOAD_TASKS', '2')),
                'download_layout': cfg.get('download_layout', 'source'),
                'lyrics_mode': cfg.get('lyrics_mode', 'both'), 'embed_metadata': True,
                'source_settings': settings}

    def track(song, sid=None):
        supplied_id = sid is not None
        candidate = bool(song.raw_data.get('_musicdl_candidate'))
        ext = str(song.ext or '').lower().lstrip('.')
        bitrate = int(song.bitrate or 0) or None
        if bitrate and bitrate > 10000:
            bitrate //= 1000
        lossless = ext in old.MusicClient.LOSSLESS_QUALITY_DEFINITIONS
        quality = f'engine:{ext or "original"}:{bitrate or 0}'
        sid = sid or hashlib.sha256(f'{song.source}|{song.root_source}|{song.identifier}|{song.song_name}|{song.singers}|{quality}|{song.file_size_bytes}'.encode()).hexdigest()[:24]
        if candidate and not supplied_id:
            sid = hashlib.sha256(f'{song.source}|{song.identifier}|{song.raw_data.get("_musicdl_config", "")}|candidate'.encode()).hexdigest()[:24]
        playable = old.songplayable(song)
        label = (ext.upper() + (' 无损' if lossless else f' {bitrate} kbps' if bitrate else '')) if ext else '原音源提供的版本'
        return {'id': sid, 'source': source_id(str(song.source)), 'source_id': str(song.identifier or sid),
                'title': str(song.song_name or ''), 'artist': str(song.singers or ''), 'album': str(song.album or ''),
                'duration': float(song.duration_s or 0), 'cover': song.cover_url or None,
                'size_bytes': song.file_size_bytes if isinstance(song.file_size_bytes, (int, float)) and song.file_size_bytes >= 0 else (len(song.downloaded_contents) if isinstance(song.downloaded_contents, (bytes, bytearray)) else None),
                'size_label': str(song.file_size) if song.file_size else None,
                'episodes': len(song.episodes or []), 'root_source': song.root_source or '',
                'qualities': [] if candidate else [{'id': quality, 'label': label, 'codec': ext, 'bitrate': bitrate, 'lossless': lossless,
                               'availability': 'resolved', 'evidence': 'engine'}],
                'playback': {'state': 'pending' if candidate else 'ready' if playable else 'download_only', 'url': f'/api/stream/{sid}' if playable else None,
                             'codec': ext, 'bitrate': bitrate, 'preview_only': False,
                             'detail': '播放或下载时解析音质与音频地址' if candidate else None if playable else '该解析结果需要原音源下载器处理，可直接下载'}}

    saved_songs = OrderedDict()
    remember_lock = threading.RLock()
    song_sizes = OrderedDict()
    song_bytes = 0
    song_budget = max(1, int(os.environ.get('MUSICDL_SONG_CACHE_MB', '32'))) * 1024 * 1024

    def remember(song, sid=None):
        nonlocal song_bytes
        value = track(song, sid)
        sid = value['id']
        # No reserialization into a lossy URL-only record: custom stream objects,
        # source headers, downloaded bytes and per-track cookies are preserved.
        path = private / f'{sid}.song'
        serialized = pickle.dumps(song, protocol=pickle.HIGHEST_PROTOCOL)
        fingerprint = hashlib.sha256(serialized).digest()
        with remember_lock:
            if song.raw_data.get('_musicdl_candidate'):
                stored = get('track', sid)
                if (stored and stored.get('resolved_until', 0) > time.time() and path.exists()
                        and (not song.raw_data.get('_musicdl_refresh')
                             or stored.get('resolved_at', 0) >= song.raw_data.get('_musicdl_started', float('inf')))):
                    return stored['track']
            if saved_songs.get(sid) != fingerprint or not path.exists():
                temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                temp.write_bytes(serialized)
                os.replace(temp, path)
                resolved_at = time.time() if not song.raw_data.get('_musicdl_candidate') else 0
                put('track', sid, {'track': value, 'resolved_at': resolved_at,
                                   'resolved_until': resolved_at + 60 if resolved_at else 0})
                saved_songs[sid] = fingerprint
                saved_songs.move_to_end(sid)
                while len(saved_songs) > 3000:
                    saved_songs.popitem(last=False)
            with old.SONG_LOCK:
                song_bytes -= song_sizes.pop(sid, 0)
                old.SONGS.pop(sid, None)
                if sid in old.SONG_ORDER:
                    old.SONG_ORDER.remove(sid)
                size = len(serialized)
                while size <= song_budget and song_sizes and (song_bytes + size > song_budget or len(old.SONG_ORDER) >= old.SONG_ORDER.maxlen):
                    evicted, used = song_sizes.popitem(last=False)
                    song_bytes -= used
                    old.SONGS.pop(evicted, None)
                    if evicted in old.SONG_ORDER:
                        old.SONG_ORDER.remove(evicted)
                if size <= song_budget:
                    if len(old.SONG_ORDER) == old.SONG_ORDER.maxlen:
                        old.SONGS.pop(old.SONG_ORDER.popleft(), None)
                    old.SONG_ORDER.append(sid)
                    old.SONGS[sid] = song
                    song_sizes[sid] = size
                    song_bytes += size
        return value

    def song_for(sid):
        if not re.fullmatch(r'[a-f0-9]{12,64}', sid):
            raise HTTPException(404, '曲目不存在')
        song = old.getsong(sid)
        if song:
            return song
        path = private / f'{sid}.song'
        if path.exists():
            song = pickle.loads(path.read_bytes())
            remember(song, sid)
            return song
        stored = get('track', sid)
        if not stored:
            raise HTTPException(404, '曲目已过期，请重新搜索')
        value = stored.get('track', stored)
        results = engine_worker.search(copy.deepcopy(old.manager.config), source_class(value['source']), f"{value['title']} {value['artist']}")
        for candidate in results:
            if str(candidate.song_name or '').strip() == value['title'].strip() and str(candidate.singers or '').strip() == value['artist'].strip():
                remember(candidate, sid)
                return candidate
        raise HTTPException(404, '未找到完全匹配的原曲目，请重新搜索选择')

    router = APIRouter()
    playback_reports = OrderedDict()

    @old.app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        route = getattr(request.scope.get('route'), 'path', request.url.path[:200])
        old.pushlog('error' if error.status_code >= 500 else 'warning',
                    f'接口拒绝：{request.method} {route} · HTTP {error.status_code} · {error.detail}', category='system')
        return await http_exception_handler(request, error)

    @old.app.middleware('http')
    async def api_errors(request: Request, call_next):
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            if request.url.path.startswith('/api/'):
                route = getattr(request.scope.get('route'), 'path', request.url.path[:200])
                old.pushlog('error', f'接口异常：{request.method} {route}\n{traceback.format_exc()}', category='system')
            raise
        if request.url.path.startswith('/api/') and response.status_code >= 400:
            route = getattr(request.scope.get('route'), 'path', request.url.path[:200])
            old.pushlog('error' if response.status_code >= 500 else 'warning',
                        f'接口失败：{request.method} {route} · HTTP {response.status_code} · 耗时 {int((time.monotonic() - started) * 1000)} ms', category='system')
        return response

    @router.post('/api/playback/events')
    def playback_event(body: PlaybackEvent):
        key = (body.track_id, body.event, body.stage, body.code, body.detail)
        now = time.monotonic()
        with old.LOG_LOCK:
            if now - playback_reports.get(key, -100) < 5:
                return {'recorded': False}
            playback_reports[key] = now
            while len(playback_reports) > 128:
                playback_reports.popitem(last=False)
        record = get('track', body.track_id) or {}
        track = record.get('track', record)
        level = 'error' if body.event == 'failed' else 'warning' if body.event == 'stalled' else 'info'
        event_label = {'started': '开始播放', 'failed': '播放失败', 'stalled': '缓冲停滞', 'recovered': '恢复播放'}[body.event]
        stage_label = {'prepare': '准备曲目', 'format': '格式检查', 'play': '启动播放', 'media': '媒体解码/网络', 'buffer': '缓冲'}[body.stage]
        old.pushlog(level, f'浏览器试听 {event_label} · 阶段 {stage_label} · {track.get("title", "未知曲目")} · '
                    f'{track.get("source", "未知音源")} · 编码 {track.get("playback", {}).get("codec", "未知")}\n'
                    f'{body.detail}\nMediaError={body.code}，networkState={body.network_state}，readyState={body.ready_state}，'
                    f'播放位置={body.position:.2f}s\n浏览器：{body.browser}', category='playback', task_id=body.track_id)
        return {'recorded': True}
    search_pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'), max_tasks_per_child=8)
    download_pool = ThreadPoolExecutor(max_workers=max(1, min(4, int(os.environ.get('MUSICDL_MAX_DOWNLOAD_TASKS', '2')))))
    searches = OrderedDict()
    search_slots = asyncio.Semaphore(max(1, min(6, int(os.environ.get('MUSICDL_SEARCH_WORKERS', '4')))))
    workers = SearchWorkers(root, idle_seconds=max(0, int(os.environ.get('MUSICDL_SEARCH_WORKER_IDLE', '20'))))
    search_budget = max(1, int(os.environ.get('MUSICDL_SEARCH_BUDGET', '120')))
    queue_budget = max(1, int(os.environ.get('MUSICDL_SEARCH_QUEUE_TIMEOUT', '30')))
    runners, runner_lock = {}, threading.RLock()

    def read_worker_logs(path, cursor=0):
        try:
            with path.open('rb') as handle:
                handle.seek(cursor)
                chunk = handle.read(262144)
            end = chunk.rfind(b'\n') + 1
            for line in chunk[:end].splitlines():
                try:
                    entry = json.loads(line)
                    old.pushlog(entry['level'], entry['message'], category=entry.get('category', 'engine'),
                                task_id=entry.get('task_id'), timestamp=entry.get('time'))
                except (ValueError, KeyError, TypeError):
                    pass
            return cursor + end
        except OSError:
            return cursor

    async def collect_worker_logs(future, path):
        cursor = 0
        try:
            while not future.done():
                cursor = read_worker_logs(path, cursor)
                await asyncio.wait({future}, timeout=.5)
            return future.result()
        finally:
            while path.exists():
                updated = read_worker_logs(path, cursor)
                if updated == cursor:
                    break
                cursor = updated
            path.unlink(missing_ok=True)

    @router.get('/')
    def index():
        return FileResponse(root / 'frontend/dist/index.html')

    @router.get('/api/health')
    def health():
        return {'ok': True, 'backend': 'original-musicdl', 'version': old.MUSICDL_VERSION, 'sources': len(registered)}

    @router.get('/api/auth/status')
    def auth_status(request: Request):
        return {'required': old.AUTH_ENABLED, 'authenticated': not old.AUTH_ENABLED or old._session_valid(request.cookies.get(old.SESSION_COOKIE))}

    @router.get('/api/config')
    def read_config():
        return config()

    @router.get('/api/engine/config')
    def read_engine_config():
        return copy.deepcopy(old.manager.config)

    @router.put('/api/engine/config')
    def write_engine_config(body: dict):
        try:
            # Keep the complete original configuration API and every provider's
            # init parameters, request overrides, threading and parser rules.
            old.putconfig(old.ConfigBody(**body))
        except (ValueError, AssertionError) as error:
            raise HTTPException(400, str(error))
        return {'saved': True, 'source_errors': old.manager.source_errors}

    @router.put('/api/config')
    def write_config(body: dict):
        cfg = copy.deepcopy(old.manager.config)
        enabled = [source_class(s) for s in body.get('sources', [])]
        if not enabled or any(s not in registered for s in enabled):
            raise HTTPException(400, '请选择已注册的音源')
        count, timeout = int(body.get('results_per_source', 5)), int(body.get('search_timeout_seconds', 30))
        if not 1 <= count <= 50 or not 0 <= timeout <= 300:
            raise HTTPException(400, '结果数或请求超时超出范围')
        cfg['music_sources'] = list(dict.fromkeys(enabled))
        cfg['global_client_cfg']['search_size_per_source'] = count
        cfg['request_timeout'] = timeout
        cfg['download_layout'] = body.get('download_layout', cfg['download_layout'])
        cfg['lyrics_mode'] = body.get('lyrics_mode', cfg['lyrics_mode'])
        if cfg['download_layout'] not in {'source', 'flat', 'artist', 'artist_album'} or cfg['lyrics_mode'] not in {'both', 'lrc', 'embed', 'none'}:
            raise HTTPException(400, '下载偏好无效')
        before = config()['source_settings']
        for key, setting in body.get('source_settings', {}).items():
            name = source_class(key)
            if name not in registered:
                raise HTTPException(400, '不支持的音源配置')
            if setting.get('cookie', '') != before[key]['cookie']:
                cfg.setdefault('cookies', {})[name] = setting.get('cookie', '')
            if setting.get('proxy', '') != before[key]['proxy']:
                override = cfg.setdefault('requests_overrides', {}).setdefault(name, {})
                proxy = setting.get('proxy', '').strip()
                if proxy and not proxy.startswith(('http://', 'https://')):
                    raise HTTPException(400, '代理必须使用 HTTP / HTTPS')
                if proxy:
                    override['proxies'] = {'http': proxy, 'https': proxy}
                else:
                    override.pop('proxies', None)
        try:
            old.manager.applyconfig(cfg)
        except Exception as error:
            raise HTTPException(400, f'原音源配置初始化失败：{type(error).__name__}')
        return {'saved': True, 'restart_required': False, 'source_errors': old.manager.source_errors}

    def emit(job, kind, value):
        job['events'].append((len(job['events']) + 1, kind, value))
        job['changed'].set()

    async def source_results(cfg, source, keyword, on_song=None):
        queued = time.monotonic()
        await asyncio.wait_for(search_slots.acquire(), queue_budget)
        operation = uuid.uuid4().hex
        log_path = private / f'{operation}.search.log'
        input_path = private / f'{operation}.search.input'
        output_path = private / f'{operation}.search.output'
        partial_path = private / f'{operation}.search.partial'
        partial_cursor = 0
        def read_partial():
            nonlocal partial_cursor
            songs = []
            if on_song and partial_path.exists():
                with partial_path.open('rb') as handle:
                    handle.seek(partial_cursor)
                    while len(header := handle.read(8)) == 8:
                        size = int.from_bytes(header, 'big')
                        payload = handle.read(size)
                        if len(payload) != size:
                            break
                        songs.append(pickle.loads(payload))
                        partial_cursor = handle.tell()
            return songs
        async def publish_partial():
            nonlocal first
            for song in await asyncio.to_thread(read_partial):
                if first is None:
                    first = time.monotonic() - started
                await deliver(on_song, song)
        process, cursor, reusable, first = None, 0, False, None
        started = time.monotonic()
        worker_key = hashlib.sha256(json.dumps([cfg, source], sort_keys=True).encode()).hexdigest()
        old.pushlog('info', f'{source} 开始搜索：{keyword} · 排队 {started - queued:.2f}s', category='search', task_id=operation)
        try:
            async with asyncio.timeout(search_budget):
                await asyncio.to_thread(input_path.write_bytes, pickle.dumps((cfg, source, keyword, str(log_path), operation, str(partial_path))))
                process = await workers.acquire(worker_key, input_path, output_path)
                while not output_path.exists() and process.poll() is None:
                    cursor = await asyncio.to_thread(read_worker_logs, log_path, cursor)
                    await publish_partial()
                    await asyncio.sleep(.1)
                if not output_path.exists():
                    raise RuntimeError('原音源搜索进程异常退出，请查看运行日志')
                songs = pickle.loads(await asyncio.to_thread(output_path.read_bytes))
                if isinstance(songs, dict) and 'worker_error' in songs:
                    raise RuntimeError(songs['worker_error'])
                reusable = True
                return songs
        finally:
            # Keep the slot until termination completes, including cancellation.
            async def cleanup():
                nonlocal cursor
                try:
                    await workers.release(worker_key, process, reusable)
                    await publish_partial()
                    while log_path.exists():
                        updated = await asyncio.to_thread(read_worker_logs, log_path, cursor)
                        if updated == cursor:
                            break
                        cursor = updated
                    for path in (input_path, output_path, log_path, partial_path, output_path.with_suffix('.tmp')):
                        path.unlink(missing_ok=True)
                    old.pushlog('info', f'{source} 搜索耗时 {time.monotonic() - started:.2f}s · '
                                f'首条增量 {f"{first:.2f}s" if first is not None else "无"}', category='search', task_id=operation)
                finally:
                    search_slots.release()
            cleanup_task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                await cleanup_task
                raise

    coordinator = SearchCoordinator(source_results, ttl=max(0, int(os.environ.get('MUSICDL_SEARCH_CACHE_TTL', '30'))))

    async def run_search(job, cfg, sources, keyword, refresh=False):
        async def run_source(name):
            sid = source_id(name)
            emitted = {}
            async def publish(song):
                if job['cancelled']:
                    return
                value = await asyncio.to_thread(remember, song)
                if emitted.get(value['id']) != value:
                    emitted[value['id']] = value
                    emit(job, 'track', value)
            try:
                songs = await coordinator.run(cfg, name, keyword, publish, refresh)
                old.pushlog('info', f'{name} 搜索完成，{len(songs)} 个结果', category='search')
                if job['cancelled']:
                    return
                for song in songs:
                    await publish(song)
                emit(job, 'source', {'id': sid, 'status': {'state': 'complete', 'count': len(songs)}})
            except asyncio.CancelledError:
                raise
            except Exception as error:
                old.pushlog('error', f'{name} 搜索失败\n{traceback.format_exc()}', category='search')
                emit(job, 'source', {'id': sid, 'status': {'state': 'failed', 'count': len(emitted), 'detail': f'原音源搜索失败（{type(error).__name__}），可检查音源设置后重试'}})
        try:
            await asyncio.gather(*(run_source(name) for name in sources))
        finally:
            if not job['done']:
                emit(job, 'done', {'cancelled': job['cancelled']})
                job['done'] = True
            if job.get('disconnect'):
                job['disconnect'].cancel()
            old.pushlog('info', '搜索已停止' if job['cancelled'] else '全部音源搜索结束', category='search')

    @router.post('/api/search/jobs')
    async def search_start(body: SearchBody):
        keyword = body.keyword.strip()
        cfg = copy.deepcopy(old.manager.config)
        cfg['_musicdl_candidates'] = os.environ.get('MUSICDL_LAZY_SEARCH', '0') == '1'
        cfg['_musicdl_refresh'] = body.refresh
        sources = [source_class(s) for s in (body.sources or config()['sources'])]
        if not keyword or not sources or any(s not in cfg['music_sources'] or s not in registered for s in sources):
            raise HTTPException(400, '关键词或启用音源无效')
        if sum(not job['done'] for job in searches.values()) >= 6:
            raise HTTPException(429, '同时搜索过多，请先停止旧搜索')
        while len(searches) >= 32:
            ended = next((key for key, value in searches.items() if value['done']), None)
            if ended is None:
                break
            del searches[ended]
        ident = uuid.uuid4().hex
        old.pushlog('info', f'开始搜索：{keyword}，{len(set(sources))} 个音源', category='search', task_id=ident)
        job = {'events': [], 'changed': asyncio.Event(), 'done': False, 'cancelled': False, 'subscribers': 0, 'disconnect': None}
        searches[ident] = job
        job['disconnect'] = asyncio.get_running_loop().call_later(30, lambda: asyncio.create_task(search_stop(ident)))
        job['task'] = asyncio.create_task(run_search(job, cfg, list(dict.fromkeys(sources)), keyword, body.refresh))
        return {'id': ident}

    @router.delete('/api/search/jobs/{ident}')
    async def search_stop(ident: str):
        if ident in searches:
            job = searches[ident]
            if job.get('disconnect'):
                job['disconnect'].cancel()
            if not job['done']:
                if not job['cancelled']:
                    job['cancelled'] = True
                    job['task'].cancel()
                await asyncio.shield(asyncio.gather(job['task'], return_exceptions=True))
                # A task cancelled before its first step never enters finally.
                if not job['done']:
                    emit(job, 'done', {'cancelled': True})
                    job['done'] = True
        return {'cancelled': True}

    @router.get('/api/search/jobs/{ident}/events')
    async def search_events(ident: str, request: Request, after: int = 0):
        if ident not in searches:
            raise HTTPException(404, '搜索已过期，请重新搜索')
        job = searches[ident]
        try:
            cursor = max(after, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            raise HTTPException(400, '事件序号无效')
        async def events():
            nonlocal cursor
            job['subscribers'] += 1
            if job.get('disconnect'):
                job['disconnect'].cancel()
            try:
                while True:
                    job['changed'].clear()
                    for sequence, kind, value in job['events'][cursor:]:
                        if sequence > cursor:
                            cursor = sequence
                            yield f'id: {sequence}\nevent: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n'
                    if job['done'] or await request.is_disconnected():
                        return
                    try:
                        await asyncio.wait_for(job['changed'].wait(), 10)
                    except asyncio.TimeoutError:
                        yield ': keepalive\n\n'
            finally:
                job['subscribers'] -= 1
                if not job['done'] and not job['subscribers']:
                    job['disconnect'] = asyncio.get_running_loop().call_later(
                        10, lambda: asyncio.create_task(search_stop(ident)))
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    async def resolved_track(sid):
        song = await asyncio.to_thread(song_for, sid)
        if song.raw_data.get('_musicdl_candidate'):
            cfg = copy.deepcopy(old.manager.config)
            if song.source not in cfg['music_sources']:
                raise HTTPException(409, '该音源已停用，请重新搜索')
            cfg['_musicdl_resolve'] = song.raw_data['search']
            try:
                songs = await coordinator.run(cfg, song.source, str(song.identifier),
                                              refresh=bool(song.raw_data.get('_musicdl_refresh')))
            except TimeoutError:
                raise HTTPException(504, '排队或音频解析超时，请稍后重试或更换音源') from None
            except RuntimeError:
                raise HTTPException(502, '音源解析失败，请查看运行日志或更换音源') from None
            if not songs:
                raise HTTPException(422, '该曲目暂未解析到可用音频，请重试或更换音源')
            song = songs[0]
        return await asyncio.to_thread(remember, song, sid)

    @router.post('/api/tracks/{sid}/playback')
    async def playback(sid: str):
        try:
            record = await asyncio.to_thread(get, 'track', sid)
            result = (await asyncio.to_thread(library_file, record['local_path']))['track'] if record and record.get('local_path') else await resolved_track(sid)
            old.pushlog('info', f'准备试听：{result["title"]} · {result["source"]} · {result["playback"]["codec"]} · 状态 {result["playback"]["state"]}', category='playback', task_id=sid)
            return result
        except Exception:
            old.pushlog('error', f'准备试听失败\n{traceback.format_exc()}', category='playback', task_id=sid)
            raise

    @router.post('/api/tracks/{sid}/versions')
    async def versions(sid: str):
        return await resolved_track(sid)

    @router.get('/api/tracks/{sid}/lyrics')
    async def lyrics(sid: str):
        record = await asyncio.to_thread(get, 'track', sid)
        if record and record.get('local_path'):
            try:
                return {'text': await asyncio.to_thread(local_library.read_lyrics, resolve_library_file(record['local_path']))}
            except (OSError, ValueError, local_library.mutagen.MutagenError):
                return {'text': ''}
        await resolved_track(sid)
        text = (await asyncio.to_thread(song_for, sid)).lyric
        return {'text': text if text and str(text).lower() not in {'none', 'null'} else ''}

    @router.get('/api/tracks/{sid}/episodes')
    def episodes(sid: str):
        return [remember(song) for song in song_for(sid).episodes or []]

    @router.get('/api/stream/{sid}')
    def stream(sid: str, request: Request):
        try:
            return old.stream_song(sid, request, song=song_for(sid))
        except Exception:
            old.pushlog('error', f'试听音频请求失败\n{traceback.format_exc()}', category='playback', task_id=sid)
            raise

    @router.get('/api/favorites')
    def favorites():
        return values('favorite')

    @router.put('/api/favorites/{sid}')
    def favorite_add(sid: str):
        record = get('track', sid)
        if not record:
            raise HTTPException(404, '曲目不存在')
        put('favorite', sid, record['track'])
        return {'saved': True}

    @router.delete('/api/favorites/{sid}')
    def favorite_remove(sid: str):
        remove('favorite', sid)
        return {'saved': True}

    @router.get('/api/playlist')
    def playlist_read():
        saved = get('playlist', 'main', [])
        return saved if not saved or isinstance(saved[0], dict) else [get('track', sid)['track'] for sid in saved if get('track', sid)]

    @router.put('/api/playlist')
    def playlist_write(ids: list[str]):
        if len(ids) > 3000:
            raise HTTPException(400, '播放列表最多 3000 首')
        result = [get('track', sid)['track'] for sid in ids if get('track', sid)]
        put('playlist', 'main', result)
        return {'saved': True}

    @router.post('/api/playlist/import')
    async def playlist_import(body: ImportBody):
        url = body.id.strip()
        if not url.startswith(('http://', 'https://')):
            templates = {'netease': 'https://music.163.com/playlist?id={}', 'qq': 'https://y.qq.com/n/ryqq/playlist/{}',
                         'kuwo': 'https://www.kuwo.cn/playlist_detail/{}', 'migu': 'https://music.migu.cn/v3/music/playlist/{}'}
            if body.source not in templates or not re.fullmatch(r'[\w-]+', url):
                raise HTTPException(400, '该音源请粘贴完整歌单链接')
            url = templates[body.source].format(url)
        old.validatepublicurl(url, '歌单链接', old.PLAYLIST_ALLOWED_HOSTS)
        operation = uuid.uuid4().hex
        log_path = private / f'{operation}.playlist.log'
        old.pushlog('info', '开始解析歌单', category='playlist', task_id=operation)
        future = asyncio.wrap_future(search_pool.submit(engine_worker.playlist_logged, copy.deepcopy(old.manager.config), url, str(log_path), operation))
        # Keep draining the original parser even when the browser disconnects.
        async def finish_import():
            try:
                parsed = await collect_worker_logs(future, log_path)
                songs, issues = parsed['songs'], parsed['issues']
                if not songs and not issues:
                    old.pushlog('warning', '未解析到曲目：请检查歌单链接、音源设置或 Cookie。原引擎未返回具体原因。', category='playlist', task_id=operation)
                    raise HTTPException(422, '未解析到曲目，未创建歌单。请检查歌单链接、公开状态和音源配置，更多信息可查看运行日志。')
                imported = list({t['id']: t for t in (remember(song) for song in songs)}.values())
                source = imported[0]['source'] if imported else body.source
                # Native parsers place the actual playlist title in their work directory.
                native_name = ''
                if songs:
                    directory = re.split(r'[/\\]', str(songs[0].work_dir or ''))[-1]
                    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2} (.+)', directory)
                    native_name = match[1][:100] if match else ''
                name = body.name.strip() or native_name or f'{LABELS.get(source, source) or "平台"}歌单 {len(values("collection")) + 1}'
                detail = f'成功导入 {len(imported)} 首' + (f'，{len(issues)} 首未获取到可用资源，详见未导入曲目。' if issues else '。')
                collection = {'id': operation, 'name': name, 'source': source,
                              'source_url': url, 'created_at': int(time.time()),
                              'tracks': imported, 'issues': issues, 'detail': detail,
                              'task_id': operation}
                put('collection', operation, collection)
                old.pushlog('warning' if issues else 'info', detail, category='playlist', task_id=operation)
                return {'playlist': collection, 'tracks': imported, 'detail': detail}
            except HTTPException:
                raise
            except Exception:
                old.pushlog('error', f'歌单导入失败\n{traceback.format_exc()}', category='playlist', task_id=operation)
                raise HTTPException(502, '歌单解析失败，请在运行日志中查看原因；可稍后重试或检查音源配置。')
        return await asyncio.shield(asyncio.create_task(finish_import()))

    @router.get('/api/playlists')
    def collections():
        return [{key: value for key, value in c.items() if key not in {'tracks', 'issues'}} |
                {'track_count': len(c['tracks']), 'issue_count': len(c['issues']),
                 'cover': next((t['cover'] for t in c['tracks'] if t.get('cover')), None)}
                for c in values('collection')]

    def collection_for(ident):
        value = get('collection', ident)
        if value is None:
            raise HTTPException(404, '歌单不存在')
        return value

    @router.get('/api/playlists/{ident}')
    def collection_read(ident: str):
        return collection_for(ident)

    @router.patch('/api/playlists/{ident}')
    def collection_rename(ident: str, body: PlaylistNameBody):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, '请输入歌单名称')
        value = collection_for(ident)
        value['name'] = name
        put('collection', ident, value)
        return value

    @router.delete('/api/playlists/{ident}')
    def collection_delete(ident: str):
        collection_for(ident)
        remove('collection', ident)
        # Shared track records and audio files belong to other library views too.
        return {'deleted': True}

    @router.delete('/api/playlists/{ident}/tracks/{sid}')
    def collection_remove_track(ident: str, sid: str):
        value = collection_for(ident)
        value['tracks'] = [t for t in value['tracks'] if t['id'] != sid]
        put('collection', ident, value)
        return value

    def file_token(path):
        path = Path(path).resolve()
        for base in old.manager.workdirs():
            if path.is_relative_to(base):
                return json.dumps([str(base), str(path.relative_to(base))], ensure_ascii=False)
        return None

    def run_download(job, song, control):
        ident = job['id']
        cfg = copy.deepcopy(old.manager.config)
        input_path, output_path = private / f'{ident}.input', private / f'{ident}.progress'
        staging = old.FILE_ROOT / '.partial' / ident
        log_path, log_cursor = Path(str(output_path) + '.log'), 0
        def clear_staging():
            if staging.exists() and staging.resolve().is_relative_to(old.FILE_ROOT.resolve()) and not staging.is_symlink():
                shutil.rmtree(staging)
        try:
            with runner_lock:
                if control['stop']:
                    return
                original_song = copy.deepcopy(song)
                old.applylayout(original_song)
                # Isolate each job's partial files; publish only completed outputs.
                if staging.is_symlink() or not staging.resolve().is_relative_to(old.FILE_ROOT.resolve()):
                    raise ValueError('临时下载目录不在音乐库中')
                staging.mkdir(parents=True, exist_ok=True)
                output_path.unlink(missing_ok=True)
                intended_dir = Path(original_song.work_dir).resolve()
                if not any(intended_dir.is_relative_to(base) for base in old.manager.workdirs()):
                    raise ValueError('曲目保存路径不在音乐库中')
                original_song.work_dir, original_song._save_path = str(staging), None
                input_path.write_bytes(pickle.dumps({'song': original_song, 'config': cfg,
                                                    'bytes': job['bytes'], 'total_bytes': job['total_bytes']}, protocol=pickle.HIGHEST_PROTOCOL))
                process = subprocess.Popen([sys.executable, str(root / 'engine_worker.py'), str(input_path), str(output_path)],
                                           cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                           start_new_session=os.name != 'nt')
                control['process'] = process
            last_progress, last_saved = None, 0.0
            while process.poll() is None:
                log_cursor = read_worker_logs(log_path, log_cursor)
                if output_path.exists():
                    try:
                        progress = json.loads(output_path.read_text(encoding='utf-8'))
                        with runner_lock:
                            if not control['stop']:
                                job.update({k: progress[k] for k in ('bytes', 'total_bytes', 'detail') if k in progress})
                                job['status'] = 'downloading'
                                snapshot = (job['bytes'], job['total_bytes'], job['detail'])
                                now = time.monotonic()
                                if snapshot != last_progress and now - last_saved >= 2:
                                    put('download', ident, job)
                                    last_progress, last_saved = snapshot, now
                    except (ValueError, OSError):
                        pass
                time.sleep(.25)
            with runner_lock:
                log_cursor = read_worker_logs(log_path, log_cursor)
                if control['stop']:
                    return
                outcome = json.loads(output_path.read_text(encoding='utf-8')) if output_path.exists() else {}
                if outcome.get('status') != 'complete':
                    job.update(status='failed', detail=outcome.get('detail', '原音源下载进程退出，请查看日志'))
                else:
                    completed = [Path(p).resolve() for p in outcome.get('files', [])]
                    if not completed or any(not p.is_relative_to(staging.resolve()) for p in completed):
                        raise ValueError('下载器文件不在该任务目录中')
                    moved = {}
                    intended_dir.mkdir(parents=True, exist_ok=True)
                    publish_stems = {}
                    for index, audio_path in enumerate(completed, 1):
                        stem = local_library.download_stem(job['track']['title'], job['track'].get('artist', ''))
                        if len(completed) > 1:
                            stem += f'-{index}'
                        siblings = [p for p in audio_path.parent.iterdir() if p.is_file() and p.with_suffix('') == audio_path.with_suffix('')]
                        parent = intended_dir / audio_path.relative_to(staging).parent
                        candidate, collision = stem, 0
                        while any((parent / (candidate + p.suffix)).exists() for p in siblings):
                            collision += 1
                            candidate = f'{stem}-{ident[:8]}' + (f'-{collision}' if collision > 1 else '')
                        publish_stems[audio_path] = candidate
                    for file in staging.rglob('*'):
                        if not file.is_file() or file.is_symlink():
                            continue
                        resolved = file.resolve()
                        if resolved not in completed and not any(file.with_suffix('') == p.with_suffix('') for p in completed):
                            continue
                        relative = file.relative_to(staging)
                        audio_match = next((p for p in completed if file.with_suffix('') == p.with_suffix('')), None)
                        if audio_match:
                            relative = relative.with_name(publish_stems[audio_match] + file.suffix)
                        target = intended_dir / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.exists():
                            target = target.with_name(f'{target.stem}-{ident[:8]}{target.suffix}')
                        os.replace(file, target)
                        moved[resolved] = target
                    audio = moved.get(completed[0])
                    if audio is None:
                        raise ValueError('下载器没有保存音频文件')
                    job.update(status='complete', bytes=audio.stat().st_size, total_bytes=audio.stat().st_size,
                               file=file_token(audio), detail=outcome['detail'])
                old.pushlog('info' if job['status'] == 'complete' else 'error',
                            f'{job["track"]["title"]} · {job["track"]["source"]}：{job["detail"]}', category='download', task_id=ident)
                put('download', ident, job)
        except Exception as error:
            old.pushlog('error', f'下载任务异常\n{traceback.format_exc()}', category='download', task_id=ident)
            with runner_lock:
                if not control['stop']:
                    job.update(status='failed', detail=f'原引擎下载失败：{type(error).__name__}')
                    put('download', ident, job)
        finally:
            while log_path.exists():
                updated = read_worker_logs(log_path, log_cursor)
                if updated == log_cursor:
                    break
                log_cursor = updated
            log_path.unlink(missing_ok=True)
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            if job['status'] == 'complete' or control.get('cancel'):
                clear_staging()
            with runner_lock:
                runners.pop(ident, None)
            control['finished'].set()

    def launch(job, song):
        old.pushlog('info', f'{job["track"]["title"]} · {job["track"]["source"]} 加入下载队列，音质 {job["quality_label"]}',
                    category='download', task_id=job['id'])
        control = {'stop': False, 'process': None, 'finished': threading.Event()}
        runners[job['id']] = control
        download_pool.submit(run_download, job, song, control)

    @router.get('/api/downloads')
    def downloads():
        return values('download')

    @router.post('/api/downloads')
    def enqueue(items: list[DownloadItem]):
        return enqueue_tracks(items)

    def enqueue_tracks(items, job_ids=None):
        if not 1 <= len(items) <= 200:
            raise HTTPException(400, '每批请选择 1–200 首')
        prepared = []
        for item in items:
            song = song_for(item.track_id)
            value = track(song, item.track_id)
            if item.quality_id != value['qualities'][0]['id']:
                raise HTTPException(409, '原解析结果的音质已变化，请重新打开音质选择')
            if song.episodes:
                raise HTTPException(400, '请先展开分集，再选择具体曲目下载')
            prepared.append((song, value))
        result = []
        with runner_lock:
            for index, (song, value) in enumerate(prepared):
                quality = value['qualities'][0]
                ident = job_ids[index] if job_ids else uuid.uuid4().hex
                existing = get('download', ident)
                if existing:
                    result.append(existing)
                    continue
                job = {'id': ident, 'track': value, 'quality_id': quality['id'], 'quality_label': quality['label'],
                       'status': 'queued', 'bytes': 0, 'total_bytes': None, 'detail': '等待原音源下载器',
                       'file': None, 'created_at': int(time.time())}
                put('download', job['id'], job)
                launch(job, song)
                result.append(job)
        return result

    @router.post('/api/downloads/{ident}/{action}')
    def control_download(ident: str, action: str):
        with runner_lock:
            job = get('download', ident)
            if not job:
                raise HTTPException(404, '任务不存在')
            active = runners.get(ident)
            if action in {'retry', 'resume'}:
                if active or job['status'] == 'complete':
                    raise HTTPException(409, '任务正在执行或已经完成')
                song = song_for(job['track']['id'])
                if track(song)['qualities'][0]['id'] != job['quality_id']:
                    raise HTTPException(409, '音质已变化，请重新选择下载版本')
                if job['status'] == 'cancelled':
                    job.update(bytes=0, total_bytes=None)
                job.update(status='queued', detail='准备继续下载，检查音源续传支持')
                put('download', ident, job)
                launch(job, song)
                return job
            if action not in {'pause', 'cancel'} or job['status'] == 'complete':
                raise HTTPException(400, '操作无效')
            if active:
                active['stop'] = True
                active['cancel'] = action == 'cancel'
                terminate(active['process'])
            job.update(status='paused' if action == 'pause' else 'cancelled',
                       detail='已暂停，保留下载进度' if action == 'pause' else '已取消，临时文件已清理')
            put('download', ident, job)
            old.pushlog('info', f'{job["track"]["title"]}：{job["detail"]}', category='download', task_id=ident)
        if active:
            active['finished'].wait(20)
        if action == 'cancel' and (not active or active['finished'].is_set()):
            staging = old.FILE_ROOT / '.partial' / ident
            if staging.exists() and staging.resolve().is_relative_to(old.FILE_ROOT.resolve()) and not staging.is_symlink():
                shutil.rmtree(staging)
            job.update(bytes=0, total_bytes=None)
            put('download', ident, job)
        return job

    @router.delete('/api/downloads/{ident}')
    def remove_download(ident: str):
        with runner_lock:
            job = get('download', ident)
            if not job:
                raise HTTPException(404, '任务不存在')
            if ident in runners or job['status'] not in {'complete', 'failed', 'paused', 'cancelled'}:
                raise HTTPException(409, '请先取消或暂停下载，等待任务停止后再移除记录')
            staging = old.FILE_ROOT / '.partial' / ident
            if staging.exists() and staging.resolve().is_relative_to(old.FILE_ROOT.resolve()) and not staging.is_symlink():
                shutil.rmtree(staging)
            remove('download', ident)
            old.pushlog('info', f'已移除下载记录：{job["track"]["title"]}（保留已完成的音乐文件）', category='download', task_id=ident)
        return {'removed': True}

    @router.get('/api/files')
    def files():
        result = []
        for value in old.files()['files']:
            if '.partial' in Path(value['rel']).parts:
                continue
            if value['name'].startswith('.musicdl-tags-'):
                continue
            token = json.dumps([value['dir'], value['rel']], ensure_ascii=False)
            try:
                result.append(library_file(token))
            except (HTTPException, OSError):
                continue
        return result

    def resolve_library_file(path):
        try:
            base, relative = json.loads(path)
            if not isinstance(base, str) or not isinstance(relative, str):
                raise ValueError('Invalid file token')
        except (ValueError, TypeError):
            # Paths saved by the earlier Rust build remain readable under its root.
            base, relative = str(old.FILE_ROOT), path
        if '.partial' in Path(relative).parts:
            raise HTTPException(403, '不能操作下载中的临时文件')
        return old.resolvefile(base, relative)

    def library_file(token):
        target = resolve_library_file(token)
        base, relative = json.loads(file_token(target))
        info = target.stat()
        result = {'path': file_token(target), 'name': target.name, 'size': info.st_size,
                  'folder': Path(relative).parent.as_posix(), 'root': base, 'editable': False}
        if target.suffix.lower() not in local_library.AUDIO_EXTENSIONS:
            return result
        tags = local_library.read_metadata(str(target), info.st_mtime_ns, info.st_size)
        ident = 'local-' + hashlib.sha256(result['path'].encode()).hexdigest()[:24]
        codec = target.suffix.lower().lstrip('.')
        track = {'id': ident, 'source': 'local', 'source_id': 'local', 'title': tags['title'], 'artist': tags['artist'],
                 'album': tags['album'], 'duration': tags['duration'], 'size_bytes': info.st_size, 'qualities': [],
                 'cover': '/api/files/cover?path=' + quote(result['path']) + f'&v={info.st_mtime_ns}' if tags['has_cover'] else None,
                 'playback': {'state': 'ready', 'url': '/api/files/audio?path=' + quote(result['path']),
                              'codec': codec, 'bitrate': round(tags['bitrate'] / 1000) if tags['bitrate'] else None, 'detail': '音乐库文件', 'preview_only': False}}
        record = {'track': track, 'local_path': result['path']}
        if get('track', ident) != record:
            put('track', ident, record)
        result.update(track=track, editable=tags['editable'])
        return result

    @router.get('/api/files/audio')
    def local_audio(path: str):
        target = resolve_library_file(path)
        if target.suffix.lower() not in local_library.AUDIO_EXTENSIONS:
            raise HTTPException(400, '请选择音频文件')
        return FileResponse(target, media_type=local_library.MEDIA_TYPES.get(target.suffix.lower(), 'application/octet-stream'),
                            filename=target.name, content_disposition_type='inline')

    @router.get('/api/files/cover')
    def local_cover(path: str):
        target = resolve_library_file(path)
        try:
            picture = local_library.audio_cover(local_library.mutagen.File(target))
        except (OSError, ValueError, local_library.mutagen.MutagenError):
            picture = None
        if not picture:
            raise HTTPException(404, '文件没有封面')
        data, mime = picture
        if mime not in {'image/jpeg', 'image/png', 'image/webp'}:
            raise HTTPException(415, '不支持该封面格式')
        return Response(data, media_type=mime)

    @router.patch('/api/files/tags')
    def edit_file_tags(path: str, body: FileTagsBody):
        target = resolve_library_file(path)
        if target.suffix.lower() not in local_library.AUDIO_EXTENSIONS:
            raise HTTPException(400, '请选择音频文件')
        try:
            local_library.update_metadata(target, body.title.strip(), body.artist.strip(), body.album.strip())
        except (ValueError, local_library.mutagen.MutagenError) as error:
            raise HTTPException(400, f'无法编辑标签：{error}')
        except OSError:
            raise HTTPException(409, '文件正在使用或没有写入权限，请停止播放或关闭占用程序后重试')
        result = library_file(path)
        for kind in ('favorite', 'playlist'):
            if kind == 'favorite' and get(kind, result['track']['id']):
                put(kind, result['track']['id'], result['track'])
            elif kind == 'playlist':
                saved = get(kind, 'main', [])
                if any(isinstance(t, dict) and t.get('id') == result['track']['id'] for t in saved):
                    put(kind, 'main', [result['track'] if t['id'] == result['track']['id'] else t for t in saved])
        return result

    @router.get('/api/files/content')
    def file_content(path: str):
        target = resolve_library_file(path)
        return FileResponse(target, filename=target.name)

    @router.delete('/api/files')
    def delete_library_file(path: str):
        target = resolve_library_file(path)
        try:
            target.unlink()
        except OSError:
            raise HTTPException(409, '文件正在使用或无法删除，请关闭占用程序后重试')
        # Keep history, but stop offering a link to a deleted file.
        with runner_lock:
            for job in values('download'):
                if job.get('file') == file_token(target):
                    job.update(file=None, detail='音乐文件已从音乐库删除')
                    put('download', job['id'], job)
        local_id = 'local-' + hashlib.sha256(file_token(target).encode()).hexdigest()[:24]
        remove('track', local_id)
        remove('favorite', local_id)
        saved = get('playlist', 'main', [])
        if any(isinstance(t, dict) and t.get('id') == local_id for t in saved):
            put('playlist', 'main', [t for t in saved if not isinstance(t, dict) or t.get('id') != local_id])
        old.pushlog('info', f'已删除音乐文件：{target.name}', category='download')
        return {'deleted': True}

    @old.app.on_event('shutdown')
    async def shutdown():
        for ident in list(searches):
            await search_stop(ident)
        await coordinator.close()
        await workers.close()
        for ident in list(runners):
            await asyncio.to_thread(control_download, ident, 'pause')
        if hasattr(search_pool, 'terminate_workers'):
            search_pool.terminate_workers()
        else:
            search_pool.shutdown(wait=False, cancel_futures=True)
        download_pool.shutdown(wait=False, cancel_futures=True)

    for job in values('download'):
        if job['status'] in {'queued', 'resolving', 'downloading', 'enriching'}:
            job.update(status='paused', detail='服务已重启，下载进度已保留，可继续下载')
            put('download', job['id'], job)

    # New UI routes take precedence; every non-conflicting original endpoint,
    # including resolve, episodes, logs, file operations and updater, is retained.
    old.app.router.routes[0:0] = router.routes
    old.app.mount('/assets', StaticFiles(directory=root / 'frontend/dist/assets'), name='assets')
    old.app.state.engine_test = {'remember': remember, 'song_for': song_for, 'search_pool': search_pool,
                                'source_results': source_results, 'config': config, 'registered': registered, 'workers': workers, 'coordinator': coordinator}

    async def bot_search(keyword, sources, on_track=None):
        cfg = copy.deepcopy(old.manager.config)
        tracks, partial = {}, False
        async def run_source(source):
            nonlocal partial
            name = source_class(source)
            if name not in cfg['music_sources'] or name not in registered:
                raise HTTPException(400, '机器人音源未启用或无效')
            loop = asyncio.get_running_loop()
            async def publish(song):
                value = await asyncio.to_thread(remember, song)
                fresh = value['id'] not in tracks
                tracks[value['id']] = value
                if fresh and on_track:
                    await deliver(on_track, value)
                # Slow parsers must not cut off a source that is still returning songs.
                if not deadline.expired():
                    deadline.reschedule(loop.time() + BOT_SEARCH_IDLE_TIMEOUT)
            try:
                async with asyncio.timeout(BOT_SEARCH_IDLE_TIMEOUT) as deadline:
                    songs = await coordinator.run(cfg, name, keyword, publish)
                    for song in songs:
                        await publish(song)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                partial = True
                old.pushlog('warning', f'机器人音源 {name} 搜索失败或超时：{type(error).__name__}', category='search')
        await asyncio.gather(*(run_source(s) for s in sources or config()['sources']))
        return list(tracks.values()), partial

    def bot_enqueue(value, ident):
        return enqueue_tracks([DownloadItem(track_id=value['id'], quality_id=value['qualities'][0]['id'])], [ident])[0]

    import bot_notifications
    bot_notifications.install(old.app, get=get, put=put, values=values, remove=remove,
                              search=bot_search, search_progress=bot_search, enqueue=bot_enqueue, control=control_download,
                              resolve=resolve_library_file, log=old.pushlog,
                              project_proxy=lambda: old.manager.config.get('proxy_url', ''),
                              source_catalog=lambda: [s for s in catalog if s['engine_name'] in old.manager.config['music_sources']])
