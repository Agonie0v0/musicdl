"""Prepare bind-mounted storage, then run the application as musicdl."""
import os
from pathlib import Path
import stat
import sys
import tempfile


def runtime_ids():
    current_uid, current_gid = os.getuid(), os.getgid()
    try:
        uid = int(os.environ.get('PUID', '10001' if current_uid == 0 else str(current_uid)))
        gid = int(os.environ.get('PGID', '10001' if current_uid == 0 else str(current_gid)))
    except ValueError:
        raise SystemExit('PUID 和 PGID 必须是数字，请填写 NAS 用户的 UID/GID。')
    if not 1 <= uid <= 2147483647 or not 0 <= gid <= 2147483647:
        raise SystemExit('PUID 和 PGID 范围无效：PUID 必须大于 0，PGID 不能小于 0。')
    if current_uid != 0 and (uid, gid) != (current_uid, current_gid):
        raise SystemExit('容器已通过 user 指定运行身份，无法切换到 PUID/PGID；请移除 user 或让两者一致。')
    return uid, gid


def repair_owner(path, uid, gid):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.chown(path, uid, gid, follow_symlinks=False)
    # Changing ownership alone does not restore write/search bits on restrictive
    # NAS mounts. Repair only owner permissions, keeping group/other bits intact.
    required = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
    mode = stat.S_IMODE(info.st_mode)
    if mode & required != required:
        path.chmod(mode | required)


def check_writable(directory):
    directory.mkdir(parents=True, exist_ok=True)
    # os.access can succeed while NAS ACLs/read-only mounts still reject writes.
    with tempfile.TemporaryFile(dir=directory):
        pass


def main():
    data = Path(os.environ.get('MUSICDL_DATA_DIR', '/data'))
    music = Path(os.environ.get('MUSICDL_FILE_ROOT') or os.environ.get('MUSICDL_DOWNLOAD_DIR', '/music'))
    if os.environ.get('HOME') == '/data/home':
        os.environ['HOME'] = str(data / 'home')
    private = data / 'engine'
    uid, gid = runtime_ids()
    try:
        if os.getuid() == 0:
            for directory in (data, music):
                directory.mkdir(parents=True, exist_ok=True)
                repair_owner(directory, uid, gid)
            if private.is_symlink():
                raise SystemExit(f'{private} 不能是符号链接，请使用数据目录内的真实文件夹。')
            private.mkdir(exist_ok=True)
            # Only application state needs recursive ownership repair. Do not
            # change an existing shared music library or follow its symlinks.
            for parent, directories, files in os.walk(data, followlinks=False):
                for name in directories + files:
                    repair_owner(Path(parent) / name, uid, gid)
            private.chmod(0o700)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
        if private.is_symlink():
            raise SystemExit(f'{private} 不能是符号链接，请使用数据目录内的真实文件夹。')
        for directory in (data, private, music):
            check_writable(directory)
    except OSError as error:
        raise SystemExit(
            f'存储目录权限检查失败（运行用户 {uid}:{gid}）：{error}。'
            '请确认 /data 和 /music 不是只读挂载，并在 NAS 共享文件夹权限中授予该用户读写权限。'
            '可通过 PUID/PGID 指定 NAS 用户；使用 user 非 root 启动时，需提前修复已有 data/engine 的所有者和权限。'
        ) from error
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()
