import hashlib
import os
import copy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from legacy import app


WRITE_HEADERS = {'X-MusicDL-Request': '1'}


def test_audio_disconnect_logs_original_error_and_byte_count(monkeypatch):
    import asyncio
    from unittest.mock import Mock
    from starlette.requests import Request
    song = SimpleNamespace(source='KugouMusicClient', ext='flac', song_name='断流测试', downloaded_contents=None,
                           protocol='HTTP', download_url='https://example.org/audio',
                           default_download_headers={}, default_download_cookies={})
    upstream = Mock(status_code=200, headers={'Content-Type': 'audio/flac'})
    def chunks(**kwargs):
        yield b'fLaC'
        raise ConnectionError('HTTP 503 https://audio.example/song?signature=private-link')
    upstream.iter_content = chunks
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={})
    monkeypatch.setattr(app, 'getsong', lambda sid: song)
    monkeypatch.setattr(app.manager, 'get', lambda: SimpleNamespace(music_clients={song.source: provider}, requests_overrides={}))
    monkeypatch.setattr(app, 'validatepublicurl', lambda url, label: url)
    monkeypatch.setattr(app, 'preview_request', lambda *args: upstream)
    request = Request({'type': 'http', 'headers': []})
    response = app.stream('disconnect-test', request)
    async def consume():
        async for chunk in response.body_iterator:
            pass
    with pytest.raises(ConnectionError):
        asyncio.run(consume())
    entry = next(entry for entry in reversed(app.LOGS) if '试听音频传输中断' in entry['message'])
    assert entry['task_id'] == 'disconnect-test' and entry['level'] == 'error'
    assert 'HTTP 503' in entry['message'] and '已传输 4 bytes' in entry['message']
    assert 'private-link' not in entry['message']
    upstream.close.assert_called_once()


@pytest.mark.parametrize('encoded,partial', [(False, False), (True, False), (True, True)])
def test_audio_stream_normalizes_mime_and_compressed_range(monkeypatch, encoded, partial):
    from unittest.mock import Mock
    content = b'fLaC' + b'\0' * 20
    song = SimpleNamespace(source='KugouMusicClient', ext='flac', song_name='stream fixture', downloaded_contents=None,
                           protocol='HTTP', download_url='https://example.org/audio',
                           default_download_headers={'User-Agent': 'fixture'}, default_download_cookies={})
    monkeypatch.setattr(app, 'getsong', lambda sid: song)
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={}, get=lambda url, **kwargs: app.pyrequests.get(url, **kwargs))
    monkeypatch.setattr(app.manager, 'get', lambda: SimpleNamespace(music_clients={'KugouMusicClient': provider}, requests_overrides={}))
    monkeypatch.setattr(app, 'validatepublicurl', lambda url, label: url)
    monkeypatch.setattr(app, 'AUTH_ENABLED', False)
    first = Mock()
    first.status_code = 206 if partial else 200
    first.headers = {'Content-Type': 'application/octet-stream', 'Content-Length': '999' if encoded else str(len(content))}
    if encoded:
        first.headers.update({'Content-Encoding': 'gzip'})
    if partial:
        first.headers.update({'Content-Range': 'bytes 0-998/999'})
    first.iter_content.return_value = iter([content])
    second = Mock()
    second.status_code, second.headers = 200, first.headers.copy()
    second.headers.pop('Content-Range', None)
    second.iter_content.return_value = iter([content])
    calls = []
    def get(url, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        return first if len(calls) == 1 else second
    monkeypatch.setattr(app.pyrequests, 'get', get)
    response = TestClient(app.app).get('/api/stream/fixture', headers={'Range': 'bytes=0-'})
    assert response.status_code == 200
    assert response.content == content
    assert response.headers['content-type'] == 'audio/flac'
    assert calls[0]['headers']['Accept-Encoding'] == 'identity'
    if encoded:
        assert 'content-length' not in response.headers
        assert 'content-range' not in response.headers
        assert response.headers['accept-ranges'] == 'none'
    else:
        assert response.headers['content-length'] == str(len(content))
    if partial:
        assert len(calls) == 2 and 'Range' not in calls[1]['headers']
        second.close.assert_called_once()
    first.close.assert_called_once()


def test_audio_stream_rejects_error_page_instead_of_labelling_it_audio(monkeypatch):
    from unittest.mock import Mock
    song = SimpleNamespace(source='KugouMusicClient', ext='flac', song_name='invalid fixture', downloaded_contents=None,
                           protocol='HTTP', download_url='https://example.org/audio',
                           default_download_headers={}, default_download_cookies={})
    monkeypatch.setattr(app, 'getsong', lambda sid: song)
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={}, get=lambda url, **kwargs: app.pyrequests.get(url, **kwargs))
    monkeypatch.setattr(app.manager, 'get', lambda: SimpleNamespace(music_clients={'KugouMusicClient': provider}, requests_overrides={}))
    monkeypatch.setattr(app, 'validatepublicurl', lambda url, label: url)
    monkeypatch.setattr(app, 'AUTH_ENABLED', False)
    upstream = Mock(status_code=200, headers={'Content-Type': 'text/html'})
    upstream.iter_content.return_value = iter([b'<!DOCTYPE html><html>access denied</html>'])
    monkeypatch.setattr(app.pyrequests, 'get', lambda *args, **kwargs: upstream)
    response = TestClient(app.app).get('/api/stream/fixture')
    assert response.status_code == 502
    assert '播放日志' in response.json()['detail']
    upstream.close.assert_called_once()
    assert any(entry['category'] == 'playback' and '在线试听拉取失败' in entry['message'] for entry in app.LOGS)


