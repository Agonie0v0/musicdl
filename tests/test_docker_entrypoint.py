"""Storage preparation checks; Linux container smoke tests cover real UID changes."""
import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest

spec = importlib.util.spec_from_file_location('docker_entrypoint', Path(__file__).resolve().parents[1] / 'docker-entrypoint.py')
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


def configure(monkeypatch, tmp_path, uid=0, gid=0):
    monkeypatch.setenv('MUSICDL_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('MUSICDL_DOWNLOAD_DIR', str(tmp_path / 'music'))
    monkeypatch.delenv('MUSICDL_FILE_ROOT', raising=False)
    monkeypatch.delenv('PUID', raising=False)
    monkeypatch.delenv('PGID', raising=False)
    monkeypatch.setattr(os, 'getuid', lambda: uid, raising=False)
    monkeypatch.setattr(os, 'getgid', lambda: gid, raising=False)
    monkeypatch.setattr(sys, 'argv', ['docker-entrypoint.py', 'python', 'app.py'])


def test_storage_is_prepared_before_drop_and_checked_after_drop(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setenv('PUID', '1026')
    monkeypatch.setenv('PGID', '100')
    monkeypatch.setattr(os, 'chown', lambda path, uid, gid, **kwargs: calls.append(('chown', Path(path), uid, gid)), raising=False)
    monkeypatch.setattr(os, 'setgroups', lambda groups: calls.append(('groups', groups)), raising=False)
    monkeypatch.setattr(os, 'setgid', lambda gid: calls.append(('gid', gid)), raising=False)
    monkeypatch.setattr(os, 'setuid', lambda uid: calls.append(('uid', uid)), raising=False)
    real_check = entrypoint.check_writable
    monkeypatch.setattr(entrypoint, 'check_writable', lambda path: (calls.append(('check', path)), real_check(path)))
    monkeypatch.setattr(os, 'execvp', lambda *args: calls.append(('exec', args)))
    entrypoint.main()
    assert (tmp_path / 'data/engine').is_dir()
    drop = calls.index(('uid', 1026))
    assert all(c[0] not in {'chown', 'groups', 'gid'} for c in calls[drop + 1:])
    assert len([c for c in calls[drop + 1:] if c[0] == 'check']) == 3
    assert any(c == ('chown', tmp_path / 'data/engine', 1026, 100) for c in calls)
    assert calls[-1][0] == 'exec'


def test_owner_permission_repair_and_symlink_skip(monkeypatch, tmp_path):
    path = tmp_path / 'state'
    path.mkdir()
    modes, owners = [], []
    info = type('Info', (), {'st_mode': stat.S_IFDIR | 0o500, 'st_uid': 0, 'st_gid': 0})()
    monkeypatch.setattr(Path, 'lstat', lambda path: info)
    monkeypatch.setattr(Path, 'chmod', lambda path, mode: modes.append(mode))
    monkeypatch.setattr(os, 'chown', lambda *args, **kwargs: owners.append(args), raising=False)
    entrypoint.repair_owner(path, 10001, 10001)
    assert modes == [0o700] and len(owners) == 1
    info.st_mode = stat.S_IFLNK | 0o777
    entrypoint.repair_owner(path, 10001, 10001)
    assert len(modes) == len(owners) == 1


def test_nonroot_permission_error_explains_nas_configuration(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 1026, 100)
    def denied(path):
        raise PermissionError(f'Permission denied: {path / "engine"}')
    monkeypatch.setattr(entrypoint, 'check_writable', denied)
    with pytest.raises(SystemExit, match='1026:100.*PUID/PGID'):
        entrypoint.main()


@pytest.mark.parametrize('uid,gid', [('abc', '100'), ('0', '100'), ('1026', '-1')])
def test_invalid_identity_is_rejected(monkeypatch, tmp_path, uid, gid):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv('PUID', uid)
    monkeypatch.setenv('PGID', gid)
    with pytest.raises(SystemExit, match='PUID 和 PGID'):
        entrypoint.runtime_ids()


def test_nonroot_user_cannot_silently_ignore_requested_identity(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path, 1026, 100)
    monkeypatch.setenv('PUID', '10001')
    with pytest.raises(SystemExit, match='user'):
        entrypoint.runtime_ids()
