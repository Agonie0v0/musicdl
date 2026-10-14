"""Own the optional official telegram-bot-api child process, bound to loopback."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


LOCAL_URL = 'http://127.0.0.1:8081'


class LocalBotAPI:
    def __init__(self, log):
        self.log = log
        self.process = None
        self.settings = None
        self.watch = None
        self.lock = asyncio.Lock()
        self.failure = ''

    @staticmethod
    def binary():
        return shutil.which(os.environ.get('MUSICDL_TG_LOCAL_BINARY', 'telegram-bot-api'))

    @staticmethod
    def credentials(config):
        api_id, api_hash = config['tg_local_api_id'].strip(), config['tg_local_api_hash'].strip()
        if not api_id.isdigit() or not 0 < int(api_id) < 2147483648:
            raise ValueError('内置 Telegram 服务需要有效的 API ID（正整数）')
        import re
        if not re.fullmatch(r'[a-fA-F0-9]{32}', api_hash):
            raise ValueError('内置 Telegram 服务需要 32 位 API Hash，请从 my.telegram.org 获取')
        return api_id, api_hash

    def validate(self, config):
        settings = self.credentials(config)
        if not self.binary():
            raise ValueError('未找到官方 telegram-bot-api 程序。请使用包含内置服务的新 Docker 镜像；源码部署需安装该程序并加入 PATH')
        return settings

    def matches(self, config):
        return self.running and self.settings == self.credentials(config)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def status(self):
        return {'available': bool(self.binary()), 'running': self.running, 'detail': self.failure}

    def _launch(self, settings):
        # Refuse occupied ports instead of accidentally trusting another local server.
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', 8081))
            except OSError:
                raise ValueError('内置 Telegram 服务端口 8081 已被占用，请停止冲突服务或使用自建 API 地址') from None
        data = Path(os.environ.get('MUSICDL_DATA_DIR', Path(__file__).resolve().parent / 'data')) / 'telegram-bot-api'
        data.mkdir(parents=True, exist_ok=True)
        (data / 'temp').mkdir(exist_ok=True)
        if os.name != 'nt':
            data.chmod(0o700)
        env = {**os.environ, 'TELEGRAM_API_ID': settings[0], 'TELEGRAM_API_HASH': settings[1]}
        self.process = subprocess.Popen(
            [self.binary(), '--local', '--http-ip-address=127.0.0.1', '--http-port=8081',
             f'--dir={data.resolve()}', f'--temp-dir={data.resolve() / "temp"}', '--verbosity=0'],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.running:
                raise RuntimeError('内置 Telegram 服务启动失败，请检查 API ID/API Hash、数据目录权限及程序依赖')
            try:
                with socket.create_connection(('127.0.0.1', 8081), timeout=.2):
                    return
            except OSError:
                time.sleep(.1)
        self._terminate()
        raise RuntimeError('内置 Telegram 服务启动超时，请检查数据目录权限与服务器资源')

    def _terminate(self):
        if self.running:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    async def start(self, config):
        settings = self.validate(config)
        async with self.lock:
            if self.running and self.settings == settings:
                return
            await self._stop()
            try:
                await self._spawn(settings)
            except Exception as error:
                self.failure = str(error)
                await asyncio.to_thread(self._terminate)
                raise
            self.settings, self.failure = settings, ''
            self.watch = asyncio.create_task(self._monitor())
            self.log('info', '内置 Telegram 本地服务已启动，仅监听本机 8081 端口', category='bot')

    async def _monitor(self):
        while True:
            await asyncio.sleep(5)
            if not self.running:
                self.failure = '内置 Telegram 服务意外退出，正在重启'
                self.log('warning', self.failure, category='bot')
                try:
                    await self._spawn(self.settings)
                    self.failure = ''
                except Exception:
                    self.failure = '内置 Telegram 服务重启失败，请检查程序、端口和数据目录权限'
                    self.log('error', self.failure, category='bot')
                    await asyncio.to_thread(self._terminate)

    async def _spawn(self, settings):
        task = asyncio.create_task(asyncio.to_thread(self._launch, settings))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.to_thread(self._terminate)
            raise

    async def _stop(self):
        if self.watch:
            self.watch.cancel()
            await asyncio.gather(self.watch, return_exceptions=True)
            self.watch = None
        await asyncio.to_thread(self._terminate)
        self.settings = None

    async def stop(self):
        async with self.lock:
            await self._stop()
