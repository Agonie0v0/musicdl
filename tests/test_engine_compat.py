"""Behavior checks against the installed original engine, not rewritten URLs."""
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
import wave
import threading
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_root = Path(tempfile.mkdtemp(prefix='musicdl-compat-test-'))
os.environ.update(MUSICDL_USER='', MUSICDL_PASS='', MUSICDL_COOKIE_SECURE='0',
                  MUSICDL_TRUSTED_HOSTS='testserver,localhost,127.0.0.1',
                  MUSICDL_CONFIG=str(_root / 'config.json'), MUSICDL_DATA_DIR=str(_root / 'data'),
                  MUSICDL_FILE_ROOT=str(_root / 'music'), MUSICDL_BIND='127.0.0.1')

from fastapi.testclient import TestClient
from musicdl.modules import SongInfo
import app
import engine_worker

HEADERS = {'X-MusicDL-Request': '1'}


def test_bot_uses_shared_engine_queue_and_signed_link_without_web_login(monkeypatch):
    import asyncio
    from bot_notifications import DownloadLinks
    bot = app.app.state.bots
    state = app.app.state.engine_test
    def inline_submit(fn, *args):
        future = Future()
        future.set_result([fixture_song()])
        return future
    monkeypatch.setattr(state['search_pool'], 'submit', inline_submit)
    monkeypatch.setattr(bot, 'sources', ['netease'])
    monkeypatch.setattr(bot, 'tg_users', {'123'})
    monkeypatch.setattr(bot, 'links', DownloadLinks(bot.get, bot.put, bot.links.resolve if bot.links else
                       lambda token: app.original.resolvefile(*json.loads(token)), 'https://music.example.com', 86400))
    message = {'id': 'd' * 64, 'channel': 'tg', 'user': '123', 'text': '/search bot-fixture'}
    asyncio.run(bot.command(message))
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
