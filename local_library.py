"""Read and edit local audio tags without replacing the audio content."""
from functools import lru_cache
import os
import re
from pathlib import Path
import shutil
import tempfile

import mutagen
from mutagen.id3 import ID3, TIT2, TPE1, TALB
from mutagen.mp4 import MP4
from mutagen.asf import ASF

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.mp4', '.ogg', '.opus', '.wav', '.aac', '.aiff', '.aif', '.wma', '.ape', '.wv'}
MEDIA_TYPES = {'.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.m4a': 'audio/mp4', '.mp4': 'audio/mp4', '.ogg': 'audio/ogg', '.opus': 'audio/ogg', '.wav': 'audio/wav', '.aac': 'audio/aac'}


def download_stem(title, artist):
    def clean(value, fallback):
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', str(value or '')).strip(' .')
        value = value.encode('utf-8')[:100].decode('utf-8', errors='ignore').rstrip(' .')
        return value or fallback
    stem = clean(title, '未知歌曲') + ' - ' + clean(artist, '未知歌手')
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]', stem.split('.')[0], re.I):
        stem = '_' + stem
    return stem


def tag_keys(audio):
    if isinstance(audio.tags, ID3):
        return ('TIT2', 'TPE1', 'TALB')
    if isinstance(audio, MP4):
        return ('\xa9nam', '\xa9ART', '\xa9alb')
    if isinstance(audio, ASF):
        return ('Title', 'Author', 'WM/AlbumTitle')
    return ('title', 'artist', 'album')


def text_tag(tags, key):
    value = (tags or {}).get(key, '')
    if hasattr(value, 'text'):
        value = value.text
    if isinstance(value, (list, tuple)):
        return ' / '.join(str(item) for item in value)
    return str(value)


def audio_cover(audio):
    if not audio:
        return None
    if isinstance(audio.tags, ID3):
        pictures = audio.tags.getall('APIC')
        if pictures:
            return pictures[0].data, pictures[0].mime
    if isinstance(audio, MP4) and audio.tags and audio.tags.get('covr'):
        cover = audio.tags['covr'][0]
        return bytes(cover), 'image/png' if cover.imageformat == 14 else 'image/jpeg'
    pictures = getattr(audio, 'pictures', [])
    if pictures:
        return pictures[0].data, pictures[0].mime
    return None


@lru_cache(maxsize=4096)
def read_metadata(filename, modified_ns, size):
    result = {'title': Path(filename).stem, 'artist': '', 'album': '', 'duration': 0,
              'bitrate': None, 'has_cover': False, 'editable': False}
    try:
        audio = mutagen.File(filename)
        if audio is None:
            return result
        for field, key in zip(('title', 'artist', 'album'), tag_keys(audio)):
            result[field] = text_tag(audio.tags, key) or result[field]
        result.update(duration=float(audio.info.length), bitrate=getattr(audio.info, 'bitrate', None),
                      has_cover=bool(audio_cover(audio)), editable=hasattr(audio, 'add_tags'))
    except (OSError, mutagen.MutagenError, ValueError):
        pass
    return result


def update_metadata(path, title, artist, album):
    # Save to a sibling copy and replace only after tag writing succeeds, so a
    # failed edit leaves the original audio and its other tags intact.
    fd, filename = tempfile.mkstemp(prefix='.musicdl-tags-', suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(filename)
    try:
        shutil.copy2(path, temporary)
        audio = mutagen.File(temporary)
        if audio is None or not hasattr(audio, 'add_tags'):
            raise ValueError('此文件格式不支持编辑标签')
        if audio.tags is None:
            audio.add_tags()
        for key, value, frame in zip(tag_keys(audio), (title, artist, album), (TIT2, TPE1, TALB)):
            if isinstance(audio.tags, ID3):
                audio.tags.delall(key)
                if value:
                    audio.tags.add(frame(encoding=3, text=[value]))
            elif value:
                audio.tags[key] = [value]
            elif key in audio.tags:
                del audio.tags[key]
        audio.save()
        os.replace(temporary, path)
        read_metadata.cache_clear()
    finally:
        temporary.unlink(missing_ok=True)


def read_lyrics(path):
    sidecar = path.with_suffix('.lrc')
    if sidecar.is_file() and not sidecar.is_symlink():
        return sidecar.read_text(encoding='utf-8-sig', errors='replace')
    audio = mutagen.File(path)
    if audio is None:
        return ''
    if isinstance(audio.tags, ID3):
        frames = audio.tags.getall('USLT')
        return str(frames[0].text) if frames else ''
    return text_tag(audio.tags, '\xa9lyr' if isinstance(audio, MP4) else 'lyrics')
