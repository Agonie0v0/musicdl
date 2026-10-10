"""Prepare bind-mounted storage, then run the application as musicdl."""
import os
from pathlib import Path
import pwd
import sys


def main():
    data = Path(os.environ.get('MUSICDL_DATA_DIR', '/data'))
    music = Path(os.environ.get('MUSICDL_FILE_ROOT') or os.environ.get('MUSICDL_DOWNLOAD_DIR', '/music'))
    if os.getuid() == 0:
        account = pwd.getpwnam('musicdl')
        for directory in (data, music):
            directory.mkdir(parents=True, exist_ok=True)
            os.chown(directory, account.pw_uid, account.pw_gid)
        # Only application state needs recursive ownership repair. Do not change
        # ownership of an existing shared music library or follow its symlinks.
        for parent, directories, files in os.walk(data, followlinks=False):
            for name in directories + files:
                os.chown(Path(parent) / name, account.pw_uid, account.pw_gid, follow_symlinks=False)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    for directory in (data, music):
        if not os.access(directory, os.W_OK | os.X_OK):
            raise SystemExit(f'Cannot write to {directory}. Grant the container user write access to the mounted directory.')
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()
