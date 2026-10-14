"""Behavior checks against the installed original engine, not rewritten URLs."""
import copy
import asyncio
import io
import json
import os
import pickle
import sys
from pathlib import Path
import tempfile
import time
import wave
import threading
import pytest
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_root = Path(tempfile.mkdtemp(prefix='musicdl-compat-test-'))
os.environ.update(MUSICDL_USER='', MUSICDL_PASS='', MUSICDL_COOKIE_SECURE='0',
                  MUSICDL_CONFIG=str(_root / 'config.json'), MUSICDL_DATA_DIR=str(_root / 'data'),
                  MUSICDL_FILE_ROOT=str(_root / 'music'), MUSICDL_BIND='127.0.0.1')

from fastapi.testclient import TestClient
from musicdl.modules import SongInfo
import app
import engine_worker
import engine_api
from engine_api import SearchBody

HEADERS = {'X-MusicDL-Request': '1'}


def test_bot_uses_shared_engine_queue_and_signed_link_without_web_login(monkeypatch):
    import asyncio
    import pickle
    import engine_api
    from bot_notifications import DownloadLinks
    bot = app.app.state.bots
    original_popen = engine_api.subprocess.Popen
    def search_process(args, **kwargs):
        if '--search' not in args:
            return original_popen(args, **kwargs)
        Path(args[-1]).write_bytes(pickle.dumps([fixture_song()]))
        class Completed:
            returncode = 0
            def poll(self):
                return 0
        return Completed()
    monkeypatch.setattr(engine_api.subprocess, 'Popen', search_process)
    monkeypatch.setattr(bot, 'sources', ['netease'])
    monkeypatch.setattr(bot, 'tg_users', {'123'})
    monkeypatch.setattr(bot, 'links', DownloadLinks(bot.get, bot.put, bot.links.resolve if bot.links else
                       lambda token: app.original.resolvefile(*json.loads(token)), 'https://music.example.com', 86400))
    message = {'id': 'd' * 64, 'channel': 'tg', 'user': '123', 'text': '/search bot-fixture'}
    async def search():
        await bot.command(message)
        await asyncio.gather(*bot.search_tasks.values())
    asyncio.run(search())
    message.update(id='e' * 64, text='/download dddddddd 1')
    asyncio.run(bot.command(message))
    ident = 'e' * 32
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        job = bot.get('download', ident)
        if job['status'] in {'complete', 'failed'}:
            break
        time.sleep(.2)
    assert job['status'] == 'complete', job
    from local_library import download_stem
    assert Path(json.loads(job['file'])[1]).stem.startswith(download_stem(job['track']['title'], job['track']['artist']))
    assert bot.get('bot_task', ident)['owner'] == 'tg:123'
    # Normal APIs remain session protected; only the signed route is public.
    monkeypatch.setattr(app.original, 'AUTH_ENABLED', True)
    client = TestClient(app.app)
    assert client.get('/api/downloads').status_code == 401
    url = bot.links.create(job)
    from urllib.parse import urlsplit
    link = urlsplit(url)
    response = client.get(link.path + '?' + link.query, headers={'Range': 'bytes=0-43'})
    assert response.status_code == 206
    assert response.content.startswith(b'RIFF')
    assert len(response.content) == 44
    assert client.get(link.path, params={'expires': int(time.time()) + 100, 'signature': '0' * 64}).status_code == 403


def test_import_creates_managed_playlist_with_native_failure_report(monkeypatch):
    client = TestClient(app.app)
    failure = {'id': 1950343972, 'name': '可能', 'ar': [{'name': '程响'}],
               'noCopyrightRcmd': {'typeDesc': '其它版本可播'}}
    def native_playlist(config, url):
        app.original.LoggerHandle.warning(f'Fail to parse track info {failure}')
        return [fixture_song()]
    monkeypatch.setattr(engine_worker, 'playlist', native_playlist)
    def inline_submit(fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as error:
            future.set_exception(error)
        return future
    monkeypatch.setattr(app.app.state.engine_test['search_pool'], 'submit', inline_submit)
    response = client.post('/api/playlist/import', headers=HEADERS,
                           json={'source': 'netease', 'id': '1234', 'name': '通勤歌单'})
    assert response.status_code == 200, response.text
    saved = response.json()['playlist']
    ident = saved['id']
    track = saved['tracks'][0]
    assert saved['name'] == '通勤歌单'
    assert saved['issues'][0]['title'] == '可能'
    assert saved['issues'][0]['artist'] == '程响'
    assert '其它版本可播' in saved['issues'][0]['reason']
    assert '1 首' in saved['detail']
    summaries = client.get('/api/playlists').json()
    summary = next(c for c in summaries if c['id'] == ident)
    assert summary['track_count'] == summary['issue_count'] == 1
    assert 'tracks' not in summary
    assert client.get(f'/api/playlists/{ident}').json()['tracks'] == saved['tracks']
    # Saved collections and complete SongInfo survive outside in-memory results.
    import sqlite3
    with sqlite3.connect(_root / 'data/musicdl.db') as db:
        body = json.loads(db.execute("SELECT body FROM records WHERE kind='collection' AND id=?", (ident,)).fetchone()[0])
        assert body['tracks'][0]['id'] == track['id']
    logs = client.get('/api/logs').json()['logs']
    assert any(l['task_id'] == ident and '跳过曲目「可能」' in l['message'] for l in logs)
    assert not any(l['task_id'] == ident and 'Fail to parse track info' in l['message'] for l in logs)
    assert client.patch(f'/api/playlists/{ident}', headers=HEADERS, json={'name': ' '}).status_code == 400
    assert client.patch(f'/api/playlists/{ident}', headers=HEADERS, json={'name': '新名称'}).json()['name'] == '新名称'
    assert client.delete(f'/api/playlists/{ident}/tracks/{track["id"]}', headers=HEADERS).json()['tracks'] == []
    assert client.get(f'/api/tracks/{track["id"]}/lyrics').status_code == 200
    assert client.delete(f'/api/playlists/{ident}', headers=HEADERS).status_code == 200
    assert client.get(f'/api/playlists/{ident}').status_code == 404


def test_empty_import_is_not_false_success(monkeypatch):
    future = Future()
    future.set_result({'songs': [], 'issues': []})
    monkeypatch.setattr(app.app.state.engine_test['search_pool'], 'submit', lambda *args: future)
    client = TestClient(app.app)
    before = client.get('/api/playlists').json()
    result = client.post('/api/playlist/import', headers=HEADERS, json={'source': 'netease', 'id': '1234'})
    assert result.status_code == 422
    assert '未创建歌单' in result.json()['detail']
    assert client.get('/api/playlists').json() == before


def test_download_record_management_preserves_audio_and_rejects_active_tasks():
    import sqlite3
    client = TestClient(app.app)
    audio = app.original.FILE_ROOT / 'management-test.wav'
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b'keep-completed-audio')
    staging = app.original.FILE_ROOT / '.partial' / 'management-paused'
    staging.mkdir(parents=True, exist_ok=True)
    (staging / 'partial.wav').write_bytes(b'partial')
    records = [
        {'id': 'management-complete', 'status': 'complete', 'track': {'title': 'completed'}},
        {'id': 'management-paused', 'status': 'paused', 'track': {'title': 'paused'}},
        {'id': 'management-active', 'status': 'queued', 'track': {'title': 'active'}},
    ]
    with sqlite3.connect(_root / 'data/musicdl.db') as db:
        for record in records:
            db.execute('INSERT INTO records(kind,id,body) VALUES (?,?,?)', ('download', record['id'], json.dumps(record)))
    assert client.delete('/api/downloads/management-active', headers=HEADERS).status_code == 409
    assert client.delete('/api/downloads/management-complete', headers=HEADERS).status_code == 200
    assert audio.read_bytes() == b'keep-completed-audio'
    assert client.delete('/api/downloads/management-paused', headers=HEADERS).status_code == 200
    assert not staging.exists()
    assert client.delete('/api/downloads/management-paused', headers=HEADERS).status_code == 404
    with sqlite3.connect(_root / 'data/musicdl.db') as db:
        db.execute("DELETE FROM records WHERE kind='download' AND id='management-active'")
    audio.unlink()


