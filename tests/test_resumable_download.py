"""Byte integrity across interrupted responses and Range edge cases."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from resumable_download import transfer


class Response:
    def __init__(self, status, headers, contents, interrupted=False):
        self.status_code, self.headers, self.contents = status, headers, contents
        self.interrupted, self.closed = interrupted, False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('http error')

    def iter_content(self, chunk_size):
        for pos in range(0, len(self.contents), chunk_size):
            yield self.contents[pos:pos+chunk_size]
        if self.interrupted:
            raise ConnectionError('connection lost')

    def close(self):
        self.closed = True


class Provider:
    default_headers = {'User-Agent': 'original-client'}

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def get(self, url, **options):
        self.requests.append((url, options))
        return self.responses.pop(0)


class Progress:
    def update(self, *args, **kwargs):
        self.state = kwargs


def fixture(tmp_path):
    song = SimpleNamespace(save_path=str(tmp_path / 'audio.flac'), download_url='https://audio.example/song',
                           default_download_headers={'Referer': 'provider'}, default_download_cookies={'key': 'original'}, chunk_size=4)
    return song, Path(song.save_path + '.part'), Path(song.save_path + '.part.json')


def checkpoint(song, part, meta, contents=b'first', total=11):
    part.write_bytes(contents)
    meta.write_text(json.dumps({'url': hashlib.sha256(song.download_url.encode()).hexdigest(),
                               'validator': '"v1"', 'validator_header': 'ETag', 'total': total, 'complete': False}))


def test_interruption_then_resume_keeps_exact_bytes_and_original_requests(tmp_path):
    song, part, meta = fixture(tmp_path)
    broken = Response(200, {'Content-Length': '11', 'ETag': '"v1"'}, b'first', True)
    provider, progress = Provider(broken), Progress()
    with pytest.raises(ConnectionError):
        transfer(provider, song, {'proxies': {'https': 'original-proxy'}}, progress, 0)
    assert broken.closed and part.read_bytes() == b'first'
    assert not Path(song.save_path).exists()
    response = Response(206, {'Content-Length': '6', 'Content-Range': 'bytes 5-10/11', 'ETag': '"v1"'}, b'second')
    resumed = Provider(response)
    transfer(resumed, song, {'proxies': {'https': 'original-proxy'}}, progress, 0)
    assert Path(song.save_path).read_bytes() == b'firstsecond'
    options = resumed.requests[0][1]
    assert options['headers'] == {'Referer': 'provider', 'Accept-Encoding': 'identity', 'Range': 'bytes=5-', 'If-Range': '"v1"'}
    assert options['cookies'] == {'key': 'original'}
    assert options['proxies'] == {'https': 'original-proxy'}
    assert json.loads(meta.read_text())['complete']
    # Postprocessing recovery doesn't request or duplicate the raw body.
    transfer(Provider(), song, {}, progress, 0)
    assert Path(song.save_path).read_bytes() == b'firstsecond'


@pytest.mark.parametrize('etag', ['"v1"', '"v2"'])
def test_ignored_range_or_changed_resource_restarts_without_appending(tmp_path, etag):
    song, part, meta = fixture(tmp_path)
    checkpoint(song, part, meta)
    transfer(Provider(Response(200, {'Content-Length': '9', 'ETag': etag}, b'new-audio')), song, {}, Progress(), 0)
    assert Path(song.save_path).read_bytes() == b'new-audio'
    assert part.read_bytes() == b'new-audio'


@pytest.mark.parametrize('headers', [
    {'Content-Range': 'bytes 0-5/11', 'ETag': '"v1"'},
    {'Content-Range': 'bytes 5-10/11', 'ETag': '"changed"'},
    {'Content-Range': 'bytes 5-10/100', 'ETag': '"v1"'},
])
def test_bad_range_never_corrupts_or_publishes_partial_audio(tmp_path, headers):
    song, part, meta = fixture(tmp_path)
    checkpoint(song, part, meta)
    response = Response(206, headers, b'second')
    with pytest.raises(RuntimeError):
        transfer(Provider(response), song, {}, Progress(), 0)
    assert part.read_bytes() == b'first'
    assert not Path(song.save_path).exists()
    assert response.closed


def test_416_at_exact_end_recovers_only_matching_representation(tmp_path):
    song, part, meta = fixture(tmp_path)
    checkpoint(song, part, meta, b'complete', 8)
    transfer(Provider(Response(416, {'Content-Range': 'bytes */8', 'ETag': '"v1"'}, b'')), song, {}, Progress(), 0)
    assert Path(song.save_path).read_bytes() == b'complete'
    assert json.loads(meta.read_text())['complete']


def test_truncated_response_retains_progress_without_publishing(tmp_path):
    song, part, meta = fixture(tmp_path)
    with pytest.raises(RuntimeError):
        transfer(Provider(Response(200, {'Content-Length': '100', 'ETag': '"v1"'}, b'short')), song, {}, Progress(), 0)
    assert part.read_bytes() == b'short'
    assert not Path(song.save_path).exists()


def test_compressed_provider_still_downloads_with_original_decoded_stream(tmp_path):
    song, part, meta = fixture(tmp_path)
    checkpoint(song, part, meta)
    provider = Provider(
        Response(206, {'Content-Encoding': 'gzip'}, b''),
        Response(200, {'Content-Encoding': 'gzip', 'Content-Length': '40', 'ETag': '"v1"'}, b'decoded-audio'),
    )
    transfer(provider, song, {}, Progress(), 0)
    assert Path(song.save_path).read_bytes() == b'decoded-audio'
    assert 'Range' not in provider.requests[-1][1]['headers']
    assert not json.loads(meta.read_text())['validator']


def test_adapter_preserves_source_overrides_and_native_non_http_paths(monkeypatch):
    from musicdl.modules import MusicClientBuilder
    from musicdl.modules.sources.base import BaseMusicClient
    from resumable_download import install
    overrides = {name: cls._download for name, cls in MusicClientBuilder.REGISTERED_MODULES.items()
                 if '_download' in cls.__dict__}
    called = []
    def native(*args, **kwargs):
        called.append(args[1])
        return ['native-result']
    monkeypatch.setattr(BaseMusicClient, '_download', native)
    install()
    wrapper = BaseMusicClient._download
    install()
    assert BaseMusicClient._download is wrapper
    for name, method in overrides.items():
        assert MusicClientBuilder.REGISTERED_MODULES[name]._download is method
    provider = SimpleNamespace(default_download_headers={})
    for song in [SimpleNamespace(protocol='HLS'), SimpleNamespace(protocol='HTTP', downloaded_contents=b'audio')]:
        assert wrapper(provider, song) == ['native-result']
    assert len(called) == 2
