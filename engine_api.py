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
import uuid
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import engine_worker

ALIASES = {'migu': 'MiguMusicClient', 'netease': 'NeteaseMusicClient', 'qq': 'QQMusicClient',
           'kuwo': 'KuwoMusicClient', 'qianqian': 'QianqianMusicClient'}
LABELS = {'migu': '咪咕', 'netease': '网易云', 'qq': 'QQ音乐', 'kuwo': '酷我', 'qianqian': '千千',
          'KugouMusicClient': '酷狗', 'BilibiliMusicClient': '哔哩哔哩', 'GequbaoMusicClient': '歌曲宝',
          'FangpiMusicClient': '放屁音乐网', 'XimalayaMusicClient': '喜马拉雅'}


def source_id(name):
    return next((key for key, value in ALIASES.items() if name == value), name)


def source_class(name):
    return ALIASES.get(name, name)


class SearchBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    sources: list[str] | None = None


class DownloadItem(BaseModel):
    track_id: str = Field(min_length=1, max_length=100)
    quality_id: str = Field(min_length=1, max_length=100)


class ImportBody(BaseModel):
    source: str = ''
    id: str = Field(min_length=1, max_length=2048)


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
    catalog = [{'id': source_id(name), 'label': LABELS.get(source_id(name), name.removesuffix('MusicClient')),
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
                'available_sources': catalog, 'engine': f'musicdl {old.MUSICDL_VERSION}',
                'results_per_source': cfg['global_client_cfg'].get('search_size_per_source', 5),
                'search_timeout_seconds': cfg.get('request_timeout', 30),
                'playback_timeout_seconds': cfg.get('request_timeout', 30),
                'download_concurrency': int(os.environ.get('MUSICDL_MAX_DOWNLOAD_TASKS', '2')),
                'download_layout': cfg.get('download_layout', 'source'),
                'lyrics_mode': cfg.get('lyrics_mode', 'both'), 'embed_metadata': True,
                'source_settings': settings}

    def track(song, sid=None):
        ext = str(song.ext or '').lower().lstrip('.')
        bitrate = int(song.bitrate or 0) or None
        if bitrate and bitrate > 10000:
            bitrate //= 1000
        lossless = ext in old.MusicClient.LOSSLESS_QUALITY_DEFINITIONS
        quality = f'engine:{ext or "original"}:{bitrate or 0}'
        sid = sid or hashlib.sha256(f'{song.source}|{song.root_source}|{song.identifier}|{song.song_name}|{song.singers}|{quality}'.encode()).hexdigest()[:24]
        playable = old.songplayable(song)
        label = (ext.upper() + (' 无损' if lossless else f' {bitrate} kbps' if bitrate else '')) if ext else '原音源提供的版本'
        return {'id': sid, 'source': source_id(str(song.source)), 'source_id': str(song.identifier or sid),
                'title': str(song.song_name or ''), 'artist': str(song.singers or ''), 'album': str(song.album or ''),
                'duration': float(song.duration_s or 0), 'cover': song.cover_url or None,
                'episodes': len(song.episodes or []), 'root_source': song.root_source or '',
                'qualities': [{'id': quality, 'label': label, 'codec': ext, 'bitrate': bitrate, 'lossless': lossless,
                               'availability': 'resolved', 'evidence': 'engine'}],
                'playback': {'state': 'ready' if playable else 'download_only', 'url': f'/api/stream/{sid}' if playable else None,
                             'codec': ext, 'bitrate': bitrate, 'preview_only': False,
                             'detail': None if playable else '该解析结果需要原音源下载器处理，可直接下载'}}

    def remember(song, sid=None):
        value = track(song, sid)
        sid = value['id']
        # No reserialization into a lossy URL-only record: custom stream objects,
        # source headers, downloaded bytes and per-track cookies are preserved.
        path = private / f'{sid}.song'
        temp = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        temp.write_bytes(pickle.dumps(song, protocol=pickle.HIGHEST_PROTOCOL))
        os.replace(temp, path)
        with old.SONG_LOCK:
            if sid not in old.SONGS and len(old.SONG_ORDER) == old.SONG_ORDER.maxlen:
                old.SONGS.pop(old.SONG_ORDER[0], None)
            if sid not in old.SONGS:
                old.SONG_ORDER.append(sid)
            old.SONGS[sid] = song
        put('track', sid, {'track': value})
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
    search_pool = ProcessPoolExecutor(max_workers=6, mp_context=multiprocessing.get_context('spawn'))
    download_pool = ThreadPoolExecutor(max_workers=max(1, min(4, int(os.environ.get('MUSICDL_MAX_DOWNLOAD_TASKS', '2')))))
    searches, cache, inflight = OrderedDict(), OrderedDict(), {}
    runners, runner_lock = {}, threading.RLock()

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
        cache.clear()
        return {'saved': True}

    @router.put('/api/config')
    def write_config(body: dict):
        cfg = copy.deepcopy(old.manager.config)
        enabled = [source_class(s) for s in body.get('sources', [])]
        if not enabled or any(s not in registered for s in enabled):
            raise HTTPException(400, '请选择已注册的音源')
        count, timeout = int(body.get('results_per_source', 5)), int(body.get('search_timeout_seconds', 30))
        if not 1 <= count <= 50 or not 1 <= timeout <= 120:
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
        cache.clear()
        return {'saved': True, 'restart_required': False}

    def emit(job, kind, value):
        job['events'].append((len(job['events']) + 1, kind, value))
        job['changed'].set()

    async def source_results(cfg, source, keyword):
        key = hashlib.sha256(json.dumps([cfg, source, keyword], sort_keys=True).encode()).hexdigest()
        cached = cache.get(key)
        if cached and time.monotonic() - cached[0] < 120:
            return cached[1]
        if key not in inflight:
            # The original engine owns its retries and multi-stage parsing.
            # Do not discard it after the former six-second playback deadline.
            future = search_pool.submit(engine_worker.search, cfg, source, keyword)
            inflight[key] = asyncio.wrap_future(future)
        future = inflight[key]
        try:
            songs = await asyncio.shield(future)
            cache[key] = (time.monotonic(), songs)
            while len(cache) > 128:
                cache.popitem(last=False)
            return songs
        finally:
            if future.done():
                inflight.pop(key, None)

    async def run_search(job, cfg, sources, keyword):
        async def run_source(name):
            sid = source_id(name)
            try:
                songs = await source_results(cfg, name, keyword)
                if job['cancelled']:
                    return
                for song in songs:
                    emit(job, 'track', remember(song))
                emit(job, 'source', {'id': sid, 'status': {'state': 'complete', 'count': len(songs)}})
            except asyncio.CancelledError:
                raise
            except Exception as error:
                old.pushlog('error', f'{name} 搜索失败: {type(error).__name__}')
                emit(job, 'source', {'id': sid, 'status': {'state': 'failed', 'count': 0, 'detail': f'原音源搜索失败（{type(error).__name__}），可检查音源设置后重试'}})
        try:
            await asyncio.gather(*(run_source(name) for name in sources))
        finally:
            emit(job, 'done', {'cancelled': job['cancelled']})
            job['done'] = True

    @router.post('/api/search/jobs')
    async def search_start(body: SearchBody):
        keyword = body.keyword.strip()
        cfg = copy.deepcopy(old.manager.config)
        sources = [source_class(s) for s in (body.sources or config()['sources'])]
        if not keyword or not sources or any(s not in cfg['music_sources'] or s not in registered for s in sources):
            raise HTTPException(400, '关键词或启用音源无效')
        if sum(not job['done'] for job in searches.values()) >= 6:
            raise HTTPException(429, '同时搜索过多，请先停止旧搜索')
        while len(searches) >= 128:
            ended = next((key for key, value in searches.items() if value['done']), None)
            if ended is None:
                break
            del searches[ended]
        ident = uuid.uuid4().hex
        job = {'events': [], 'changed': asyncio.Event(), 'done': False, 'cancelled': False}
        searches[ident] = job
        job['task'] = asyncio.create_task(run_search(job, cfg, list(dict.fromkeys(sources)), keyword))
        return {'id': ident}

    @router.delete('/api/search/jobs/{ident}')
    async def search_stop(ident: str):
        if ident in searches:
            searches[ident]['cancelled'] = True
            searches[ident]['task'].cancel()
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
            while True:
                job['changed'].clear()
                for sequence, kind, value in list(job['events']):
                    if sequence > cursor:
                        cursor = sequence
                        yield f'id: {sequence}\nevent: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n'
                if job['done'] or await request.is_disconnected():
                    return
                try:
                    await asyncio.wait_for(job['changed'].wait(), 10)
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @router.post('/api/tracks/{sid}/playback')
    def playback(sid: str):
        return remember(song_for(sid), sid)

    @router.post('/api/tracks/{sid}/versions')
    def versions(sid: str):
        return remember(song_for(sid), sid)

    @router.get('/api/tracks/{sid}/lyrics')
    def lyrics(sid: str):
        text = song_for(sid).lyric
        return {'text': text if text and str(text).lower() not in {'none', 'null'} else ''}

    @router.get('/api/tracks/{sid}/episodes')
    def episodes(sid: str):
        return [remember(song) for song in song_for(sid).episodes or []]

    @router.get('/api/stream/{sid}')
    def stream(sid: str, request: Request):
        song_for(sid)
        return old.stream(sid, request)

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
        songs = await asyncio.wrap_future(search_pool.submit(engine_worker.playlist, copy.deepcopy(old.manager.config), url))
        return {'tracks': [remember(song) for song in songs], 'detail': f'原引擎解析到 {len(songs)} 首曲目'}

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
                clear_staging()
                staging.mkdir(parents=True, exist_ok=True)
                intended_dir = Path(original_song.work_dir).resolve()
                if not any(intended_dir.is_relative_to(base) for base in old.manager.workdirs()):
                    raise ValueError('曲目保存路径不在音乐库中')
                original_song.work_dir, original_song._save_path = str(staging), None
                input_path.write_bytes(pickle.dumps({'song': original_song, 'config': cfg}, protocol=pickle.HIGHEST_PROTOCOL))
                process = subprocess.Popen([sys.executable, str(root / 'engine_worker.py'), str(input_path), str(output_path)],
                                           cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                           start_new_session=os.name != 'nt')
                control['process'] = process
            while process.poll() is None:
                if output_path.exists():
                    try:
                        progress = json.loads(output_path.read_text(encoding='utf-8'))
                        with runner_lock:
                            if not control['stop']:
                                job.update({k: progress[k] for k in ('bytes', 'total_bytes', 'detail') if k in progress})
                                job['status'] = 'downloading'
                                put('download', ident, job)
                    except (ValueError, OSError):
                        pass
                time.sleep(.25)
            with runner_lock:
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
                    for file in staging.rglob('*'):
                        if not file.is_file() or file.is_symlink():
                            continue
                        resolved = file.resolve()
                        if resolved not in completed and not any(file.with_suffix('') == p.with_suffix('') for p in completed):
                            continue
                        target = intended_dir / file.relative_to(staging)
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
                put('download', ident, job)
        except Exception as error:
            with runner_lock:
                if not control['stop']:
                    job.update(status='failed', detail=f'原引擎下载失败：{type(error).__name__}')
                    put('download', ident, job)
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            clear_staging()
            with runner_lock:
                runners.pop(ident, None)
            control['finished'].set()

    def launch(job, song):
        control = {'stop': False, 'process': None, 'finished': threading.Event()}
        runners[job['id']] = control
        download_pool.submit(run_download, job, song, control)

    @router.get('/api/downloads')
    def downloads():
        return values('download')

    @router.post('/api/downloads')
    def enqueue(items: list[DownloadItem]):
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
            for song, value in prepared:
                quality = value['qualities'][0]
                job = {'id': uuid.uuid4().hex, 'track': value, 'quality_id': quality['id'], 'quality_label': quality['label'],
                       'status': 'queued', 'bytes': 0, 'total_bytes': None, 'detail': '等待原音源下载器',
                       'file': None, 'created_at': int(time.time())}
                put('download', job['id'], job)
                launch(job, song)
                result.append(job)
        return result

    def terminate(process):
        if process and process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)

    @router.post('/api/downloads/{ident}/{action}')
    def control_download(ident: str, action: str):
        with runner_lock:
            job = get('download', ident)
            if not job:
                raise HTTPException(404, '任务不存在')
            active = runners.get(ident)
            if action == 'retry':
                if active or job['status'] == 'complete':
                    raise HTTPException(409, '任务正在执行或已经完成')
                song = song_for(job['track']['id'])
                if track(song)['qualities'][0]['id'] != job['quality_id']:
                    raise HTTPException(409, '音质已变化，请重新选择下载版本')
                job.update(status='queued', bytes=0, total_bytes=None, detail='重新使用原音源下载器下载')
                put('download', ident, job)
                launch(job, song)
                return job
            if action not in {'pause', 'cancel'} or job['status'] == 'complete':
                raise HTTPException(400, '操作无效')
            if active:
                active['stop'] = True
                terminate(active['process'])
            job.update(status='paused' if action == 'pause' else 'cancelled', detail='已停止；继续时从头下载')
            put('download', ident, job)
        if active:
            active['finished'].wait(20)
        return job

    @router.get('/api/files')
    def files():
        result = []
        for value in old.files()['files']:
            if '.partial' in Path(value['rel']).parts:
                continue
            result.append({'path': json.dumps([value['dir'], value['rel']], ensure_ascii=False), 'name': value['name'], 'size': value['size']})
        return result

    @router.get('/api/files/content')
    def file_content(path: str):
        try:
            base, relative = json.loads(path)
        except (ValueError, TypeError):
            # Paths saved by the earlier Rust build remain readable under its root.
            base, relative = str(old.FILE_ROOT), path
        target = old.resolvefile(base, relative)
        return FileResponse(target, filename=target.name)

    @old.app.on_event('shutdown')
    async def shutdown():
        for ident in list(runners):
            await asyncio.to_thread(control_download, ident, 'pause')
        if hasattr(search_pool, 'terminate_workers'):
            search_pool.terminate_workers()
        else:
            search_pool.shutdown(wait=False, cancel_futures=True)
        download_pool.shutdown(wait=False, cancel_futures=True)

    for job in values('download'):
        if job['status'] in {'queued', 'resolving', 'downloading', 'enriching'}:
            job.update(status='paused', detail='服务已重启，可继续使用原音源下载器下载')
            put('download', job['id'], job)

    # New UI routes take precedence; every non-conflicting original endpoint,
    # including resolve, episodes, logs, file operations and updater, is retained.
    old.app.router.routes[0:0] = router.routes
    old.app.mount('/assets', StaticFiles(directory=root / 'frontend/dist/assets'), name='assets')
    old.app.state.engine_test = {'remember': remember, 'song_for': song_for, 'search_pool': search_pool,
                                'source_results': source_results, 'config': config, 'registered': registered}
