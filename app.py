'''
musicdl WebUI backend
Wraps musicdl.MusicClient with a FastAPI server:
search / playlist parse / download tasks / file management / full settings.
'''
import sys
# rich progress prints spinner glyphs that crash on GBK Windows consoles
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import os
import re
import json
import time
import uuid
import copy
import threading
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests as pyrequests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# silence rich live progress rendering (musicdl uses it heavily; useless in a server console)
from rich.progress import Progress as _RichProgress
_rich_progress_init = _RichProgress.__init__
def _quiet_progress_init(self, *args, **kwargs):
    kwargs['disable'] = True
    _rich_progress_init(self, *args, **kwargs)
_RichProgress.__init__ = _quiet_progress_init

from musicdl.musicdl import MusicClient, DEFAULT_MUSIC_SOURCES
from musicdl.modules import MusicClientBuilder, LoggerHandle
from musicdl import __version__ as MUSICDL_VERSION

BASE_DIR = Path(__file__).resolve().parent
# musicdl resolves relative work_dirs against the process cwd — pin it to the project dir
os.chdir(BASE_DIR)
CONFIG_PATH = BASE_DIR / 'config.json'
ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

SOURCE_CATEGORIES = {
    '华语平台': ['QQMusicClient', 'KugouMusicClient', 'StreetVoiceMusicClient', 'SodaMusicClient', 'FiveSingMusicClient',
               'NeteaseMusicClient', 'QianqianMusicClient', 'MiguMusicClient', 'KuwoMusicClient', 'BilibiliMusicClient',
               'BodianMusicClient', 'MOOVMusicClient'],
    '国际流媒体': ['YouTubeMusicClient', 'JooxMusicClient', 'AppleMusicClient', 'JamendoMusicClient', 'SoundCloudMusicClient',
                'DeezerMusicClient', 'QobuzMusicClient', 'SpotifyMusicClient', 'TIDALMusicClient', 'FMAMusicClient',
                'JioSaavnMusicClient', 'OpenGameArtMusicClient', 'SunoMusicClient'],
    '电台 / 有声': ['XimalayaMusicClient', 'LizhiMusicClient', 'QingtingMusicClient', 'LRTSMusicClient', 'ITunesMusicClient'],
    '聚合网关': ['MP3JuiceMusicClient', 'TuneHubMusicClient', 'GDStudioMusicClient', 'MyFreeMP3MusicClient', 'JBSouMusicClient',
              'XiaoBaiMusicClient'],
    '下载站 / 爬虫': ['MituMusicClient', 'BuguyyMusicClient', 'GequbaoMusicClient', 'YinyuedaoMusicClient', 'FLMP3MusicClient',
                  'FangpiMusicClient', 'FiveSongMusicClient', 'KKWSMusicClient', 'GequhaiMusicClient', 'LivePOOMusicClient',
                  'HTQYYMusicClient', 'TwoT58MusicClient', 'ZhuolinMusicClient'],
}

DEFAULT_CONFIG = {
    'music_sources': list(DEFAULT_MUSIC_SOURCES),
    'global_client_cfg': {
        'work_dir': 'musicdl_outputs',
        'search_size_per_source': 5,
        'search_size_per_page': 10,
        'strict_limit_search_size_per_page': True,
        'max_retries': 3,
        'auto_set_proxies': False,
        'random_update_ua': False,
        'maintain_session': False,
        'enable_search_curl_cffi': False,
        'enable_parse_curl_cffi': False,
        'enable_download_curl_cffi': False,
    },
    'proxy_url': '',
    'request_timeout': 30,
    'default_threads': 10,
    'cookies': {},
    'init_music_clients_cfg': {},
    'clients_threadings': {},
    'requests_overrides': {},
    'search_rules': {},
}

LOGS: deque = deque(maxlen=800)


def pushlog(level: str, message: str):
    LOGS.append({'time': time.strftime('%H:%M:%S'), 'level': level, 'message': ANSI_RE.sub('', str(message))})


# LoggerHandle uses staticmethods and gets deep-copied into every music client,
# so hook the class itself: every copy shares these methods.
def _hookloggerclass():
    for level in ('debug', 'info', 'warning', 'error'):
        orig = getattr(LoggerHandle, level)
        def make(level, orig):
            def wrapped(message, disable_print: bool = False, **kwargs):
                pushlog(level, message)
                try:
                    orig(message, disable_print=True)
                except Exception:
                    pass
            return staticmethod(wrapped)
        setattr(LoggerHandle, level, make(level, orig))


