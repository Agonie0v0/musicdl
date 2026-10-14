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
from datetime import datetime, timezone
import uuid
import copy
import hashlib
import ipaddress
import socket
import tempfile
import threading
from pathlib import Path
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests as pyrequests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import secrets

# silence rich live progress rendering (musicdl uses it heavily; useless in a
# server console). Guarded so a rich/musicdl change can't stop the app booting.
try:
    from rich.progress import Progress as _RichProgress
    _rich_progress_init = _RichProgress.__init__
    def _quiet_progress_init(self, *args, **kwargs):
        kwargs['disable'] = True
        _rich_progress_init(self, *args, **kwargs)
    _RichProgress.__init__ = _quiet_progress_init
except Exception:
    pass

from musicdl.musicdl import MusicClient, DEFAULT_MUSIC_SOURCES
from musicdl.modules import MusicClientBuilder, LoggerHandle
from musicdl.modules.sources.base import BaseMusicClient
from musicdl.modules.utils import usedownloadheaderscookies
from musicdl import __version__ as MUSICDL_VERSION

BASE_DIR = Path(os.environ.get('MUSICDL_PROJECT_DIR', Path(__file__).resolve().parent)).resolve()
# musicdl resolves relative work_dirs against the process cwd — pin it to the project dir
os.chdir(BASE_DIR)

IS_WINDOWS = os.name == 'nt'

# --- musicdl internal tweaks (all optional, all crash-proof) -----------------
# Everything below reaches into musicdl's *private* internals to add niceties
# (clean download dir, lyrics mode, log capture). Those internals can change
# between musicdl versions, so each patch is wrapped: if a symbol is gone, we
# skip just that enhancement and record it — the app still starts and the core
# (search / play / download, all via musicdl's PUBLIC api) keeps working.
# This is what lets you upgrade musicdl freely without me re-adapting each time.
PATCH_STATUS: dict = {}


def _lyrics_mode():
    try:
        return manager.config.get('lyrics_mode', 'both')
    except Exception:
        return 'both'


def apply_musicdl_patches():
    # 1) stop writing search_results.pkl / download_results.pkl clutter
    try:
        if hasattr(BaseMusicClient, '_savetopkl'):
            BaseMusicClient._savetopkl = lambda self, *a, **k: None
            PATCH_STATUS['no_pkl'] = 'ok'
        else:
            PATCH_STATUS['no_pkl'] = 'skip: _savetopkl missing'
    except Exception as err:
        PATCH_STATUS['no_pkl'] = f'skip: {err}'

    # 2) flatten the per-search "<timestamp> <keyword>/" folder so downloads
    #    land in "<work_dir>/<Source>/<song>.<ext>" (navidrome-friendly)
    try:
        _orig_cwd = getattr(BaseMusicClient, '_constructuniqueworkdir', None)
        if _orig_cwd is not None:
            def _flat_workdir(self, keyword, sort_by_search_kwd_and_time=True):
                return _orig_cwd(self, keyword, sort_by_search_kwd_and_time=False)
            BaseMusicClient._constructuniqueworkdir = _flat_workdir
            PATCH_STATUS['flat_workdir'] = 'ok'
        else:
            PATCH_STATUS['flat_workdir'] = 'skip: _constructuniqueworkdir missing'
    except Exception as err:
        PATCH_STATUS['flat_workdir'] = f'skip: {err}'

    # 3) lyrics mode: gate the two halves musicdl always runs (write .lrc /
    #    embed USLT). If missing, lyrics fall back to musicdl's default (both).
    try:
        from musicdl.modules.utils.songinfoutils import SongInfoUtils
        _orig_savelrc = getattr(SongInfoUtils, 'savelrctofile', None)
        _orig_embed = getattr(SongInfoUtils, 'embedlyrics', None)
        if _orig_savelrc and _orig_embed:
            def _gated_savelrc(audio_path, lyrics_text, *, overwrite=False):
                if _lyrics_mode() == 'embed':
                    return False
                return _orig_savelrc(audio_path, lyrics_text, overwrite=overwrite)
            def _gated_embed(audio_path, *, overwrite, lyrics_text):
                if _lyrics_mode() == 'lrc':
                    return False
                return _orig_embed(audio_path, overwrite=overwrite, lyrics_text=lyrics_text)
            SongInfoUtils.savelrctofile = staticmethod(_gated_savelrc)
            SongInfoUtils.embedlyrics = staticmethod(_gated_embed)
            PATCH_STATUS['lyrics_mode'] = 'ok'
        else:
            PATCH_STATUS['lyrics_mode'] = 'skip: lyric helpers missing'
    except Exception as err:
        PATCH_STATUS['lyrics_mode'] = f'skip: {err}'


apply_musicdl_patches()
# MUSICDL_CONFIG lets Docker/NAS persist settings on a mounted volume
CONFIG_PATH = Path(os.environ.get('MUSICDL_CONFIG', BASE_DIR / 'config.json'))
_file_root_raw = Path(os.environ.get('MUSICDL_FILE_ROOT') or os.environ.get('MUSICDL_WORKDIR') or BASE_DIR / 'musicdl_outputs')
FILE_ROOT = (_file_root_raw if _file_root_raw.is_absolute() else BASE_DIR / _file_root_raw).resolve()
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
        # MUSICDL_WORKDIR lets Docker/NAS deployments point straight at a
        # navidrome/Jellyfin music library without editing config.json
        'work_dir': str(FILE_ROOT),
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
    # 代理作用范围：all 全部音源 / selected 仅 proxy_sources 里的音源
    'proxy_scope': 'all',
    'proxy_sources': [],
    'request_timeout': 30,
    'default_threads': 10,
    # 下载分类方式：source 按音源 / artist 按歌手 / artist_album 歌手·专辑 / flat 全部平铺
    'download_layout': 'source',
    # 歌词保存方式：lrc 单独文件 / embed 嵌入歌曲 / both 两者都要
    'lyrics_mode': 'both',
    'cookies': {},
    'init_music_clients_cfg': {},
    'clients_threadings': {},
    'requests_overrides': {},
    'search_rules': {},
}

LOGS: deque = deque(maxlen=800)
LOG_LOCK = threading.RLock()
LOG_SEQUENCE = 0


