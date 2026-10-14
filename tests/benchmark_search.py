"""Offline cold/warm worker benchmark: python tests/benchmark_search.py.

Uses the actual engine and IPC, but replaces search URLs with an empty list.
It never contacts a music platform or reads the deployment's configuration.
"""
import asyncio
import json
import os
from pathlib import Path
import pickle
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def benchmark(directory):
    from legacy import app as original
    from search_runtime import SearchWorkers
    wrapper = directory / 'worker'
    wrapper.mkdir()
    (wrapper / 'engine_worker.py').write_text(
        f'import sys, runpy\nsys.path.insert(0, {str(ROOT)!r})\n'
        'from musicdl.modules.sources.qq import QQMusicClient\n'
        'QQMusicClient._constructsearchurls = lambda *args, **kwargs: []\n'
        f'runpy.run_path({str(ROOT / "engine_worker.py")!r}, run_name="__main__")\n', encoding='utf-8')
    results = {}
    for mode, idle in [('fresh', 0), ('reused', 20)]:
        workers = SearchWorkers(wrapper, idle_seconds=idle)
        samples = []
        try:
            for index in range(3):
                inp, out = directory / f'{mode}{index}.in', directory / f'{mode}{index}.out'
                inp.write_bytes(pickle.dumps((original.manager.config, 'QQMusicClient', 'offline',
                                               str(directory / f'{mode}{index}.log'), 'benchmark', None)))
                start = time.perf_counter()
                process = await workers.acquire('QQ', inp, out)
                try:
                    async with asyncio.timeout(30):
                        while not out.exists() and process.poll() is None:
                            await asyncio.sleep(.01)
                    assert pickle.loads(out.read_bytes()) == []
                    samples.append(round(time.perf_counter() - start, 3))
                finally:
                    await workers.release('QQ', process, out.exists())
        finally:
            await workers.close()
        results[mode + '_seconds'] = samples
    results['warm_overhead_reduction_percent'] = round(
        (1 - statistics.median(results['reused_seconds'][1:]) / statistics.median(results['fresh_seconds'])) * 100, 1)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='musicdl-benchmark-') as folder:
        directory = Path(folder)
        os.environ.update(MUSICDL_PROJECT_DIR=str(ROOT), MUSICDL_CONFIG=str(directory / 'config.json'),
                          MUSICDL_FILE_ROOT=str(directory / 'music'), MUSICDL_DATA_DIR=str(directory / 'data'),
                          MUSICDL_USER='', MUSICDL_PASS='')
        asyncio.run(benchmark(directory))