def test_preview_uses_original_download_backend_cookies_and_request_overrides(monkeypatch):
    from unittest.mock import Mock
    response = Mock(status_code=200, headers={'Content-Type': 'audio/flac'})
    response.iter_content.return_value = iter([b'fLaC' + b'\0' * 20])
    provider = SimpleNamespace(default_download_headers={'User-Agent': 'download-agent'},
        default_download_cookies={'session': 'download-cookie'}, default_headers={'User-Agent': 'search-agent'},
        default_cookies={}, enable_download_curl_cffi=True, enable_curl_cffi=False, _initsession=Mock())
    def provider_get(url, **options):
        assert provider.enable_curl_cffi is True
        assert provider.default_headers == provider.default_download_headers
        assert provider.default_cookies == provider.default_download_cookies
        assert options['cookies'] == {'session': 'download-cookie'}
        assert options['params'] == {'needed': 'yes'}
        assert options['timeout'] == 90
        assert options['proxies'] == {'https': 'http://proxy.example:7890'}
        assert options['headers']['X-Required'] == 'keep'
        assert options['headers']['Range'] == 'bytes=0-'
        return response
    provider.get = Mock(side_effect=provider_get)
    overrides = {'MiguMusicClient': {'headers': {'X-Required': 'keep'}, 'params': {'needed': 'yes'},
                                      'timeout': 90, 'proxies': {'https': 'http://proxy.example:7890'}}}
    song = SimpleNamespace(source='MiguMusicClient', ext='flac', song_name='fixture', downloaded_contents=None,
                           protocol='HTTP', download_url='https://example.org/audio',
                           default_download_headers={}, default_download_cookies={})
    monkeypatch.setattr(app, 'getsong', lambda sid: song)
    monkeypatch.setattr(app.manager, 'get', lambda: SimpleNamespace(music_clients={'MiguMusicClient': provider}, requests_overrides=overrides))
    monkeypatch.setattr(app, 'validatepublicurl', lambda url, label: url)
    monkeypatch.setattr(app, 'AUTH_ENABLED', False)
    result = TestClient(app.app).get('/api/stream/fixture', headers={'Range': 'bytes=0-'})
    assert result.status_code == 200 and result.content.startswith(b'fLaC')
    provider.get.assert_called_once()
    provider._initsession.assert_called_once()
    assert overrides['MiguMusicClient']['headers'] == {'X-Required': 'keep'}


