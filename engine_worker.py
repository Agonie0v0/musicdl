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
from contextlib import contextmanager
from pathlib import Path

_clients = {}


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


def search_logged(config, source, keyword, path, task_id):
    from legacy import app as original
    with capture_logs(path, 'search', task_id):
        try:
            return search(config, source, keyword)
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
    config = copy.deepcopy(config)
    if source:
        config['music_sources'] = [source]
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    if key not in _clients:
        manager = original.ClientManager()
        manager.config = config
        manager.build()
        if len(_clients) >= 4:
            _clients.clear()
        _clients[key] = manager.client
    return _clients[key]


def search(config, source, keyword):
    client = client_for(config, source)
    # These are exactly the arguments used by the original searchsubset.
    return client.music_clients[source].search(
        keyword=keyword, num_threadings=client.clients_threadings[source],
        request_overrides=client.requests_overrides[source], rule=client.search_rules[source])


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
            if not force and time.monotonic() - last_write < .2:
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
    download(sys.argv[1], sys.argv[2])