def redactlog(message):
    text = ANSI_RE.sub('', str(message))
    def hide_url(match):
        try:
            url = urlsplit(match[0])
            return f'{url.scheme}://{url.hostname or "[主机]"}/[地址已隐藏]'
        except ValueError:
            return '[地址已隐藏]'
    text = re.sub(r'https?://[^\s<>\'\"]+', hide_url, text)
    text = re.sub(r'(?im)\b(cookies?|authorization|password|passwd|token|secret|api[_-]?key|MUSICDL_PASS)[\'\"]?\s*[:=]\s*[^\n]+',
                  r'\1=[已隐藏]', text)
    return text[:16000]


def pushlog(level: str, message: str, category='system', task_id=None, timestamp=None):
    try:
        from error_messages import explain_error
    except ModuleNotFoundError:
        # The legacy app is also launched directly from ``legacy/`` in
        # compatibility checks, where the project root is not on sys.path.
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from error_messages import explain_error
    global LOG_SEQUENCE
    with LOG_LOCK:
        LOG_SEQUENCE += 1
        entry = {'id': LOG_SEQUENCE, 'time': timestamp or datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'level': level, 'category': category, 'task_id': task_id, 'message': redactlog(message)}
        if level in {'warning', 'error'}:
            entry['reason'] = explain_error(message)
        LOGS.append(entry)
        return entry


# LoggerHandle uses staticmethods and gets deep-copied into every music client,
# so hook the class itself: every copy shares these methods. Guarded like the
# other patches — if musicdl reworks LoggerHandle, we just lose the log panel.
def _hookloggerclass():
    try:
        for level in ('debug', 'info', 'warning', 'error'):
            orig = getattr(LoggerHandle, level, None)
            if orig is None:
                continue
            def make(level, orig):
                def wrapped(message, disable_print: bool = False, **kwargs):
                    pushlog(level, message)
                    try:
                        orig(message, disable_print=True)
                    except Exception:
                        pass
                return staticmethod(wrapped)
            setattr(LoggerHandle, level, make(level, orig))
        PATCH_STATUS['log_capture'] = 'ok'
    except Exception as err:
        PATCH_STATUS['log_capture'] = f'skip: {err}'


_hookloggerclass()

try:
    from provider_compat import install
    PATCH_STATUS.update({f'audio_compat_{source}': state for source, state in
                        install(lambda *args, **kwargs: pushlog(*args, **kwargs)).items()})
except Exception as err:
    PATCH_STATUS['audio_compat'] = f'skip: {type(err).__name__}'
    pushlog('error', f'音质兼容修复未加载：{type(err).__name__}', category='search')


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