@pytest.mark.parametrize('host,cookies,strict,compatible', [
    ('fs.youthandroid2.kugou.com', {}, False, True),
    ('fs.youthandroid2.kugou.com', {'account': 'secret'}, False, False),
    ('fs.youthandroid2.kugou.com', {}, True, False),
    ('other.example.org', {}, False, False),
    ('fs.youthandroid2.kugou.com.evil.example', {}, False, False),
])
def test_kugou_certificate_retry_is_scoped_to_public_audio_cdn(monkeypatch, host, cookies, strict, compatible):
    from unittest.mock import Mock
    response = Mock()
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={}, get=Mock(side_effect=[None, response]))
    monkeypatch.setenv('MUSICDL_PREVIEW_STRICT_TLS', '1' if strict else '0')
    options = {'headers': {'Range': 'bytes=0-'}, 'cookies': cookies}
    if compatible:
        assert app.preview_request(provider, 'KugouMusicClient', f'https://{host}/file.flac', options) is response
        assert provider.get.call_args.kwargs['verify'] is False
        assert provider.get.call_args.kwargs['allow_redirects'] is False
        assert 'verify' not in options
    else:
        with pytest.raises(RuntimeError):
            app.preview_request(provider, 'KugouMusicClient', f'https://{host}/file.flac', options)
        assert provider.get.call_count == 1


def test_range_in_middle_of_audio_is_not_mistaken_for_error_json(monkeypatch):
    from unittest.mock import Mock
    content = b'{' + b'\x80' * 30
    response = Mock(status_code=206, headers={'Content-Range': 'bytes 1024-1054/2048', 'Content-Length': '31'})
    response.iter_content.return_value = iter([content])
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={}, get=Mock(return_value=response))
    song = SimpleNamespace(source='MiguMusicClient', ext='flac', song_name='fixture', downloaded_contents=None,
        protocol='HTTP', download_url='https://example.org/audio', default_download_headers={}, default_download_cookies={})
    monkeypatch.setattr(app, 'getsong', lambda sid: song)
    monkeypatch.setattr(app.manager, 'get', lambda: SimpleNamespace(music_clients={'MiguMusicClient': provider}, requests_overrides={}))
    monkeypatch.setattr(app, 'validatepublicurl', lambda url, label: url)
    monkeypatch.setattr(app, 'AUTH_ENABLED', False)
    result = TestClient(app.app).get('/api/stream/fixture', headers={'Range': 'bytes=1024-'})
    assert result.status_code == 206 and result.content == content


@pytest.mark.parametrize('options,session', [
    ({'headers': {'Authorization': 'Bearer private'}}, None),
    ({'auth': ('user', 'password')}, None),
    ({'verify': True}, None),
    ({'verify': '/custom/ca.pem'}, None),
    ({}, SimpleNamespace(cookies={'session': 'private'}, headers={})),
    ({}, SimpleNamespace(cookies={}, headers={'Authorization': 'Bearer private'})),
])
def test_certificate_compatibility_never_overrides_credentials_or_explicit_trust(monkeypatch, options, session):
    from unittest.mock import Mock
    provider = SimpleNamespace(default_download_headers={}, default_download_cookies={},
                               session=session, get=Mock(return_value=None))
    monkeypatch.setenv('MUSICDL_PREVIEW_STRICT_TLS', '0')
    with pytest.raises(RuntimeError):
        app.preview_request(provider, 'KugouMusicClient', 'https://fs.youthandroid2.kugou.com/audio.flac', options)
    provider.get.assert_called_once()


@pytest.fixture(autouse=True)
def reset_security_state():
    app.SESSIONS.clear()
    app.LOGIN_FAILURES.clear()
    with app.UPDATE_LOCK:
        app.UPDATE_STATE.update(running=False, ok=None, detail='', to=None)
    yield
    app.SESSIONS.clear()
    app.LOGIN_FAILURES.clear()
    with app.UPDATE_LOCK:
        app.UPDATE_STATE.update(running=False, ok=None, detail='', to=None)


def test_unsafe_api_requires_custom_header():
    with TestClient(app.app) as client:
        denied = client.post('/api/auth/login', json={'username': 'x', 'password': 'y'})
        allowed = client.post('/api/auth/login', headers=WRITE_HEADERS,
                              json={'username': 'x', 'password': 'y'})
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_host_does_not_require_deployment_allowlist():
    with TestClient(app.app) as client:
        response = client.get('/api/config', headers={'Host': 'music.example.com'})
    assert response.status_code == 200


