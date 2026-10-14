"""Process isolation around the original engine; no rewritten provider protocols."""
import copy
import ast
import hashlib
import json
import os
import pickle
import sys
import threading
import time
import traceback
import struct
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

_clients = OrderedDict()


@contextmanager
def capture_logs(path, category, task_id, transform=None):
    from legacy import app as original
    previous = original.pushlog
    write_lock = threading.Lock()
    def forward(level, message, **kwargs):
        if transform:
            message = transform(level, message)
        kwargs.setdefault('category', category)
        kwargs.setdefault('task_id', task_id)
        entry = previous(level, message, **kwargs)
        try:
            with write_lock, open(path, 'a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass
        return entry
    original.pushlog = forward
    try:
        yield
    finally:
        original.pushlog = previous


def search_logged(config, source, keyword, path, task_id, partial_path=None):
    from legacy import app as original
    errors = []
    def observe(level, message):
        if level == 'error' and str(message).startswith(f'{source}._search >>>'):
            errors.append(message)
        return message
    with capture_logs(path, 'search', task_id, transform=observe):
        try:
            songs = search(config, source, keyword, partial_path)
            if not songs and errors:
                raise RuntimeError('原音源搜索请求失败，请查看运行日志')
            return songs
        except Exception:
            original.pushlog('error', f'{source} 搜索异常\n{traceback.format_exc()}')
            raise


def playlist_logged(config, url, path, task_id):
    from legacy import app as original
    issues = {}
    def describe(level, message):
        issue = playlist_issue(message)
        if issue:
            key = issue['source_id'] or f"{issue['title']}|{issue['artist']}"
            issues[key] = issue
            return f'跳过曲目「{issue["title"]}」 · {issue["artist"] or "歌手未知"} · ID {issue["source_id"] or "未知"}：{issue["reason"]}'
        return message
    with capture_logs(path, 'playlist', task_id, describe):
        try:
            songs = playlist(config, url)
            return {'songs': songs, 'issues': list(issues.values())}
        except Exception:
            original.pushlog('error', f'歌单解析异常\n{traceback.format_exc()}')
            raise


def playlist_issue(message):
    """Explain native per-track failures without changing any parser decisions."""
    prefix = 'Fail to parse track info '
    if not str(message).startswith(prefix):
        return None
    try:
        data = ast.literal_eval(str(message)[len(prefix):])
        if not isinstance(data, dict):
            return None
        artists = data.get('ar') or data.get('artists') or data.get('singer') or []
        if isinstance(artists, list):
            artist = ', '.join(str(a.get('name', '')) for a in artists if isinstance(a, dict))
        else:
            artist = str(artists)
        hint = data.get('noCopyrightRcmd') or {}
        reason = '原音源未返回可用音频地址，该曲目未导入；可尝试换源搜索。'
        if isinstance(hint, dict) and hint.get('typeDesc'):
            reason += f' 音源提示：{hint["typeDesc"]}。'
        return {'source_id': str(data.get('id') or data.get('mid') or data.get('songmid') or ''),
                'title': str(data.get('name') or data.get('title') or data.get('songname') or '未知曲目'),
                'artist': artist, 'reason': reason}
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return None


def client_for(config, source=None):
    from legacy import app as original
    config = copy.deepcopy({k: v for k, v in config.items() if not k.startswith('_musicdl_')})
    if source:
        config['music_sources'] = [source]
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if key not in _clients:
        manager = original.ClientManager()
        manager.config = config
        manager.build()
        # Keep one source per reusable worker; avoid retaining many provider sessions.
        if _clients:
            # An in-process legacy caller may still be using the previous client.
            _clients.popitem(last=False)
        _clients[key] = manager.client
    return _clients[key]


def search(config, source, keyword, partial_path=None):
    from legacy import app as original
    client = client_for(config, source)
    provider = client.music_clients[source]
    if cfg_song := config.get('_musicdl_resolve'):
        songs = provider._resolve_candidate(copy.deepcopy(cfg_song), client.requests_overrides[source])
        for song in songs:
            song.work_dir, song._save_path = provider.work_dir, None
        return songs
    provider._musicdl_config = hashlib.sha256(json.dumps({k: v for k, v in config.items() if not k.startswith('_musicdl_')}, sort_keys=True).encode()).hexdigest()
    provider._musicdl_refresh = bool(config.get('_musicdl_refresh'))
    provider._musicdl_started = time.time()
    previous_mode = getattr(provider, '_musicdl_candidates', False)
    provider._musicdl_candidates = bool(config.get('_musicdl_candidates'))
    original.pushlog('info', f'{source} 搜索参数：每源 {provider.search_size_per_source} 条，'
                     f'每页 {provider.search_size_per_page} 条，严格分页限制 {provider.strict_limit_search_size_per_page}',
                     category='search')
    # Observe native appends without replacing any response parser or arguments.
    # A slow later track must not hide already resolved tracks at a bot's deadline.
    native_page = provider._search if partial_path else None
    write_lock = threading.Lock()
    if partial_path:
        class Results(list):
            def append(self, song):
                super().append(song)
                if song.with_valid_download_url or song.raw_data.get('_musicdl_candidate'):
                    try:
                        snapshot = copy.copy(song)
                        snapshot.work_dir, snapshot._save_path = provider.work_dir, None
                        data = pickle.dumps(snapshot, protocol=pickle.HIGHEST_PROTOCOL)
                        with write_lock, open(partial_path, 'ab') as handle:
                            handle.write(struct.pack('!Q', len(data)) + data)
                    except Exception as error:
                        original.pushlog('warning', f'{source} 无法增量传递曲目，等待完整结果：{type(error).__name__}',
                                         category='search')
        # BaseMusicClient collects the original list after _search returns.
        # Mirror native appends into that list as well as the IPC stream.
        def page(*args, **kwargs):
            args = list(args)
            target = args[3] if len(args) > 3 else kwargs.get('song_infos')
            if target is None:
                return native_page(*args, **kwargs)
            observed = Results(target)
            if len(args) > 3:
                args[3] = observed
            else:
                kwargs['song_infos'] = observed
            try:
                return native_page(*args, **kwargs)
            finally:
                target[:] = observed
        provider._search = page
    try:
        # These are exactly the arguments used by the original searchsubset.
        songs = provider.search(
            keyword=keyword, num_threadings=client.clients_threadings[source],
            request_overrides=client.requests_overrides[source], rule=client.search_rules[source])
    finally:
        provider._musicdl_candidates = previous_mode
        if partial_path:
            provider._search = native_page
    lossless = sum(str(song.ext).lower() in original.MusicClient.LOSSLESS_QUALITY_DEFINITIONS for song in songs)
    largest = max((int(song.file_size_bytes or 0) for song in songs), default=0)
    original.pushlog('info', f'{source} 搜索结果：{len(songs)} 条，无损 {lossless} 条，最大文件 {largest} 字节',
                     category='search')
    return songs


def playlist(config, url):
    return client_for(config).parseplaylist(url)


def download(input_path, output_path):
    from legacy import app as original
    with capture_logs(str(output_path) + '.log', 'download', Path(input_path).stem):
        try:
            _download(input_path, output_path)
        except Exception:
            original.pushlog('error', f'下载进程异常\n{traceback.format_exc()}')
            Path(output_path).write_text(json.dumps({'status': 'failed', 'detail': '下载进程异常，请查看运行日志'}), encoding='utf-8')


def _download(input_path, output_path):
    input_path, output_path = Path(input_path).resolve(), Path(output_path).resolve()
    from legacy import app as original
    from rich.progress import Progress
    # The input is created only by this service in its private data directory.
    with open(input_path, 'rb') as handle:
        payload = pickle.load(handle)
    song, config = payload['song'], payload['config']
    import resumable_download
    resumable_download.install()
    original.manager.config = config
    client = client_for(config, song.source)
    state = {'status': 'downloading', 'bytes': payload.get('bytes', 0),
             'total_bytes': payload.get('total_bytes'), 'detail': ''}
    lock = threading.RLock()
    last_write = 0.0

    def publish(force=False):
        nonlocal last_write
        with lock:
            if not force and time.monotonic() - last_write < 1:
                return
            last_write = time.monotonic()
            temporary = str(output_path) + '.tmp'
            Path(temporary).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, output_path)

    original_update, original_advance = Progress.update, Progress.advance

    def observe(progress, task_id):
        try:
            task = progress.tasks[task_id]
            if task.fields.get('kind') == 'download':
                with lock:
                    state['bytes'] = max(0, int(task.completed))
                    state['total_bytes'] = int(task.total) if task.total else None
                    description = str(task.description)
                    if description.startswith(('正在下载', '断点续传', '音源不支持续传', '下载中断')):
                        if state['detail'] != description:
                            original.pushlog('warning' if description.startswith('下载中断') else 'info', description)
                        state['detail'] = description
                publish()
        except (IndexError, KeyError):
            pass

    def update(progress, task_id, *args, **kwargs):
        result = original_update(progress, task_id, *args, **kwargs)
        observe(progress, task_id)
        return result

    def advance(progress, task_id, *args, **kwargs):
        result = original_advance(progress, task_id, *args, **kwargs)
        observe(progress, task_id)
        return result

    Progress.update, Progress.advance = update, advance
    publish(True)
    try:
        # Full SongInfo survives intact: bytes, headers, cookies, HLS/custom
        # stream objects and source-specific download overrides all stay here.
        retries = max(0, min(5, int(config.get('global_client_cfg', {}).get('max_retries', 3))))
        completed = []
        for attempt in range(retries + 1):
            state['detail'] = f'原音源下载器，第 {attempt + 1} 次尝试'
            publish(True)
            completed = client.download([copy.deepcopy(song)]) or []
            if completed:
                break
        if not completed:
            raise RuntimeError('原音源下载器未返回文件；请查看原版日志或更换解析配置')
        files = [str(Path(item.save_path).resolve()) for item in completed]
        if not all(Path(p).is_file() and Path(p).stat().st_size for p in files):
            raise RuntimeError('原音源下载器返回的文件不存在或为空')
        state.update(status='complete', files=files, bytes=sum(Path(p).stat().st_size for p in files), detail='已由原音源下载器保存')
    except Exception as error:
        # Avoid exposing signed URLs or account material through progress APIs.
        original.pushlog('error', f'原引擎下载失败\n{traceback.format_exc()}')
        state.update(status='failed', detail=str(error) if 'http' not in str(error) else '原音源下载失败，请检查网络或解析配置')
    finally:
        publish(True)


if __name__ == '__main__':
    if sys.argv[1] == '--search':
        keep_alive = '--keep-alive' in sys.argv
        paths = sys.argv[-2:]
        while paths:
            input_path, output_path = (Path(p).resolve() for p in paths)
            try:
                result = search_logged(*pickle.loads(input_path.read_bytes()))
            except Exception:
                result = {'worker_error': '原音源搜索失败，请查看运行日志'}
            temporary = output_path.with_suffix('.tmp')
            temporary.write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(temporary, output_path)
            if not keep_alive:
                break
            line = sys.stdin.readline()
            paths = json.loads(line) if line else None
    else:
        download(sys.argv[1], sys.argv[2])