def _resolve_allowed_workdir(raw) -> Path:
    value = Path(str(raw or FILE_ROOT))
    if value.is_absolute():
        target = value.resolve()
    else:
        legacy = (BASE_DIR / value).resolve()
        target = legacy if legacy == FILE_ROOT or FILE_ROOT in legacy.parents else (FILE_ROOT / value).resolve()
    if target != FILE_ROOT and FILE_ROOT not in target.parents:
        raise ValueError(f'下载目录必须位于允许根目录 {FILE_ROOT} 内')
    return target


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def normalizeconfigpaths(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    global_cfg = cfg.setdefault('global_client_cfg', {})
    global_cfg['work_dir'] = str(_resolve_allowed_workdir(global_cfg.get('work_dir')))
    cfg['default_threads'] = _bounded_int(cfg.get('default_threads'), 5, 1, 50)
    cfg['request_timeout'] = _bounded_int(cfg.get('request_timeout'), 30, 0, 300)
    raw_threadings = cfg.get('clients_threadings')
    if not isinstance(raw_threadings, dict):
        raw_threadings = {}
    cfg['clients_threadings'] = {
        str(source): _bounded_int(value, cfg['default_threads'], 1, 50)
        for source, value in raw_threadings.items()
    }
    per_source = cfg.get('init_music_clients_cfg')
    if not isinstance(per_source, dict):
        per_source = {}
        cfg['init_music_clients_cfg'] = per_source
    for source, source_cfg in per_source.items():
        if isinstance(source_cfg, dict) and source_cfg.get('work_dir'):
            source_cfg['work_dir'] = str(_resolve_allowed_workdir(source_cfg['work_dir']))
    return cfg


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
    try:
        return normalizeconfigpaths(cfg)
    except Exception as err:
        pushlog('error', f'配置中的下载目录不安全，已回退到 {FILE_ROOT}: {err}')
        cfg['global_client_cfg']['work_dir'] = str(FILE_ROOT)
        cfg['init_music_clients_cfg'] = {}
        return normalizeconfigpaths(cfg)


def saveconfig(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(CONFIG_PATH.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{CONFIG_PATH.name}.', suffix='.tmp', dir=CONFIG_PATH.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, CONFIG_PATH)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass
        raise


class ClientManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = loadconfig()
        self.client: MusicClient | None = None
        self.build_error: str | None = None
        self.source_errors: dict[str, str] = {}

    '''build a MusicClient from current config'''
    def build(self):
        cfg = self.config
        sources = [s for s in cfg['music_sources'] if s in MusicClientBuilder.REGISTERED_MODULES]
        if not sources:
            raise ValueError('至少需要启用一个音源')
        init_cfgs, threadings, req_overrides, rules = {}, {}, {}, {}
        proxy_url = (cfg.get('proxy_url') or '').strip()
        proxy_scope = cfg.get('proxy_scope', 'all')
        proxy_sources = set(cfg.get('proxy_sources') or [])
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
            use_proxy = proxy_url and (proxy_scope == 'all' or (proxy_scope == 'selected' and source in proxy_sources))
            if use_proxy and 'proxies' not in overrides:
                overrides['proxies'] = {'http': proxy_url, 'https': proxy_url}
            timeout = int(cfg.get('request_timeout', 30) or 0)
            if timeout > 0 and 'timeout' not in overrides:
                overrides['timeout'] = timeout
            req_overrides[source] = overrides
            rules[source] = cfg['search_rules'].get(source, {})
        # One provider's missing account/parser settings must not disable all
        # the other providers. Keep native MusicClient objects and merge only
        # their source registries; every provider retains its native defaults.
        client, errors = None, {}
        for source in sources:
            try:
                part = MusicClient(music_sources=[source], init_music_clients_cfg={source: init_cfgs[source]},
                                   clients_threadings={source: threadings[source]},
                                   requests_overrides={source: req_overrides[source]}, search_rules={source: rules[source]})
            except Exception as error:
                parser = init_cfgs[source].get('quark_parser_config')
                hint = ('需要配置夸克解析器 Cookie：分源客户端参数 quark_parser_config.cookies'
                        if source == 'XiagebaMusicClient' and not (isinstance(parser, dict) and parser.get('cookies'))
                        else f'初始化失败（{type(error).__name__}），请检查分源配置与运行日志')
                errors[source] = hint
                pushlog('warning', f'{source} 暂不可用：{hint}\n{error}')
                continue
            if client is None:
                client = part
            else:
                client.music_sources.extend(part.music_sources)
                for key in ('music_clients', 'work_dirs', 'clients_threadings', 'requests_overrides', 'search_rules'):
                    getattr(client, key).update(getattr(part, key))
        if client is None:
            raise ValueError('所选音源均初始化失败，请检查音源配置与运行日志')
        self.client = client
        self.source_errors = errors
        self.build_error = None
        pushlog('info', f'已初始化 {len(client.music_sources)} 个音源，{len(errors)} 个暂不可用: {", ".join(s.removesuffix("MusicClient") for s in client.music_sources)}')

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
            old_config, old_client, old_error, old_source_errors = self.config, self.client, self.build_error, self.source_errors
            self.config = normalizeconfigpaths(cfg)
            try:
                self.build()
                saveconfig(self.config)
            except Exception:
                self.config, self.client, self.build_error = old_config, old_client, old_error
                self.source_errors = old_source_errors
                raise

    '''all work dirs (for the file manager)'''
    def workdirs(self) -> list[Path]:
        dirs = {self.config['global_client_cfg'].get('work_dir') or 'musicdl_outputs'}
        for per_cfg in self.config['init_music_clients_cfg'].values():
            if isinstance(per_cfg, dict) and per_cfg.get('work_dir'):
                dirs.add(per_cfg['work_dir'])
        resolved = []
        for d in dirs:
            resolved.append(_resolve_allowed_workdir(d))
        return resolved


manager = ClientManager()

# in-memory registry of SongInfo objects keyed by short ids
SONGS: dict[str, object] = {}
SONG_ORDER: deque = deque(maxlen=3000)
SONG_LOCK = threading.RLock()
TASKS: dict[str, dict] = {}
TASK_ORDER: list[str] = []
TASK_LOCK = threading.RLock()
ACTIVE_SONG_IDS: set[str] = set()
DOWNLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, min(4, int(os.environ.get('MUSICDL_MAX_DOWNLOAD_TASKS', '2')))))


def registersong(song) -> str:
    with SONG_LOCK:
        sid = uuid.uuid4().hex[:12]
        if len(SONG_ORDER) == SONG_ORDER.maxlen:
            SONGS.pop(SONG_ORDER[0], None)
        SONGS[sid] = song
        SONG_ORDER.append(sid)
    return sid


def getsong(sid: str):
    with SONG_LOCK:
        return SONGS.get(sid)


def stablesongid(song) -> str:
    identity = '|'.join(str(value or '').strip().lower() for value in (
        song.source, getattr(song, 'identifier', ''), song.song_name, song.singers, song.album,
    ))
    return hashlib.blake2s(identity.encode('utf-8'), digest_size=10).hexdigest()


def serializesong(sid: str, song) -> dict:
    return {
        'id': sid,
        'stable_id': stablesongid(song),
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


def _read_secret(name: str) -> str | None:
    '''Read NAME or NAME_FILE, never both. Docker secrets can use *_FILE.'''
    value = os.environ.get(name)
    file_name = os.environ.get(f'{name}_FILE')
    if value and file_name:
        raise RuntimeError(f'{name} 与 {name}_FILE 不能同时设置')
    if file_name:
        try:
            value = Path(file_name).read_text(encoding='utf-8').strip()
        except Exception as err:
            raise RuntimeError(f'无法读取 {name}_FILE: {err}') from err
    return value or None


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


SERVER_HOST = os.environ.get('MUSICDL_HOST', '127.0.0.1').strip()
IS_LOOPBACK_BIND = SERVER_HOST.lower() in {'127.0.0.1', '::1', 'localhost'}
AUTH_USER = _read_secret('MUSICDL_USER')
AUTH_PASS = _read_secret('MUSICDL_PASS')
if bool(AUTH_USER) != bool(AUTH_PASS):
    raise RuntimeError('MUSICDL_USER 与 MUSICDL_PASS 必须同时设置；拒绝以半配置认证启动')
AUTH_ENABLED = bool(AUTH_USER and AUTH_PASS)
if not AUTH_ENABLED and not IS_LOOPBACK_BIND and not _env_flag('MUSICDL_ALLOW_INSECURE_NO_AUTH'):
    raise RuntimeError('非回环监听必须配置 MUSICDL_USER/MUSICDL_PASS；如明确接受风险可设置 MUSICDL_ALLOW_INSECURE_NO_AUTH=1')

AUTH_USER_DIGEST = hashlib.sha256((AUTH_USER or '').encode('utf-8')).digest()
AUTH_PASS_DIGEST = hashlib.sha256((AUTH_PASS or '').encode('utf-8')).digest()
SESSION_COOKIE = 'musicdl_session'
SESSION_TTL = max(300, int(os.environ.get('MUSICDL_SESSION_TTL', '43200')))
COOKIE_SECURE = _env_flag('MUSICDL_COOKIE_SECURE', default=not IS_LOOPBACK_BIND)
SESSIONS: dict[str, float] = {}
SESSION_LOCK = threading.Lock()
LOGIN_FAILURES: dict[str, deque] = {}
LOGIN_LOCK = threading.Lock()
LOGIN_WINDOW = 300
LOGIN_MAX_FAILURES = 8
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}



def _client_key(request: Request) -> str:
    return request.client.host if request.client else 'unknown'


def _login_limited(key: str) -> bool:
    now = time.time()
    with LOGIN_LOCK:
        attempts = LOGIN_FAILURES.setdefault(key, deque())
        while attempts and attempts[0] <= now - LOGIN_WINDOW:
            attempts.popleft()
        return len(attempts) >= LOGIN_MAX_FAILURES


def _record_login_failure(key: str):
    with LOGIN_LOCK:
        LOGIN_FAILURES.setdefault(key, deque()).append(time.time())


def _clear_login_failures(key: str):
    with LOGIN_LOCK:
        LOGIN_FAILURES.pop(key, None)


def _session_valid(token: str | None) -> bool:
    if not token:
        return False
    now = time.time()
    with SESSION_LOCK:
        expired = [sid for sid, expires in SESSIONS.items() if expires <= now]
        for sid in expired:
            SESSIONS.pop(sid, None)
        return SESSIONS.get(token, 0) > now


@app.middleware('http')
async def security_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith('/api/'):
        if request.method.upper() in UNSAFE_METHODS and request.headers.get('x-musicdl-request') != '1':
            return JSONResponse(status_code=403, content={'detail': '请求来源校验失败'})
        if AUTH_ENABLED and path not in {'/api/auth/login', '/api/auth/status', '/api/health'}:
            if not _session_valid(request.cookies.get(SESSION_COOKIE)):
                return JSONResponse(status_code=401, content={'detail': '登录已失效，请重新登录'})
    return await call_next(request)

class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)

