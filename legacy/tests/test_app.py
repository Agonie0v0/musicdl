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


def test_untrusted_host_is_rejected():
    with TestClient(app.app) as client:
        response = client.get('/api/config', headers={'Host': 'evil.example'})
    assert response.status_code == 400


def test_unicode_login_uses_random_revocable_session(monkeypatch):
    username, password = '用户', '临时密码-足够长'
    monkeypatch.setattr(app, 'AUTH_ENABLED', True)
    monkeypatch.setattr(app, 'COOKIE_SECURE', False)
    monkeypatch.setattr(app, 'AUTH_USER_DIGEST', hashlib.sha256(username.encode()).digest())
    monkeypatch.setattr(app, 'AUTH_PASS_DIGEST', hashlib.sha256(password.encode()).digest())
    with TestClient(app.app) as client:
        login = client.post('/api/auth/login', headers=WRITE_HEADERS,
                            json={'username': username, 'password': password})
        cookie = login.headers.get('set-cookie', '')
        authenticated = client.get('/api/config')
        logout = client.post('/api/auth/logout', headers=WRITE_HEADERS, json={})
        after_logout = client.get('/api/config')
    assert login.status_code == 200
    assert username not in cookie and password not in cookie
    assert app.SESSION_COOKIE in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert authenticated.status_code == 200
    assert logout.status_code == 200
    assert after_logout.status_code == 401


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
    new_config = copy.deepcopy(old_config)
    new_config['proxy_url'] = 'http://proxy.invalid:1'

    def fake_build():
        manager.client = object()

    def fail_save(_cfg):
        raise OSError('只读文件系统')

    monkeypatch.setattr(manager, 'build', fake_build)
    monkeypatch.setattr(app, 'saveconfig', fail_save)
    with pytest.raises(OSError):
        manager.applyconfig(new_config)
    assert manager.config == old_config
    assert manager.client is old_client


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
