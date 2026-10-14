"""Bounded search reuse. A cancelled native request still owns a killable process."""
import asyncio
import hashlib
import inspect
import json
import os
import pickle
import signal
import subprocess
import sys
import time
from collections import OrderedDict


async def deliver(callback, song):
    if callback:
        result = callback(song)
        if inspect.isawaitable(result):
            await result


def terminate(process):
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


class SearchWorkers:
    def __init__(self, root, idle_seconds=20, keep=2):
        self.root, self.idle_seconds, self.keep = root, idle_seconds, keep
        self.idle = []
        self.cleanups = set()

    async def acquire(self, key, input_path, output_path):
        # Prefer the same source/configuration; each process runs only one task.
        self.idle.sort(key=lambda item: item[0] == key)
        while self.idle:
            _, process, timer = self.idle.pop()
            timer.cancel()
            if process.poll() is None:
                try:
                    process.stdin.write(json.dumps([str(input_path), str(output_path)]) + '\n')
                    process.stdin.flush()
                    return process
                except (BrokenPipeError, OSError):
                    pass
            await self.dispose(process)
        return subprocess.Popen(
            [sys.executable, str(self.root / 'engine_worker.py'), '--search',
             *(['--keep-alive'] if self.idle_seconds > 0 else []), str(input_path), str(output_path)], cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')

    async def release(self, key, process, reusable):
        if (reusable and process and process.poll() is None and self.idle_seconds > 0
                and len(self.idle) < self.keep and getattr(process, 'stdin', None)):
            def expire():
                self.idle[:] = [item for item in self.idle if item[1] is not process]
                task = asyncio.create_task(self.dispose(process))
                self.cleanups.add(task)
                task.add_done_callback(self.cleanups.discard)
            timer = asyncio.get_running_loop().call_later(self.idle_seconds, expire)
            self.idle.append((key, process, timer))
        else:
            await self.dispose(process)

    async def dispose(self, process):
        try:
            await asyncio.to_thread(terminate, process)
        finally:
            if process and getattr(process, 'stdin', None):
                try:
                    process.stdin.close()
                except OSError:
                    pass

    async def close(self):
        items, self.idle = self.idle, []
        for _, process, timer in items:
            timer.cancel()
            await self.dispose(process)
        if self.cleanups:
            await asyncio.gather(*self.cleanups)


class SearchCoordinator:
    def __init__(self, search, ttl=30, max_bytes=8 * 1024 * 1024):
        self.search, self.ttl, self.max_bytes = search, ttl, max_bytes
        self.cache, self.flights = OrderedDict(), {}
        self.cache_bytes = 0

    async def run(self, cfg, source, keyword, publish=None, refresh=False):
        # Refresh controls invalidation; it must not create a separate cache key.
        cache_config = {k: v for k, v in cfg.items() if k != '_musicdl_refresh'}
        key = hashlib.sha256(json.dumps([cache_config, source, keyword.strip()], sort_keys=True).encode()).hexdigest()
        now = time.monotonic()
        for cached_key, (until, payload) in list(self.cache.items()):
            if until <= now or (refresh and cached_key == key):
                self.cache.pop(cached_key)
                self.cache_bytes -= len(payload)
        if not refresh and key in self.cache:
            _, payload = self.cache[key]
            self.cache.move_to_end(key)
            songs = pickle.loads(payload)
            for song in songs:
                await deliver(publish, song)
            return songs
        flight = self.flights.get(key)
        if flight is None:
            flight = {'songs': [], 'changed': asyncio.Event(), 'users': 0}
            def append(song):
                flight['songs'].append(song)
                flight['changed'].set()
            async def run():
                try:
                    songs = await self.search(cfg, source, keyword, append)
                    # Never cache an empty or failed search; retry must stay useful.
                    if songs and self.ttl > 0:
                        payload = pickle.dumps(songs, protocol=pickle.HIGHEST_PROTOCOL)
                        if len(payload) <= self.max_bytes:
                            while self.cache and (self.cache_bytes + len(payload) > self.max_bytes or len(self.cache) >= 32):
                                _, (_, evicted) = self.cache.popitem(last=False)
                                self.cache_bytes -= len(evicted)
                            previous = self.cache.pop(key, None)
                            if previous:
                                self.cache_bytes -= len(previous[1])
                            self.cache[key] = (time.monotonic() + self.ttl, payload)
                            self.cache_bytes += len(payload)
                    return songs
                finally:
                    flight['changed'].set()
            flight['task'] = asyncio.create_task(run())
            self.flights[key] = flight
        flight['users'] += 1
        cursor = 0
        try:
            while True:
                flight['changed'].clear()
                while cursor < len(flight['songs']):
                    await deliver(publish, flight['songs'][cursor])
                    cursor += 1
                if flight['task'].done():
                    return flight['task'].result()
                await flight['changed'].wait()
        finally:
            flight['users'] -= 1
            if not flight['users']:
                if self.flights.get(key) is flight:
                    del self.flights[key]
                if not flight['task'].done():
                    flight['task'].cancel()
                await asyncio.shield(asyncio.gather(flight['task'], return_exceptions=True))

    async def close(self):
        tasks = [flight['task'] for flight in self.flights.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()
        self.cache_bytes = 0