@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    if not AUTH_ENABLED:
        return {"success": True}
    key = _client_key(request)
    if _login_limited(key):
        raise HTTPException(status_code=429, detail='登录失败次数过多，请 5 分钟后再试', headers={'Retry-After': str(LOGIN_WINDOW)})
    user_ok = secrets.compare_digest(hashlib.sha256(body.username.encode('utf-8')).digest(), AUTH_USER_DIGEST)
    pass_ok = secrets.compare_digest(hashlib.sha256(body.password.encode('utf-8')).digest(), AUTH_PASS_DIGEST)
    if user_ok and pass_ok:
        token = secrets.token_urlsafe(32)
        with SESSION_LOCK:
            SESSIONS[token] = time.time() + SESSION_TTL
        _clear_login_failures(key)
        # Remove cookies from older releases: browsers send /api before /,
        # but Starlette's cookie mapping keeps the last duplicate value.
        response.delete_cookie(key=SESSION_COOKIE, path='/', secure=COOKIE_SECURE,
                               httponly=True, samesite='strict')
        response.set_cookie(key=SESSION_COOKIE, value=token, httponly=True, secure=COOKIE_SECURE,
                            samesite='strict', path='/api', max_age=SESSION_TTL)
        return {"success": True}
    _record_login_failure(key)
    raise HTTPException(status_code=401, detail='用户名或密码错误')

@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with SESSION_LOCK:
            SESSIONS.pop(token, None)
    response.delete_cookie(key=SESSION_COOKIE, path='/api', secure=COOKIE_SECURE, httponly=True, samesite='strict')
    response.delete_cookie(key=SESSION_COOKIE, path='/', secure=COOKIE_SECURE, httponly=True, samesite='strict')
    return {"success": True}

@app.on_event('startup')
async def raisethreadpool():
    # sync endpoints share one AnyIO threadpool (default 40). Slow source
    # searches and long-lived audio streams each hold a worker, so raise it.
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = _bounded_int(
        os.environ.get('MUSICDL_API_WORKERS'), 50, 10, 100,
    )
    # surface which internal patches took on this musicdl version
    ok = [k for k, v in PATCH_STATUS.items() if v == 'ok']
    skipped = {k: v for k, v in PATCH_STATUS.items() if v != 'ok'}
    pushlog('info', f'musicdl {MUSICDL_VERSION} · 增强补丁生效: {", ".join(ok) or "无"}')
    if skipped:
        pushlog('warning', f'以下增强在此 musicdl 版本上未生效（核心功能不受影响）: {skipped}')


class SearchBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    sources: list[str] | None = Field(default=None, max_length=100)


class PlaylistBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class DownloadBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)


class ValidateBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class TaskIdsBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class ConfigBody(BaseModel):
    music_sources: list[str]
    global_client_cfg: dict
    lazy_search: bool | None = None
    proxy_url: str = ''
    proxy_scope: str = 'all'
    proxy_sources: list[str] = []
    request_timeout: int = Field(default=30, ge=0, le=300)
    default_threads: int = Field(default=5, ge=1, le=50)
    download_layout: str = 'source'
    lyrics_mode: str = 'both'
    cookies: dict = {}
    init_music_clients_cfg: dict = {}
    clients_threadings: dict = {}
    requests_overrides: dict = {}
    search_rules: dict = {}


@app.get('/api/meta')
def meta():
    registered = list(MusicClientBuilder.REGISTERED_MODULES.keys())
    categorized = {cat: [s for s in names if s in registered] for cat, names in SOURCE_CATEGORIES.items()}
    leftovers = [s for s in registered if not any(s in names for names in SOURCE_CATEGORIES.values())]
    if leftovers:
        categorized['其他'] = leftovers
    return {'version': MUSICDL_VERSION, 'categories': categorized, 'default_sources': list(DEFAULT_MUSIC_SOURCES),
            'platform': sys.platform, 'is_windows': IS_WINDOWS, 'auth_enabled': AUTH_ENABLED,
            'cookie_secure': COOKIE_SECURE}


@app.get('/api/health')
def health():
    '''installed musicdl version + which internal enhancements took effect.
    Lets you upgrade musicdl and see at a glance if anything degraded.'''
    return {'musicdl_version': MUSICDL_VERSION, 'sources_registered': len(MusicClientBuilder.REGISTERED_MODULES),
            'patches': PATCH_STATUS}


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r'\d+', str(v))[:4])


@app.get('/api/musicdl/latest')
def musicdllatest():
    '''check PyPI for the newest musicdl (read-only; never installs anything)'''
    try:
        r = pyrequests.get('https://pypi.org/pypi/musicdl/json', timeout=8)
        r.raise_for_status()
        latest = r.json()['info']['version']
        return {'current': MUSICDL_VERSION, 'latest': latest,
                'update_available': _vtuple(latest) > _vtuple(MUSICDL_VERSION)}
    except Exception as err:
        raise HTTPException(502, f'查询 PyPI 失败: {err}')


# --- in-app upgrade: pip install -U musicdl, then restart to load it ---------
UPDATE_STATE = {'running': False, 'ok': None, 'detail': '', 'to': None}
UPDATE_LOCK = threading.Lock()
SELF_UPDATE_ENABLED = _env_flag('MUSICDL_ALLOW_SELF_UPDATE', default=not Path('/.dockerenv').exists())


def _runupdate():
    import subprocess
    try:
        proc = subprocess.run([sys.executable, '-m', 'pip', 'install', '-U', 'musicdl'],
                              capture_output=True, text=True, timeout=600)
        out = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if proc.returncode != 0:
            with UPDATE_LOCK:
                UPDATE_STATE.update(ok=False, detail=out[-500:] or f'pip 退出码 {proc.returncode}')
        else:
            try:
                from importlib.metadata import version as _pkgver
                newv = _pkgver('musicdl')
            except Exception:
                newv = None
            with UPDATE_LOCK:
                UPDATE_STATE.update(ok=True, to=newv)
        with UPDATE_LOCK:
            update_ok, update_to = UPDATE_STATE['ok'], UPDATE_STATE['to']
        pushlog('info', f'musicdl 升级: ok={update_ok} to={update_to}')
    except Exception as err:
        with UPDATE_LOCK:
            UPDATE_STATE.update(ok=False, detail=str(err))
    finally:
        with UPDATE_LOCK:
            UPDATE_STATE['running'] = False


