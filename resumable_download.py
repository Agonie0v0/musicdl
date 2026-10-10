"""Range transport for the original engine's ordinary HTTP downloads.

Provider parsing, request clients, headers/cookies and postprocessing stay in
musicdl. Custom download methods, HLS and predownloaded bytes are untouched.
"""
import copy
import hashlib
import json
import os
import re
import shutil
import traceback
from pathlib import Path


def transfer(provider, song, overrides, progress, task_id):
    """Retain raw bytes until the original engine finishes tagging the audio."""
    target = Path(song.save_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.part')
    checkpoint = target.with_name(target.name + '.part.json')
    fingerprint = hashlib.sha256(song.download_url.encode()).hexdigest()
    try:
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    offset = partial.stat().st_size if partial.exists() else 0
    if saved.get('url') != fingerprint or not saved.get('validator'):
        offset = 0

    def save(metadata):
        temporary = checkpoint.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata), encoding='utf-8')
        os.replace(temporary, checkpoint)

    # A crash during tagging can safely repeat postprocessing from raw bytes.
    if saved.get('url') == fingerprint and saved.get('complete') and partial.exists() and partial.stat().st_size == saved.get('total'):
        progress.update(task_id, completed=saved['total'], total=saved['total'])
        shutil.copyfile(partial, target)
        return

    options = copy.deepcopy(overrides or {})
    headers = dict(song.default_download_headers or options.pop('headers', {}) or provider.default_headers)
    headers = {k: v for k, v in headers.items() if k.lower() not in {'range', 'if-range', 'accept-encoding'}}
    headers['Accept-Encoding'] = 'identity'
    if offset:
        headers.update(Range=f'bytes={offset}-', **{'If-Range': saved['validator']})
    options['headers'] = headers
    if song.default_download_cookies:
        options['cookies'] = song.default_download_cookies
    response = None
    try:
        # Preserve the original engine's request backend, proxies and TLS fallback.
        try:
            response = provider.get(song.download_url, stream=True, **options)
            if not (offset and response.status_code == 416):
                response.raise_for_status()
        except Exception:
            if response is not None:
                response.close()
            options['verify'] = False
            response = provider.get(song.download_url, stream=True, **options)
            if not (offset and response.status_code == 416):
                response.raise_for_status()

        if response.status_code == 416 and offset:
            # The process may stop after the last byte, before the completion
            # checkpoint. Only trust a matching representation and exact length.
            if (response.headers.get('Content-Range') == f'bytes */{offset}' and saved.get('total') == offset and
                    response.headers.get(saved['validator_header']) == saved['validator']):
                saved['complete'] = True
                save(saved)
                progress.update(task_id, completed=offset, total=offset)
                shutil.copyfile(partial, target)
                return
            response.close()
            headers.pop('Range', None)
            headers.pop('If-Range', None)
            response = provider.get(song.download_url, stream=True, **options)
            response.raise_for_status()
            offset = 0

        encoded = response.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}
        if encoded and response.status_code == 206:
            # Range offsets describe encoded bytes, while iter_content decodes
            # them. Keep these providers working, but restart their full stream.
            response.close()
            headers.pop('Range', None)
            headers.pop('If-Range', None)
            response = provider.get(song.download_url, stream=True, **options)
            response.raise_for_status()
            offset = 0
            encoded = response.headers.get('Content-Encoding', 'identity').lower() not in {'', 'identity'}

        total = None
        if response.status_code == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match or int(match[1]) != offset or int(match[2]) < offset or int(match[2]) >= int(match[3]):
                raise RuntimeError('音源返回了无效的续传范围，已保留临时文件')
            total = int(match[3])
            if offset and saved.get('total') and saved['total'] != total:
                raise RuntimeError('音源文件大小已变化，已保留临时文件')
            # Servers must honor If-Range. Also reject explicit validator changes.
            current = response.headers.get(saved.get('validator_header', ''), '')
            if offset and current and current != saved['validator']:
                raise RuntimeError('音源文件版本已变化，已保留临时文件')
        elif response.status_code == 200:
            # No range support or If-Range mismatch: overwrite instead of append.
            offset = 0
            length = response.headers.get('Content-Length', '')
            total = int(length) if length.isdigit() and not encoded else None
        else:
            raise RuntimeError('音源返回了不支持的下载响应')
        etag = response.headers.get('ETag', '')
        validator_header = 'ETag' if etag and not etag.startswith('W/') else 'Last-Modified'
        validator = response.headers.get(validator_header) or ''
        if offset and not validator:
            validator_header, validator = saved['validator_header'], saved['validator']
        if encoded:
            validator = ''
        metadata = {'url': fingerprint, 'validator': validator, 'validator_header': validator_header,
                    'total': total, 'complete': False}

        # Truncate before checkpointing a new representation, even on interruption.
        with partial.open('ab' if offset else 'wb', buffering=0) as handle:
            save(metadata)
            detail = f'断点续传，从 {offset / 1048576:.2f} MB 继续' if offset else ('音源不支持续传或文件已变化，重新下载' if saved else '正在下载')
            if encoded:
                detail = '音源不支持续传：使用压缩传输，重新下载'
            progress.update(task_id, completed=offset, total=total, description=detail)
            written = offset
            for chunk in response.iter_content(chunk_size=song.chunk_size):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                progress.update(task_id, completed=written, total=total, description=detail)
            if (total is not None and written != total) or not written:
                raise RuntimeError('下载未完成，已保留临时文件，可继续下载')
        metadata.update(total=written, complete=True)
        save(metadata)
        shutil.copyfile(partial, target)
    finally:
        if response is not None:
            response.close()


def install():
    from musicdl.modules.sources.base import BaseMusicClient
    from musicdl.modules.utils import SongInfoUtils, usedownloadheaderscookies
    from pathvalidate import sanitize_filepath
    original = BaseMusicClient._download
    if getattr(original, '_musicdl_resumable', False):
        return

    @usedownloadheaderscookies
    def download(self, song_info, request_overrides=None, downloaded_song_infos=None, progress=None,
                 song_progress_id=0, auto_supplement_song=True):
        results = downloaded_song_infos if downloaded_song_infos is not None else []
        if (song_info.protocol.upper() != 'HTTP' or song_info.downloaded_contents or
                not isinstance(song_info.download_url, str) or not song_info.download_url.startswith(('http://', 'https://'))):
            return original(self, song_info, request_overrides, results, progress, song_progress_id, auto_supplement_song)
        song = copy.deepcopy(song_info)
        song._save_path = sanitize_filepath(song.save_path)
        song.work_dir = str(Path(song.save_path).parent)
        try:
            transfer(self, song, request_overrides, progress, song_progress_id)
            results.append(SongInfoUtils.supplsonginfothensavelyricsthenwritetags(
                song, logger_handle=self.logger_handle, disable_print=self.disable_print) if auto_supplement_song else song)
        except Exception as error:
            progress.update(song_progress_id, description=f'下载中断：{type(error).__name__}，可继续下载')
            self.logger_handle.error(f'HTTP 下载中断：{type(error).__name__}\n{traceback.format_exc()}', disable_print=self.disable_print)
        return results

    download._musicdl_resumable = True
    BaseMusicClient._download = download
