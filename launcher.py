"""Desktop launcher: detect an existing service before importing application state."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def service_status(host, port):
    local = '127.0.0.1' if host in {'0.0.0.0', 'localhost'} else '::1' if host == '::' else host
    address = f'[{local}]' if ':' in local else local
    url = f'http://{address}:{port}'
    try:
        # A configured system proxy must not intercept the local startup check.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + '/api/health', timeout=2) as response:
            health = json.load(response)
        if isinstance(health, dict) and health.get('ok') is True:
            if health.get('backend') == 'original-musicdl':
                return 'running', url
            if health.get('backend') == 'rust':
                return 'old-version', url
    except (OSError, ValueError, urllib.error.URLError):
        pass
    try:
        with socket.create_connection((local, port), timeout=1):
            return 'occupied', url
    except OSError:
        return 'available', url


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    logger = logging.getLogger('musicdl.launcher')
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    file_log = RotatingFileHandler(logs / 'startup.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    file_log.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(file_log)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        host = os.environ.get('MUSICDL_HOST') or os.environ.get('MUSICDL_BIND', '127.0.0.1')
        port = int(os.environ.get('MUSICDL_PORT', '8765'))
        if not 1 <= port <= 65535:
            raise ValueError('MUSICDL_PORT 必须在 1–65535 之间')
        status, url = service_status(host, port)
        if status == 'running':
            logger.info('musicdl 已在运行：%s。正在打开现有服务，不会重复启动。', url)
            if os.environ.get('MUSICDL_NO_BROWSER') != '1':
                webbrowser.open(url)
            return 0
        if status in {'occupied', 'old-version'}:
            logger.error('端口 %s 已被%s占用。请关闭该程序，或在 .env 中设置其他 MUSICDL_PORT 后重试。',
                         port, '旧 Rust 版 musicdl' if status == 'old-version' else '其他程序')
            return 1
        logger.info('正在启动 musicdl：%s；启动日志：%s', url, logs / 'startup.log')
        with subprocess.Popen([sys.executable, '-u', str(ROOT / 'app.py')], cwd=ROOT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace') as process:
            for line in process.stdout:
                logger.info(line.rstrip())
            code = process.wait()
        if code:
            logger.error('启动或运行失败（退出码 %s），请查看上述错误及 logs/startup.log。', code)
        return code
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception('启动失败，错误已记录到 logs/startup.log。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