def test_unicode_login_uses_random_revocable_session(monkeypatch):
    username, password = '用户', '临时密码-足够长'
    monkeypatch.setattr(app, 'AUTH_ENABLED', True)
    monkeypatch.setattr(app, 'COOKIE_SECURE', False)
    monkeypatch.setattr(app, 'AUTH_USER_DIGEST', hashlib.sha256(username.encode()).digest())
    monkeypatch.setattr(app, 'AUTH_PASS_DIGEST', hashlib.sha256(password.encode()).digest())
    with TestClient(app.app) as client:
        login = client.post('/api/auth/login', headers=WRITE_HEADERS,
                            json={'username': username, 'password': password})
        cookie = next(value for value in login.headers.get_list('set-cookie') if 'Path=/api' in value)
        authenticated = client.get('/api/config')
        logout = client.post('/api/auth/logout', headers=WRITE_HEADERS, json={})
        after_logout = client.get('/api/config')
    assert login.status_code == 200
    assert username not in cookie and password not in cookie
    assert app.SESSION_COOKIE in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert authenticated.status_code == 200
    assert logout.status_code == 200
    assert after_logout.status_code == 401


def test_login_removes_legacy_root_cookie(monkeypatch):
    monkeypatch.setattr(app, 'AUTH_ENABLED', True)
    monkeypatch.setattr(app, 'COOKIE_SECURE', False)
    monkeypatch.setattr(app, 'AUTH_USER_DIGEST', hashlib.sha256(b'admin').digest())
    monkeypatch.setattr(app, 'AUTH_PASS_DIGEST', hashlib.sha256(b'test-password').digest())
    with TestClient(app.app) as client:
        client.cookies.set(app.SESSION_COOKIE, 'stale-session', domain='testserver.local', path='/')
        assert client.get('/api/config').status_code == 401
        response = client.post('/api/auth/login', headers=WRITE_HEADERS,
                               json={'username': 'admin', 'password': 'test-password'})
        assert response.status_code == 200
        assert [(cookie.path, cookie.value) for cookie in client.cookies.jar
                if cookie.name == app.SESSION_COOKIE] == [('/api', next(iter(app.SESSIONS)))]
        assert client.get('/api/config').status_code == 200


def test_runtime_logs_redact_secrets_and_retain_diagnostic_context(monkeypatch):
    entry = app.pushlog('error', 'Download failed: HTTP 403 https://user:pass@audio.example/song?token=private\nCookie: session=private-cookie\nAuthorization: Bearer private-key\nValueError: wrong format',
                        category='download', task_id='fixture-task')
    assert 'HTTP 403' in entry['message'] and 'ValueError: wrong format' in entry['message']
    assert 'audio.example' in entry['message']
    for secret in ('user:pass', 'private', 'session=private-cookie', 'Bearer private-key'):
        assert secret not in entry['message']
    with TestClient(app.app) as client:
        logs = client.get('/api/logs').json()['logs']
    assert entry in logs
    assert entry['id'] > 0 and entry['category'] == 'download' and entry['task_id'] == 'fixture-task'


def test_runtime_logs_have_timezone_and_preserve_worker_event_time():
    from datetime import datetime, timezone
    entry = app.pushlog('info', 'timezone test')
    actual = datetime.fromisoformat(entry['time'])
    assert actual.utcoffset().total_seconds() == 0
    assert abs((datetime.now(timezone.utc) - actual).total_seconds()) < 3
    original_time = '2026-10-14T03:01:50+00:00'
    forwarded = app.pushlog('info', 'worker event', timestamp=original_time)
    assert forwarded['time'] == original_time


def test_workdir_cannot_escape_fixed_root(monkeypatch, tmp_path):
    root = (tmp_path / 'music').resolve()
    root.mkdir()
    monkeypatch.setattr(app, 'FILE_ROOT', root)
    assert app._resolve_allowed_workdir(root / 'artist') == root / 'artist'
    with pytest.raises(ValueError):
        app._resolve_allowed_workdir(tmp_path.parent / 'outside')


def test_config_write_is_atomic_and_private(monkeypatch, tmp_path):
    target = tmp_path / 'data' / 'config.json'
    monkeypatch.setattr(app, 'CONFIG_PATH', target)
    app.saveconfig({'cookies': {'source': 'secret'}})
    assert target.exists()
    assert 'secret' in target.read_text(encoding='utf-8')
    assert not list(target.parent.glob('*.tmp'))
    if os.name != 'nt':
        assert target.stat().st_mode & 0o777 == 0o600


