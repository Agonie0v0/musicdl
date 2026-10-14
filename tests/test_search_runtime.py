"""Search sharing and worker lifecycle checks without contacting music providers."""
import asyncio

from search_runtime import SearchCoordinator, SearchWorkers


def test_shared_search_survives_one_subscriber_cancelling_and_caches_by_config():
    async def check():
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def source(cfg, name, keyword, publish):
            calls.append((cfg, keyword))
            publish({'id': 'one'})
            entered.set()
            await finish.wait()
            return [{'id': 'one'}]
        runtime = SearchCoordinator(source)
        first_results, second_results = [], []
        first = asyncio.create_task(runtime.run({}, 'QQ', 'hello', first_results.append))
        await entered.wait()
        second = asyncio.create_task(runtime.run({}, 'QQ', 'hello', second_results.append))
        while not second_results:
            await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert len(calls) == 1 and not second.done()
        finish.set()
        assert await second == [{'id': 'one'}]
        assert await runtime.run({}, 'QQ', 'hello') == [{'id': 'one'}]
        assert len(calls) == 1
        await runtime.run({'cookie': 'changed'}, 'QQ', 'hello')
        await runtime.run({}, 'QQ', 'hello', refresh=True)
        assert len(calls) == 3
        await runtime.run({'_musicdl_refresh': True}, 'QQ', 'hello', refresh=True)
        await runtime.run({'_musicdl_refresh': False}, 'QQ', 'hello')
        assert len(calls) == 4
        await runtime.close()
    asyncio.run(check())


def test_last_subscriber_cancels_native_work_and_empty_results_do_not_cache():
    async def check():
        entered, stopped = asyncio.Event(), asyncio.Event()
        calls = 0
        async def source(cfg, name, keyword, publish):
            nonlocal calls
            calls += 1
            if keyword == 'empty':
                return []
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        runtime = SearchCoordinator(source)
        task = asyncio.create_task(runtime.run({}, 'QQ', 'blocked'))
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert stopped.is_set() and not runtime.flights
        await runtime.run({}, 'QQ', 'empty')
        await runtime.run({}, 'QQ', 'empty')
        assert calls == 3 and not runtime.cache
    asyncio.run(check())


def test_cache_expires_and_never_exceeds_byte_budget():
    async def check():
        calls = 0
        async def source(*args):
            nonlocal calls
            calls += 1
            return ['x' * 80]
        runtime = SearchCoordinator(source, ttl=.01, max_bytes=180)
        await runtime.run({}, 'QQ', 'one')
        await asyncio.sleep(.02)
        await runtime.run({}, 'QQ', 'one')
        await runtime.run({}, 'QQ', 'two')
        assert calls == 3 and len(runtime.cache) == 1
        assert runtime.cache_bytes <= 180
        runtime.max_bytes = 1
        await runtime.run({}, 'QQ', 'large')
        assert len(runtime.cache) == 1
    asyncio.run(check())


def test_real_worker_reuses_pid_and_exits_after_idle_timeout(tmp_path):
    # Exercise the real process transport using a small deterministic child.
    (tmp_path / 'engine_worker.py').write_text('''
import json, os, sys
from pathlib import Path
paths = sys.argv[-2:]
while paths:
    Path(paths[1]).write_text(str(os.getpid()))
    line = sys.stdin.readline()
    paths = json.loads(line) if line else None
''', encoding='utf-8')
    async def check():
        workers = SearchWorkers(tmp_path, idle_seconds=.1)
        first, child_pid = None, None
        try:
            for index in range(2):
                output = tmp_path / f'{index}.out'
                process = await workers.acquire('QQ', tmp_path / 'input', output)
                for _ in range(100):
                    if output.exists():
                        break
                    await asyncio.sleep(.01)
                assert output.exists()
                if first is None:
                    first, child_pid = process, int(output.read_text())
                assert first.pid == process.pid
                assert child_pid == int(output.read_text())
                await workers.release('QQ', process, True)
            await asyncio.sleep(.2)
            await workers.close()
            assert first.poll() is not None
        finally:
            await workers.close()
            if first and first.poll() is None:
                await workers.dispose(first)
    asyncio.run(check())