@app.post('/api/musicdl/update')
def musicdlupdate():
    '''upgrade musicdl in the background (new version loads after restart)'''
    if not SELF_UPDATE_ENABLED:
        raise HTTPException(403, '当前部署已禁用运行时自更新，请通过重建镜像升级')
    with UPDATE_LOCK:
        if UPDATE_STATE['running']:
            return {'started': False, 'running': True}
        UPDATE_STATE.update(running=True, ok=None, detail='', to=None)
    threading.Thread(target=_runupdate, daemon=True).start()
    return {'started': True}


@app.get('/api/musicdl/update')
def musicdlupdatestatus():
    with UPDATE_LOCK:
        return dict(UPDATE_STATE, current=MUSICDL_VERSION, enabled=SELF_UPDATE_ENABLED)


@app.post('/api/restart')
def restartserver():
    '''exit with code 88; start.bat / start.sh loop on 88, Docker restart policy revives'''
    with UPDATE_LOCK:
        if UPDATE_STATE['running']:
            raise HTTPException(409, 'musicdl 正在升级，完成前不能重启')
    pushlog('info', '收到重启请求，1 秒后重启…')
    threading.Timer(1.0, lambda: os._exit(88)).start()
    return {'ok': True}


@app.get('/api/config')
def getconfig():
    return manager.config


@app.put('/api/config')
def putconfig(body: ConfigBody):
    try:
        cfg = normalizeconfigpaths(body.model_dump())
        proxy = cfg['proxy_url'].strip()
        if proxy and (urlsplit(proxy).scheme not in {'http', 'https'} or not urlsplit(proxy).hostname):
            raise ValueError('项目代理必须为 HTTP / HTTPS 地址')
        if cfg['proxy_scope'] not in {'all', 'selected', 'none'}:
            raise ValueError('无效的音源代理作用范围')
        cfg['proxy_url'] = proxy
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

DEFAULT_PLAYLIST_HOSTS = (
    'music.163.com', 'qq.com', 'kugou.com', 'kuwo.cn', 'migu.cn', 'taihe.com',
    '5sing.kugou.com', 'bilibili.com', 'youtube.com', 'youtu.be', 'soundcloud.com',
    'spotify.com', 'joox.com', 'deezer.com', 'qobuz.com', 'tidal.com', 'music.apple.com',
    'jamendo.com', 'streetvoice.com', 'ximalaya.com', 'lizhi.fm', 'qingting.fm',
)
PLAYLIST_ALLOWED_HOSTS = DEFAULT_PLAYLIST_HOSTS + tuple(
    item.strip().lower().lstrip('.') for item in os.environ.get('MUSICDL_PLAYLIST_HOSTS', '').split(',') if item.strip()
)


def validatepublicurl(raw_url: str, label: str = 'URL', allowed_hosts: tuple[str, ...] = ()) -> str:
    '''Reject non-HTTP and non-public destinations before handing a URL to a dependency.'''
    try:
        parsed = urlsplit(str(raw_url or '').strip())
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('仅允许 http/https URL')
        if parsed.username or parsed.password:
            raise ValueError('URL 不允许包含用户名或密码')
        hostname = parsed.hostname.lower().rstrip('.')
        if allowed_hosts and not any(hostname == suffix or hostname.endswith(f'.{suffix}') for suffix in allowed_hosts):
            raise ValueError('域名不在允许的平台列表中')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        try:
            addresses = {ipaddress.ip_address(hostname)}
        except ValueError:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)}
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError('目标地址不是公网地址')
    except Exception as err:
        raise HTTPException(400, f'{label} 不安全: {err}') from err
    return parsed.geturl()


def songplayable(song) -> bool:
    if song.downloaded_contents and isinstance(song.downloaded_contents, (bytes, bytearray)):
        return True
    return isinstance(song.download_url, str) and song.download_url.startswith('http') and str(song.protocol or 'HTTP').upper() == 'HTTP'


@app.get('/api/song/{sid}')
def songdetail(sid: str):
    song = getsong(sid)
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


@usedownloadheaderscookies
def _preview_get(client, url, options):
    # Select the exact download-stage backend, session and defaults of musicdl.
    return client.get(url, **copy.deepcopy(options))


def preview_request(client, source, url, options):
    response = None
    try:
        response = _preview_get(client, url, options)
        if response is not None:
            return response
        raise RuntimeError('原音源客户端未返回音频响应')
    except Exception:
        host = urlsplit(url).hostname or ''
        headers = options.get('headers') or {}
        session = getattr(client, 'session', None)
        session_headers = getattr(session, 'headers', {}) or {}
        has_credentials = (options.get('cookies') or options.get('auth') or getattr(session, 'auth', None)
                           or getattr(session, 'cookies', None)
                           or any(k.lower() in {'authorization', 'cookie', 'proxy-authorization'}
                                  for k in [*headers, *session_headers]))
        # The Kugou FLAC CDN serves a mismatched certificate. Match the original
        # downloader's compatibility retry only for credential-free audio CDNs.
        compatible_cdn = source == 'KugouMusicClient' and re.fullmatch(r'fs(?:[a-z0-9]*|\.[a-z0-9-]+)\.kugou\.com', host)
        explicit_verification = 'verify' in options and options['verify'] is not False
        if urlsplit(url).scheme != 'https' or not compatible_cdn or has_credentials or explicit_verification or _env_flag('MUSICDL_PREVIEW_STRICT_TLS'):
            raise
        if response is not None:
            response.close()
        pushlog('warning', '酷狗音频 CDN 请求失败，使用原引擎的证书兼容重试；未发送账号 Cookie 或认证头', category='playback')
        retry = {**options, 'verify': False, 'allow_redirects': False}
        response = _preview_get(client, url, retry)
        if response is None:
            raise RuntimeError('酷狗音频 CDN 兼容重试失败')
        return response


@app.get('/api/stream/{sid}')
def stream(sid: str, request: Request):
    return stream_song(sid, request)