def test_library_deletion_checks_paths_and_clears_download_link():
    import sqlite3
    client = TestClient(app.app)
    base = app.original.FILE_ROOT
    base.mkdir(parents=True, exist_ok=True)
    audio = base / 'delete-fixture.wav'
    lyrics = base / 'delete-fixture.lrc'
    audio.write_bytes(b'audio')
    lyrics.write_text('lyrics', encoding='utf-8')
    token = json.dumps([str(base), audio.name], ensure_ascii=False)
    job = {'id': 'management-file', 'status': 'complete', 'track': {'title': 'fixture'}, 'file': token}
    with sqlite3.connect(_root / 'data/musicdl.db') as db:
        db.execute('INSERT INTO records(kind,id,body) VALUES (?,?,?)', ('download', job['id'], json.dumps(job)))
    outside = base.parent / 'must-keep.txt'
    outside.write_text('keep', encoding='utf-8')
    assert client.delete('/api/files', params={'path': json.dumps([str(base), '../must-keep.txt'])}, headers=HEADERS).status_code == 403
    assert client.delete('/api/files', params={'path': json.dumps([str(base.parent), outside.name])}, headers=HEADERS).status_code == 403
    assert client.delete('/api/files', params={'path': json.dumps([str(base), '.partial/job/audio.wav'])}, headers=HEADERS).status_code == 403
    assert client.delete('/api/files', params={'path': token}, headers=HEADERS).status_code == 200
    assert not audio.exists() and lyrics.exists() and outside.exists()
    assert next(j for j in client.get('/api/downloads').json() if j['id'] == job['id'])['file'] is None
    assert client.get('/api/files/content', params={'path': token}).status_code == 404
    assert client.delete('/api/downloads/management-file', headers=HEADERS).status_code == 200
    lyrics.unlink()