def test_config_concurrency_values_are_bounded():
    cfg = copy.deepcopy(app.manager.config)
    cfg['default_threads'] = 9999
    cfg['request_timeout'] = -1
    cfg['clients_threadings'] = {'DemoMusicClient': 5000}
    normalized = app.normalizeconfigpaths(cfg)
    assert normalized['default_threads'] == 50
    assert normalized['request_timeout'] == 0
    assert normalized['clients_threadings']['DemoMusicClient'] == 50


def test_loopback_playlist_url_is_rejected_before_dependency_call():
    with pytest.raises(HTTPException) as caught:
        app.validatepublicurl('http://127.0.0.1:12345/private', '歌单链接')
    assert caught.value.status_code == 400


def test_playlist_host_must_be_a_known_music_platform():
    with pytest.raises(HTTPException) as caught:
        app.validatepublicurl('https://example.com/redirect', '歌单链接', app.PLAYLIST_ALLOWED_HOSTS)
    assert caught.value.status_code == 400


def test_expired_download_id_is_rejected_synchronously(monkeypatch):
    monkeypatch.setattr(app.manager, 'get', lambda: object())
    with pytest.raises(HTTPException) as caught:
        app.download(app.DownloadBody(ids=['expired-id']))
    assert caught.value.status_code == 404


def test_update_check_and_set_is_mutexed(monkeypatch):
    monkeypatch.setattr(app, 'SELF_UPDATE_ENABLED', True)
    monkeypatch.setattr(app, '_runupdate', lambda: None)
    first = app.musicdlupdate()
    second = app.musicdlupdate()
    assert first == {'started': True}
    assert second == {'started': False, 'running': True}


def test_running_tasks_are_never_trimmed(monkeypatch):
    tasks = {f'task-{idx}': {'status': 'running'} for idx in range(201)}
    order = list(tasks)
    monkeypatch.setattr(app, 'TASKS', tasks)
    monkeypatch.setattr(app, 'TASK_ORDER', order)
    app._trim_finished_tasks_locked(limit=200)
    assert len(app.TASK_ORDER) == 201
    assert set(app.TASK_ORDER) == set(tasks)


def test_song_stable_id_is_deterministic():
    song = SimpleNamespace(source='DemoMusicClient', identifier='42', song_name='歌名', singers='歌手', album='专辑')
    assert app.stablesongid(song) == app.stablesongid(song)


def test_download_body_has_hard_limit():
    with pytest.raises(ValidationError):
        app.DownloadBody(ids=[str(i) for i in range(101)])


def test_validate_reports_only_stale_ids():
    song = SimpleNamespace(source='DemoMusicClient', identifier='7', song_name='歌', singers='手', album='辑')
    sid = app.registersong(song)
    result = app.validatesongs(app.ValidateBody(ids=[sid, 'nope-0001', 'nope-0001']))
    assert result['invalid'] == ['nope-0001']


def test_download_queues_valid_and_reports_expired(monkeypatch):
    song = SimpleNamespace(source='DemoMusicClient', identifier='8', song_name='歌', singers='手', album='辑')
    sid = app.registersong(song)
    monkeypatch.setattr(app.manager, 'get', lambda: object())

    class FakeFuture:
        def add_done_callback(self, cb):
            pass

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            return FakeFuture()

    monkeypatch.setattr(app, 'DOWNLOAD_EXECUTOR', FakeExecutor())
    resp = app.download(app.DownloadBody(ids=[sid, 'expired-0001']))
    assert resp['queued'] == 1
    assert resp['expired'] == ['expired-0001']
    with app.TASK_LOCK:
        app.TASK_ORDER.remove(resp['task_id'])
        app.TASKS.pop(resp['task_id'], None)
        app.ACTIVE_SONG_IDS.discard(sid)


class FakeFuture:
    def add_done_callback(self, cb):
        pass


class FakeExecutor:
    def submit(self, fn, *args, **kwargs):
        return FakeFuture()


def _make_task(task_id, entries):
    return {'id': task_id, 'status': 'partial', 'created_at': '00:00:00',
            'finished_at': '00:00:01', 'songs': entries}


