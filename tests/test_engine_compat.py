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


def test_pause_stops_original_download_worker_and_cleans_partial_file():
    received = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            received.update(referer=self.headers.get('Referer'), cookie=self.headers.get('Cookie'))
            self.send_response(200)
            self.send_header('Content-Length', str(1024 * 1024))
            self.end_headers()
            try:
                for _ in range(4096):
                    self.wfile.write(b'\0' * 256)
                    self.wfile.flush()
                    time.sleep(.02)
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
        assert not (app.original.FILE_ROOT / '.partial' / ident).exists()
    finally:
        server.shutdown()