_hookloggerclass()


def parsecookies(raw) -> dict:
    '''accept a raw cookie string ("a=1; b=2") or an already-parsed dict'''
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out = {}
    for part in str(raw or '').split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def loadconfig() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            for key, value in saved.items():
                if key == 'global_client_cfg' and isinstance(value, dict):
                    cfg['global_client_cfg'].update(value)
                else:
                    cfg[key] = value
        except Exception as err:
            pushlog('error', f'读取 config.json 失败，使用默认配置: {err}')
    return cfg


def saveconfig(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')


class ClientManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = loadconfig()
        self.client: MusicClient | None = None
        self.build_error: str | None = None

    '''build a MusicClient from current config'''
    def build(self):
        cfg = self.config
        sources = [s for s in cfg['music_sources'] if s in MusicClientBuilder.REGISTERED_MODULES]
        if not sources:
            raise ValueError('至少需要启用一个音源')
        init_cfgs, threadings, req_overrides, rules = {}, {}, {}, {}
        proxy_url = (cfg.get('proxy_url') or '').strip()
        for source in sources:
            per_cfg = dict(cfg['global_client_cfg'])
            per_cfg.update(cfg['init_music_clients_cfg'].get(source, {}))
            cookie_dict = parsecookies((cfg.get('cookies') or {}).get(source, ''))
            if cookie_dict:
                for ck_key in ('default_search_cookies', 'default_download_cookies', 'default_parse_cookies'):
                    if not per_cfg.get(ck_key):
                        per_cfg[ck_key] = cookie_dict
            init_cfgs[source] = per_cfg
            threadings[source] = int(cfg['clients_threadings'].get(source, cfg.get('default_threads', 5)))
            overrides = dict(cfg['requests_overrides'].get(source, {}))
            if proxy_url and 'proxies' not in overrides:
                overrides['proxies'] = {'http': proxy_url, 'https': proxy_url}
            timeout = int(cfg.get('request_timeout', 30) or 0)
            if timeout > 0 and 'timeout' not in overrides:
                overrides['timeout'] = timeout
            req_overrides[source] = overrides
            rules[source] = cfg['search_rules'].get(source, {})
        client = MusicClient(music_sources=sources, init_music_clients_cfg=init_cfgs,
                             clients_threadings=threadings, requests_overrides=req_overrides, search_rules=rules)
        self.client = client
        self.build_error = None
        pushlog('info', f'已初始化 {len(sources)} 个音源: {", ".join(s.removesuffix("MusicClient") for s in sources)}')

    '''return a usable client, building lazily'''
    def get(self) -> MusicClient:
        with self.lock:
            if self.client is None:
                try:
                    self.build()
                except Exception as err:
                    self.build_error = str(err)
                    raise
            return self.client

    '''apply a new config: persist + rebuild'''
    def applyconfig(self, cfg: dict):
        with self.lock:
            old = self.config
            self.config = cfg
            try:
                self.build()
            except Exception:
                self.config = old
                self.client = None
                raise
            saveconfig(cfg)

    '''all work dirs (for the file manager)'''
    def workdirs(self) -> list[Path]:
        dirs = {self.config['global_client_cfg'].get('work_dir') or 'musicdl_outputs'}
        for per_cfg in self.config['init_music_clients_cfg'].values():
            if isinstance(per_cfg, dict) and per_cfg.get('work_dir'):
                dirs.add(per_cfg['work_dir'])
        resolved = []
        for d in dirs:
            p = Path(d)
            resolved.append((p if p.is_absolute() else BASE_DIR / p).resolve())
        return resolved


manager = ClientManager()

# in-memory registry of SongInfo objects keyed by short ids
SONGS: dict[str, object] = {}
SONG_ORDER: deque = deque(maxlen=3000)
TASKS: dict[str, dict] = {}
TASK_ORDER: list[str] = []


def registersong(song) -> str:
    sid = uuid.uuid4().hex[:12]
    if len(SONG_ORDER) == SONG_ORDER.maxlen:
        SONGS.pop(SONG_ORDER[0], None)
    SONGS[sid] = song
    SONG_ORDER.append(sid)
    return sid


def serializesong(sid: str, song) -> dict:
    return {
        'id': sid,
        'song_name': str(song.song_name or ''),
        'singers': str(song.singers or ''),
        'album': str(song.album or ''),
        'ext': str(song.ext or ''),
        'file_size': str(song.file_size or ''),
        'duration': str(song.duration or ''),
        'source': str(song.source or '').removesuffix('MusicClient'),
        'root_source': str(song.root_source or ''),
        'episodes': len(song.episodes) if song.episodes else 0,
        'lossless': (song.ext or '').lower() in MusicClient.LOSSLESS_QUALITY_DEFINITIONS,
    }


app = FastAPI(title='musicdl WebUI')


@app.on_event('startup')
async def raisethreadpool():
    # sync endpoints share one AnyIO threadpool (default 40). Slow source
    # searches and long-lived audio streams each hold a worker, so raise it.
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = 200


class SearchBody(BaseModel):
    keyword: str
    sources: list[str] | None = None


class PlaylistBody(BaseModel):
    url: str


class DownloadBody(BaseModel):
    ids: list[str]


class ConfigBody(BaseModel):
    music_sources: list[str]
    global_client_cfg: dict
    proxy_url: str = ''
    request_timeout: int = 30
    default_threads: int = 5
    cookies: dict = {}
    init_music_clients_cfg: dict = {}
    clients_threadings: dict = {}
    requests_overrides: dict = {}
    search_rules: dict = {}


@app.get('/')
def index():
    return RedirectResponse('/static/index.html')


@app.get('/api/meta')
def meta():
    registered = list(MusicClientBuilder.REGISTERED_MODULES.keys())
    categorized = {cat: [s for s in names if s in registered] for cat, names in SOURCE_CATEGORIES.items()}
    leftovers = [s for s in registered if not any(s in names for names in SOURCE_CATEGORIES.values())]
    if leftovers:
        categorized['其他'] = leftovers
    return {'version': MUSICDL_VERSION, 'categories': categorized, 'default_sources': list(DEFAULT_MUSIC_SOURCES)}


@app.get('/api/config')
def getconfig():
    return manager.config


@app.put('/api/config')
def putconfig(body: ConfigBody):
    cfg = body.model_dump()
    try:
        manager.applyconfig(cfg)
    except Exception as err:
        raise HTTPException(400, f'配置未生效: {err}')
    return {'ok': True}


def searchsubset(client: MusicClient, keyword: str, sources: list[str]) -> dict:
    '''like MusicClient.search but restricted to a subset of its clients'''
    def search_one(ms):
        try:
            return ms, client.music_clients[ms].search(
                keyword=keyword, num_threadings=client.clients_threadings.get(ms, 5),
                request_overrides=client.requests_overrides.get(ms, {}), rule=client.search_rules.get(ms, {}))
        except Exception as err:
            pushlog('error', f'{ms}.search 失败: {err}')
            return ms, []
    with ThreadPoolExecutor(max_workers=min(len(sources), 10)) as ex:
        return dict(ex.map(search_one, sources))


@app.post('/api/search')
def search(body: SearchBody):
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(400, '关键词不能为空')
    try:
        client = manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    sources = [s for s in (body.sources or client.music_sources) if s in client.music_clients]
    if not sources:
        raise HTTPException(400, '选中的音源均未启用，请先在设置中启用')
    pushlog('info', f'搜索: {keyword} ({", ".join(s.removesuffix("MusicClient") for s in sources)})')
    results = searchsubset(client, keyword, sources)
    payload = []
    for _, songs in results.items():
        for song in songs:
            payload.append(serializesong(registersong(song), song))
    pushlog('info', f'搜索完成: {keyword} → {len(payload)} 个结果')
    return {'keyword': keyword, 'results': payload}


AUDIO_MIME = {
    'mp3': 'audio/mpeg', 'flac': 'audio/flac', 'wav': 'audio/wav', 'm4a': 'audio/mp4', 'aac': 'audio/aac',
    'ogg': 'audio/ogg', 'opus': 'audio/opus', 'ape': 'audio/x-ape', 'wma': 'audio/x-ms-wma', 'mp4': 'audio/mp4',
}


def songplayable(song) -> bool:
    if song.downloaded_contents and isinstance(song.downloaded_contents, (bytes, bytearray)):
        return True
    return isinstance(song.download_url, str) and song.download_url.startswith('http') and str(song.protocol or 'HTTP').upper() == 'HTTP'


@app.get('/api/song/{sid}')
def songdetail(sid: str):
    song = SONGS.get(sid)
    if song is None:
        raise HTTPException(404, '结果已过期，请重新搜索')
    lyric = song.lyric if song.lyric and str(song.lyric).strip().lower() not in {'null', 'none'} else None
    data = serializesong(sid, song)
    data.update({
        'lyric': lyric,
        'cover_url': song.cover_url or None,
        'playable': songplayable(song),
        'stream': f'/api/stream/{sid}',
        'bitrate': song.bitrate,
    })
    return data


@app.get('/api/stream/{sid}')
def stream(sid: str, request: Request):
    song = SONGS.get(sid)
    if song is None:
        raise HTTPException(404, '结果已过期，请重新搜索')
    try:
        client = manager.get().music_clients.get(song.source)
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    if client is None:
        raise HTTPException(400, '对应音源已被禁用')
    mime = AUDIO_MIME.get(str(song.ext or '').lower().lstrip('.'), 'application/octet-stream')
    # some sources return the audio bytes directly during search/parse
    if song.downloaded_contents and isinstance(song.downloaded_contents, (bytes, bytearray)):
        return Response(content=bytes(song.downloaded_contents), media_type=mime,
                        headers={'Accept-Ranges': 'none'})
    if not songplayable(song):
        raise HTTPException(415, '该曲目不支持在线播放（加密或流媒体协议），请使用下载功能')
    headers = dict(song.default_download_headers or getattr(client, 'default_headers', None) or {})
    cookies = dict(song.default_download_cookies or {})
    if request.headers.get('range'):
        headers['Range'] = request.headers['range']
    proxies = manager.config['requests_overrides'].get(song.source, {}).get('proxies')
    if proxies is None and (manager.config.get('proxy_url') or '').strip():
        p = manager.config['proxy_url'].strip()
        proxies = {'http': p, 'https': p}
    kwargs = {'stream': True, 'headers': headers, 'cookies': cookies, 'timeout': 30, 'proxies': proxies}
    try:
        upstream = pyrequests.get(song.download_url, **kwargs)
        upstream.raise_for_status()
    except Exception:
        try:
            upstream = pyrequests.get(song.download_url, verify=False, **kwargs)
            upstream.raise_for_status()
        except Exception as err:
            raise HTTPException(502, f'拉取音频失败: {err}')
    resp_headers = {'Accept-Ranges': upstream.headers.get('Accept-Ranges', 'bytes')}
    for h in ('Content-Length', 'Content-Range'):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    media_type = upstream.headers.get('Content-Type') or mime
    if 'text' in media_type or 'html' in media_type:
        media_type = mime
    return StreamingResponse(upstream.iter_content(chunk_size=256 * 1024), status_code=upstream.status_code,
                             media_type=media_type, headers=resp_headers)


class ResolveBody(BaseModel):
    source: str
    title: str
    artist: str = ''
    keyword: str = ''


@app.post('/api/resolve')
def resolve(body: ResolveBody):
    '''re-find an expired track (e.g. favorites restored after a server restart)'''
    try:
        client = manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    source = body.source if body.source.endswith('MusicClient') else body.source + 'MusicClient'
    if source not in client.music_clients:
        raise HTTPException(400, f'音源 {body.source} 未启用，请先在设置中启用')
    keyword = (body.keyword or f'{body.title} {body.artist}').strip()
    if not keyword:
        raise HTTPException(400, '缺少可用于重新搜索的关键词')
    results = searchsubset(client, keyword, [source]).get(source, [])
    if not results:
        raise HTTPException(404, '未能重新找到该曲目')
    best = None
    for song in results:
        if str(song.song_name or '').strip() == body.title.strip():
            best = song
            break
    best = best or results[0]
    return serializesong(registersong(best), best)


@app.get('/api/song/{sid}/episodes')
def episodes(sid: str):
    song = SONGS.get(sid)
    if song is None:
        raise HTTPException(404, '结果已过期，请重新搜索')
    if not song.episodes:
        return {'results': []}
    return {'results': [serializesong(registersong(ep), ep) for ep in song.episodes]}


@app.post('/api/playlist')
def playlist(body: PlaylistBody):
    url = body.url.strip()
    if not url:
        raise HTTPException(400, '歌单链接不能为空')
    try:
        client = manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    pushlog('info', f'解析歌单: {url}')
    songs = client.parseplaylist(url)
    if not songs:
        pushlog('warning', f'未能从歌单解析到歌曲: {url}')
    return {'results': [serializesong(registersong(song), song) for song in songs]}


def runtask(task_id: str):
    task = TASKS[task_id]
    task['status'] = 'running'
    client = manager.client
    entries = task['songs']
    valid = [e for e in entries if e['_song'] is not None and e['_song'].source in client.music_clients]
    for e in entries:
        if e['_song'] is None:
            e['status'], e['detail'] = 'failed', '结果已过期'
        elif e not in valid:
            e['status'], e['detail'] = 'failed', '对应音源已被禁用'
        else:
            e['status'] = 'running'
    try:
        done = client.download([e['_song'] for e in valid])
        # musicdl deep-copies SongInfo objects while downloading, so match by identity fields
        def matchkey(s):
            return (str(s.source), str(s.identifier or ''), str(s.song_name or ''))
        done_map = {matchkey(s): s for s in done}
        for e in valid:
            hit = done_map.get(matchkey(e['_song']))
            if hit is not None:
                e['status'] = 'done'
                try:
                    e['detail'] = os.path.basename(hit.save_path)
                except Exception:
                    e['detail'] = ''
            else:
                e['status'], e['detail'] = 'failed', '下载失败，详见日志'
        task['status'] = 'done' if all(e['status'] == 'done' for e in entries) else ('failed' if all(e['status'] == 'failed' for e in entries) else 'partial')
    except Exception as err:
        for e in valid:
            if e['status'] == 'running':
                e['status'], e['detail'] = 'failed', str(err)
        task['status'] = 'failed'
        pushlog('error', f'下载任务异常: {err}')
    task['finished_at'] = time.strftime('%H:%M:%S')


@app.post('/api/download')
def download(body: DownloadBody):
    if not body.ids:
        raise HTTPException(400, '未选择任何歌曲')
    try:
        manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    task_id = uuid.uuid4().hex[:8]
    songs = []
    for sid in body.ids:
        song = SONGS.get(sid)
        songs.append({
            '_song': song, 'id': sid, 'status': 'pending', 'detail': '',
            'song_name': str(song.song_name) if song is not None else '(已过期)',
            'singers': str(song.singers) if song is not None else '',
            'source': str(song.source or '').removesuffix('MusicClient') if song is not None else '',
        })
    TASKS[task_id] = {'id': task_id, 'status': 'pending', 'created_at': time.strftime('%H:%M:%S'), 'finished_at': None, 'songs': songs}
    TASK_ORDER.insert(0, task_id)
    threading.Thread(target=runtask, args=(task_id,), daemon=True).start()
    return {'task_id': task_id}


@app.get('/api/tasks')
def tasks():
    out = []
    for tid in TASK_ORDER[:50]:
        t = TASKS[tid]
        out.append({k: v for k, v in t.items() if k != 'songs'} | {'songs': [{k: v for k, v in e.items() if k != '_song'} for e in t['songs']]})
    return {'tasks': out}


@app.get('/api/files')
def files():
    out = []
    for workdir in manager.workdirs():
        if not workdir.is_dir():
            continue
        for path in workdir.rglob('*'):
            if not path.is_file() or path.suffix.lower() == '.pkl':
                continue
            stat = path.stat()
            out.append({
                'name': path.name,
                'dir': str(workdir),
                'rel': str(path.relative_to(workdir)),
                'size': stat.st_size,
                'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime)),
            })
    out.sort(key=lambda f: f['mtime'], reverse=True)
    return {'files': out}


def resolvefile(d: str, rel: str) -> Path:
    workdir = Path(d).resolve()
    if workdir not in manager.workdirs():
        raise HTTPException(403, '目录不在允许范围内')
    target = (workdir / rel).resolve()
    if workdir != target and workdir not in target.parents:
        raise HTTPException(403, '非法路径')
    if not target.is_file():
        raise HTTPException(404, '文件不存在')
    return target


@app.get('/api/file')
def getfile(d: str = Query(...), rel: str = Query(...)):
    target = resolvefile(d, rel)
    return FileResponse(target, filename=target.name)


@app.delete('/api/file')
def deletefile(d: str = Query(...), rel: str = Query(...)):
    target = resolvefile(d, rel)
    target.unlink()
    pushlog('info', f'已删除文件: {target.name}')
    return {'ok': True}


@app.get('/api/logs')
def logs():
    return {'logs': list(LOGS)}


@app.post('/api/logs/clear')
def clearlogs():
    LOGS.clear()
    return {'ok': True}


app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8765)