def stream_song(sid, request, song=None):
    song = song if song is not None else getsong(sid)
    if song is None:
        raise HTTPException(404, '结果已过期，请重新搜索')
    try:
        engine = manager.get()
        client = engine.music_clients.get(song.source)
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
    download_url = validatepublicurl(song.download_url, '音频地址')
    kwargs = copy.deepcopy(engine.requests_overrides.get(song.source, {}))
    headers = dict(song.default_download_headers or kwargs.pop('headers', {}) or client.default_download_headers)
    # iter_content decodes compression; byte ranges must refer to raw audio.
    headers = {k: v for k, v in headers.items() if k.lower() not in {'range', 'if-range', 'accept-encoding'}}
    headers['Accept-Encoding'] = 'identity'
    cookies = dict(song.default_download_cookies or kwargs.pop('cookies', {}) or client.default_download_cookies)
    if request.headers.get('range'):
        headers['Range'] = request.headers['range']
    if request.headers.get('if-range'):
        headers['If-Range'] = request.headers['if-range']
    kwargs.update(stream=True, headers=headers, cookies=cookies)
    kwargs.setdefault('timeout', 30)
    upstream = None
    try:
        upstream = preview_request(client, song.source, download_url, kwargs)
        upstream.raise_for_status()
        encoded = upstream.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}
        if encoded and upstream.status_code == 206:
            # A decoded partial compressed body cannot carry encoded offsets.
            upstream.close()
            headers.pop('Range', None)
            headers.pop('If-Range', None)
            upstream = preview_request(client, song.source, download_url, kwargs)
            upstream.raise_for_status()
            encoded = upstream.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}
            if upstream.status_code != 200:
                raise ValueError('音源未返回完整音频')
        chunks = iter(upstream.iter_content(chunk_size=64 * 1024))
        first = next(chunks, b'')
        prefix = first.lstrip()[:32].lower()
        starts_at_zero = upstream.status_code == 200 or re.match(r'bytes 0-', upstream.headers.get('Content-Range', ''))
        if not first or (starts_at_zero and prefix.startswith((b'<!doctype', b'<html', b'<?xml', b'{', b'['))):
            raise ValueError('音源返回空内容或错误页面')
    except Exception as err:
        if upstream is not None:
            upstream.close()
        pushlog('error', f'在线试听拉取失败：{song.song_name} · {song.source} · {type(err).__name__}: {err} · HTTP {getattr(upstream, "status_code", "未知")}', category='playback', task_id=sid)
        raise HTTPException(502, '在线试听音频请求失败，请查看播放日志或重新搜索')
    resp_headers = {'Accept-Ranges': 'none' if encoded else upstream.headers.get('Accept-Ranges', 'none')}
    for h in (() if encoded else ('Content-Length', 'Content-Range', 'ETag', 'Last-Modified')):
        if h in upstream.headers:
            resp_headers[h] = upstream.headers[h]
    # Source format is authoritative: some CDNs label FLAC as octet-stream.
    media_type = mime if mime != 'application/octet-stream' else upstream.headers.get('Content-Type', mime)
    pushlog('debug', f'音频上游响应：{song.song_name} · {song.source} · HTTP {upstream.status_code} · MIME {media_type} · '
            f'Range {request.headers.get("range", "完整请求")[:120]} · Content-Range {upstream.headers.get("Content-Range", "无")[:120]} · '
            f'Content-Length {upstream.headers.get("Content-Length", "未知")} · 首包 {len(first)} bytes', category='playback', task_id=sid)
    def audio_chunks():
        sent, started = 0, time.monotonic()
        try:
            sent += len(first)
            yield first
            for chunk in chunks:
                sent += len(chunk)
                yield chunk
        except Exception as error:
            pushlog('error', f'试听音频传输中断：{song.song_name} · {song.source} · 已传输 {sent} bytes · '
                    f'{type(error).__name__}: {error}', category='playback', task_id=sid)
            raise
        finally:
            upstream.close()
            pushlog('debug', f'试听音频请求结束：{song.song_name} · {sent} bytes · 耗时 {time.monotonic() - started:.2f}s', category='playback', task_id=sid)
    return StreamingResponse(audio_chunks(), status_code=upstream.status_code,
                             media_type=media_type, headers=resp_headers)