def test_delete_tasks_removes_finished_and_skips_running(monkeypatch):
    finished = _make_task('done-1', [{'_song': object(), 'id': 'a', 'status': 'failed',
                                      'detail': '', 'song_name': 's', 'singers': '', 'source': 'x'}])
    running = _make_task('run-1', [{'_song': object(), 'id': 'b', 'status': 'running',
                                    'detail': '', 'song_name': 's', 'singers': '', 'source': 'x'}])
    running['status'] = 'running'
    monkeypatch.setattr(app, 'TASKS', {'done-1': finished, 'run-1': running})
    monkeypatch.setattr(app, 'TASK_ORDER', ['run-1', 'done-1'])
    result = app.deletetasks(app.TaskIdsBody(ids=['done-1', 'run-1']))
    assert result == {'removed': 1, 'skipped': 1}
    assert app.TASK_ORDER == ['run-1']
    assert 'done-1' not in app.TASKS


def test_retry_requeues_only_failed_songs(monkeypatch):
    song = SimpleNamespace(source='DemoMusicClient', identifier='9', song_name='歌', singers='手', album='辑')
    entries = [
        {'_song': object(), 'id': 'ok', 'status': 'done', 'detail': 'ok.flac',
         'song_name': '成功', 'singers': '', 'source': 'x'},
        {'_song': song, 'id': 'bad', 'status': 'failed', 'detail': '失败',
         'song_name': '失败', 'singers': '', 'source': 'x'},
    ]
    task = _make_task('t-1', entries)
    monkeypatch.setattr(app, 'TASKS', {'t-1': task})
    monkeypatch.setattr(app, 'TASK_ORDER', ['t-1'])
    monkeypatch.setattr(app, 'ACTIVE_SONG_IDS', set())
    monkeypatch.setattr(app, 'DOWNLOAD_EXECUTOR', FakeExecutor())
    result = app.retrytask('t-1')
    assert result == {'task_id': 't-1', 'retrying': 1}
    assert entries[0]['status'] == 'done'          # 成功的不动
    assert entries[1]['status'] == 'pending'        # 失败的重新排队
    assert task['status'] == 'pending'


def test_retry_rejects_when_all_failed_songs_expired(monkeypatch):
    entries = [{'_song': None, 'id': 'gone', 'status': 'failed', 'detail': '过期',
                'song_name': '过期', 'singers': '', 'source': 'x'}]
    task = _make_task('t-2', entries)
    monkeypatch.setattr(app, 'TASKS', {'t-2': task})
    monkeypatch.setattr(app, 'TASK_ORDER', ['t-2'])
    with pytest.raises(HTTPException) as caught:
        app.retrytask('t-2')
    assert caught.value.status_code == 410


def test_config_rolls_back_when_persistence_fails(monkeypatch):
    manager = app.ClientManager()
    old_config = copy.deepcopy(manager.config)
    old_client = object()
    manager.client = old_client
    manager.source_errors = {'previous-source': 'previous error'}
    new_config = copy.deepcopy(old_config)
    new_config['proxy_url'] = 'http://proxy.invalid:1'

    def fake_build():
        manager.client = object()
        manager.source_errors = {'new-source': 'new error'}

    def fail_save(_cfg):
        raise OSError('只读文件系统')

    monkeypatch.setattr(manager, 'build', fake_build)
    monkeypatch.setattr(app, 'saveconfig', fail_save)
    with pytest.raises(OSError):
        manager.applyconfig(new_config)
    assert manager.config == old_config
    assert manager.client is old_client
    assert manager.source_errors == {'previous-source': 'previous error'}


@pytest.mark.parametrize(
    ('username', 'password', 'expected'),
    [('', '', '非回环监听必须配置'), ('admin', '', '必须同时设置')],
)
def test_non_loopback_auth_configuration_fails_closed(username, password, expected):
    env = os.environ.copy()
    for name in ('MUSICDL_USER', 'MUSICDL_PASS', 'MUSICDL_USER_FILE', 'MUSICDL_PASS_FILE',
                 'MUSICDL_ALLOW_INSECURE_NO_AUTH'):
        env.pop(name, None)
    if username:
        env['MUSICDL_USER'] = username
    if password:
        env['MUSICDL_PASS'] = password
    env['MUSICDL_HOST'] = '0.0.0.0'
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    result = subprocess.run(
        [sys.executable, '-c', 'import app'], cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert result.returncode != 0
    assert expected in result.stderr