def test_local_song_library_plays_and_edits_real_audio_tags():
    from mutagen.wave import WAVE
    from mutagen.id3 import TIT2, TPE1, TALB, TXXX, APIC
    client = TestClient(app.app)
    folder = app.original.FILE_ROOT / 'library-fixture'
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / 'song.wav'
    with wave.open(str(audio), 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\0\0' * 8000)
    native = WAVE(audio)
    native.add_tags()
    for frame in (TIT2(encoding=3, text=['本地歌曲']), TPE1(encoding=3, text=['本地歌手']),
                  TALB(encoding=3, text=['本地专辑']), TXXX(encoding=3, desc='keep', text=['unchanged']),
                  APIC(encoding=3, mime='image/png', type=3, data=b'png-fixture')):
        native.tags.add(frame)
    native.save()
    lyrics = audio.with_suffix('.lrc')
    lyrics.write_text('[00:00.00]本地歌词', encoding='utf-8')
    file = next(f for f in client.get('/api/files').json() if f['name'] == 'song.wav')
    track = file['track']
    assert file['folder'] == 'library-fixture' and file['editable']
    assert (track['title'], track['artist'], track['album']) == ('本地歌曲', '本地歌手', '本地专辑')
    assert track['duration'] == 1 and track['source'] == 'local'
    assert client.post(f'/api/tracks/{track["id"]}/playback', headers=HEADERS).json()['playback']['url'] == track['playback']['url']
    streamed = client.get(track['playback']['url'], headers={'Range': 'bytes=0-19'})
    assert streamed.status_code == 206 and len(streamed.content) == 20
    assert streamed.headers['content-type'] == 'audio/wav'
    assert client.get(track['cover']).content == b'png-fixture'
    assert client.get(f'/api/tracks/{track["id"]}/lyrics').json()['text'] == '[00:00.00]本地歌词'
    client.put('/api/playlist', json=[track['id']], headers=HEADERS)
    client.put(f'/api/favorites/{track["id"]}', headers=HEADERS)
    updated = client.patch('/api/files/tags', params={'path': file['path']}, headers=HEADERS,
                           json={'title': '新标题', 'artist': '新歌手', 'album': '新专辑'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['track']['title'] == '新标题'
    assert client.get('/api/playlist').json()[0]['title'] == '新标题'
    native = WAVE(audio)
    assert native.tags['TXXX:keep'].text == ['unchanged'] and native.tags.getall('APIC')[0].data == b'png-fixture'
    with wave.open(str(audio), 'rb') as reader:
        assert reader.readframes(8000) == b'\0\0' * 8000
    assert not list(folder.glob('.musicdl-tags-*'))
    client.delete('/api/files', params={'path': file['path']}, headers=HEADERS)
    assert not any(t['id'] == track['id'] for t in client.get('/api/playlist').json())
    assert not any(t['id'] == track['id'] for t in client.get('/api/favorites').json())
    lyrics.unlink()


def test_search_playback_does_not_require_or_create_a_download(monkeypatch):
    client = TestClient(app.app)
    song = fixture_song()
    song.identifier, song.song_name = 'cached-playback', 'cached playback'
    track = app.app.state.engine_test['remember'](song)
    before = client.get('/api/downloads').json()
    prepared = client.post(f'/api/tracks/{track["id"]}/playback', headers=HEADERS).json()
    assert prepared['id'] == track['id']
    assert prepared['playback']['url'] == track['playback']['url']
    response = client.get(prepared['playback']['url'])
    assert response.status_code == 200 and response.content == song.downloaded_contents
    assert response.headers['content-type'] == 'audio/wav'
    assert client.get('/api/downloads').json() == before


def test_tag_edit_failure_keeps_file_and_checks_library_boundary():
    client = TestClient(app.app)
    target = app.original.FILE_ROOT / 'invalid-tag-audio.wav'
    target.write_bytes(b'not-audio')
    token = json.dumps([str(app.original.FILE_ROOT), target.name])
    tags = {'title': 'new', 'artist': '', 'album': ''}
    result = client.patch('/api/files/tags', params={'path': token}, headers=HEADERS, json=tags)
    assert result.status_code == 400
    assert target.read_bytes() == b'not-audio'
    assert not list(target.parent.glob('.musicdl-tags-*'))
    outside = json.dumps([str(app.original.FILE_ROOT), '../outside.wav'])
    assert client.patch('/api/files/tags', params={'path': outside}, headers=HEADERS, json=tags).status_code == 403
    assert client.get('/api/files/audio', params={'path': outside}).status_code == 403
    target.unlink()


def test_fully_unresolved_import_retains_missing_song_report(monkeypatch):
    future = Future()
    future.set_result({'songs': [], 'issues': [{'title': '不可用', 'source_id': '42', 'artist': '', 'reason': '资源不可用'}]})
    monkeypatch.setattr(app.app.state.engine_test['search_pool'], 'submit', lambda *args: future)
    client = TestClient(app.app)
    result = client.post('/api/playlist/import', headers=HEADERS, json={'source': 'netease', 'id': '1234'})
    assert result.status_code == 200
    saved = result.json()['playlist']
    assert saved['tracks'] == [] and len(saved['issues']) == 1
    assert client.delete(f'/api/playlists/{saved["id"]}', headers=HEADERS).status_code == 200


def test_registry_and_complete_settings_round_trip():
    client = TestClient(app.app)
    response = client.get('/api/config')
    assert response.status_code == 200
    config = response.json()
    names = {s['id'] for s in config['available_sources']}
    assert len(names) == len(app.original.MusicClientBuilder.REGISTERED_MODULES) == 57
    assert {'GDStudioMusicClient', 'TuneHubMusicClient', 'KugouMusicClient', 'FangpiMusicClient'} <= names
    original = copy.deepcopy(app.original.manager.config)
    try:
        app.original.manager.config['search_rules']['MiguMusicClient'] = {'custom-parser-option': 'keep'}
        app.original.manager.config['init_music_clients_cfg']['MiguMusicClient'] = {'maintain_session': True}
        config['results_per_source'] = 4
        assert client.put('/api/config', json=config, headers=HEADERS).status_code == 200
        restored = client.get('/api/engine/config').json()
        assert restored['search_rules']['MiguMusicClient']['custom-parser-option'] == 'keep'
        assert restored['init_music_clients_cfg']['MiguMusicClient']['maintain_session'] is True
    finally:
        app.original.manager.config = original
        app.original.manager.client = None


def fixture_song():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000)
    return SongInfo(source='MiguMusicClient', root_source='Fixture', identifier='generated-wav',
                    song_name='Generated integration audio', singers='Tests', album='Tests', ext='wav',
                    duration_s=1, downloaded_contents=buffer.getvalue(), download_url_status={'ok': True},
                    default_download_headers={'Referer': 'https://example.org/required'},
                    default_download_cookies={'fixture': 'must-survive'},
                    work_dir=str(_root / 'music' / 'MiguMusicClient'))


def test_all_sources_save_with_missing_parser_isolated_and_proxy_recommendations():
    client = TestClient(app.app)
    manager = app.original.manager
    original, old_errors = copy.deepcopy(manager.config), manager.source_errors.copy()
    try:
        cfg = client.get('/api/engine/config').json()
        cfg['music_sources'] = list(app.original.MusicClientBuilder.REGISTERED_MODULES)
        cfg['init_music_clients_cfg'].pop('XiagebaMusicClient', None)
        cfg['global_client_cfg'].pop('quark_parser_config', None)
        response = client.put('/api/engine/config', json=cfg, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert 'quark_parser_config.cookies' in response.json()['source_errors']['XiagebaMusicClient']
        assert client.get('/api/engine/config').json()['music_sources'] == cfg['music_sources']
        native = manager.get()
        assert 'MiguMusicClient' in native.music_clients and 'KuwoMusicClient' in native.music_clients
        assert 'XiagebaMusicClient' not in native.music_clients
        assert set(native.music_sources) == set(native.music_clients)
        sources = {s['engine_name']: s for s in client.get('/api/config').json()['available_sources']}
        assert sources['XiagebaMusicClient']['init_error']
        assert sources['YouTubeMusicClient']['proxy_recommended']
        assert sources['SpotifyMusicClient']['proxy_recommended']
        assert not sources['KuwoMusicClient']['proxy_recommended']
        assert not sources['NeteaseMusicClient']['proxy_recommended']
        # Native per-source threading/overrides still match after combining clients.
        assert native.clients_threadings['MiguMusicClient'] == cfg['clients_threadings'].get('MiguMusicClient', cfg['default_threads'])
        # An entirely unusable selection is still rejected atomically.
        failed = {**cfg, 'music_sources': ['XiagebaMusicClient']}
        rejected = client.put('/api/engine/config', json=failed, headers=HEADERS)
        assert rejected.status_code == 400
        assert manager.client is native
        assert manager.config['music_sources'] == cfg['music_sources']
        assert manager.source_errors == response.json()['source_errors']
    finally:
        manager.config, manager.client, manager.source_errors = original, None, old_errors


def test_full_settings_save_preserves_all_original_fields_and_timeout_zero():
    client = TestClient(app.app)
    original = copy.deepcopy(app.original.manager.config)
    try:
        cfg = client.get('/api/engine/config').json()
        cfg['request_timeout'] = 0
        cfg['default_threads'] = 12
        cfg['proxy_scope'] = 'selected'
        cfg['proxy_sources'] = ['GDStudioMusicClient']
        cfg['global_client_cfg'].update(search_size_per_page=25, max_retries=5,
                                      strict_limit_search_size_per_page=False,
                                      default_parse_cookies={'preserve': 'yes'})
        cfg['init_music_clients_cfg']['MiguMusicClient'] = {'maintain_session': True}
        cfg['clients_threadings']['MiguMusicClient'] = 7
        cfg['requests_overrides']['MiguMusicClient'] = {'headers': {'X-Custom': 'keep'}}
        cfg['search_rules']['MiguMusicClient'] = {'quality': 'keep'}
        response = client.put('/api/engine/config', json=cfg, headers=HEADERS)
        assert response.status_code == 200, response.text
        restored = client.get('/api/engine/config').json()
        for key in cfg:
            assert restored[key] == cfg[key], key
        cfg['request_timeout'] = 300
        assert client.put('/api/engine/config', json=cfg, headers=HEADERS).status_code == 200
        assert client.get('/api/engine/config').json()['request_timeout'] == 300
        compact = client.get('/api/config').json()
        assert client.put('/api/config', json=compact, headers=HEADERS).status_code == 200
    finally:
        app.original.manager.config = original
        app.original.manager.client = None


def test_search_size_uses_original_metadata_without_network_requests():
    song = fixture_song()
    remember = app.app.state.engine_test['remember']
    assert remember(song)['size_bytes'] == len(song.downloaded_contents)
    song.downloaded_contents = None
    song.file_size_bytes = 31457280
    song.file_size = '30 MB'
    value = remember(song)
    assert value['size_bytes'] == 31457280
    assert value['size_label'] == '30 MB'
    song.file_size_bytes = None
    assert remember(song)['size_bytes'] is None
    song.file_size = None
    assert remember(song)['size_label'] is None


def test_exact_song_info_persists_and_original_downloader_saves_audio():
    client = TestClient(app.app)
    song = fixture_song()
    state = app.app.state.engine_test
    track = state['remember'](song)
    app.original.SONGS.pop(track['id'])
    restored = state['song_for'](track['id'])
    assert restored.default_download_headers == song.default_download_headers
    assert restored.default_download_cookies == song.default_download_cookies
    assert restored.downloaded_contents == song.downloaded_contents
    assert track['qualities'][0]['evidence'] == 'engine'
    invalid = client.post('/api/downloads', headers=HEADERS, json=[{'track_id': track['id'], 'quality_id': 'fake-flac'}])
    assert invalid.status_code == 409
    result = client.post('/api/downloads', headers=HEADERS, json=[{'track_id': track['id'], 'quality_id': track['qualities'][0]['id']}])
    assert result.status_code == 200, result.text
    ident = result.json()[0]['id']
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        job = next(j for j in client.get('/api/downloads').json() if j['id'] == ident)
        if job['status'] in {'complete', 'failed'}:
            break
        time.sleep(.2)
    assert job['status'] == 'complete', job
    response = client.get('/api/files/content', params={'path': job['file']})
    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content)) as audio:
        assert audio.readframes(8000) == b'\0\0' * 8000
    assert job['bytes'] == len(response.content)
    logs = client.get('/api/logs').json()['logs']
    assert any(entry['category'] == 'download' and entry['task_id'] == ident and
               'Start to download music files' in entry['message'] for entry in logs)


def test_original_search_receives_unmodified_parser_arguments(monkeypatch):
    captured = {}
    class Provider:
        search_size_per_source = 5
        search_size_per_page = 5
        strict_limit_search_size_per_page = True
        def search(self, **kwargs):
            captured.update(kwargs)
            return [fixture_song()]
    class Client:
        music_clients = {'MiguMusicClient': Provider()}
        clients_threadings = {'MiguMusicClient': 7}
        requests_overrides = {'MiguMusicClient': {'timeout': 90, 'headers': {'X-Custom': 'yes'}}}
        search_rules = {'MiguMusicClient': {'quality': 'best', 'custom': [1, 2]}}
    monkeypatch.setattr(engine_worker, 'client_for', lambda *args: Client())
    result = engine_worker.search({}, 'MiguMusicClient', 'query')
    assert len(result) == 1
    assert captured == {'keyword': 'query', 'num_threadings': 7,
                        'request_overrides': Client.requests_overrides['MiguMusicClient'],
                        'rule': Client.search_rules['MiguMusicClient']}


def test_episodes_and_non_http_download_results_are_not_blocked():
    client = TestClient(app.app)
    song = fixture_song()
    song.episodes = [copy.deepcopy(song)]
    parent = app.app.state.engine_test['remember'](song)
    children = client.get(f"/api/tracks/{parent['id']}/episodes").json()
    assert len(children) == 1
    song.episodes, song.downloaded_contents = None, None
    song.identifier = 'hls-case'
    song.protocol, song.download_url = 'HLS', 'https://example.org/stream.m3u8'
    result = app.app.state.engine_test['remember'](song)
    assert result['playback']['state'] == 'download_only'
    assert result['qualities']


def test_pause_preserves_bytes_and_new_worker_resumes_original_download():
    received, ranges = {}, []
    audio = io.BytesIO()
    frames = bytes(range(256)) * 1024
    with wave.open(audio, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(frames)
    contents = audio.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            received.update(referer=self.headers.get('Referer'), cookie=self.headers.get('Cookie'))
            offset = int(self.headers['Range'].split('=')[1].split('-')[0]) if self.headers.get('Range') else 0
            ranges.append((offset, self.headers.get('If-Range')))
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(contents)-1}/{len(contents)}')
            self.send_header('Content-Length', str(len(contents) - offset))
            self.send_header('ETag', '"audio-v1"')
            self.end_headers()
            try:
                for index in range(offset, len(contents), 256):
                    self.wfile.write(contents[index:index+256])
                    self.wfile.flush()
                    time.sleep(.001 if offset else .02)
            except (BrokenPipeError, ConnectionError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = TestClient(app.app)
    try:
        song = fixture_song()
        song.identifier = 'cancel-case'
        song.downloaded_contents = None
        song.chunk_size = 256
        song.download_url = f'http://127.0.0.1:{server.server_port}/generated.wav'
        value = app.app.state.engine_test['remember'](song)
        response = client.post('/api/downloads', headers=HEADERS, json=[{'track_id':value['id'], 'quality_id':value['qualities'][0]['id']}])
        assert response.status_code == 200
        ident = response.json()[0]['id']
        for _ in range(100):
            job = next(j for j in client.get('/api/downloads').json() if j['id']==ident)
            if job['bytes'] > 0:
                break
            time.sleep(.1)
        assert job['bytes'] > 0
        assert received == {'referer':'https://example.org/required','cookie':'fixture=must-survive'}
        result = client.post(f'/api/downloads/{ident}/pause', headers=HEADERS)
        assert result.status_code == 200
        assert result.json()['status'] == 'paused'
        staging = app.original.FILE_ROOT / '.partial' / ident
        part = next(staging.rglob('*.part'))
        saved_size = part.stat().st_size
        assert saved_size > 0
        assert client.get('/api/files').json() == [] or all('.partial' not in f['path'] for f in client.get('/api/files').json())
        assert client.post(f'/api/downloads/{ident}/resume', headers=HEADERS).status_code == 200
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = next(j for j in client.get('/api/downloads').json() if j['id'] == ident)
            if job['status'] in {'complete', 'failed'}:
                break
            time.sleep(.1)
        assert job['status'] == 'complete', job
        assert (saved_size, '"audio-v1"') in ranges
        downloaded = client.get('/api/files/content', params={'path': job['file']})
        with wave.open(io.BytesIO(downloaded.content)) as reader:
            assert reader.readframes(len(frames)//2) == frames
        assert not staging.exists()

        # A cancelled paused job must release its retained raw file.
        response = client.post('/api/downloads', headers=HEADERS, json=[{'track_id':value['id'], 'quality_id':value['qualities'][0]['id']}])
        ident = response.json()[0]['id']
        staging = app.original.FILE_ROOT / '.partial' / ident
        for _ in range(100):
            if any(p.stat().st_size > 0 for p in staging.rglob('*.part')):
                break
            time.sleep(.1)
        assert client.post(f'/api/downloads/{ident}/pause', headers=HEADERS).status_code == 200
        assert staging.exists()
        assert client.post(f'/api/downloads/{ident}/cancel', headers=HEADERS).status_code == 200
        assert not staging.exists()
    finally:
        server.shutdown()


def endpoint(path, method):
    return next(r.endpoint for r in app.app.routes
                if getattr(r, 'path', '') == path and method in getattr(r, 'methods', set()))


def test_cancel_before_start_does_not_exhaust_job_limit():
    async def check():
        start = endpoint('/api/search/jobs', 'POST')
        stop = endpoint('/api/search/jobs/{ident}', 'DELETE')
        for i in range(12):
            job = await start(SearchBody(keyword=f'cancel-{i}', sources=['netease']))
            await stop(job['id'])
    asyncio.run(check())


def test_browser_playback_errors_are_correlated_redacted_and_deduplicated(monkeypatch):
    client = TestClient(app.app)
    song = fixture_song()
    song.identifier = 'browser-error-fixture'
    track = app.app.state.engine_test['remember'](song)
    report = {'track_id': track['id'], 'event': 'failed', 'stage': 'media', 'code': 4,
              'network_state': 3, 'ready_state': 0, 'position': 1.5, 'browser': 'Test Chromium',
              'detail': '音频格式不支持 HTTP 403 https://audio.example/song?signature=private-url\nCookie: private-cookie'}
    assert client.post('/api/playback/events', json=report).status_code == 403
    assert client.post('/api/playback/events', headers=HEADERS, json=report).json()['recorded'] is True
    assert client.post('/api/playback/events', headers=HEADERS, json=report).json()['recorded'] is False
    entries = [entry for entry in client.get('/api/logs').json()['logs'] if entry['task_id'] == track['id']]
    assert len(entries) == 1
    assert entries[0]['level'] == 'error' and entries[0]['category'] == 'playback'
    assert song.song_name in entries[0]['message'] and 'MediaError=4' in entries[0]['message']
    assert 'HTTP 403' in entries[0]['message'] and 'Test Chromium' in entries[0]['message']
    assert 'private-url' not in entries[0]['message'] and 'private-cookie' not in entries[0]['message']
    monkeypatch.setattr(app.original, 'AUTH_ENABLED', True)
    assert client.get('/api/bots/config').status_code == 401
    assert client.put('/api/bots/config', headers=HEADERS, json={'tg_enabled': False}).status_code == 401
    assert client.post('/api/playback/events', headers=HEADERS, json=report).status_code == 401


def test_cancel_blocked_workers_releases_slots_and_retry_is_fresh(monkeypatch, tmp_path):
    original_popen = engine_api.subprocess.Popen
    processes = []
    song_path = tmp_path / 'songs.pickle'
    song_path.write_bytes(pickle.dumps([fixture_song()]))
    # Exercise real child-process termination and IPC without external APIs.
    script = (
        'import pickle,sys,time; from pathlib import Path; '
        'keyword=pickle.loads(Path(sys.argv[1]).read_bytes())[2]; '
        'time.sleep(60 if keyword.startswith("blocked") else 0); '
        'Path(sys.argv[2]).write_bytes(Path(sys.argv[3]).read_bytes())'
    )
    def launch(args, **kwargs):
        if '--search' not in args:
            return original_popen(args, **kwargs)
        process = original_popen([sys.executable, '-c', script, args[-2], args[-1], str(song_path)], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(engine_api.subprocess, 'Popen', launch)

    async def check():
        search = app.app.state.engine_test['source_results']
        cfg = app.original.manager.config
        tasks = [asyncio.create_task(search(cfg, 'NeteaseMusicClient', f'blocked-{i}')) for i in range(6)]
        try:
            for _ in range(100):
                if len(processes) == 6:
                    break
                await asyncio.sleep(.01)
            assert len(processes) == 6
            fresh = asyncio.create_task(search(cfg, 'NeteaseMusicClient', 'retry'))
            for task in tasks:
                task.cancel()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
            assert all(p.poll() is not None for p in processes[:6])
            result = await asyncio.wait_for(fresh, 10)
            assert result[0].song_name == fixture_song().song_name
            # Neither empty nor successful results may suppress a user's retry.
            song_path.write_bytes(pickle.dumps([]))
            assert await search(cfg, 'NeteaseMusicClient', 'retry') == []
            song_path.write_bytes(pickle.dumps([fixture_song()]))
            assert len(await search(cfg, 'NeteaseMusicClient', 'retry')) == 1
            assert len(processes) == 9
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    asyncio.run(check())


def native_quality_provider(name):
    cls = app.original.MusicClientBuilder.REGISTERED_MODULES[name]
    provider = object.__new__(cls)
    provider.default_cookies = {}
    return provider


@pytest.mark.parametrize('empty_primary', [False, True])
@pytest.mark.parametrize('record_count', [1, 5])
def test_qq_empty_search_retries_signed_native_endpoint_without_losing_song(empty_primary, record_count):
    from unittest.mock import Mock
    import requests
    from musicdl.modules.sources.qq import QQMusicClientUtils
    provider = native_quality_provider('QQMusicClient')
    provider.default_search_headers = {'Referer': 'https://y.qq.com/'}
    provider._initsession = Mock()
    provider.use_encrypted_endpoint = False
    provider.strict_limit_search_size_per_page = True
    provider.search_size_per_page = 5
    provider.logger_handle = Mock()
    provider.disable_print = True
    song = quality_song('flac', 26012257, 'QQMusicClient')
    song.song_name, song.singers = '稻香', '周杰伦'
    parsed = [copy.deepcopy(song) for _ in range(record_count)]
    for index, item in enumerate(parsed):
        item.identifier = f'song-mid-{index}'
    provider._parsewiththirdpartapis = Mock(side_effect=parsed)
    provider._parsewithofficialapiv1 = Mock(side_effect=parsed)
    payload = {'music.search.SearchCgiService.DoSearchForQQMusicMobile': {'param': {'query': '稻香 周杰伦', 'page_num': 2, 'num_per_page': 5}}}
    records = [{'mid': item.identifier, 'title': '稻香', 'singer': [{'name': '周杰伦'}]} for item in parsed]
    def response(records):
        value = requests.Response()
        value.status_code = 200
        value._content = json.dumps({'code': 0, 'music.search.SearchCgiService.DoSearchForQQMusicMobile':
                                    {'code': 0, 'data': {'body': {'item_song': records}}}}).encode()
        return value
    provider.post = Mock(side_effect=[response([] if empty_primary else records), response(records)])
    overrides = {'timeout': 17, 'proxies': {'https': 'http://proxy.example:7890'}, 'headers': {'X-Required': 'keep'},
                 'params': {'custom': 'keep'}}
    request = {'url': QQMusicClientUtils.endpoint, 'data': json.dumps(payload).encode(), 'page_no': 2}
    songs = []
    provider._search('稻香 周杰伦', request, overrides, songs, Mock())
    assert songs == parsed and len(songs) == record_count
    assert all(item.song_name == '稻香' and item.singers == '周杰伦' for item in songs)
    assert provider.post.call_count == (2 if empty_primary else 1)
    if empty_primary:
        assert provider.post.call_args.args[0] == QQMusicClientUtils.enc_endpoint
        options = provider.post.call_args.kwargs
        assert options['data'] == request['data'] and json.loads(options['data']) == payload
        assert options['params'] == {'custom': 'keep', 'sign': QQMusicClientUtils.sign(payload)}
        assert all(options[k] == overrides[k] for k in ('timeout', 'proxies', 'headers'))
    assert not provider.use_encrypted_endpoint and overrides['params'] == {'custom': 'keep'}


def test_native_empty_search_error_is_reported_as_failure(monkeypatch, tmp_path):
    def fail(*args):
        app.original.pushlog('error', 'QQMusicClient._search >>> 稻香 on page 1 (Error: rejected)')
        return []
    monkeypatch.setattr(engine_worker, 'search', fail)
    with pytest.raises(RuntimeError, match='搜索请求失败'):
        engine_worker.search_logged({}, 'QQMusicClient', '稻香', str(tmp_path / 'search.log'), 'diagnostic')


def quality_song(ext, size, source='KugouMusicClient'):
    return SongInfo(source=source, identifier='same-platform-id', song_name='同一歌曲', singers='歌手',
                    ext=ext, file_size_bytes=size, duration_s=200,
                    download_url='https://example.org/audio.' + ext,
                    download_url_status={'ok': True})


@pytest.mark.parametrize('source', ['KugouMusicClient', 'KuwoMusicClient', 'QQMusicClient', 'NeteaseMusicClient'])
def test_native_fallback_continues_from_mp3_to_lossless_and_retains_mp3_on_failure(monkeypatch, source):
    provider = native_quality_provider(source)
    assert app.original.PATCH_STATUS['audio_compat_' + source] == 'ok'
    calls = []
    lossy, lossless = quality_song('mp3', 8000000, source), quality_song('flac', 88706509, source)
    outcome = [lossy, RuntimeError('parser unavailable'), lossless]
    def parse(result, overrides):
        calls.append(overrides)
        value = outcome.pop(0) if outcome else RuntimeError('unavailable')
        if isinstance(value, Exception):
            raise value
        return value
    for name in vars(type(provider)):
        if name.startswith('_parsewith') and name.endswith('api'):
            monkeypatch.setattr(provider, name, parse)
    assert provider._parsewiththirdpartapis({}, {'timeout': 30}) is lossless
    assert len(calls) == 3
    # Keeping an MP3 fallback is necessary when no lossless interface succeeds.
    calls.clear()
    outcome[:] = [lossy, RuntimeError('unavailable')]
    assert provider._parsewiththirdpartapis({}, {'timeout': 30}) is lossy
    # Account-bound native behavior must still take precedence.
    if source != 'NeteaseMusicClient':
        calls.clear()
        provider.default_cookies = {'account': 'configured'}
        assert not provider._parsewiththirdpartapis({}, {}).with_valid_download_url
        assert not calls


@pytest.mark.parametrize('source,method', [
    ('KugouMusicClient', '_parsewithlzmhhhapi'), ('KuwoMusicClient', '_parsewithxcloudvapi'),
    ('QQMusicClient', '_parsewithlzmhhhapi'), ('NeteaseMusicClient', '_parsewithlzmhhhapi')])
def test_native_parser_request_overrides_reach_http_without_keyword_collision(monkeypatch, source, method):
    import requests
    provider = native_quality_provider(source)
    captured = []
    class ReachedHTTP(Exception):
        pass
    def request(url, **kwargs):
        captured.append(kwargs)
        raise ReachedHTTP('isolated transport')
    monkeypatch.setattr(requests, 'post', request)
    overrides = {'timeout': 30, 'headers': {'X-Test': 'override'}, 'verify': True,
                 'proxies': {'https': 'http://localhost:7890'}}
    result = {'hash': 'test', 'mid': 'test', 'id': 'test', 'duration': 200,
              'albumname': 'album', 'album_name': 'album', 'rid': 'test'}
    with pytest.raises(ReachedHTTP):
        getattr(provider, method)(result, overrides)
    assert len(captured) == 1
    for key, value in overrides.items():
        assert captured[0][key] == value
    assert overrides['headers'] == {'X-Test': 'override'}


@pytest.mark.parametrize('source', ['KugouMusicClient', 'KuwoMusicClient', 'QQMusicClient', 'NeteaseMusicClient'])
def test_quality_dedup_retains_real_versions_with_same_platform_id(source):
    provider = native_quality_provider(source)
    mp3 = quality_song('mp3', 8000000, source)
    cd = quality_song('flac', 25000000, source)
    hires = quality_song('flac', 88706509, source)
    assert provider._removeduplicates([mp3, cd, hires, copy.deepcopy(hires)]) == [mp3, cd, hires]


def test_reconstructed_result_ids_do_not_merge_different_flac_sizes():
    remember, song_for = (app.app.state.engine_test[key] for key in ('remember', 'song_for'))
    cd, hires = quality_song('flac', 25000000), quality_song('flac', 88706509)
    first, second = remember(cd), remember(hires)
    assert first['id'] != second['id']
    assert first['qualities'][0]['lossless'] and second['qualities'][0]['lossless']
    assert song_for(first['id']).file_size_bytes == 25000000
    assert song_for(second['id']).file_size_bytes == 88706509


def test_parser_failures_and_audio_sizes_are_forwarded_to_search_logs(monkeypatch, tmp_path):
    import requests
    provider = native_quality_provider('KugouMusicClient')
    def fail(url, **kwargs):
        raise requests.ConnectionError('https://example.org/private?token=secret')
    monkeypatch.setattr(requests, 'post', fail)
    path = tmp_path / 'search.log'
    with engine_worker.capture_logs(path, 'search', 'quality-diagnostic'):
        with pytest.raises(requests.ConnectionError):
            provider._parsewithlzmhhhapi({'hash': 'test', 'duration': 200}, {'timeout': 30})
    entries = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert any('_parsewithlzmhhhapi' in entry['message'] and 'ConnectionError' in entry['message']
               and entry['task_id'] == 'quality-diagnostic' for entry in entries)
    assert 'token=secret' not in path.read_text(encoding='utf-8')


def test_failed_primary_recovers_flac_through_real_native_backup_parser(monkeypatch, tmp_path):
    import requests
    from types import SimpleNamespace
    provider = native_quality_provider('KugouMusicClient')
    def unavailable(*args, **kwargs):
        raise requests.ConnectionError('primary unavailable')
    # Only the native lzmhhh parser remains live; its HTTP and audio probe are mocked.
    for name in vars(type(provider)):
        if name.startswith('_parsewith') and name.endswith('api') and name != '_parsewithlzmhhhapi':
            monkeypatch.setattr(provider, name, unavailable)
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'data': 'https://example.org/test.flac'}).encode()
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: response)
    provider.audio_link_tester = SimpleNamespace(test=lambda **kwargs: {
        'ok': True, 'ext': 'flac', 'file_size_bytes': 88706509, 'file_size': '84.6 MB',
        'download_url': 'https://example.org/test.flac'})
    log = tmp_path / 'recovery.log'
    with engine_worker.capture_logs(log, 'search', 'recover-flac'):
        result = provider._parsewiththirdpartapis({'hash': 'test', 'duration': 200,
                                                 'songname': '稻香', 'singername': '周杰伦'},
                                                {'timeout': 30})
    assert result.ext == 'flac' and result.file_size_bytes == 88706509
    entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    assert any('88706509' in entry['message'] and 'flac' in entry['message']
               and entry['task_id'] == 'recover-flac' for entry in entries)


def test_worker_streams_native_appends_without_losing_final_results(monkeypatch, tmp_path):
    import struct
    from types import SimpleNamespace
    class Provider:
        search_size_per_source = search_size_per_page = 5
        strict_limit_search_size_per_page = True
        work_dir = str(tmp_path / 'music')
        def _search(self, keyword, url, overrides, songs, progress):
            songs.append(quality_song('flac', 88706509))
            return songs
        def search(self, keyword, **kwargs):
            songs = []
            self._search(keyword, '', {}, songs, None)
            # Native search sets directories after all pages return.
            for song in songs:
                song.work_dir = self.work_dir + '/native-finished'
            return songs
    provider = Provider()
    native = provider._search
    monkeypatch.setattr(engine_worker, 'client_for', lambda *args: SimpleNamespace(
        music_clients={'KugouMusicClient': provider}, clients_threadings={'KugouMusicClient': 1},
        requests_overrides={'KugouMusicClient': {}}, search_rules={'KugouMusicClient': {}}))
    path = tmp_path / 'partial'
    songs = engine_worker.search({}, 'KugouMusicClient', 'test', str(path))
    data = path.read_bytes()
    size = struct.unpack('!Q', data[:8])[0]
    partial = pickle.loads(data[8:8 + size])
    assert len(songs) == 1 and partial.file_size_bytes == songs[0].file_size_bytes == 88706509
    assert partial.work_dir == provider.work_dir
    assert songs[0].work_dir.endswith('/native-finished')
    assert provider._search == native


def test_resolved_tracks_survive_cancellation_of_slow_native_source(monkeypatch, tmp_path):
    original_popen = engine_api.subprocess.Popen
    audio = tmp_path / 'resolved.song'
    audio.write_bytes(pickle.dumps(quality_song('flac', 88706509)))
    # Publish a fragmented IPC frame, then block on a later unresolved track.
    script = (
        'import pickle,struct,sys,time; from pathlib import Path; '
        'args=pickle.loads(Path(sys.argv[1]).read_bytes()); '
        'data=Path(sys.argv[2]).read_bytes(); '
        'f=open(args[5],"ab"); f.write(struct.pack("!Q",len(data))[:4]); f.flush(); '
        'time.sleep(.15); f.write(struct.pack("!Q",len(data))[4:]+data); f.flush(); '
        'time.sleep(60)'
    )
    children = []
    def launch(args, **kwargs):
        if '--search' not in args:
            return original_popen(args, **kwargs)
        child = original_popen([sys.executable, '-c', script, args[-2], str(audio)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine_api.subprocess, 'Popen', launch)
    received = []
    async def check():
        search = app.app.state.engine_test['source_results']
        task = asyncio.create_task(search(app.original.manager.config, 'KugouMusicClient', 'slow-source', received.append))
        try:
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(.02)
            assert len(received) == 1 and received[0].file_size_bytes == 88706509
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            terminated = all(child.poll() is not None for child in children)
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()
        assert terminated
        assert not list((_root / 'data' / 'engine').glob('*.search.partial'))
    asyncio.run(check())


@pytest.mark.parametrize('finish', [True, False])
def test_bot_waits_for_progressing_source_and_stops_when_idle(monkeypatch, tmp_path, finish):
    original_popen = engine_api.subprocess.Popen
    monkeypatch.setattr(engine_api, 'BOT_SEARCH_IDLE_TIMEOUT', 1)
    monkeypatch.setitem(app.original.manager.config, 'music_sources', ['QQMusicClient'])
    songs = [quality_song('flac', 26012257, 'QQMusicClient') for _ in range(5)]
    for index, song in enumerate(songs):
        song.identifier = f'idle-test-{index}'
        (tmp_path / str(index)).write_bytes(pickle.dumps(song))
    (tmp_path / 'complete').write_bytes(pickle.dumps(songs))
    script = '''
import pickle, struct, sys, time
from pathlib import Path
args = pickle.loads(Path(sys.argv[1]).read_bytes())
root = Path(sys.argv[3])
with open(args[5], 'ab') as handle:
    for index in range(5):
        time.sleep(.35)
        data = (root / str(index)).read_bytes()
        handle.write(struct.pack('!Q', len(data)) + data)
        handle.flush()
if sys.argv[4] == 'True':
    Path(sys.argv[2]).write_bytes((root / 'complete').read_bytes())
else:
    time.sleep(60)
'''
    children = []
    def launch(args, **kwargs):
        if '--search' not in args:
            return original_popen(args, **kwargs)
        child = original_popen([sys.executable, '-c', script, args[-2], args[-1], str(tmp_path), str(finish)], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine_api.subprocess, 'Popen', launch)
    async def check():
        tracks, partial = await asyncio.wait_for(app.app.state.bots.search('idle-test', ['qq']), 10)
        assert len(tracks) == 5
        assert all(track['size_bytes'] == 26012257 for track in tracks)
        assert partial is not finish
        assert all(child.poll() is not None for child in children)
    asyncio.run(check())


def test_domain_changes_keep_api_auth_and_request_guard(monkeypatch):
    client = TestClient(app.app)
    monkeypatch.setattr(app.original, 'AUTH_ENABLED', True)
    for host in ['music.example.com', '192.168.23.45:8765', '[::1]:8765']:
        assert client.get('/api/health', headers={'Host': host}).status_code == 200
        assert client.get('/api/engine/config', headers={'Host': host}).status_code == 401
        assert client.put('/api/engine/config', headers={'Host': host}, json={}).status_code == 403


def test_invalid_project_proxy_does_not_change_config():
    client = TestClient(app.app)
    original = client.get('/api/engine/config').json()
    for proxy in ['not-a-url', 'socks5://localhost:1080', 'http://']:
        response = client.put('/api/engine/config', headers=HEADERS, json={**original, 'proxy_url': proxy})
        assert response.status_code == 400
        assert client.get('/api/engine/config').json() == original


def test_project_proxy_source_scope_and_address_changes():
    manager = app.original.ClientManager()
    manager.config.update(music_sources=['MiguMusicClient'], proxy_url='http://proxy.example:7890',
                          proxy_scope='selected', proxy_sources=['MiguMusicClient'], requests_overrides={})
    for scope, address, expected in [('selected', 'http://proxy.example:7890', True),
                                     ('selected', 'http://changed.example:8080', True),
                                     ('none', 'http://changed.example:8080', False),
                                     ('all', '', False)]:
        manager.config.update(proxy_scope=scope, proxy_url=address)
        manager.build()
        overrides = manager.client.requests_overrides['MiguMusicClient']
        assert overrides.get('proxies') == ({'http': address, 'https': address} if expected else None)
