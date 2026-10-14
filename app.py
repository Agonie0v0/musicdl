"""React UI with the original musicdl provider/search/parser/download engine."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
os.environ.setdefault('MUSICDL_PROJECT_DIR', str(ROOT))
os.environ.setdefault('MUSICDL_HOST', os.environ.get('MUSICDL_BIND', '127.0.0.1'))
if os.environ.get('MUSICDL_DOWNLOAD_DIR'):
    os.environ.setdefault('MUSICDL_FILE_ROOT', os.environ['MUSICDL_DOWNLOAD_DIR'])

from legacy import app as original
from engine_api import install

app = original.app
# Spawned playlist workers must not reopen the API database or recover downloads.
if __name__ != '__mp_main__':
    install(original, ROOT)

if __name__ == '__main__':
    import multiprocessing
    import uvicorn
    multiprocessing.freeze_support()
    uvicorn.run(app, host=original.SERVER_HOST, port=int(os.environ.get('MUSICDL_PORT', '8765')))