class ResolveBody(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    artist: str = Field(default='', max_length=500)
    keyword: str = Field(default='', max_length=500)


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
    song = getsong(sid)
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
    url = validatepublicurl(url, '歌单链接', PLAYLIST_ALLOWED_HOSTS)
    try:
        client = manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    pushlog('info', f'解析歌单: {url}')
    songs = client.parseplaylist(url)
    if not songs:
        pushlog('warning', f'未能从歌单解析到歌曲: {url}')
    return {'results': [serializesong(registersong(song), song) for song in songs]}


def _safeseg(name: str, fallback: str) -> str:
    '''sanitize one path segment (folder name) for the download layout'''
    seg = re.sub(r'[\\/:*?"<>|]', ' ', str(name or '')).strip().strip('.').strip()
    return seg[:120] if seg else fallback


def applylayout(song, cfg=None):
    '''set song.work_dir per the configured 下载分类方式 (download_layout)'''
    cfg = manager.config if cfg is None else cfg
    layout = cfg.get('download_layout', 'source')
    base = cfg['global_client_cfg'].get('work_dir') or 'musicdl_outputs'
    if layout == 'source':
        # Incremental/cached search results may still point at the provider root.
        base = cfg.get('init_music_clients_cfg', {}).get(song.source, {}).get('work_dir') or base
        segs = [_safeseg(song.source, '未知音源')]
    elif layout == 'flat':
        segs = []
    elif layout == 'artist':
        segs = [_safeseg(song.singers, '未知歌手')]
    elif layout == 'artist_album':
        segs = [_safeseg(song.singers, '未知歌手'), _safeseg(song.album, '未知专辑')]
    else:
        return
    song.work_dir = os.path.join(base, *segs)
    song._save_path = None  # force save_path to recompute from the new work_dir


def _songlabel(e: dict) -> str:
    name = e.get('song_name') or '未知歌曲'
    singers = e.get('singers') or ''
    return f'{name} - {singers}' if singers else name


def _download_retry_limit() -> int:
    try:
        return max(0, min(5, int(manager.config.get('global_client_cfg', {}).get('max_retries', 3))))
    except Exception:
        return 3


def runtask(task_id: str, task: dict):
    task['status'] = 'running'
    task['progress'] = 0
    client = manager.client
    entries = task['songs']
    # 只处理尚未成功的：retry 时已完成的保持 done，不重复下载
    todo = [e for e in entries if e['status'] != 'done']
    valid = [e for e in todo if e['_song'] is not None and e['_song'].source in client.music_clients]
    valid_ids = {id(e) for e in valid}  # identity, not dict equality (同曲可重复入队)
    for e in todo:
        if e['_song'] is None:
            e['status'], e['detail'], e['progress'] = 'failed', '搜索结果已过期，请重新搜索该歌曲后再下载', 100
            pushlog('warning', f'跳过「{_songlabel(e)}」：搜索结果已过期（服务重启会清空结果缓存）')
        elif id(e) not in valid_ids:
            e['status'], e['detail'], e['progress'] = 'failed', f'音源 {e.get("source") or "?"} 当前未启用', 100
            pushlog('warning', f'跳过「{_songlabel(e)}」：所属音源当前未在设置里启用')
        else:
            e['status'] = 'running'
            e['detail'] = '正在准备下载'
            e['progress'] = 0
            e['attempts'] = int(e.get('attempts') or 0)
            try:
                applylayout(e['_song'])
            except Exception as err:
                pushlog('warning', f'下载「{_songlabel(e)}」时无法使用设置的分类目录，已改用默认目录。原因：{err}')
    if valid:
        preview = '、'.join(_songlabel(e) for e in valid[:3]) + ('…' if len(valid) > 3 else '')
        pushlog('info', f'下载任务 {task_id} 已开始，共 {len(valid)} 首：{preview}')
    try:
        def matchkey(s):
            return (str(s.source), str(s.identifier or ''), str(s.song_name or ''))
        retry_limit = _download_retry_limit()
        remaining = valid
        attempt = 0
        while remaining and attempt <= retry_limit:
            attempt += 1
            for e in remaining:
                e['attempts'] = attempt
                e['status'] = 'running'
                e['detail'] = f'正在下载（第 {attempt} 次尝试）'
                e['progress'] = 30
            if attempt > 1:
                names = '、'.join(_songlabel(e) for e in remaining[:3]) + ('…' if len(remaining) > 3 else '')
                pushlog('info', f'下载任务 {task_id} 正在自动重试第 {attempt - 1} 次：{names}')

            done = client.download([e['_song'] for e in remaining]) or []
            # musicdl deep-copies SongInfo objects while downloading, so match by identity fields
            done_map = {matchkey(s): s for s in done}
            next_remaining = []
            for e in remaining:
                hit = done_map.get(matchkey(e['_song']))
                if hit is not None:
                    e['status'] = 'done'
                    e['progress'] = 100
                    try:
                        e['detail'] = os.path.basename(hit.save_path)
                    except Exception:
                        e['detail'] = '已保存'
                    pushlog('info', f'下载成功：{_songlabel(e)}' + (f'，文件：{e["detail"]}' if e['detail'] else ''))
                elif attempt <= retry_limit:
                    e['status'] = 'pending'
                    e['progress'] = 0
                    e['detail'] = f'本次未拿到可用文件，准备第 {attempt + 1} 次重试'
                    next_remaining.append(e)
                else:
                    e['status'] = 'failed'
                    e['progress'] = 100
                    e['detail'] = '多次尝试后仍未拿到可用文件，可能需要登录/VIP、歌曲下架或音源暂时不可用'
                    pushlog('warning', f'下载失败：{_songlabel(e)}。原因：音源 {e.get("source") or "?"} 未返回可用文件，已达到重试上限')
            done_cnt = sum(1 for e in entries if e['status'] == 'done')
            failed_cnt = sum(1 for e in entries if e['status'] == 'failed')
            task['progress'] = round(((done_cnt + failed_cnt) / max(1, len(entries))) * 100)
            remaining = next_remaining
        task['status'] = 'done' if all(e['status'] == 'done' for e in entries) else ('failed' if all(e['status'] == 'failed' for e in entries) else 'partial')
        ok_cnt = sum(1 for e in entries if e['status'] == 'done')
        fail_cnt = sum(1 for e in entries if e['status'] == 'failed')
        task['progress'] = 100
        pushlog('info', f'下载任务 {task_id} 已结束：成功 {ok_cnt} 首' + (f'，失败 {fail_cnt} 首，可在下载管理里手动重试' if fail_cnt else ''))
    except Exception as err:
        for e in valid:
            if e['status'] == 'running':
                e['status'], e['detail'], e['progress'] = 'failed', f'下载中断：{err}', 100
        task['status'] = 'failed'
        task['progress'] = 100
        pushlog('error', f'下载任务 {task_id} 中断。错误信息：{err}')
    task['finished_at'] = time.strftime('%H:%M:%S')
    with TASK_LOCK:
        for entry in entries:
            ACTIVE_SONG_IDS.discard(entry['id'])


def _trim_finished_tasks_locked(limit: int = 200):
    if len(TASK_ORDER) <= limit:
        return
    for tid in list(reversed(TASK_ORDER)):
        if len(TASK_ORDER) <= limit:
            break
        task = TASKS.get(tid)
        if task and task.get('status') in {'done', 'failed', 'partial'}:
            TASK_ORDER.remove(tid)
            TASKS.pop(tid, None)


def _download_future_done(task: dict, future):
    err = future.exception()
    if err is not None:
        task['status'] = 'failed'
        task['progress'] = 100
        task['finished_at'] = time.strftime('%H:%M:%S')
        pushlog('error', f'下载任务后台线程异常：{err}')
    with TASK_LOCK:
        for entry in task['songs']:
            ACTIVE_SONG_IDS.discard(entry['id'])


@app.post('/api/songs/validate')
def validatesongs(body: ValidateBody):
    '''批量检查哪些 id 已失效（服务重启后收藏里的旧 id）——一次调用，前端只重解析失效的'''
    invalid = [sid for sid in dict.fromkeys(body.ids) if getsong(sid) is None]
    return {'invalid': invalid}


@app.post('/api/download')
def download(body: DownloadBody):
    ids = list(dict.fromkeys(body.ids))
    songs_by_id = {sid: getsong(sid) for sid in ids}
    # 失效的挑出来告知前端，不再因为个别失效把整批 404 掉
    expired = [sid for sid, song in songs_by_id.items() if song is None]
    ids = [sid for sid in ids if songs_by_id[sid] is not None]
    if not ids:
        raise HTTPException(404, '所选歌曲结果均已过期，请重新解析后再下载')
    try:
        manager.get()
    except Exception as err:
        raise HTTPException(400, f'音源初始化失败: {err}')
    task_id = uuid.uuid4().hex[:8]
    songs = []
    for sid in ids:
        song = songs_by_id[sid]
        songs.append({
            '_song': song, 'id': sid, 'status': 'pending', 'detail': '',
            'progress': 0, 'attempts': 0,
            'song_name': str(song.song_name),
            'singers': str(song.singers),
            'source': str(song.source or '').removesuffix('MusicClient'),
        })
    task = {'id': task_id, 'status': 'pending', 'progress': 0, 'created_at': time.strftime('%H:%M:%S'), 'finished_at': None, 'songs': songs}
    with TASK_LOCK:
        new_ids = [sid for sid in ids if sid not in ACTIVE_SONG_IDS]
        if not new_ids:
            raise HTTPException(409, '所选歌曲均已有下载任务正在进行')
        # Update ids and songs list to only include new_ids
        ids = new_ids
        songs = [s for s in songs if s['id'] in new_ids]
        task['songs'] = songs

        _trim_finished_tasks_locked(limit=199)
        if len(TASK_ORDER) >= 200:
            raise HTTPException(503, '活动任务过多，请稍后再试')
        TASKS[task_id] = task
        TASK_ORDER.insert(0, task_id)
        ACTIVE_SONG_IDS.update(ids)
    future = DOWNLOAD_EXECUTOR.submit(runtask, task_id, task)
    future.add_done_callback(lambda finished: _download_future_done(task, finished))
    pushlog('info', f'已添加下载任务 {task_id}：{len(ids)} 首歌进入下载列表'
            + (f'；另有 {len(expired)} 首因搜索结果过期被跳过' if expired else ''))
    return {'task_id': task_id, 'queued': len(ids), 'expired': expired}


@app.post('/api/tasks/{task_id}/retry')
def retrytask(task_id: str):
    '''重试一个任务里下载失败的歌曲（已成功的保持不变，不重复下载）'''
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            raise HTTPException(404, '任务不存在或已被清除')
        if task.get('status') in ('running', 'pending'):
            raise HTTPException(409, '任务仍在下载中，请等它结束后再重试')
        failed = [e for e in task['songs'] if e['status'] == 'failed']
        if not failed:
            raise HTTPException(400, '该任务没有失败的歌曲，无需重试')
        alive = [e for e in failed if e['_song'] is not None]
        if not alive:
            raise HTTPException(410, '失败歌曲的搜索结果均已过期，请回到搜索结果里重新下载')
        for e in alive:
            e['status'], e['detail'], e['progress'], e['attempts'] = 'pending', '', 0, 0
        task['status'] = 'pending'
        task['progress'] = 0
        task['finished_at'] = None
        ACTIVE_SONG_IDS.update(e['id'] for e in alive)
    future = DOWNLOAD_EXECUTOR.submit(runtask, task_id, task)
    future.add_done_callback(lambda finished: _download_future_done(task, finished))
    pushlog('info', f'已手动重试任务 {task_id}：{len(alive)} 首失败歌曲重新加入下载队列')
    return {'task_id': task_id, 'retrying': len(alive)}


@app.post('/api/tasks/delete')
def deletetasks(body: TaskIdsBody):
    '''批量删除下载任务记录（正在下载/排队中的会被跳过，避免丢掉进行中任务）'''
    removed, skipped = 0, 0
    with TASK_LOCK:
        for tid in dict.fromkeys(body.ids):
            task = TASKS.get(tid)
            if not task:
                continue
            if task.get('status') in ('running', 'pending'):
                skipped += 1
                continue
            if tid in TASK_ORDER:
                TASK_ORDER.remove(tid)
            TASKS.pop(tid, None)
            removed += 1
    return {'removed': removed, 'skipped': skipped}


@app.post('/api/tasks/clear-finished')
def clearfinishedtasks():
    with TASK_LOCK:
        finished = [tid for tid in TASK_ORDER if TASKS.get(tid, {}).get('status') in ('done', 'failed', 'partial')]
        for tid in finished:
            TASK_ORDER.remove(tid)
            TASKS.pop(tid, None)
    return {'removed': len(finished)}


@app.post('/api/open-dir')
def opendir():
    '''open the main download dir in the OS file manager (local desktop only)'''
    main = _resolve_allowed_workdir(manager.config['global_client_cfg'].get('work_dir'))
    main.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        # headless / NAS / Docker deployments have no desktop to open
        raise HTTPException(400, f'服务端非本机桌面环境，下载目录为：{main}')
    try:
        os.startfile(str(main))
    except Exception as err:
        raise HTTPException(500, f'无法打开目录: {err}')
    return {'ok': True, 'dir': str(main)}


@app.post('/api/cache/clear')
def clearcache():
    '''remove musicdl cache clutter: stray *.pkl files and empty result folders,
    keeping every real audio file. Safe to run anytime.'''
    removed_files, freed = 0, 0
    for workdir in manager.workdirs():
        if not workdir.is_dir():
            continue
        for path in workdir.rglob('*.pkl'):
            if path.is_file():
                try:
                    freed += path.stat().st_size
                    path.unlink()
                    removed_files += 1
                except Exception:
                    pass
        # prune now-empty directories (deepest first) — e.g. old timestamp folders
        for path in sorted((p for p in workdir.rglob('*') if p.is_dir()),
                           key=lambda p: len(p.parts), reverse=True):
            try:
                if not any(path.iterdir()):
                    path.rmdir()
            except Exception:
                pass
    pushlog('info', f'清理缓存: 删除 {removed_files} 个缓存文件，释放 {freed // 1024} KB')
    return {'removed': removed_files, 'freed_kb': freed // 1024}


@app.get('/api/tasks')
def tasks():
    with TASK_LOCK:
        out = []
        for tid in TASK_ORDER[:50]:
            t = TASKS.get(tid)
            if not t:
                continue
            out.append({k: v for k, v in t.items() if k != 'songs'} | {'songs': [{k: v for k, v in e.items() if k != '_song'} for e in t['songs']]})
    return {'tasks': out}


@app.get('/api/files')
def files():
    out = []
    for workdir in manager.workdirs():
        if not workdir.is_dir():
            continue
        for path in workdir.rglob('*'):
            try:
                if not path.is_file() or path.suffix.lower() == '.pkl':
                    continue
                stat = path.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
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
    with LOG_LOCK:
        return {'logs': list(LOGS)}


@app.post('/api/logs/clear')
def clearlogs():
    with LOG_LOCK:
        LOGS.clear()
    return {'ok': True}


if __name__ == '__main__':
    import uvicorn
    # 0.0.0.0 + env overrides so the same image runs in Docker / on a NAS
    host = os.environ.get('MUSICDL_HOST', '127.0.0.1')
    port = int(os.environ.get('MUSICDL_PORT', '8765'))
    uvicorn.run(app, host=host, port=port)
